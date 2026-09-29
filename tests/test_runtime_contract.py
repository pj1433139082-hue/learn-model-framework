from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

import yaml  # type: ignore[import-untyped]

from model_lab.manifest import load_validated_asset, validate_manifest
from model_lab.runtime_contract import (
    RuntimeObservation,
    check_build_environment,
    verify_mlflow_bundle,
)


def environment(*, native_runtime: dict[str, str] | None = None) -> dict[str, object]:
    return {
        "operating_system": "linux",
        "architecture": "x86_64",
        "python": ">=3.11,<3.12",
        "mlflow": "==2.22.2",
        "package_format": "mlflow-pyfunc",
        "dependency_lock": "runtime/requirements.lock.txt",
        "native_runtime": native_runtime or {"kind": "none"},
    }


def manifest_document(target: str = "cpu-iot") -> dict[str, object]:
    target_document: dict[str, object] = {
        "kind": target,
        "environment": environment(),
    }
    if target in {"gpu", "chronos"}:
        target_document["runtime"] = {
            "accelerator": "gpu",
            "gpu": {
                "vendor": "nvidia",
                "compute_capability": ">=8.0",
                "memory_gb_min": 16,
            },
            "cuda": {"version": ">=12.1,<13"},
            "framework": {"name": "pytorch", "version": ">=2.3,<3"},
            "artifact": {"format": "safetensors"},
        }
    return {
        "schema_version": "1.0",
        "compatibility": {"model_lab": ">=0.1.0,<1.0.0"},
        "model": {
            "id": f"runtime-{target}",
            "name": "Runtime test",
            "version": "0.1.0",
            "lifecycle_state": "draft",
            "owner": "model-lab-tests",
        },
        "target": target_document,
        "contract": {"inputs": [], "outputs": []},
        "artifacts": [],
    }


def write_manifest(root: Path, document: dict[str, object]) -> Path:
    path = root / "model.yaml"
    path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def write_lock(root: Path, content: str | None = None) -> Path:
    runtime = root / "runtime"
    runtime.mkdir(exist_ok=True)
    lock = runtime / "requirements.lock.txt"
    lock.write_text(
        content
        or (
            "mlflow==2.22.2\n"
            "cloudpickle==3.1.2\n"
            "numpy==2.3.3\n"
            "pandas==2.3.3\n"
        ),
        encoding="utf-8",
    )
    return lock


def write_valid_bundle(root: Path) -> Path:
    bundle = root / "release-bundle" / "service"
    artifacts = bundle / "artifacts"
    artifacts.mkdir(parents=True)
    (bundle / "MLmodel").write_text(
        yaml.safe_dump(
            {
                "flavors": {
                    "python_function": {
                        "loader_module": "deploy",
                        "artifacts": {
                            "logic_bundle": {"path": "artifacts/logic_bundle.pkl"},
                            "custom_sources": {"path": "artifacts/custom_sources.pkl"},
                            "model_file": {"path": "artifacts/model.bin"},
                        },
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (bundle / "python_env.yaml").write_text(
        'python: "3.11.9"\n', encoding="utf-8"
    )
    (bundle / "conda.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "mlflow-env",
                "channels": ["conda-forge"],
                "dependencies": [
                    "python=3.11.9",
                    {
                        "pip": [
                            "mlflow==2.22.2",
                            "cloudpickle==3.1.2",
                            "numpy==2.3.3",
                            "pandas==2.3.3",
                        ]
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (bundle / "requirements.txt").write_text(
        "mlflow==2.22.2\n"
        "cloudpickle==3.1.2\n"
        "numpy==2.3.3\n"
        "pandas==2.3.3\n",
        encoding="utf-8",
    )
    (artifacts / "logic_bundle.pkl").write_bytes(b"opaque logic")
    (artifacts / "custom_sources.pkl").write_bytes(b"opaque sources")
    (artifacts / "model.bin").write_bytes(b"opaque model")
    return bundle


class ManifestEnvironmentTests(unittest.TestCase):
    def test_cpu_gpu_and_chronos_share_one_typed_environment_contract(self) -> None:
        for target in ("cpu-iot", "gpu", "chronos"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temporary:
                path = write_manifest(Path(temporary), manifest_document(target))

                validation, asset = load_validated_asset(path)

                self.assertTrue(validation.valid, validation.as_dict())
                self.assertIsNotNone(asset)
                assert asset is not None
                contract = asset.environment_requirements
                self.assertIsNotNone(contract)
                assert contract is not None
                self.assertEqual(contract.operating_system, "linux")
                self.assertEqual(contract.architecture, "x86_64")
                self.assertEqual(contract.python, ">=3.11,<3.12")
                self.assertEqual(contract.mlflow, "==2.22.2")
                self.assertEqual(contract.native_runtime.kind, "none")

    def test_legacy_manifest_without_environment_remains_valid(self) -> None:
        document = manifest_document()
        target = document["target"]
        assert isinstance(target, dict)
        target.pop("environment")
        with tempfile.TemporaryDirectory() as temporary:
            validation, asset = load_validated_asset(
                write_manifest(Path(temporary), document)
            )

        self.assertTrue(validation.valid, validation.as_dict())
        self.assertIsNotNone(asset)
        assert asset is not None
        self.assertIsNone(asset.environment_requirements)

    def test_environment_unknown_fields_and_invalid_specifiers_fail_closed(self) -> None:
        cases = (
            ("surprise", True, "unknown_field", "target.environment.surprise"),
            (
                "python",
                "not a specifier",
                "invalid_environment_version_constraint",
                "target.environment.python",
            ),
            (
                "dependency_lock",
                "../requirements.txt",
                "unsafe_dependency_lock_path",
                "target.environment.dependency_lock",
            ),
        )
        for field, value, code, path in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                document = manifest_document()
                target = document["target"]
                assert isinstance(target, dict)
                contract = target["environment"]
                assert isinstance(contract, dict)
                contract[field] = value

                report = validate_manifest(write_manifest(Path(temporary), document))

                self.assertIn(
                    (code, path),
                    {(issue.code, issue.path) for issue in report.issues},
                )

    def test_pyarmor_environment_requires_complete_named_artifact_metadata(self) -> None:
        document = manifest_document()
        target = document["target"]
        assert isinstance(target, dict)
        target["environment"] = environment(
            native_runtime={
                "kind": "pyarmor",
                "python_abi": "cp311",
                "platform": "linux_x86_64",
                "artifact": "pyarmor-runtime-cp311-linux-x86-64",
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            missing = validate_manifest(write_manifest(Path(temporary), document))

            complete_document = deepcopy(document)
            artifacts = complete_document["artifacts"]
            assert isinstance(artifacts, list)
            artifacts.append(
                {
                    "name": "pyarmor-runtime-cp311-linux-x86-64",
                    "kind": "native-runtime",
                    "uri": "artifact://runtime/pyarmor-runtime.so",
                    "sha256": "1" * 64,
                }
            )
            complete = validate_manifest(
                write_manifest(Path(temporary), complete_document)
            )

        self.assertIn(
            ("missing_native_runtime_artifact", "target.environment.native_runtime.artifact"),
            {(issue.code, issue.path) for issue in missing.issues},
        )
        self.assertTrue(complete.valid, complete.as_dict())

    def test_native_runtime_none_rejects_pyarmor_only_fields(self) -> None:
        document = manifest_document()
        target = document["target"]
        assert isinstance(target, dict)
        target["environment"] = environment(
            native_runtime={"kind": "none", "python_abi": "cp311"}
        )
        with tempfile.TemporaryDirectory() as temporary:
            report = validate_manifest(write_manifest(Path(temporary), document))

        self.assertIn(
            ("unexpected_native_runtime_field", "target.environment.native_runtime.python_abi"),
            {(issue.code, issue.path) for issue in report.issues},
        )


class BuildEnvironmentTests(unittest.TestCase):
    def test_matching_observation_and_exact_dependency_lock_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = write_manifest(root, manifest_document())
            write_lock(root)

            report = check_build_environment(
                manifest,
                observation=RuntimeObservation(
                    operating_system="Linux",
                    architecture="AMD64",
                    python="3.11.9",
                    python_abi="cp311",
                ),
            )

        self.assertTrue(report.valid, report.as_dict())
        self.assertEqual(report.current.operating_system, "linux")
        self.assertEqual(report.current.architecture, "x86_64")

    def test_python_and_platform_mismatches_have_stable_codes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = write_manifest(root, manifest_document())
            write_lock(root)

            report = check_build_environment(
                manifest,
                observation=RuntimeObservation(
                    operating_system="Windows",
                    architecture="AMD64",
                    python="3.12.13",
                    python_abi="cp312",
                ),
            )

        codes = {issue.code for issue in report.issues}
        self.assertIn("build_python_mismatch", codes)
        self.assertIn("build_platform_mismatch", codes)

    def test_missing_and_unpinned_dependency_locks_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = write_manifest(root, manifest_document())
            missing = check_build_environment(
                manifest,
                observation=RuntimeObservation("linux", "x86_64", "3.11.9", "cp311"),
            )
            write_lock(root, "mlflow>=2.22\ncloudpickle==3.1.2\n")
            unpinned = check_build_environment(
                manifest,
                observation=RuntimeObservation("linux", "x86_64", "3.11.9", "cp311"),
            )

        self.assertIn("missing_dependency_lock", {issue.code for issue in missing.issues})
        self.assertIn("dependency_not_pinned", {issue.code for issue in unpinned.issues})
        self.assertIn("missing_required_dependency", {issue.code for issue in unpinned.issues})

    def test_dependency_lock_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = write_manifest(root, manifest_document())
            target = root / "actual-requirements.txt"
            target.write_text("mlflow==2.22.2\n", encoding="utf-8")
            runtime = root / "runtime"
            runtime.mkdir()
            lock = runtime / "requirements.lock.txt"
            try:
                lock.symlink_to(target)
            except OSError as error:
                self.skipTest(f"symlinks unavailable: {error}")

            report = check_build_environment(
                manifest,
                observation=RuntimeObservation("linux", "x86_64", "3.11.9", "cp311"),
            )

        self.assertIn("unsafe_dependency_lock", {issue.code for issue in report.issues})


class BundleVerificationTests(unittest.TestCase):
    def _workspace(self, root: Path) -> tuple[Path, Path]:
        manifest = write_manifest(root, manifest_document())
        write_lock(root)
        return manifest, write_valid_bundle(root)

    def test_complete_bundle_passes_without_loading_serialized_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, bundle = self._workspace(Path(temporary))

            report = verify_mlflow_bundle(manifest, bundle)

        self.assertTrue(report.valid, report.as_dict())
        self.assertEqual(dict(report.dependency_pins)["mlflow"], "2.22.2")
        self.assertIn("model_file", dict(report.artifact_paths))

    def test_every_required_file_is_reported_individually(self) -> None:
        required = (
            "MLmodel",
            "python_env.yaml",
            "conda.yaml",
            "requirements.txt",
            "artifacts/logic_bundle.pkl",
            "artifacts/custom_sources.pkl",
        )
        for relative in required:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                manifest, bundle = self._workspace(Path(temporary))
                (bundle / relative).unlink()

                report = verify_mlflow_bundle(manifest, bundle)

                self.assertIn(
                    ("missing_mlflow_bundle_file", f"bundle.{relative}"),
                    {(issue.code, issue.path) for issue in report.issues},
                )

    def test_duplicate_yaml_key_and_oversized_metadata_fail_before_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, bundle = self._workspace(Path(temporary))
            (bundle / "python_env.yaml").write_text(
                'python: "3.11.9"\npython: "3.12.0"\n', encoding="utf-8"
            )
            duplicate = verify_mlflow_bundle(manifest, bundle)

            (bundle / "python_env.yaml").write_bytes(b"x" * (1024 * 1024 + 1))
            oversized = verify_mlflow_bundle(manifest, bundle)

        self.assertIn("bundle_yaml_invalid", {issue.code for issue in duplicate.issues})
        self.assertIn("bundle_metadata_too_large", {issue.code for issue in oversized.issues})

    def test_bundle_versions_and_pins_must_agree_with_contract_and_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, bundle = self._workspace(Path(temporary))
            (bundle / "python_env.yaml").write_text(
                'python: "3.12.1"\n', encoding="utf-8"
            )
            (bundle / "requirements.txt").write_text(
                "mlflow>=2.22\ncloudpickle==3.1.2\nnumpy==2.3.3\npandas==2.3.3\n",
                encoding="utf-8",
            )

            report = verify_mlflow_bundle(manifest, bundle)

        codes = {issue.code for issue in report.issues}
        self.assertIn("bundle_python_mismatch", codes)
        self.assertIn("dependency_not_pinned", codes)
        self.assertIn("bundle_mlflow_mismatch", codes)

    def test_mlmodel_requires_pyfunc_and_safe_existing_artifact_mappings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, bundle = self._workspace(Path(temporary))
            (bundle / "MLmodel").write_text(
                yaml.safe_dump(
                    {
                        "flavors": {
                            "python_function": {
                                "artifacts": {
                                    "logic_bundle": {"path": "../outside.pkl"}
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            report = verify_mlflow_bundle(manifest, bundle)

        codes = {issue.code for issue in report.issues}
        self.assertIn("unsafe_bundle_artifact_path", codes)
        self.assertIn("missing_mlflow_artifact_mapping", codes)

    def test_absent_python_function_flavor_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, bundle = self._workspace(Path(temporary))
            (bundle / "MLmodel").write_text("flavors: {}\n", encoding="utf-8")

            report = verify_mlflow_bundle(manifest, bundle)

        self.assertIn("missing_mlflow_pyfunc_flavor", {issue.code for issue in report.issues})

    def test_windows_mlflow_artifact_paths_normalize_without_allowing_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = manifest_document()
            target = document["target"]
            assert isinstance(target, dict)
            target_environment = target["environment"]
            assert isinstance(target_environment, dict)
            target_environment["operating_system"] = "windows"
            manifest = write_manifest(root, document)
            write_lock(root)
            bundle = write_valid_bundle(root)
            mlmodel_path = bundle / "MLmodel"
            mlmodel = yaml.safe_load(mlmodel_path.read_text(encoding="utf-8"))
            mappings = mlmodel["flavors"]["python_function"]["artifacts"]
            for value in mappings.values():
                value["path"] = value["path"].replace("/", "\\")
            mlmodel_path.write_text(
                yaml.safe_dump(mlmodel, sort_keys=False), encoding="utf-8"
            )

            valid = verify_mlflow_bundle(manifest, bundle)
            self.assertTrue(valid.valid, valid.as_dict())
            self.assertEqual(
                dict(valid.artifact_paths)["logic_bundle"],
                "artifacts/logic_bundle.pkl",
            )

            for unsafe in ("..\\outside.pkl", "C:\\secret.pkl", "\\\\host\\share.pkl"):
                with self.subTest(path=unsafe):
                    mappings["logic_bundle"]["path"] = unsafe
                    mlmodel_path.write_text(
                        yaml.safe_dump(mlmodel, sort_keys=False), encoding="utf-8"
                    )
                    invalid = verify_mlflow_bundle(manifest, bundle)
                    self.assertIn(
                        "unsafe_bundle_artifact_path",
                        {issue.code for issue in invalid.issues},
                    )

    def test_windows_native_runtime_cannot_pass_a_linux_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = manifest_document()
            target = document["target"]
            assert isinstance(target, dict)
            target["environment"] = environment(
                native_runtime={
                    "kind": "pyarmor",
                    "python_abi": "cp311",
                    "platform": "linux_x86_64",
                    "artifact": "pyarmor-runtime-cp311-linux-x86-64",
                }
            )
            artifacts = document["artifacts"]
            assert isinstance(artifacts, list)
            artifacts.append(
                {
                    "name": "pyarmor-runtime-cp311-linux-x86-64",
                    "kind": "native-runtime",
                    "uri": "artifact://runtime/pyarmor_runtime.pyd",
                    "sha256": "1" * 64,
                }
            )
            manifest = write_manifest(root, document)
            write_lock(root)
            bundle = write_valid_bundle(root)
            native = bundle / "artifacts" / "pyarmor_runtime.pyd"
            native.write_bytes(b"MZ")
            mlmodel = yaml.safe_load((bundle / "MLmodel").read_text(encoding="utf-8"))
            mlmodel["flavors"]["python_function"]["artifacts"][
                "pyarmor-runtime-cp311-linux-x86-64"
            ] = {"path": "artifacts/pyarmor_runtime.pyd"}
            (bundle / "MLmodel").write_text(
                yaml.safe_dump(mlmodel, sort_keys=False), encoding="utf-8"
            )

            report = verify_mlflow_bundle(manifest, bundle)

        self.assertIn(
            "native_runtime_platform_mismatch",
            {issue.code for issue in report.issues},
        )


if __name__ == "__main__":
    unittest.main()
