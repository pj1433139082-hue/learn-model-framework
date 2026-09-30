"""Upload one already-built MLflow service through the company connection.

The company helper supplies connection settings, but its deployment function
builds a *new* service. This adapter uploads the digest-pinned service as-is.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import importlib.util
import json
import math
import numbers
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
from urllib.parse import urlsplit
import zipfile

import yaml  # type: ignore[import-untyped]

from model_lab.bundle_build import _tree_digest
from model_lab.deployment import build_dry_run_plan
from model_lab.online_acceptance import PROFILE, verify_online_acceptance
from model_lab.path_safety import is_link_or_junction
from model_lab.runtime_contract import check_build_environment
from model_lab.workflow_state import write_publication_receipt, write_snapshot


_COMPANY_MODULE = "infra_model_deploy/resources/infra/mlops_deploy_iot2.py"
_SERVICE_ARTIFACT = "service"


class PublishBlocked(RuntimeError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class PublishExecutionResult:
    status: str
    reason: str
    model_id: str
    version: str
    experiment: str
    owner: str
    bundle_sha256: str
    run_id: str | None = None
    phase: str | None = None
    error_type: str | None = None
    remote_writes: bool | None = False
    receipt_path: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "model_id": self.model_id,
            "version": self.version,
            "experiment": self.experiment,
            "owner": self.owner,
            "bundle_sha256": self.bundle_sha256,
            "run_id": self.run_id,
            "phase": self.phase,
            "error_type": self.error_type,
            "receipt_path": self.receipt_path,
            "safety": {"remote_writes": self.remote_writes},
        }

    def as_text(self) -> str:
        action = "EXISTING PUBLICATION" if self.status == "verified" else "PUBLISH"
        lines = [
            f"{self.model_id} {self.version}: {action} {self.status.upper()}",
            f"Reason: {self.reason}",
            f"Experiment: {self.experiment}",
            f"Bundle SHA-256: {self.bundle_sha256}",
        ]
        if self.run_id:
            lines.append(f"Run ID: {self.run_id}")
        if self.phase:
            lines.append(f"Phase: {self.phase}")
        if self.error_type:
            lines.append(f"Error type: {self.error_type}")
        if self.receipt_path:
            lines.append(f"Local receipt: {self.receipt_path}")
        return "\n".join(lines)


def _verified_company_package(path: Path, expected_sha256: str) -> None:
    if is_link_or_junction(path) or not path.is_file():
        raise PublishBlocked("company_package_missing", "company package is missing or linked")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected_sha256:
        raise PublishBlocked("company_package_digest_mismatch", "company package digest differs from model.yaml")
    try:
        with zipfile.ZipFile(path) as archive:
            if _COMPANY_MODULE not in archive.namelist():
                raise PublishBlocked("company_package_invalid", "company connection module is missing")
    except zipfile.BadZipFile as error:
        raise PublishBlocked("company_package_invalid", "company package is not a ZIP archive") from error


def _init_company_connection(path: Path) -> str:
    expected_tracking = os.environ.get("MODEL_LAB_ALLOWED_TRACKING_HOST", "")
    expected_artifact = os.environ.get("MODEL_LAB_ALLOWED_ARTIFACT_HOST", "")
    if not expected_tracking or not expected_artifact:
        raise PublishBlocked(
            "company_target_unconfigured",
            "private deployment profile must provide the two approved MLflow hostnames",
        )
    for host in (expected_tracking, expected_artifact):
        if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", host) is None:
            raise PublishBlocked("company_target_invalid", "approved host must be a DNS hostname")
    # Import in this dedicated CLI process: the reviewed company module sets
    # MLflow/S3 variables and clears proxy variables for its own internal route.
    prefix = f"{path}/infra_model_deploy/resources/infra"
    sys.path.insert(0, prefix)
    try:
        module = importlib.import_module("mlops_deploy_iot2")
    finally:
        sys.path.remove(prefix)
    if not str(getattr(module, "__file__", "")).startswith(str(path)):
        raise PublishBlocked("company_package_import_mismatch", "connection module came from another location")
    uri = os.environ.get("MLFLOW_TRACKING_URI", "")
    endpoints = (
        (uri, expected_tracking, "tracking"),
        (os.environ.get("MLFLOW_S3_ENDPOINT_URL", ""), expected_artifact, "artifact_store"),
    )
    for endpoint, expected_host, label in endpoints:
        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" or parsed.hostname != expected_host:
            raise PublishBlocked(f"{label}_target_invalid", f"company {label} target is not the pinned HTTPS host")
        try:
            with socket.create_connection((expected_host, parsed.port or 443), timeout=5):
                pass
        except OSError as error:
            raise PublishBlocked(f"{label}_unreachable", f"company {label} host is not reachable") from error
    # The company helper sets this to true, but the internal HTTPS certificate
    # validates normally. Keep server identity verification enabled for S3.
    os.environ["MLFLOW_S3_IGNORE_TLS"] = "false"
    return uri


def _platform_sample(workspace: Path, manifest: dict[str, object]) -> tuple[object, float | dict[str, float | int]]:
    import pandas as pd

    serving = manifest["serving"]
    if not isinstance(serving, dict) or serving.get("mode") != "point-code":
        raise PublishBlocked("serving_mode_unsupported", "only accepted point-code models are enabled")
    receipt = json.loads((workspace / "validation/online-acceptance.json").read_text(encoding="utf-8"))
    if receipt["profile"] == PROFILE:
        return pd.DataFrame([receipt["request_row"]]), dict(receipt["prediction_outputs"])
    raise PublishBlocked("acceptance_profile_unsupported", "this online acceptance profile has no platform sample")


def _check_remote_service(
    client: object, run_id: str, expected_digest: str, sample: object,
    expected: float | dict[str, float | int], output_fields: tuple[str, ...],
) -> None:
    import mlflow

    with tempfile.TemporaryDirectory(prefix="model-lab-publish-") as temporary:
        downloaded = Path(client.download_artifacts(run_id, _SERVICE_ARTIFACT, dst_path=temporary))  # type: ignore[attr-defined]
        if _tree_digest(downloaded) != expected_digest:
            raise PublishBlocked("remote_bundle_digest_mismatch", "downloaded service differs from accepted bundle")
    model = mlflow.pyfunc.load_model(f"runs:/{run_id}/{_SERVICE_ARTIFACT}")
    prediction = model.predict(sample)
    if len(prediction) != 1 or tuple(prediction.columns) != output_fields:
        raise PublishBlocked("remote_prediction_invalid", "remote model returned an unexpected shape")
    if isinstance(expected, dict):
        if set(expected) != set(output_fields):
            raise PublishBlocked("remote_prediction_invalid", "accepted outputs differ from the declared contract")
        for field in output_fields:
            target = expected[field]
            observed = prediction.iloc[0][field]
            if (isinstance(target, bool) or not isinstance(target, numbers.Real)
                    or isinstance(observed, bool) or not isinstance(observed, numbers.Real)):
                raise PublishBlocked("remote_prediction_invalid", f"{field} is not a numeric output")
            value = float(observed)
            if not math.isfinite(value) or abs(value - float(target)) > 1e-6:
                raise PublishBlocked("remote_prediction_mismatch", f"{field} differs from accepted IoT result")
            if type(target) is int and not value.is_integer():
                raise PublishBlocked("remote_prediction_mismatch", f"{field} is not an integer output")
    else:
        value = float(prediction.iloc[0, 0])
        if not math.isfinite(value) or abs(value - expected) > 1e-6:
            raise PublishBlocked("remote_prediction_mismatch", "remote prediction differs from accepted IoT result")


def publish_fixed_bundle(
    manifest_path: str | Path,
    *,
    experiment: str,
    owner: str,
    company_package: str | Path,
) -> PublishExecutionResult:
    """Upload and reload the exact accepted service; never rebuild the model."""
    from model_lab.environment import ensure_external_workspace
    ensure_external_workspace(manifest_path)
    path = Path(manifest_path).resolve(strict=True)
    workspace = path.parent
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    model = document["model"]
    model_id = model["id"]
    version = model["version"]
    local_bundle = workspace / "release-bundle/service"
    expected_digest = next((
        artifact["sha256"] for artifact in document["artifacts"]
        if artifact["kind"] == "mlflow-pyfunc"
    ), "")
    phase = "local-preflight"
    run_id: str | None = None
    remote_writes: bool | None = False
    client = None
    try:
        if not expected_digest:
            raise PublishBlocked("service_missing", "model.yaml does not declare an MLflow service")
        plan = build_dry_run_plan(path)
        if plan.target != "cpu-iot" or plan.lifecycle_state != "releasable":
            raise PublishBlocked("target_not_enabled", "this publisher requires a releasable CPU-IoT model")
        runtime = check_build_environment(path)
        if not runtime.valid:
            raise PublishBlocked("runtime_mismatch", "publish must run on the declared Linux Python 3.11 target")
        accepted, message = verify_online_acceptance(workspace, document)
        if not accepted:
            raise PublishBlocked("online_acceptance_missing", message)
        outputs = document["contract"]["outputs"]
        if not outputs:
            raise PublishBlocked("output_profile_unsupported", "fixed-bundle publication requires declared outputs")
        provider = document["serving"]["provider"]
        package = Path(company_package).resolve(strict=True)
        _verified_company_package(package, provider["archive_sha256"])
        if importlib.util.find_spec("boto3") is None:
            raise PublishBlocked("artifact_client_dependency_missing", "boto3 is required for MLflow S3 artifacts")
        sample, expected = _platform_sample(workspace, document)
        phase = "connection"
        uri = _init_company_connection(package)
        import mlflow
        from mlflow.models import Model
        from mlflow.tracking import MlflowClient

        mlflow.set_tracking_uri(uri)
        client = MlflowClient(tracking_uri=uri)
        phase = "experiment-read"
        target = client.get_experiment_by_name(experiment)
        if target is None:
            phase = "experiment-create"
            remote_writes = None  # timeout could occur after the server accepted the write
            experiment_id = client.create_experiment(experiment)
            remote_writes = True
        else:
            experiment_id = target.experiment_id
        phase = "duplicate-check"
        prior = client.search_runs(
            [experiment_id],
            filter_string=f"tags.model_lab_bundle_sha256 = '{expected_digest}'",
            max_results=100,
        )
        if prior:
            matching = [
                item for item in prior
                if item.data.tags.get("model_lab_model_id") == model_id
                and item.data.tags.get("model_lab_model_version") == version
            ]
            finished = next((item for item in matching if item.info.status == "FINISHED"), None)
            if finished is not None:
                return PublishExecutionResult(
                    "blocked", "bundle_already_uploaded", model_id, version,
                    experiment, owner, expected_digest,
                    run_id=finished.info.run_id,
                    phase="duplicate-check", remote_writes=remote_writes,
                )
            running = next((item for item in matching if item.info.status == "RUNNING"), None)
            if running is not None:
                return PublishExecutionResult(
                    "blocked", "bundle_upload_in_progress", model_id, version,
                    experiment, owner, expected_digest,
                    run_id=running.info.run_id,
                    phase="duplicate-check", remote_writes=remote_writes,
                )
        if _tree_digest(local_bundle) != expected_digest:
            raise PublishBlocked("bundle_changed_before_upload", "service changed after preflight")
        phase = "run-create"
        if remote_writes is False:
            remote_writes = None
        run = client.create_run(
            experiment_id,
            tags={
                "username": owner,
                "python_env_desc": "Python 3.11 Linux x86_64",
                "model_type": "pyfunc",
                "deploy_tool_version": "model-lab-fixed-bundle-v1",
                "model_lab_model_id": model_id,
                "model_lab_model_version": version,
                "model_lab_bundle_sha256": expected_digest,
                "model_lab_plan_id": plan.plan_id,
            },
            run_name=f"{model_id}_{version}",
        )
        run_id = run.info.run_id
        remote_writes = True
        phase = "service-upload"
        model_info = Model.load(local_bundle / "MLmodel").to_dict()
        model_info["run_id"] = run_id
        model_info["artifact_path"] = _SERVICE_ARTIFACT
        client.set_tag(run_id, "mlflow.log-model.history", json.dumps([model_info]))
        client.log_artifacts(run_id, str(local_bundle), artifact_path=_SERVICE_ARTIFACT)
        phase = "remote-reload"
        _check_remote_service(client, run_id, expected_digest, sample, expected,
                              tuple(item["name"] for item in outputs))
        phase = "run-finish"
        client.set_terminated(run_id, status="FINISHED")
        # The remote transaction has now succeeded. A local disk error must not
        # send this verified Run back through the outer FAILED handler.
        try:
            receipt = write_publication_receipt(
                path, experiment=experiment, experiment_id=str(experiment_id),
                run_id=run_id, owner=owner, bundle_sha256=expected_digest,
                plan_id=plan.plan_id,
            )
            write_snapshot(path)
        except Exception as local_error:
            return PublishExecutionResult(
                "published", "verified_local_record_incomplete", model_id, version,
                experiment, owner, expected_digest, run_id=run_id,
                phase="local-record", error_type=type(local_error).__name__,
                remote_writes=True,
            )
        return PublishExecutionResult(
            "published", "verified", model_id, version, experiment, owner,
            expected_digest, run_id=run_id, remote_writes=True,
            receipt_path=str(receipt.relative_to(workspace)).replace("\\", "/"),
        )
    except Exception as error:
        if run_id is not None and client is not None:
            try:
                client.set_terminated(run_id, status="FAILED")
            except Exception:
                pass
        reason = error.reason if isinstance(error, PublishBlocked) else "publish_error"
        return PublishExecutionResult(
            "blocked" if remote_writes is False else "failed",
            reason, model_id, version, experiment, owner, expected_digest,
            run_id=run_id, phase=phase, error_type=type(error).__name__,
            remote_writes=remote_writes,
        )


def verify_existing_publication(
    manifest_path: str | Path, *, experiment: str, run_id: str,
    company_package: str | Path,
) -> PublishExecutionResult:
    """Read an existing Run and its service; only write a local receipt after full verification."""
    from model_lab.environment import ensure_external_workspace
    ensure_external_workspace(manifest_path)
    path = Path(manifest_path).resolve(strict=True)
    workspace = path.parent
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    model = document["model"]
    model_id, version = model["id"], model["version"]
    digest = next((artifact.get("sha256") for artifact in document.get("artifacts", [])
                   if artifact.get("kind") == "mlflow-pyfunc"), "")
    phase = "local-preflight"
    owner = ""
    try:
        if not isinstance(digest, str) or not digest:
            raise PublishBlocked("service_missing", "model.yaml does not declare an MLflow service")
        if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise PublishBlocked("run_id_invalid", "supply the exact 32-character Run ID")
        runtime = check_build_environment(path)
        if not runtime.valid:
            raise PublishBlocked("runtime_mismatch", "verify on the declared Linux Python 3.11 target")
        accepted, message = verify_online_acceptance(workspace, document)
        if not accepted:
            raise PublishBlocked("online_acceptance_missing", message)
        outputs = document["contract"]["outputs"]
        if not outputs:
            raise PublishBlocked("output_profile_unsupported", "declared outputs are required")
        provider = document["serving"]["provider"]
        package = Path(company_package).resolve(strict=True)
        _verified_company_package(package, provider["archive_sha256"])
        sample, expected = _platform_sample(workspace, document)
        phase = "connection"
        uri = _init_company_connection(package)
        import mlflow
        from mlflow.tracking import MlflowClient

        mlflow.set_tracking_uri(uri)
        client = MlflowClient(tracking_uri=uri)
        phase = "experiment-read"
        target = client.get_experiment_by_name(experiment)
        if target is None:
            raise PublishBlocked("experiment_missing", "experiment does not exist")
        phase = "run-read"
        run = client.get_run(run_id)
        tags = run.data.tags
        if run.info.experiment_id != target.experiment_id or run.info.status != "FINISHED":
            raise PublishBlocked("run_not_finished_in_experiment", "Run is not FINISHED in the named experiment")
        if (tags.get("model_lab_model_id") != model_id
                or tags.get("model_lab_model_version") != version
                or tags.get("model_lab_bundle_sha256") != digest):
            raise PublishBlocked("run_identity_mismatch", "Run tags do not match this model version and bundle")
        owner = tags.get("username", "")
        phase = "remote-reload"
        _check_remote_service(client, run_id, digest, sample, expected,
                              tuple(item["name"] for item in outputs))
        phase = "local-record"
        receipt = workspace / "validation" / "publications" / f"{run_id}.json"
        if receipt.exists():
            if is_link_or_junction(receipt):
                raise PublishBlocked("receipt_conflict", "existing local receipt is linked")
            recorded = json.loads(receipt.read_text(encoding="utf-8"))
            verification = recorded.get("verification", {})
            if (recorded.get("status") != "verified"
                    or recorded.get("experiment") != experiment
                    or recorded.get("model_id") != model_id
                    or recorded.get("model_version") != version
                    or recorded.get("bundle_sha256") != digest
                    or recorded.get("run_id") != run_id
                    or not isinstance(verification, dict)
                    or not all(verification.get(key) is True for key in
                               ("run_finished", "downloaded_digest_matched", "platform_predict_matched"))):
                raise PublishBlocked("receipt_conflict", "existing local receipt differs from the verified Run")
        else:
            receipt = write_publication_receipt(
                path, experiment=experiment, experiment_id=str(target.experiment_id),
                run_id=run_id, owner=owner, bundle_sha256=digest,
                plan_id=tags.get("model_lab_plan_id", ""),
            )
        write_snapshot(path)
        return PublishExecutionResult(
            "verified", "existing_run_verified", model_id, version, experiment,
            owner, digest, run_id=run_id, remote_writes=False,
            receipt_path=str(receipt.relative_to(workspace)).replace("\\", "/"),
        )
    except Exception as error:
        reason = error.reason if isinstance(error, PublishBlocked) else "verification_error"
        return PublishExecutionResult(
            "blocked", reason, model_id, version, experiment, owner,
            digest, run_id=run_id, phase=phase, error_type=type(error).__name__,
            remote_writes=False,
        )
