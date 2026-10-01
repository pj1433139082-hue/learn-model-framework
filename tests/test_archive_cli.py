from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import warnings
from zipfile import ZIP_STORED, ZipFile

import yaml  # type: ignore[import-untyped]

from model_lab.cli import main


_PREFIX = "infra_model_deploy/resources/infra/311/"
_LINUX_ENTRIES = {
    "module": _PREFIX + "mlops_deploy_iot_armor2_311.py",
    "runtime_init": _PREFIX + "pyarmor_runtime_000000/__init__.py",
    "native": _PREFIX + "pyarmor_runtime_000000/linux_x86_64/pyarmor_runtime.so",
}
_ARCHIVE_LIMIT = 16 * 1024 * 1024
_ENTRY_LIMIT = 2 * 1024 * 1024


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _linux_native() -> bytes:
    native = bytearray(20)
    native[:6] = b"\x7fELF\x02\x01"
    native[18:20] = (62).to_bytes(2, "little")
    return bytes(native)


def _zip_bytes(
    entries: list[tuple[str, bytes]], *, stored: bool = False
) -> bytes:
    buffer = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with ZipFile(buffer, "w", compression=ZIP_STORED if stored else 8) as archive:
            for name, content in entries:
                archive.writestr(name, content)
    return buffer.getvalue()


def _invoke(*arguments: str) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


class ArchiveCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="model-lab-archive-cli-")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_doctor_root(self, name: str = "framework-fixture") -> Path:
        root = self.root / name
        root.mkdir()
        (root / ".python-version").write_text("3.11\n", encoding="utf-8")
        (root / "pyproject.toml").write_text(
            '[project]\nname = "archive-doctor-fixture"\n'
            'requires-python = ">=3.11,<3.12"\n',
            encoding="utf-8",
        )
        editor = root / ".vscode"
        editor.mkdir()
        (editor / "settings.json").write_text(
            '{"python.defaultInterpreterPath": "${workspaceFolder}/.venv-311/Scripts/python.exe"}\n',
            encoding="utf-8",
        )
        return root

    def _doctor(
        self, root: Path, archive: Path, target: str = "linux_x86_64"
    ) -> dict[str, object]:
        code, stdout, stderr = _invoke(
            "doctor", str(root), "--company-package", str(archive),
            "--target-platform", target, "--format", "json",
        )
        self.assertEqual(code, 0, stderr)
        return json.loads(stdout)

    def _write_bundle_workspace(
        self,
        archive_path: Path,
        archive_bytes: bytes,
        components: dict[str, bytes],
        *,
        archive_pin: str | None = None,
        component_pins: dict[str, str] | None = None,
    ) -> tuple[Path, Path]:
        workspace = self.root / "private-model" / archive_path.stem
        (workspace / "runtime").mkdir(parents=True)
        (workspace / "src").mkdir()
        (workspace / "src" / "model.py").write_text(
            "def predict(value):\n    return value\n", encoding="utf-8"
        )
        sample = workspace / "sample.csv"
        sample.write_text("flow_m3h\n1.0\n", encoding="utf-8")
        (workspace / "runtime" / "requirements.lock.txt").write_text(
            "mlflow==2.22.2\ncloudpickle==3.1.2\nnumpy==2.3.3\npandas==2.3.3\n",
            encoding="utf-8",
        )
        native_digest = _digest(components["native"])
        pins = {name: _digest(value) for name, value in components.items()}
        pins.update(component_pins or {})
        document: dict[str, object] = {
            "schema_version": "1.0",
            "compatibility": {"model_lab": ">=0.1.0,<1.0.0"},
            "model": {
                "id": "synthetic-point-model",
                "name": "Synthetic point model",
                "version": "0.1.0",
                "lifecycle_state": "validated",
                "owner": "model-lab-tests",
            },
            "target": {
                "kind": "cpu-iot",
                "environment": {
                    "operating_system": "linux",
                    "architecture": "x86_64",
                    "python": ">=3.11,<3.12",
                    "mlflow": "==2.22.2",
                    "package_format": "mlflow-pyfunc",
                    "dependency_lock": "runtime/requirements.lock.txt",
                    "native_runtime": {
                        "kind": "pyarmor",
                        "python_abi": "cp311",
                        "platform": "linux_x86_64",
                        "artifact": "company_iot_native",
                    },
                },
            },
            "contract": {
                "inputs": [{"name": "flow_m3h", "type": "number", "unit": "m3/h"}],
                "outputs": [{"name": "result", "type": "number", "unit": "dimensionless"}],
            },
            "artifacts": [
                {
                    "name": "company_iot_native",
                    "kind": "model",
                    "uri": "artifact://synthetic-point-model/native",
                    "sha256": native_digest,
                }
            ],
            "serving": {
                "mode": "point-code",
                "factory_field": "factory_code",
                "point_field": "flow_m3h",
                "start_field": "start_time",
                "rolling_window": "1h",
                "interval": "1min",
                "binding": "input-bindings.yaml",
                "time": {
                    "strategy": "explicit-start",
                    "timezone": "UTC",
                    "iot_timestamp_timezone": "UTC",
                },
                "provider": {
                    "kind": "company-iot-311",
                    "archive_sha256": archive_pin or _digest(archive_bytes),
                    "module_sha256": pins["module"],
                    "runtime_init_sha256": pins["runtime_init"],
                    "native_sha256": pins["native"],
                },
            },
        }
        (workspace / "model.yaml").write_text(
            yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        (workspace / "input-bindings.yaml").write_text(
            yaml.safe_dump(
                {
                    "schema_version": "1.0",
                    "model_id": "synthetic-point-model",
                    "model_version": "0.1.0",
                    "factory_code": "EXAMPLE",
                    "point_field": "flow_m3h",
                    "point_code": "EXAMPLE-FLOW",
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        archive_path.write_bytes(archive_bytes)
        return workspace / "model.yaml", sample

    def _bundle_build(
        self, manifest: Path, sample: Path, archive: Path
    ) -> tuple[int, dict[str, object] | None, dict[str, object] | None]:
        code, stdout, stderr = _invoke(
            "bundle-build", str(manifest), "--sample-csv", str(sample),
            "--iot-package-zip", str(archive), "--format", "json",
        )
        success = json.loads(stdout) if stdout else None
        failure = json.loads(stderr) if stderr else None
        return code, success, failure

    def test_valid_synthetic_archive_previews_and_doctor_keeps_capability_optional(self) -> None:
        components = {
            "module": b"# synthetic provider\n",
            "runtime_init": b"# synthetic runtime\n",
            "native": _linux_native(),
        }
        archive_entries = [
            (_LINUX_ENTRIES[label], content)
            for label, content in components.items()
        ]
        archive_entries.append(("unrelated/large.bin", b"x" * (_ENTRY_LIMIT + 1)))
        archive_bytes = _zip_bytes(archive_entries)
        archive = self.root / "synthetic-provider.zip"
        manifest, sample = self._write_bundle_workspace(
            archive, archive_bytes, components
        )

        report = self._doctor(self._write_doctor_root(), archive)
        code, success, failure = self._bundle_build(manifest, sample, archive)

        self.assertTrue(report["valid"])
        self.assertTrue(report["deployment_capability_ready"])
        self.assertEqual(code, 0, failure)
        self.assertEqual(success["status"], "planned")  # type: ignore[index]

    def test_unpinned_doctor_and_pinned_build_keep_distinct_digest_policies(self) -> None:
        components = {
            "module": b"# synthetic provider\n",
            "runtime_init": b"# synthetic runtime\n",
            "native": _linux_native(),
        }
        archive_bytes = _zip_bytes(
            [(_LINUX_ENTRIES[label], content) for label, content in components.items()]
        )
        archive = self.root / "synthetic-provider.zip"
        manifest, sample = self._write_bundle_workspace(
            archive, archive_bytes, components, archive_pin="f" * 64
        )

        report = self._doctor(self._write_doctor_root(), archive)
        code, _, failure = self._bundle_build(manifest, sample, archive)

        self.assertTrue(report["valid"])
        self.assertTrue(report["deployment_capability_ready"])
        self.assertEqual(code, 2)
        self.assertEqual(
            failure["error"]["code"],  # type: ignore[index]
            "point_provider_archive_digest_mismatch",
        )

        component_archive = self.root / "component-pin-provider.zip"
        component_manifest, component_sample = self._write_bundle_workspace(
            component_archive,
            archive_bytes,
            components,
            component_pins={"module": "f" * 64},
        )
        component_report = self._doctor(
            self._write_doctor_root("component-pin-framework"), component_archive
        )
        component_code, _, component_failure = self._bundle_build(
            component_manifest, component_sample, component_archive
        )
        self.assertTrue(component_report["deployment_capability_ready"])
        self.assertEqual(component_code, 2)
        self.assertEqual(
            component_failure["error"]["code"],  # type: ignore[index]
            "point_provider_component_digest_mismatch",
        )

    def test_duplicate_unrequested_member_is_rejected_by_doctor_and_build(self) -> None:
        components = {
            "module": b"# synthetic provider\n",
            "runtime_init": b"# synthetic runtime\n",
            "native": _linux_native(),
        }
        entries = [(_LINUX_ENTRIES[label], content) for label, content in components.items()]
        entries.extend((("unrelated/notes.txt", b"first"), ("unrelated/notes.txt", b"second")))
        archive_bytes = _zip_bytes(entries)
        archive = self.root / "synthetic-provider.zip"
        manifest, sample = self._write_bundle_workspace(
            archive, archive_bytes, components
        )

        report = self._doctor(self._write_doctor_root(), archive)
        code, _, failure = self._bundle_build(manifest, sample, archive)

        self.assertTrue(report["valid"])
        self.assertFalse(report["deployment_capability_ready"])
        self.assertEqual(code, 2)
        self.assertEqual(
            failure["error"]["code"],  # type: ignore[index]
            "point_provider_archive_duplicate",
        )

    def test_underreported_central_size_with_larger_stored_stream_fails_closed(self) -> None:
        components = {
            "module": b"m" * (_ENTRY_LIMIT + 1),
            "runtime_init": b"# synthetic runtime\n",
            "native": _linux_native(),
        }
        archive_bytes = bytearray(
            _zip_bytes(
                [(_LINUX_ENTRIES[label], content) for label, content in components.items()],
                stored=True,
            )
        )

        # Keep the real stored payload, compressed size, and CRC, but understate
        # the central-directory uncompressed size by one byte. This is an
        # inconsistent ZIP fixture; it does not assert which reader guard fires.
        central = archive_bytes.find(b"PK\x01\x02")
        self.assertGreaterEqual(central, 0)
        name_length = int.from_bytes(archive_bytes[central + 28:central + 30], "little")
        member_name = bytes(archive_bytes[central + 46:central + 46 + name_length]).decode("utf-8")
        self.assertEqual(member_name, _LINUX_ENTRIES["module"])
        compressed_size = int.from_bytes(archive_bytes[central + 20:central + 24], "little")
        self.assertEqual(compressed_size, len(components["module"]))
        self.assertGreater(compressed_size, _ENTRY_LIMIT)
        archive_bytes[central + 24:central + 28] = _ENTRY_LIMIT.to_bytes(4, "little")

        archive = self.root / "underreported-provider.zip"
        manifest, sample = self._write_bundle_workspace(
            archive, bytes(archive_bytes), components
        )

        report = self._doctor(self._write_doctor_root(), archive)
        code, _, failure = self._bundle_build(manifest, sample, archive)

        self.assertTrue(report["valid"])
        self.assertFalse(report["deployment_capability_ready"])
        self.assertEqual(code, 2)
        self.assertIn(
            failure["error"]["code"],  # type: ignore[index]
            {"point_provider_archive_invalid", "point_provider_component_digest_mismatch"},
        )

    def test_missing_directory_oversized_and_corrupt_archives_fail_closed(self) -> None:
        cases: list[tuple[str, bytes, dict[str, bytes], str]] = []
        base = {
            "module": b"# synthetic provider\n",
            "runtime_init": b"# synthetic runtime\n",
            "native": _linux_native(),
        }
        cases.append(
            (
                "missing-member",
                _zip_bytes([(_LINUX_ENTRIES["module"], base["module"]), (_LINUX_ENTRIES["runtime_init"], base["runtime_init"])]),
                base,
                "point_provider_component_missing",
            )
        )
        cases.append(
            (
                "directory-member",
                _zip_bytes(
                    [
                        (_LINUX_ENTRIES["module"], base["module"]),
                        (_LINUX_ENTRIES["runtime_init"], base["runtime_init"]),
                        (_PREFIX + "pyarmor_runtime_000000/linux_x86_64/", b""),
                    ]
                ),
                base,
                "point_provider_component_missing",
            )
        )
        oversized = {**base, "module": b"x" * (_ENTRY_LIMIT + 1)}
        cases.append(
            (
                "oversized-member",
                _zip_bytes([(_LINUX_ENTRIES[label], value) for label, value in oversized.items()]),
                oversized,
                "point_provider_component_invalid",
            )
        )
        cases.append(("corrupt-zip", b"not a ZIP archive", base, "point_provider_archive_invalid"))

        for name, content, pins, expected_code in cases:
            with self.subTest(name=name):
                archive = self.root / f"{name}.zip"
                manifest, sample = self._write_bundle_workspace(archive, content, pins)
                report = self._doctor(self._write_doctor_root(f"framework-{name}"), archive)
                code, _, failure = self._bundle_build(manifest, sample, archive)
                self.assertTrue(report["valid"])
                self.assertFalse(report["deployment_capability_ready"])
                self.assertEqual(code, 2)
                self.assertEqual(failure["error"]["code"], expected_code)  # type: ignore[index]

    def test_oversized_archive_is_rejected_before_zip_parsing(self) -> None:
        components = {
            "module": b"# synthetic provider\n",
            "runtime_init": b"# synthetic runtime\n",
            "native": _linux_native(),
        }
        archive_bytes = b"x" * (_ARCHIVE_LIMIT + 1)
        archive = self.root / "oversized-provider.zip"
        manifest, sample = self._write_bundle_workspace(
            archive, archive_bytes, components
        )

        report = self._doctor(self._write_doctor_root(), archive)
        code, _, failure = self._bundle_build(manifest, sample, archive)

        self.assertTrue(report["valid"])
        self.assertFalse(report["deployment_capability_ready"])
        self.assertEqual(code, 2)
        self.assertEqual(
            failure["error"]["code"],  # type: ignore[index]
            "point_provider_archive_missing",
        )

    def test_doctor_retains_windows_pe_header_branch(self) -> None:
        components = {
            "module": b"# synthetic provider\n",
            "runtime_init": b"# synthetic runtime\n",
            "native": b"MZ" + b"synthetic-pe-header",
        }
        entries = [
            (_LINUX_ENTRIES["module"], components["module"]),
            (_LINUX_ENTRIES["runtime_init"], components["runtime_init"]),
            (
                _PREFIX + "pyarmor_runtime_000000/windows_x86_64/pyarmor_runtime.pyd",
                components["native"],
            ),
        ]
        archive = self.root / "synthetic-windows-provider.zip"
        archive.write_bytes(_zip_bytes(entries))

        report = self._doctor(
            self._write_doctor_root(), archive, target="windows_x86_64"
        )

        self.assertTrue(report["valid"])
        self.assertTrue(report["deployment_capability_ready"])


if __name__ == "__main__":
    unittest.main()
