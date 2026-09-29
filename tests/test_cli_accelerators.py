from __future__ import annotations

import builtins
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from model_lab.cli import main


REPO_ROOT = Path(__file__).resolve().parents[1]
CPU_MANIFEST = (
    REPO_ROOT / "tests" / "fixtures" / "minimal-deployable" / "model.yaml"
)
CHRONOS_MANIFEST = (
    REPO_ROOT / "tests" / "fixtures" / "minimal-chronos" / "model.yaml"
)
MISSING_GPU_METADATA_MANIFEST = (
    REPO_ROOT / "tests" / "fixtures" / "missing-gpu-metadata" / "model.yaml"
)


def run_model_lab(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "model_lab", *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


class AcceleratorDryRunTests(unittest.TestCase):
    def test_minimal_chronos_uses_the_common_offline_plan_contract(self) -> None:
        cpu = run_model_lab("dry-run", str(CPU_MANIFEST), "--format", "json")
        chronos = run_model_lab(
            "dry-run", str(CHRONOS_MANIFEST), "--format", "json"
        )

        self.assertEqual(cpu.returncode, 0, cpu.stderr)
        self.assertEqual(chronos.returncode, 0, chronos.stderr)
        cpu_plan = json.loads(cpu.stdout)
        chronos_plan = json.loads(chronos.stdout)
        self.assertEqual(set(chronos_plan), set(cpu_plan))
        self.assertEqual(chronos_plan["target"], "chronos")
        self.assertEqual(chronos_plan["adapter"], "chronos")
        self.assertEqual(
            chronos_plan["requirements"],
            {
                "accelerator": "gpu",
                "artifact": {"format": "safetensors"},
                "cuda": {"version": ">=12.1,<13.0"},
                "framework": {"name": "pytorch", "version": ">=2.3,<3.0"},
                "gpu": {
                    "compute_capability": ">=8.0",
                    "memory_gb_min": 16,
                    "vendor": "nvidia",
                },
            },
        )
        self.assertEqual(
            chronos_plan["artifacts"][0]["uri"],
            "artifact://minimal-chronos/weights.safetensors",
        )

    def test_missing_gpu_runtime_metadata_reports_exact_field_paths(self) -> None:
        result = run_model_lab(
            "validate", str(MISSING_GPU_METADATA_MANIFEST), "--format", "json"
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        issues = json.loads(result.stdout)["issues"]
        self.assertEqual(
            {
                issue["path"]
                for issue in issues
                if issue["code"] == "missing_required_field"
            },
            {
                "target.runtime.artifact.format",
                "target.runtime.cuda.version",
                "target.runtime.framework.version",
                "target.runtime.gpu.memory_gb_min",
            },
        )
        self.assertTrue(
            all(issue["suggestion"].startswith("add target.runtime") for issue in issues)
        )

    def test_chronos_dry_run_does_not_import_ml_stacks_or_touch_network(self) -> None:
        original_import = builtins.__import__

        def guarded_import(
            name: str,
            globals: object = None,
            locals: object = None,
            fromlist: tuple[str, ...] = (),
            level: int = 0,
        ) -> object:
            if name.split(".", 1)[0] in {"torch", "transformers", "chronos"}:
                raise AssertionError(f"offline dry-run imported {name}")
            return original_import(name, globals, locals, fromlist, level)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(builtins, "__import__", side_effect=guarded_import),
            mock.patch.object(
                socket.socket,
                "connect",
                side_effect=AssertionError("offline dry-run opened a socket"),
            ),
            mock.patch.dict(
                os.environ,
                {"HTTP_PROXY": "http://127.0.0.1:7897"},
                clear=False,
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main(
                ["dry-run", str(CHRONOS_MANIFEST), "--format", "json"]
            )

        self.assertEqual(exit_code, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        self.assertFalse(json.loads(stdout.getvalue())["safety"]["remote_writes"])

    def test_generic_gpu_target_uses_the_same_runtime_contract(self) -> None:
        source = CHRONOS_MANIFEST.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "gpu.yaml"
            manifest.write_text(
                source.replace("kind: chronos", "kind: gpu"),
                encoding="utf-8",
            )
            result = run_model_lab(
                "dry-run", str(manifest), "--format", "json"
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["target"], "gpu")
        self.assertEqual(payload["adapter"], "gpu")
        self.assertEqual(payload["requirements"]["accelerator"], "gpu")

    def test_cpu_target_rejects_gpu_runtime_metadata(self) -> None:
        source = CHRONOS_MANIFEST.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "contradictory-cpu.yaml"
            manifest.write_text(
                source.replace("kind: chronos", "kind: cpu-iot"),
                encoding="utf-8",
            )
            result = run_model_lab(
                "dry-run", str(manifest), "--format", "json"
            )

        self.assertEqual(result.returncode, 3, result.stderr)
        issues = json.loads(result.stdout)["issues"]
        self.assertIn(
            {
                "code": "unexpected_target_runtime",
                "path": "target.runtime",
                "message": "cpu-iot targets must not declare GPU runtime requirements",
                "suggestion": "remove target.runtime from the cpu-iot manifest",
            },
            issues,
        )

    def test_invalid_gpu_runtime_values_are_field_level_contract_issues(self) -> None:
        source = CHRONOS_MANIFEST.read_text(encoding="utf-8")
        invalid = (
            source.replace("accelerator: gpu", "accelerator: cpu")
            .replace("memory_gb_min: 16", "memory_gb_min: 0")
            .replace('version: ">=12.1,<13.0"', 'version: "twelve"')
            .replace("name: pytorch", "name: tensorflow")
            .replace("format: safetensors", "format: pickle")
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "invalid-runtime.yaml"
            manifest.write_text(invalid, encoding="utf-8")
            result = run_model_lab(
                "validate", str(manifest), "--format", "json"
            )

        self.assertEqual(result.returncode, 3, result.stderr)
        issues = json.loads(result.stdout)["issues"]
        self.assertEqual(
            {(issue["code"], issue["path"]) for issue in issues},
            {
                ("invalid_gpu_runtime_value", "target.runtime.accelerator"),
                (
                    "invalid_gpu_runtime_value",
                    "target.runtime.gpu.memory_gb_min",
                ),
                (
                    "invalid_runtime_version_constraint",
                    "target.runtime.cuda.version",
                ),
                ("unsupported_framework", "target.runtime.framework.name"),
                (
                    "unsupported_artifact_format",
                    "target.runtime.artifact.format",
                ),
            },
        )

    def test_gpu_memory_requirement_must_be_finite(self) -> None:
        source = CHRONOS_MANIFEST.read_text(encoding="utf-8")
        for yaml_value in (".nan", ".inf", "-.inf"):
            with self.subTest(yaml_value=yaml_value):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    manifest = Path(temporary_directory) / "non-finite-memory.yaml"
                    manifest.write_text(
                        source.replace(
                            "memory_gb_min: 16",
                            f"memory_gb_min: {yaml_value}",
                        ),
                        encoding="utf-8",
                    )
                    result = run_model_lab(
                        "validate", str(manifest), "--format", "json"
                    )

                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertNotIn("NaN", result.stdout)
                self.assertNotIn("Infinity", result.stdout)
                issues = json.loads(result.stdout)["issues"]
                self.assertIn(
                    {
                        "code": "invalid_gpu_runtime_value",
                        "path": "target.runtime.gpu.memory_gb_min",
                        "message": (
                            "minimum GPU memory must be a positive finite runtime number"
                        ),
                        "suggestion": (
                            "set a positive GPU memory requirement within the runtime "
                            "numeric range"
                        ),
                    },
                    issues,
                )

    def test_gpu_memory_requirement_rejects_unrepresentable_integer(self) -> None:
        source = CHRONOS_MANIFEST.read_text(encoding="utf-8")
        huge_integer = "1" + ("0" * 400)
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "huge-memory.yaml"
            manifest.write_text(
                source.replace(
                    "memory_gb_min: 16",
                    f"memory_gb_min: {huge_integer}",
                ),
                encoding="utf-8",
            )
            result = run_model_lab(
                "validate", str(manifest), "--format", "json"
            )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        issues = json.loads(result.stdout)["issues"]
        self.assertIn(
            (
                "invalid_gpu_runtime_value",
                "target.runtime.gpu.memory_gb_min",
            ),
            {(issue["code"], issue["path"]) for issue in issues},
        )


if __name__ == "__main__":
    unittest.main()
