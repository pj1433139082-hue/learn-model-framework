from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from model_lab.repository_doctor import DoctorObservation, run_repository_doctor


REPO_ROOT = Path(__file__).resolve().parents[1]


def write_minimal_repository(root: Path) -> None:
    (root / ".python-version").write_text("3.11\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "doctor-fixture"\nrequires-python = ">=3.11,<3.12"\n',
        encoding="utf-8",
    )
    (root / ".vscode").mkdir()
    (root / ".vscode" / "settings.json").write_text(
        '{"python.defaultInterpreterPath": "${workspaceFolder}/.venv-311/Scripts/python.exe"}\n',
        encoding="utf-8",
    )
    (root / "model_lab").mkdir()
    (root / "model_lab" / "sample.py").write_text(
        "PYTHON_MINOR = '3.11'\n", encoding="utf-8"
    )


class RepositoryDoctorTests(unittest.TestCase):
    def test_clean_python311_repository_is_ready_without_company_capability(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_minimal_repository(root)

            report = run_repository_doctor(
                root,
                observation=DoctorObservation(
                    python="3.11.15",
                    executable=str(root / ".venv-311" / "Scripts" / "python.exe"),
                ),
            )

        self.assertTrue(report.valid, report.as_dict())
        self.assertFalse(report.deployment_capability_ready)
        self.assertIn(
            "deployment_capability_missing",
            {check.code for check in report.checks if not check.passed},
        )

    def test_python312_and_local_legacy_runtime_fail_with_actionable_codes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_minimal_repository(root)
            (root / ".venv-312").mkdir()
            framework = root / "framework"
            framework.mkdir()
            (framework / "mlops_deploy_iot_armor2_312.py").write_text(
                "# legacy\n", encoding="utf-8"
            )

            report = run_repository_doctor(
                root,
                observation=DoctorObservation(
                    python="3.12.13",
                    executable=str(root / ".venv-312" / "Scripts" / "python.exe"),
                ),
            )

        self.assertFalse(report.valid)
        codes = {check.code for check in report.checks if not check.passed}
        self.assertIn("python_minor_mismatch", codes)
        self.assertIn("legacy_environment_present", codes)
        self.assertIn("legacy_runtime_present", codes)

    def test_company_capability_requires_matching_platform_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "framework-checkout"
            root.mkdir()
            write_minimal_repository(root)
            archive = Path(temporary) / "synthetic-provider.zip"
            prefix = "infra_model_deploy/resources/infra/311/"

            def write_archive(native: bytes) -> None:
                with ZipFile(archive, "w") as bundle:
                    bundle.writestr(prefix + "mlops_deploy_iot_armor2_311.py", "# synthetic\n")
                    bundle.writestr(prefix + "pyarmor_runtime_000000/__init__.py", "")
                    bundle.writestr(prefix + "pyarmor_runtime_000000/linux_x86_64/pyarmor_runtime.so", native)

            write_archive(b"not an ELF file")
            observation = DoctorObservation("3.11.15", "/tmp/python")
            with (
                patch("model_lab.repository_doctor.platform.system", return_value="Linux"),
                patch("model_lab.repository_doctor.platform.machine", return_value="x86_64"),
            ):
                self.assertFalse(
                    run_repository_doctor(root, observation=observation, company_package=archive).deployment_capability_ready
                )
                native = bytearray(20)
                native[:6] = b"\x7fELF\x02\x01"
                native[18] = 62
                write_archive(bytes(native))
                self.assertTrue(
                    run_repository_doctor(root, observation=observation, company_package=archive).deployment_capability_ready
                )
                self.assertFalse(
                    run_repository_doctor(
                        root, observation=DoctorObservation("3.12.13", "/tmp/python"), company_package=archive,
                    ).deployment_capability_ready
                )
                self.assertFalse((root / "framework").exists())

    def test_doctor_reports_source_and_defaults_to_framework_from_external_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [sys.executable, "-I", "-m", "model_lab", "doctor", "--format", "json"],
                cwd=temporary, capture_output=True, text=True, encoding="utf-8", check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(Path(payload["repository_root"]), REPO_ROOT)
        self.assertEqual(Path(payload["framework"]["root"]), REPO_ROOT)
        self.assertEqual(Path(payload["framework"]["module"]).parent, REPO_ROOT / "model_lab")
        self.assertTrue(payload["framework"]["git_commit"])

    def test_doctor_cli_reports_current_checkout_ready_on_python311(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "model_lab",
                "doctor",
                str(REPO_ROOT),
                "--format",
                "json",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["python"]["minor"], "3.11")


if __name__ == "__main__":
    unittest.main()
