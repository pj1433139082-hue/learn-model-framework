from __future__ import annotations

from contextlib import ExitStack, redirect_stderr, redirect_stdout
import hashlib
from importlib.machinery import ModuleSpec
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

import yaml

from model_lab.cli import main


def invoke(*arguments: str) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


class RecordRecoveryTests(unittest.TestCase):
    def test_new_recovers_one_snapshot_replace_failure_without_repeating_creation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="model-lab-record-recovery-") as directory:
            models_root = Path(directory) / "private-models"
            original_replace = os.replace
            injected_targets: list[Path] = []

            def fail_snapshot_replace_once(source: object, destination: object) -> None:
                target = Path(destination)
                if target.name == "workflow-state.json" and not injected_targets:
                    injected_targets.append(target)
                    raise OSError("injected workflow index replace failure")
                original_replace(source, destination)

            with patch("os.replace", side_effect=fail_snapshot_replace_once):
                code, stdout, stderr = invoke(
                    "new", "record-recovery", "--name", "Record Recovery",
                    "--owner", "test", "--models-root", str(models_root),
                    "--apply", "--format", "json",
                )

            self.assertEqual(len(injected_targets), 1)
            self.assertEqual(code, 0, stderr)
            result = json.loads(stdout)
            workspace = Path(result["destination"])
            self.assertTrue((workspace / "model.yaml").is_file())
            self.assertTrue((workspace / "workflow-state.json").is_file())
            self.assertEqual(len(list(models_root.rglob("model.yaml"))), 1)
            manifest = yaml.safe_load((workspace / "model.yaml").read_text(encoding="utf-8"))
            self.assertEqual(manifest["model"]["lifecycle_state"], "draft")

            context_code, context_stdout, context_stderr = invoke(
                "context", str(workspace), "--format", "json",
            )
            self.assertEqual(context_code, 0, context_stderr)
            context = json.loads(context_stdout)
            self.assertTrue(context["valid"])
            self.assertTrue(context["models"][0]["snapshot_in_sync"])
            self.assertEqual(len(list(models_root.rglob("model.yaml"))), 1)

    def test_new_reports_incomplete_index_after_two_failures_and_context_repairs_it(self) -> None:
        with tempfile.TemporaryDirectory(prefix="model-lab-record-recovery-persistent-") as directory:
            models_root = Path(directory) / "private-models"
            original_replace = os.replace
            failed_targets: list[Path] = []

            def fail_snapshot_replace(source: object, destination: object) -> None:
                target = Path(destination)
                if target.name == "workflow-state.json":
                    failed_targets.append(target)
                    raise OSError("persistent workflow index replace failure")
                original_replace(source, destination)

            with patch("os.replace", side_effect=fail_snapshot_replace):
                code, stdout, stderr = invoke(
                    "new", "record-recovery-persistent", "--name", "Persistent Record Recovery",
                    "--owner", "test", "--models-root", str(models_root),
                    "--apply", "--format", "json",
                )

            self.assertEqual(len(failed_targets), 2)
            self.assertEqual(code, 2)
            result = json.loads(stdout)
            self.assertEqual(result["model_id"], "record-recovery-persistent")
            self.assertEqual(result["status"], "created")
            workspace = Path(result["destination"])
            self.assertEqual(
                result["completion"],
                {"operation": "completed", "local_record": "incomplete", "repeat_operation": False},
            )
            self.assertEqual(result["local_record"]["failed_part"], "snapshot")
            self.assertEqual(result["local_record"]["snapshot_attempts"], 2)
            self.assertEqual(result["recovery"]["action"], "refresh_snapshot")
            self.assertIn("context", result["recovery"]["command"])
            self.assertIn("--write", result["recovery"]["command"])
            self.assertIn("操作已完成，但本地记录不完整", stderr)
            self.assertIn("不要重复创建、推进或上传", stderr)

            manifest = workspace / "model.yaml"
            original_manifest = manifest.read_bytes()
            parsed_manifest = yaml.safe_load(original_manifest)
            self.assertEqual(parsed_manifest["model"]["lifecycle_state"], "draft")
            self.assertEqual(len(list(models_root.rglob("model.yaml"))), 1)
            self.assertFalse((workspace / "workflow-state.json").exists())

            recovery_code, recovery_stdout, recovery_stderr = invoke(
                "context", str(workspace), "--write", "--format", "json",
            )
            self.assertEqual(recovery_code, 0, f"{recovery_stderr}\n{recovery_stdout}")
            recovered = json.loads(recovery_stdout)
            self.assertTrue(recovered["valid"])
            self.assertTrue(recovered["models"][0]["snapshot_in_sync"])
            self.assertEqual(manifest.read_bytes(), original_manifest)
            self.assertEqual(len(list(models_root.rglob("model.yaml"))), 1)
            self.assertEqual(
                yaml.safe_load(manifest.read_text(encoding="utf-8"))["model"]["lifecycle_state"],
                "draft",
            )

    def test_new_does_not_report_completion_when_workspace_fact_commit_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="model-lab-record-facts-failure-") as directory:
            models_root = Path(directory) / "private-models"
            workspace = models_root / "_drafts" / "record-facts-failure"
            original_rename = os.rename
            failed_targets: list[Path] = []

            def fail_workspace_commit(source: object, destination: object, *args: object, **kwargs: object) -> None:
                target = Path(destination)
                if target.name == workspace.name:
                    failed_targets.append(target)
                    raise OSError("injected workspace fact commit failure")
                original_rename(source, destination, *args, **kwargs)

            with patch("os.rename", side_effect=fail_workspace_commit):
                code, stdout, stderr = invoke(
                    "new", "record-facts-failure", "--name", "Record Facts Failure",
                    "--owner", "test", "--models-root", str(models_root),
                    "--apply", "--format", "json",
                )

            self.assertEqual(len(failed_targets), 1)
            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            error = json.loads(stderr)
            self.assertEqual(error["error"]["code"], "workspace_write_failed")
            self.assertNotIn("completion", error)
            self.assertFalse(workspace.exists())
            self.assertEqual(list(models_root.rglob("model.yaml")), [])
            self.assertEqual(list(models_root.rglob("workflow-state.json")), [])

    def test_unsafe_snapshot_destination_is_not_retried_and_reports_in_chinese(self) -> None:
        with tempfile.TemporaryDirectory(prefix="model-lab-record-unsafe-snapshot-") as directory:
            models_root = Path(directory) / "private-models"
            original_rename = os.rename
            original_replace = os.replace
            workspace = models_root / "_drafts" / "record-unsafe-snapshot"
            snapshot_replace_targets: list[Path] = []

            def commit_workspace_with_unsafe_snapshot(
                source: object, destination: object, *args: object, **kwargs: object,
            ) -> None:
                target = Path(destination)
                original_rename(source, destination, *args, **kwargs)
                if target == workspace:
                    (target / "workflow-state.json").mkdir()

            def count_snapshot_replace(source: object, destination: object) -> None:
                target = Path(destination)
                if target.name == "workflow-state.json":
                    snapshot_replace_targets.append(target)
                original_replace(source, destination)

            with (
                patch("os.rename", side_effect=commit_workspace_with_unsafe_snapshot),
                patch("os.replace", side_effect=count_snapshot_replace),
            ):
                code, stdout, stderr = invoke(
                    "new", "record-unsafe-snapshot", "--name", "Unsafe Snapshot Record",
                    "--owner", "test", "--models-root", str(models_root),
                    "--apply", "--format", "json",
                )

            self.assertEqual(code, 2)
            result = json.loads(stdout)
            self.assertEqual(result["status"], "created")
            self.assertEqual(
                result["completion"],
                {"operation": "completed", "local_record": "incomplete", "repeat_operation": False},
            )
            self.assertEqual(result["local_record"]["failed_part"], "snapshot")
            self.assertEqual(result["local_record"]["snapshot_attempts"], 1)
            self.assertEqual(result["local_record"]["error_type"], "ValueError")
            self.assertEqual(result["recovery"]["action"], "refresh_snapshot")
            self.assertIn("操作已完成，但本地记录不完整", stderr)
            self.assertEqual(snapshot_replace_targets, [])
            self.assertTrue((workspace / "workflow-state.json").is_dir())
            self.assertEqual(len(list(models_root.rglob("model.yaml"))), 1)

    @unittest.skipUnless(sys.platform == "linux" and sys.version_info[:2] == (3, 11),
                         "verify-publication contract requires the real Linux Python 3.11 runtime")
    def test_verified_existing_publication_survives_persistent_snapshot_failure(self) -> None:
        """Synthetic provenance exercises offline receipt consistency, not real IoT origin."""
        with tempfile.TemporaryDirectory(prefix="model-lab-verified-record-recovery-") as directory:
            root = Path(directory)
            workspace = root / "models" / "_drafts" / "verified-record-recovery"
            (workspace / "validation").mkdir(parents=True)
            (workspace / "tests").mkdir()
            bundle = workspace / "release-bundle" / "service"
            (bundle / "artifacts").mkdir(parents=True)
            (bundle / "MLmodel").write_text("synthetic service metadata\n", encoding="utf-8")
            (bundle / "artifacts" / "weights.txt").write_text("synthetic weights\n", encoding="utf-8")

            bundle_digest = hashlib.sha256()
            for path in sorted(bundle.rglob("*"), key=lambda item: item.relative_to(bundle).as_posix()):
                if path.is_file():
                    bundle_digest.update(path.relative_to(bundle).as_posix().encode("utf-8") + b"\0")
                    bundle_digest.update(path.read_bytes())
            service_sha256 = bundle_digest.hexdigest()
            provider_contents = {
                "module": b"# synthetic provider module\n",
                "runtime_init": b"# synthetic runtime initializer\n",
                "native": b"synthetic native runtime bytes\n",
            }
            provider_archive = root / "synthetic-company-package.zip"
            connection_module = (
                "import os\n"
                "os.environ['MLFLOW_TRACKING_URI'] = 'https://tracking.example.test'\n"
                "os.environ['MLFLOW_S3_ENDPOINT_URL'] = 'https://artifact.example.test'\n"
            )
            with zipfile.ZipFile(provider_archive, "w") as archive:
                archive.writestr(
                    "infra_model_deploy/resources/infra/mlops_deploy_iot2.py",
                    connection_module,
                )
                archive.writestr(
                    "infra_model_deploy/resources/infra/311/mlops_deploy_iot_armor2_311.py",
                    provider_contents["module"],
                )
                archive.writestr(
                    "infra_model_deploy/resources/infra/311/pyarmor_runtime_000000/__init__.py",
                    provider_contents["runtime_init"],
                )
                archive.writestr(
                    "infra_model_deploy/resources/infra/311/pyarmor_runtime_000000/linux_x86_64/pyarmor_runtime.so",
                    provider_contents["native"],
                )

            sha256 = lambda content: hashlib.sha256(content).hexdigest()
            archive_sha256 = sha256(provider_archive.read_bytes())
            module_sha256 = sha256(provider_contents["module"])
            runtime_init_sha256 = sha256(provider_contents["runtime_init"])
            native_sha256 = sha256(provider_contents["native"])
            manifest = {
                "schema_version": "1.0",
                "compatibility": {"model_lab": ">=0.1.0,<1.0.0"},
                "model": {
                    "id": "verified-record-recovery", "name": "Verified Record Recovery",
                    "version": "0.1.0", "lifecycle_state": "validated", "owner": "test",
                },
                "target": {
                    "kind": "cpu-iot",
                    "environment": {
                        "operating_system": "linux", "architecture": "x86_64",
                        "python": ">=3.11,<3.12", "mlflow": "==2.22.2",
                        "package_format": "mlflow-pyfunc",
                        "dependency_lock": "runtime/requirements.lock.txt",
                        "native_runtime": {
                            "kind": "pyarmor", "python_abi": "cp311",
                            "platform": "linux_x86_64", "artifact": "company_iot_native",
                        },
                    },
                },
                "contract": {
                    "inputs": [{"name": "flow", "type": "number", "unit": "m3/h"}],
                    "outputs": [{"name": "result", "type": "number", "unit": "dimensionless"}],
                },
                "artifacts": [
                    {
                        "name": "company_iot_native", "kind": "model",
                        "uri": "artifact://verified-record-recovery/native",
                        "sha256": native_sha256,
                    },
                    {
                        "name": "service", "kind": "mlflow-pyfunc",
                        "uri": "artifact://verified-record-recovery/service",
                        "sha256": service_sha256,
                    },
                ],
                "serving": {
                    "mode": "point-code", "factory_field": "factory_code",
                    "point_fields": ["flow"], "start_field": "start_time",
                    "rolling_window": "1h", "interval": "1min",
                    "binding": "input-bindings.yaml",
                    "time": {
                        "strategy": "explicit-start", "timezone": "UTC",
                        "iot_timestamp_timezone": "UTC",
                    },
                    "provider": {
                        "kind": "company-iot-311", "archive_sha256": archive_sha256,
                        "module_sha256": module_sha256,
                        "runtime_init_sha256": runtime_init_sha256,
                        "native_sha256": native_sha256,
                    },
                },
            }
            manifest_path = workspace / "model.yaml"
            manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
            (workspace / "runtime").mkdir()
            (workspace / "runtime" / "requirements.lock.txt").write_text(
                "mlflow==2.22.2\ncloudpickle==3.1.2\nnumpy==2.3.3\npandas==2.3.3\n",
                encoding="utf-8",
            )
            binding_path = workspace / "input-bindings.yaml"
            binding = {
                "schema_version": "2.1", "model_id": manifest["model"]["id"],
                "model_version": manifest["model"]["version"], "factory_code": "EXAMPLE",
                "bindings": [{
                    "input": "flow", "expected_signal": "Flow",
                    "expected_signal_kind": "instantaneous", "unit": "m3/h",
                    "aggregation": "single", "points": [{
                        "code": "EXAMPLE-P01-FLOW", "facility_name": "Example plant",
                        "equipment_name": "Meter", "acquisition_signal_name": "Flow",
                        "signal_kind": "instantaneous", "unit": "m3/h",
                        "point_id": "EXAMPLE-P01", "signal_code": "FLOW",
                    }],
                }],
            }
            binding_path.write_text(yaml.safe_dump(binding, sort_keys=False), encoding="utf-8")
            private_test = workspace / "tests" / "test_real_bundle.py"
            private_test.write_text("# synthetic accepted IoT evidence fixture\n", encoding="utf-8")
            acceptance = {
                "schema_version": "1.0", "profile": "model-iot-v1", "online_iot_fake": False,
                "model_id": manifest["model"]["id"], "version": manifest["model"]["version"],
                "provider_archive_sha256": archive_sha256, "bundle_sha256": service_sha256,
                "binding_sha256": sha256(binding_path.read_bytes()),
                "test_sha256": sha256(private_test.read_bytes()), "factory_code": "EXAMPLE",
                "point_codes": {"flow": ["EXAMPLE-P01-FLOW"]},
                "request_row": {
                    "factory_code": "EXAMPLE", "start_time": "2026-01-01 00:00:00",
                    "flow": ["EXAMPLE-P01-FLOW"],
                },
                "prediction_outputs": {"result": 1.0}, "oracle_outputs": {"result": 1.0},
                "raw_rows": 10, "window_rows": 10,
                "requested_at_utc": "2026-01-01T00:00:00+00:00",
                "received_at_utc": "2026-01-01T00:00:01+00:00",
            }
            (workspace / "validation" / "online-acceptance.json").write_text(
                json.dumps(acceptance), encoding="utf-8",
            )

            counters = {"connections": 0, "experiment_reads": 0, "run_reads": 0,
                        "downloads": 0, "model_loads": 0, "predictions": 0}
            publish_counters = {"runs_created": 0, "run_statuses": [], "logged_artifacts": 0}
            remote_run_tags: dict[str, dict[str, str]] = {}
            remote_run_status: dict[str, str] = {}
            run_id = "1" * 32
            run_tags = {
                "model_lab_model_id": manifest["model"]["id"],
                "model_lab_model_version": manifest["model"]["version"],
                "model_lab_bundle_sha256": service_sha256, "username": "synthetic-owner",
                "model_lab_plan_id": "synthetic-plan",
            }

            class FakeMlflowClient:
                def __init__(self, *, tracking_uri: str) -> None:
                    self.tracking_uri = tracking_uri

                def get_experiment_by_name(self, name: str) -> object:
                    counters["experiment_reads"] += 1
                    self.assert_experiment = name
                    return types.SimpleNamespace(experiment_id="17")

                def get_run(self, requested_run_id: str) -> object:
                    counters["run_reads"] += 1
                    return types.SimpleNamespace(
                        info=types.SimpleNamespace(
                            experiment_id="17",
                            status=remote_run_status.get(requested_run_id, "FINISHED"),
                            run_id=requested_run_id,
                        ),
                        data=types.SimpleNamespace(tags=remote_run_tags.get(requested_run_id, run_tags)),
                    )

                def search_runs(self, *_args: object, **_kwargs: object) -> list[object]:
                    return []

                def create_run(
                    self, experiment_id: str, *, tags: dict[str, str], run_name: str,
                ) -> object:
                    publish_counters["runs_created"] += 1
                    new_run_id = str(publish_counters["runs_created"] + 1) * 32
                    remote_run_tags[new_run_id] = dict(tags)
                    remote_run_status[new_run_id] = "RUNNING"
                    return types.SimpleNamespace(
                        info=types.SimpleNamespace(experiment_id=experiment_id, status="RUNNING", run_id=new_run_id),
                    )

                def set_tag(self, requested_run_id: str, key: str, value: str) -> None:
                    remote_run_tags[requested_run_id][key] = value

                def log_artifacts(self, _run_id: str, _local_dir: str, *, artifact_path: str) -> None:
                    publish_counters["logged_artifacts"] += 1

                def set_terminated(self, requested_run_id: str, *, status: str) -> None:
                    publish_counters["run_statuses"].append(status)
                    remote_run_status[requested_run_id] = status

                def download_artifacts(self, requested_run_id: str, artifact_path: str, *, dst_path: str) -> str:
                    counters["downloads"] += 1
                    destination = Path(dst_path) / "service"
                    shutil.copytree(bundle, destination)
                    return str(destination)

            model_behavior = {"prediction": 1.0}

            class FakeRemoteModel:
                def predict(self, _sample: object) -> object:
                    import pandas as pd
                    counters["predictions"] += 1
                    return pd.DataFrame({"result": [model_behavior["prediction"]]})

            mlflow_module = types.ModuleType("mlflow")
            mlflow_module.__path__ = []  # type: ignore[attr-defined]
            mlflow_module.set_tracking_uri = lambda _uri: None  # type: ignore[attr-defined]
            mlflow_module.pyfunc = types.SimpleNamespace(  # type: ignore[attr-defined]
                load_model=lambda _uri: (counters.__setitem__("model_loads", counters["model_loads"] + 1)
                                         or FakeRemoteModel())
            )
            tracking_module = types.ModuleType("mlflow.tracking")
            tracking_module.MlflowClient = FakeMlflowClient  # type: ignore[attr-defined]
            mlflow_module.tracking = tracking_module  # type: ignore[attr-defined]
            models_module = types.ModuleType("mlflow.models")

            class FakeMlflowModel:
                @classmethod
                def load(cls, _path: Path) -> object:
                    return types.SimpleNamespace(to_dict=lambda: {"synthetic_model_metadata": True})

            models_module.Model = FakeMlflowModel  # type: ignore[attr-defined]
            mlflow_module.models = models_module  # type: ignore[attr-defined]
            boto3_module = types.ModuleType("boto3")
            boto3_module.__spec__ = ModuleSpec("boto3", loader=None)

            class FakeConnection:
                def __enter__(self) -> "FakeConnection":
                    return self

                def __exit__(self, *_args: object) -> None:
                    return None

            receipt_path = workspace / "validation" / "publications" / f"{run_id}.json"

            def call_verify(*, snapshot_failure: str | None = None, fail_receipt: bool = False,
                            arguments: tuple[str, ...] | None = None,
                            extra_modules: dict[str, types.ModuleType] | None = None,
                            receipt_failure_target: Path | None = None,
                            ) -> tuple[int, str, str, list[Path], list[Path]]:
                original_replace = os.replace
                failed_snapshot_targets: list[Path] = []
                failed_receipt_targets: list[Path] = []

                def maybe_fail_snapshot_replace(source: object, destination: object) -> None:
                    target = Path(destination)
                    if target.name == "workflow-state.json":
                        failed_snapshot_targets.append(target)
                        if snapshot_failure == "always" or (
                            snapshot_failure == "once" and len(failed_snapshot_targets) == 1
                        ):
                            raise OSError("synthetic snapshot replace failure")
                    original_replace(source, destination)

                original_open = Path.open

                def maybe_fail_receipt_open(
                    path: Path, mode: str = "r", *args: object, **kwargs: object,
                ) -> object:
                    expected_receipt_path = receipt_failure_target or receipt_path
                    if fail_receipt and path == expected_receipt_path and mode == "x":
                        failed_receipt_targets.append(path)
                        raise OSError("synthetic publication receipt create failure")
                    return original_open(path, mode, *args, **kwargs)

                prior_connection_module = sys.modules.pop("mlops_deploy_iot2", None)
                try:
                    with ExitStack() as stack:
                        modules = {
                            "mlflow": mlflow_module,
                            "mlflow.tracking": tracking_module,
                            "mlflow.models": models_module,
                        }
                        modules.update(extra_modules or {})
                        stack.enter_context(patch.dict(
                            sys.modules, modules,
                        ))
                        stack.enter_context(patch.dict(os.environ, {
                            "MODEL_LAB_ALLOWED_TRACKING_HOST": "tracking.example.test",
                            "MODEL_LAB_ALLOWED_ARTIFACT_HOST": "artifact.example.test",
                        }))
                        stack.enter_context(patch("socket.create_connection", side_effect=lambda *_args, **_kwargs: (
                            counters.__setitem__("connections", counters["connections"] + 1) or FakeConnection()
                        )))
                        if snapshot_failure is not None:
                            stack.enter_context(patch("os.replace", side_effect=maybe_fail_snapshot_replace))
                        if fail_receipt:
                            stack.enter_context(patch.object(Path, "open", new=maybe_fail_receipt_open))
                        result = invoke(*(arguments or (
                            "verify-publication", str(manifest_path), "--experiment", "synthetic-experiment",
                            "--run-id", run_id, "--company-package", str(provider_archive), "--format", "json",
                        )))
                finally:
                    sys.modules.pop("mlops_deploy_iot2", None)
                    if prior_connection_module is not None:
                        sys.modules["mlops_deploy_iot2"] = prior_connection_module
                return (*result, failed_snapshot_targets, failed_receipt_targets)

            code, stdout, stderr, failed_snapshot_targets, _ = call_verify(snapshot_failure="always")

            self.assertEqual(counters, {
                "connections": 2, "experiment_reads": 1, "run_reads": 1,
                "downloads": 1, "model_loads": 1, "predictions": 1,
            }, stderr)
            self.assertGreaterEqual(len(failed_snapshot_targets), 1)
            result = json.loads(stdout)
            self.assertEqual(result["status"], "verified")
            self.assertEqual(result["reason"], "verified_local_record_incomplete")
            self.assertEqual(code, 2, stderr)
            self.assertEqual(len(failed_snapshot_targets), 2)
            self.assertEqual(
                result["completion"],
                {"operation": "completed", "local_record": "incomplete", "repeat_operation": False},
            )
            self.assertEqual(result["local_record"]["failed_part"], "snapshot")
            self.assertEqual(result["local_record"]["snapshot_attempts"], 2)
            self.assertEqual(result["recovery"]["action"], "refresh_snapshot")
            self.assertIn("context", result["recovery"]["command"])
            self.assertIn("--write", result["recovery"]["command"])
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "verified")
            self.assertEqual(receipt["verification"]["run_finished"], True)
            self.assertFalse((workspace / "workflow-state.json").exists())

            # A single failed index replacement is retried; the remote Run is only read.
            before = counters.copy()
            retry_code, retry_stdout, retry_stderr, retry_snapshot_targets, _ = call_verify(
                snapshot_failure="once",
            )
            retry_result = json.loads(retry_stdout)
            self.assertEqual(retry_code, 0, retry_stderr)
            self.assertEqual(len(retry_snapshot_targets), 2)
            self.assertEqual(retry_result["status"], "verified")
            self.assertEqual(retry_result["reason"], "existing_run_verified")
            self.assertTrue((workspace / "workflow-state.json").is_file())
            self.assertEqual(counters["run_reads"] - before["run_reads"], 1)

            # Receipt creation failure is incomplete local recording, recoverable by verifying that same Run.
            receipt_path.unlink()
            receipt_code, receipt_stdout, receipt_stderr, _, failed_receipt_targets = call_verify(
                fail_receipt=True,
            )
            receipt_result = json.loads(receipt_stdout)
            self.assertEqual(receipt_code, 2, receipt_stderr)
            self.assertEqual(len(failed_receipt_targets), 1)
            self.assertEqual(receipt_result["status"], "verified")
            self.assertEqual(receipt_result["completion"]["operation"], "completed")
            self.assertEqual(receipt_result["completion"]["local_record"], "incomplete")
            self.assertEqual(receipt_result["completion"]["repeat_operation"], False)
            self.assertEqual(receipt_result["local_record"]["failed_part"], "receipt")
            self.assertEqual(receipt_result["recovery"]["action"], "verify_existing_publication")
            recovery_command = receipt_result["recovery"]["command"]
            self.assertIn("verify-publication", recovery_command)
            self.assertIn(run_id, recovery_command)
            self.assertFalse(receipt_path.exists())

            before = counters.copy()
            recovery_code, recovery_stdout, recovery_stderr, _, _ = call_verify(
                arguments=tuple(recovery_command[2:]),
            )
            recovery_result = json.loads(recovery_stdout)
            self.assertEqual(recovery_code, 0, f"{recovery_stderr}\n{recovery_stdout}")
            self.assertEqual(recovery_result["status"], "verified")
            self.assertEqual(counters["run_reads"] - before["run_reads"], 1)
            self.assertTrue(receipt_path.is_file())
            self.assertTrue((workspace / "workflow-state.json").is_file())

            # A conflicting existing receipt remains byte-for-byte intact after remote verification.
            conflicting_receipt = b'{"status":"not-the-verified-run","preserve":"original bytes"}\n'
            receipt_path.write_bytes(conflicting_receipt)
            conflict_code, conflict_stdout, conflict_stderr, _, _ = call_verify()
            conflict_result = json.loads(conflict_stdout)
            self.assertEqual(conflict_code, 2, conflict_stderr)
            self.assertEqual(conflict_result["status"], "verified")
            self.assertEqual(conflict_result["reason"], "receipt_conflict")
            self.assertEqual(conflict_result["completion"]["local_record"], "incomplete")
            self.assertEqual(conflict_result["local_record"]["failed_part"], "receipt")
            self.assertEqual(conflict_result["recovery"]["action"], "verify_existing_publication")
            self.assertEqual(receipt_path.read_bytes(), conflicting_receipt)

            # Remote identity or prediction evidence that fails verification remains blocked, not completed.
            run_tags["model_lab_model_id"] = "different-model"
            identity_code, identity_stdout, identity_stderr, _, _ = call_verify()
            identity_result = json.loads(identity_stdout)
            self.assertEqual(identity_code, 4, identity_stderr)
            self.assertEqual(identity_result["status"], "blocked")
            self.assertEqual(identity_result["reason"], "run_identity_mismatch")
            self.assertNotIn("completion", identity_result)
            self.assertEqual(receipt_path.read_bytes(), conflicting_receipt)
            run_tags["model_lab_model_id"] = manifest["model"]["id"]

            model_behavior["prediction"] = 7.0
            prediction_code, prediction_stdout, prediction_stderr, _, _ = call_verify()
            prediction_result = json.loads(prediction_stdout)
            self.assertEqual(prediction_code, 4, prediction_stderr)
            self.assertEqual(prediction_result["status"], "blocked")
            self.assertEqual(prediction_result["reason"], "remote_prediction_mismatch")
            self.assertNotIn("completion", prediction_result)
            self.assertEqual(receipt_path.read_bytes(), conflicting_receipt)

            # Fixed-bundle publishing exercises real local preflight and fake external SDK boundaries.
            model_behavior["prediction"] = 1.0

            def make_publishable(candidate_workspace: Path, model_id: str, version: str) -> Path:
                candidate_manifest_path = candidate_workspace / "model.yaml"
                candidate_manifest = yaml.safe_load(candidate_manifest_path.read_text(encoding="utf-8"))
                candidate_manifest["model"]["id"] = model_id
                candidate_manifest["model"]["name"] = model_id.replace("-", " ").title()
                candidate_manifest["model"]["version"] = version
                candidate_manifest["model"]["lifecycle_state"] = "releasable"
                candidate_bundle = candidate_workspace / "release-bundle" / "service"
                candidate_artifacts = candidate_bundle / "artifacts"
                candidate_code = candidate_bundle / "code"
                (candidate_artifacts / "logic_bundle.pkl").write_bytes(b"synthetic logic bundle")
                (candidate_artifacts / "custom_sources.pkl").write_bytes(b"synthetic custom sources")
                (candidate_artifacts / "model.bin").write_bytes(b"synthetic model file")
                (candidate_bundle / "python_model.pkl").write_bytes(b"synthetic python model")
                (candidate_bundle / "python_env.yaml").write_text('python: "3.11.15"\n', encoding="utf-8")
                (candidate_bundle / "requirements.txt").write_text(
                    "mlflow==2.22.2\ncloudpickle==3.1.2\nnumpy==2.3.3\npandas==2.3.3\n",
                    encoding="utf-8",
                )
                (candidate_bundle / "conda.yaml").write_text(
                    "name: synthetic-env\nchannels: [conda-forge]\ndependencies:\n"
                    "  - python=3.11.15\n  - pip:\n"
                    "      - mlflow==2.22.2\n      - cloudpickle==3.1.2\n"
                    "      - numpy==2.3.3\n      - pandas==2.3.3\n",
                    encoding="utf-8",
                )
                provider_paths = {
                    "module": "code/mlops_deploy_iot_armor2_311.py",
                    "runtime_init": "code/pyarmor_runtime_000000/__init__.py",
                    "native": "code/pyarmor_runtime_000000/linux_x86_64/pyarmor_runtime.so",
                }
                for label, relative in provider_paths.items():
                    target = candidate_bundle / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(provider_contents[label])
                provenance = {
                    "archive_sha256": archive_sha256, "module_sha256": module_sha256,
                    "runtime_init_sha256": runtime_init_sha256, "native_sha256": native_sha256,
                    "source": "infra_model_deploy.zip:resources/infra/311",
                    "python_abi": "cp311", "platform": "linux_x86_64",
                }
                provenance_path = candidate_code / "company_iot_provenance.json"
                provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
                signature = [
                    {"name": "factory_code", "type": "string", "required": True},
                    {"name": "start_time", "type": "string", "required": True},
                    {"name": "flow", "type": "array", "items": {"type": "string"}, "required": True},
                ]
                (candidate_bundle / "MLmodel").write_text(yaml.safe_dump({
                    "flavors": {"python_function": {
                        "loader_module": "mlflow.pyfunc.model", "python_model": "python_model.pkl",
                        "artifacts": {
                            "logic_bundle": "artifacts/logic_bundle.pkl",
                            "custom_sources": "artifacts/custom_sources.pkl",
                            "model_file": "artifacts/model.bin",
                            "company_iot_native": provider_paths["native"],
                            "company_iot_provenance": "code/company_iot_provenance.json",
                        },
                    }},
                    "signature": {"inputs": json.dumps(signature)},
                }, sort_keys=False), encoding="utf-8")
                service_hash = hashlib.sha256()
                for path in sorted(candidate_bundle.rglob("*"), key=lambda item: item.relative_to(candidate_bundle).as_posix()):
                    if path.is_file():
                        service_hash.update(path.relative_to(candidate_bundle).as_posix().encode("utf-8") + b"\0")
                        service_hash.update(path.read_bytes())
                candidate_service_sha = service_hash.hexdigest()
                candidate_artifact = next(
                    item for item in candidate_manifest["artifacts"] if item["kind"] == "mlflow-pyfunc"
                )
                candidate_artifact["sha256"] = candidate_service_sha
                binding_candidate = candidate_workspace / "input-bindings.yaml"
                binding_doc = yaml.safe_load(binding_candidate.read_text(encoding="utf-8"))
                binding_doc["model_id"] = model_id
                binding_doc["model_version"] = version
                binding_candidate.write_text(yaml.safe_dump(binding_doc, sort_keys=False), encoding="utf-8")
                acceptance_path = candidate_workspace / "validation" / "online-acceptance.json"
                acceptance_doc = json.loads(acceptance_path.read_text(encoding="utf-8"))
                acceptance_doc.update({
                    "model_id": model_id, "version": version,
                    "bundle_sha256": candidate_service_sha,
                    "binding_sha256": sha256(binding_candidate.read_bytes()),
                })
                acceptance_path.write_text(json.dumps(acceptance_doc), encoding="utf-8")
                candidate_manifest_path.write_text(
                    yaml.safe_dump(candidate_manifest, sort_keys=False), encoding="utf-8",
                )
                return candidate_manifest_path

            def call_publish(candidate_manifest_path: Path, experiment: str, owner: str,
                             *, snapshot_failure: str | None = None,
                             fail_receipt: bool = False,
                             receipt_run_id: str | None = None,
                             ) -> tuple[int, str, str, list[Path], list[Path]]:
                expected_run_id = receipt_run_id or str(publish_counters["runs_created"] + 2) * 32
                return call_verify(
                    snapshot_failure=snapshot_failure,
                    fail_receipt=fail_receipt,
                    receipt_failure_target=(
                        candidate_manifest_path.parent / "validation" / "publications"
                        / f"{expected_run_id}.json"
                    ),
                    extra_modules={"boto3": boto3_module},
                    arguments=(
                        "publish", str(candidate_manifest_path), "--target", "cpu-iot",
                        "--experiment", experiment, "--owner", owner, "--authorize-publish",
                        "--company-package", str(provider_archive), "--apply", "--format", "json",
                    ),
                )

            publish_manifest = make_publishable(workspace, "verified-record-recovery", "0.1.0")
            publish_code, publish_stdout, publish_stderr, publish_snapshot_targets, _ = call_publish(
                publish_manifest, "synthetic-publish-experiment", "synthetic-owner",
                snapshot_failure="always",
            )
            publish_result = json.loads(publish_stdout)
            published_run_id = "2" * 32
            self.assertEqual(publish_code, 2, publish_stderr)
            self.assertEqual(publish_result["status"], "published")
            self.assertEqual(publish_result["completion"]["operation"], "completed")
            self.assertEqual(publish_result["completion"]["local_record"], "incomplete")
            self.assertEqual(publish_result["completion"]["repeat_operation"], False)
            self.assertEqual(publish_result["local_record"]["failed_part"], "snapshot")
            self.assertEqual(publish_result["recovery"]["action"], "refresh_snapshot")
            self.assertEqual(len(publish_snapshot_targets), 2)
            self.assertEqual(publish_counters["runs_created"], 1)
            self.assertEqual(publish_counters["run_statuses"], ["FINISHED"])
            published_receipt = workspace / "validation" / "publications" / f"{published_run_id}.json"
            self.assertEqual(json.loads(published_receipt.read_text(encoding="utf-8"))["status"], "verified")

            # A distinct model version is an independent publish request; receipt failure must not mark its finished Run failed.
            second_workspace = workspace.parent / "verified-record-recovery-receipt-failure"
            shutil.copytree(workspace, second_workspace)
            second_manifest = make_publishable(
                second_workspace, "verified-record-recovery-receipt-failure", "0.2.0",
            )
            second_run_id = "3" * 32
            receipt_publish_code, receipt_publish_stdout, receipt_publish_stderr, _, receipt_attempts = call_publish(
                second_manifest, "synthetic-publish-experiment-v2", "synthetic-owner",
                fail_receipt=True, receipt_run_id=second_run_id,
            )
            receipt_publish_result = json.loads(receipt_publish_stdout)
            self.assertEqual(receipt_publish_code, 2, receipt_publish_stderr)
            self.assertEqual(receipt_publish_result["status"], "published")
            self.assertEqual(receipt_publish_result["completion"]["operation"], "completed")
            self.assertEqual(receipt_publish_result["completion"]["local_record"], "incomplete")
            self.assertEqual(receipt_publish_result["local_record"]["failed_part"], "receipt")
            self.assertEqual(receipt_publish_result["recovery"]["action"], "verify_existing_publication")
            self.assertEqual(receipt_publish_result["recovery"]["command"][3], str(second_manifest))
            self.assertEqual(len(receipt_attempts), 1)
            self.assertFalse((second_workspace / "validation" / "publications" / f"{second_run_id}.json").exists())
            self.assertEqual(publish_counters["runs_created"], 2)
            self.assertEqual(publish_counters["run_statuses"], ["FINISHED", "FINISHED"])

            before_recovery_runs = publish_counters["runs_created"]
            publish_recovery_command = receipt_publish_result["recovery"]["command"]
            publish_recovery_code, publish_recovery_stdout, publish_recovery_stderr, _, _ = call_verify(
                arguments=tuple(publish_recovery_command[2:]),
                extra_modules={"boto3": boto3_module},
            )
            publish_recovery_result = json.loads(publish_recovery_stdout)
            self.assertEqual(publish_recovery_code, 0, f"{publish_recovery_stderr}\n{publish_recovery_stdout}")
            self.assertEqual(publish_recovery_result["status"], "verified")
            self.assertEqual(publish_counters["runs_created"], before_recovery_runs)
            self.assertEqual(publish_counters["run_statuses"], ["FINISHED", "FINISHED"])
            self.assertTrue((second_workspace / "validation" / "publications" / f"{second_run_id}.json").is_file())


if __name__ == "__main__":
    unittest.main()
