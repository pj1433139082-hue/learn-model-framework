from __future__ import annotations

import html
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml  # type: ignore[import-untyped]

from model_lab.catalog import build_catalog
from model_lab.scaffolding import ScaffoldRequest, WorkspaceError, scaffold_model


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_model_lab(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "model_lab", *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def request(
    model_id: str = "reusable-reference",
    *,
    target: str = "cpu-iot",
    **runtime: Any,
) -> ScaffoldRequest:
    return ScaffoldRequest(
        model_id=model_id,
        name="Reusable reference",
        owner="model-lab-tests",
        target=target,
        **runtime,
    )


def accelerated_request(target: str) -> ScaffoldRequest:
    return request(
        target=target,
        gpu_compute_capability=">=8.0",
        gpu_memory_gb_min=16,
        cuda_version=">=12.1,<13.0",
        framework_name="pytorch",
        framework_version=">=2.3,<3.0",
        artifact_format="safetensors",
    )


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class ScaffoldModelTests(unittest.TestCase):
    def test_new_workspace_seeds_machine_readable_interaction_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = scaffold_model(
                request(), Path(temporary) / "models", apply=True
            )
            workspace = Path(report.destination)

            register = yaml.safe_load(
                (workspace / "decisions.yaml").read_text(encoding="utf-8")
            )

        self.assertEqual(report.template_version, "new-model-v2")
        self.assertEqual(register["schema_version"], "1.0")
        self.assertEqual(register["model_id"], "reusable-reference")
        self.assertEqual(register["owner"], "model-lab-tests")
        decisions = register["decisions"]
        self.assertTrue(decisions)
        self.assertIn("ASK", {decision["level"] for decision in decisions})
        self.assertIn("CONFIRM", {decision["level"] for decision in decisions})
        self.assertNotIn("AUTHORIZE", {decision["level"] for decision in decisions})
        self.assertEqual(
            {decision["blocks_transition"] for decision in decisions},
            {"specified", "built", "validated", "releasable"},
        )
        self.assertTrue(all(decision["status"] == "open" for decision in decisions))

    def test_accelerated_workspace_adds_target_specific_questions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = scaffold_model(
                accelerated_request("gpu"), Path(temporary) / "models", apply=True
            )
            workspace = Path(report.destination)
            register = yaml.safe_load(
                (workspace / "decisions.yaml").read_text(encoding="utf-8")
            )

        accelerator = next(
            decision
            for decision in register["decisions"]
            if decision["id"] == "target-accelerator-contract"
        )
        self.assertEqual(accelerator["blocks_transition"], "specified")
        self.assertEqual(accelerator["level"], "ASK")
        self.assertTrue(accelerator["required"])
        self.assertIn("CUDA >=12.1,<13.0", accelerator["recommendation"])
        self.assertIn("pytorch >=2.3,<3.0", accelerator["recommendation"])
        self.assertIn("safetensors", accelerator["recommendation"])

    def test_default_runtime_profile_renders_contract_and_pinned_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = scaffold_model(
                request(), Path(temporary) / "models", apply=True
            )
            workspace = Path(report.destination)
            manifest = yaml.safe_load(
                (workspace / "model.yaml").read_text(encoding="utf-8")
            )

            self.assertEqual(
                report.runtime_profile,
                "linux-x86_64-py311-mlflow222",
            )
            self.assertEqual(
                manifest["target"]["environment"]["python"],
                ">=3.11,<3.12",
            )
            self.assertEqual(
                manifest["target"]["environment"]["operating_system"],
                "linux",
            )
            lock = (workspace / "runtime" / "requirements.lock.txt").read_text(
                encoding="utf-8"
            )
            self.assertEqual(
                lock.splitlines(),
                [
                    "mlflow==2.22.2",
                    "cloudpickle==3.1.2",
                    "numpy==2.3.3",
                    "pandas==2.3.3",
                ],
            )
            self.assertTrue((workspace / "runtime" / "README.md").is_file())
            notebook = json.loads(
                (workspace / "notebooks" / "model.ipynb").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(notebook["metadata"]["language_info"]["version"], "3.11")

    def test_legacy_and_unknown_profiles_cannot_create_new_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(WorkspaceError) as raised:
                scaffold_model(
                    request(runtime_profile="windows-x86_64-py312-legacy"),
                    Path(temporary) / "models",
                    apply=True,
                )
        self.assertEqual(raised.exception.code, "inactive_runtime_profile")

        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(WorkspaceError) as raised:
                scaffold_model(
                    request(runtime_profile="unknown-profile"),
                    Path(temporary) / "models",
                    apply=False,
                )
        self.assertEqual(raised.exception.code, "unsupported_runtime_profile")

    def test_new_cli_does_not_expose_the_legacy_runtime_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_model_lab(
                "new",
                "profile-cli-reference",
                "--name",
                "Profile CLI reference",
                "--owner",
                "model-lab-tests",
                "--models-root",
                str(Path(temporary) / "models"),
                "--runtime-profile",
                "windows-x86_64-py312-legacy",
                "--format",
                "json",
            )

        self.assertEqual(result.returncode, 2)
        self.assertNotIn("windows-x86_64-py312-legacy", result.stdout)

    def test_dry_plan_reports_the_tree_without_writing_anything(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            models_root = Path(temporary) / "models"

            report = scaffold_model(request(), models_root, apply=False)

            destination = Path(report.destination)
            self.assertEqual(report.status, "planned")
            self.assertEqual(
                destination.resolve(),
                (models_root / "_drafts" / "reusable-reference").resolve(),
            )
            self.assertTrue(report.files)
            self.assertIn("model.yaml", {Path(path).name for path in report.files})
            self.assertIn("model-spec.md", {Path(path).name for path in report.files})
            self.assertIn(
                "tests/model_test_template.py",
                {Path(path).as_posix() for path in report.files},
            )
            self.assertFalse(models_root.exists())
            payload = report.as_dict()
            self.assertEqual(payload["status"], report.status)
            self.assertEqual(payload["destination"], str(report.destination))
            self.assertEqual(payload["files"], list(report.files))

    def test_apply_creates_the_same_deterministic_tree_in_fresh_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            first_root = base / "first" / "models"
            second_root = base / "second" / "models"

            first = scaffold_model(request(), first_root, apply=True)
            second = scaffold_model(request(), second_root, apply=True)

            first_destination = Path(first.destination)
            second_destination = Path(second.destination)
            self.assertEqual(first.status, "created")
            self.assertEqual(second.status, "created")
            self.assertEqual(first.files, second.files)
            self.assertEqual(
                tree_bytes(first_destination),
                tree_bytes(second_destination),
            )
            self.assertTrue((first_destination / "model.yaml").is_file())

    def test_existing_destination_is_rejected_without_partial_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            models_root = Path(temporary) / "models"
            destination = models_root / "_drafts" / "reusable-reference"
            destination.mkdir(parents=True)
            sentinel = destination / "keep.txt"
            sentinel.write_text("owned by the user\n", encoding="utf-8")
            before = tree_bytes(models_root)

            with self.assertRaises(WorkspaceError):
                scaffold_model(request(), models_root, apply=True)

            self.assertEqual(tree_bytes(models_root), before)
            self.assertEqual(
                sentinel.read_text(encoding="utf-8"), "owned by the user\n"
            )

    def test_unreadable_or_invalid_existing_manifest_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            models_root = Path(temporary) / "models"
            broken = models_root / "broken" / "model.yaml"
            broken.parent.mkdir(parents=True)
            broken.write_text("model: [not-a-valid-manifest]\n", encoding="utf-8")

            with self.assertRaises(WorkspaceError) as raised:
                scaffold_model(request(), models_root, apply=True)

            self.assertEqual(raised.exception.code, "model_inventory_invalid")
            self.assertFalse((models_root / "_drafts").exists())

    def test_invalid_or_escaping_model_ids_are_rejected_before_writing(self) -> None:
        invalid_ids = (
            "../escape",
            "bad/name",
            "bad\\name",
            "bad id",
            "con",
            "nul",
            "trailing.",
            "",
        )
        for model_id in invalid_ids:
            with (
                self.subTest(model_id=model_id),
                tempfile.TemporaryDirectory() as temporary,
            ):
                temporary_root = Path(temporary)
                models_root = temporary_root / "models"

                with self.assertRaises(WorkspaceError):
                    scaffold_model(request(model_id), models_root, apply=True)

                self.assertEqual(list(temporary_root.iterdir()), [])

    def test_rendered_code_and_notebook_escape_user_supplied_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            models_root = Path(temporary) / "models"
            display_name = (
                r'Quoted "model" \ path and triple """ text '
                '<img src=x onerror="alert(1)">'
            )
            report = scaffold_model(
                ScaffoldRequest(
                    model_id="safe-python-name",
                    name=display_name,
                    owner="model-lab-tests",
                ),
                models_root,
                apply=True,
            )
            workspace = Path(report.destination)

            manifest = yaml.safe_load(
                (workspace / "model.yaml").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["model"]["name"], display_name)
            notebook = json.loads(
                (workspace / "notebooks" / "model.ipynb").read_text(encoding="utf-8")
            )
            self.assertIn(
                html.escape(display_name, quote=True),
                notebook["cells"][0]["source"][0],
            )
            for relative in ("src/model.py", "tests/model_test_template.py"):
                source = (workspace / relative).read_text(encoding="utf-8")
                compile(source, relative, "exec")

            for relative in ("README.md", "model-spec.md", "CHECKLIST.md"):
                markdown = (workspace / relative).read_text(encoding="utf-8")
                self.assertNotIn("<img", markdown)

    def test_network_or_device_models_root_is_rejected_before_access(self) -> None:
        with self.assertRaises(WorkspaceError) as raised:
            scaffold_model(
                request(),
                r"\\server\share\models",
                apply=False,
            )

        self.assertEqual(raised.exception.code, "unsafe_workspace_path")

    def test_surrogate_text_is_rejected_as_a_structured_request_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            unsafe_name = "bad" + chr(0xD800) + "name"

            with self.assertRaises(WorkspaceError) as raised:
                scaffold_model(
                    ScaffoldRequest(
                        model_id="safe-id",
                        name=unsafe_name,
                        owner="model-lab-tests",
                    ),
                    Path(temporary) / "models",
                    apply=False,
                )

            self.assertEqual(raised.exception.code, "invalid_name")

    def test_gpu_and_chronos_require_a_complete_runtime_contract(self) -> None:
        for target in ("gpu", "chronos"):
            with (
                self.subTest(target=target),
                tempfile.TemporaryDirectory() as temporary,
            ):
                models_root = Path(temporary) / "models"

                with self.assertRaises(WorkspaceError):
                    scaffold_model(request(target=target), models_root, apply=True)

                self.assertFalse(models_root.exists())

    def test_accelerated_targets_render_complete_runtime_contracts(self) -> None:
        for target in ("gpu", "chronos"):
            with (
                self.subTest(target=target),
                tempfile.TemporaryDirectory() as temporary,
            ):
                report = scaffold_model(
                    accelerated_request(target),
                    Path(temporary) / "models",
                    apply=True,
                )

                self.assertEqual(report.status, "created")
                self.assertTrue(report.files)
                manifest = yaml.safe_load(
                    (Path(report.destination) / "model.yaml").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(manifest["target"]["kind"], target)
                runtime = manifest["target"]["runtime"]
                self.assertEqual(runtime["accelerator"], "gpu")
                self.assertEqual(runtime["gpu"]["compute_capability"], ">=8.0")
                self.assertEqual(runtime["gpu"]["memory_gb_min"], 16.0)
                self.assertEqual(runtime["cuda"]["version"], ">=12.1,<13.0")
                self.assertEqual(runtime["framework"]["name"], "pytorch")
                self.assertEqual(runtime["framework"]["version"], ">=2.3,<3.0")
                self.assertEqual(runtime["artifact"]["format"], "safetensors")

    def test_drafts_live_below_models_drafts_and_are_not_catalog_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            models_root = Path(temporary) / "models"

            report = scaffold_model(request(), models_root, apply=True)
            catalog = build_catalog(models_root, expected_count=0)

            self.assertEqual(
                Path(report.destination).parent.resolve(),
                (models_root / "_drafts").resolve(),
            )
            self.assertTrue((Path(report.destination) / "model.yaml").is_file())
            self.assertTrue(
                catalog.valid, [issue.as_dict() for issue in catalog.issues]
            )
            self.assertEqual(catalog.discovered_count, 0)

    def test_new_cli_json_supports_plan_then_explicit_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            models_root = Path(temporary) / "models"
            arguments = (
                "new",
                "cli-reference",
                "--name",
                "CLI reference",
                "--owner",
                "model-lab-tests",
                "--models-root",
                str(models_root),
                "--format",
                "json",
            )

            planned = run_model_lab(*arguments)
            self.assertEqual(planned.returncode, 0, planned.stderr)
            self.assertEqual(planned.stderr, "")
            planned_payload = json.loads(planned.stdout)
            self.assertEqual(planned_payload["status"], "planned")
            self.assertFalse(Path(planned_payload["destination"]).exists())

            created = run_model_lab(*arguments, "--apply")
            self.assertEqual(created.returncode, 0, created.stderr)
            self.assertEqual(created.stderr, "")
            created_payload = json.loads(created.stdout)
            self.assertEqual(created_payload["status"], "created")
            self.assertEqual(created_payload["files"], planned_payload["files"])
            self.assertTrue(Path(created_payload["destination"]).is_dir())

    def test_new_cli_point_mode_scaffolds_unset_binding_and_time_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            models_root = Path(temporary) / "models"
            arguments = (
                "new", "coldstart-point", "--name", "Cold-start point model",
                "--owner", "model-lab-tests", "--target", "cpu-iot",
                "--serving-mode", "point-code", "--models-root", str(models_root),
                "--format", "json",
            )

            planned = run_model_lab(*arguments)
            self.assertEqual(planned.returncode, 0, planned.stderr)
            preview = json.loads(planned.stdout)
            self.assertEqual(preview["serving_mode"], "point-code")
            self.assertIn("input-bindings.yaml", preview["files"])
            self.assertFalse(Path(preview["destination"]).exists())

            created = run_model_lab(*arguments, "--apply")
            self.assertEqual(created.returncode, 0, created.stderr)
            workspace = Path(json.loads(created.stdout)["destination"])
            manifest = yaml.safe_load((workspace / "model.yaml").read_text(encoding="utf-8"))
            binding = yaml.safe_load((workspace / "input-bindings.yaml").read_text(encoding="utf-8"))
            self.assertEqual(manifest["serving"]["mode"], "point-code")
            self.assertEqual(manifest["serving"]["point_fields"], [])
            self.assertIsNone(manifest["serving"]["time"]["strategy"])
            self.assertIsNone(manifest["serving"]["rolling_window"])
            self.assertEqual(binding["model_id"], "coldstart-point")
            self.assertEqual(binding["schema_version"], "2.1")
            self.assertIsNone(binding["factory_code"])
            self.assertEqual(binding["bindings"], [])

    def test_new_cli_does_not_offer_point_serving_on_gpu(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_model_lab(
                "new", "gpu-point", "--name", "GPU point", "--owner", "tests",
                "--target", "gpu", "--serving-mode", "point-code",
                "--models-root", str(Path(temporary) / "models"), "--format", "json",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("point-code serving requires cpu-iot", result.stderr)



    def test_apply_fails_fast_when_drafts_root_is_not_writable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            models = Path(temporary) / "models"
            with patch.object(Path, "mkdir", side_effect=PermissionError("denied")):
                with self.assertRaises(WorkspaceError) as raised:
                    scaffold_model(request(), models, apply=True)
        self.assertEqual(raised.exception.code, "workspace_write_failed")

if __name__ == "__main__":
    unittest.main()
