from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from model_lab.bundle_build import BundleBuildError, _tree_digest
from model_lab.manifest import (
    ArtifactReference,
    ContractField,
    EnvironmentRequirements,
    LifecycleState,
    ManifestError,
    RuntimeRequirements,
    ValidationReport,
    load_validated_asset,
)
from model_lab.runtime_contract import BundleCheckReport, verify_mlflow_bundle


_DRY_RUN_LIFECYCLE_STATES = (
    LifecycleState.VALIDATED,
    LifecycleState.RELEASABLE,
    LifecycleState.RELEASED,
)
_PLACEHOLDER_SHA256 = "0" * 64


def _platform_inputs_from_mlmodel(bundle: Path) -> tuple[dict[str, object], ...]:
    """Read only the declared MLflow request fields, without loading the model."""
    document = yaml.safe_load((bundle / "MLmodel").read_text(encoding="utf-8"))
    signature = document.get("signature") if isinstance(document, Mapping) else None
    raw_inputs = signature.get("inputs") if isinstance(signature, Mapping) else None
    try:
        inputs = json.loads(raw_inputs) if isinstance(raw_inputs, str) else raw_inputs
    except (TypeError, ValueError):
        return ()
    if not isinstance(inputs, list):
        return ()
    fields: list[dict[str, object]] = []
    for declaration in inputs:
        if not isinstance(declaration, Mapping):
            return ()
        name = declaration.get("name")
        field_type = declaration.get("type")
        if not isinstance(name, str) or not isinstance(field_type, str):
            return ()
        field: dict[str, object] = {
            "name": name,
            "type": field_type,
            "required": declaration.get("required", True) is True,
        }
        if field_type == "array":
            items = declaration.get("items")
            if isinstance(items, Mapping) and isinstance(items.get("type"), str):
                field["items"] = {"type": items["type"]}
        fields.append(field)
    return tuple(fields)


class ContractViolationError(ValueError):
    def __init__(self, report: ValidationReport) -> None:
        super().__init__("manifest violates the asset contract")
        self.report = report


def _safety_status() -> dict[str, object]:
    return {
        "credentials_read": False,
        "proxy_configuration": "unchanged",
        "remote_client_initialized": False,
        "remote_writes": False,
        "tls_verification": "unchanged",
    }


@dataclass(frozen=True)
class PlanStep:
    sequence: int
    action: str
    effect: str

    def as_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "action": self.action,
            "effect": self.effect,
            "execution": "planned-only",
        }


@dataclass(frozen=True)
class TargetAdapter:
    kind: str
    actions: tuple[tuple[str, str], ...]

    def plan_steps(self) -> tuple[PlanStep, ...]:
        return tuple(
            PlanStep(sequence=index, action=action, effect=effect)
            for index, (action, effect) in enumerate(self.actions, start=1)
        )


_TARGET_ADAPTERS = {
    "cpu-iot": TargetAdapter(
        kind="cpu-iot",
        actions=(
            ("validate-contract", "local-read"),
            ("resolve-artifacts", "local-read"),
            ("build-release-bundle", "local-write"),
            ("upload-model", "remote-write"),
            ("load-model", "remote-write"),
            ("verify-inference", "remote-read"),
        ),
    ),
    "gpu": TargetAdapter(
        kind="gpu",
        actions=(
            ("validate-contract", "local-read"),
            ("validate-gpu-runtime", "local-read"),
            ("resolve-artifacts", "local-read"),
            ("verify-artifact-compatibility", "local-read"),
            ("build-release-bundle", "local-write"),
            ("upload-model", "remote-write"),
            ("load-model", "remote-write"),
            ("verify-inference", "remote-read"),
        ),
    ),
    "chronos": TargetAdapter(
        kind="chronos",
        actions=(
            ("validate-contract", "local-read"),
            ("validate-gpu-runtime", "local-read"),
            ("validate-chronos-contract", "local-read"),
            ("resolve-artifacts", "local-read"),
            ("verify-artifact-compatibility", "local-read"),
            ("build-release-bundle", "local-write"),
            ("upload-model", "remote-write"),
            ("load-model", "remote-write"),
            ("verify-inference", "remote-read"),
        ),
    ),
}


@dataclass(frozen=True)
class DryRunPlan:
    plan_id: str
    model_id: str
    model_version: str
    lifecycle_state: str
    target: str
    adapter: str
    inputs: tuple[ContractField, ...]
    outputs: tuple[ContractField, ...]
    artifacts: tuple[ArtifactReference, ...]
    runtime_requirements: RuntimeRequirements | None
    steps: tuple[PlanStep, ...]
    environment_requirements: EnvironmentRequirements | None = None
    bundle_verification: BundleCheckReport | None = None
    platform_inputs: tuple[dict[str, object], ...] = ()

    def requirements_dict(self) -> dict[str, object]:
        if self.runtime_requirements is None:
            return {"accelerator": "cpu"}
        return self.runtime_requirements.as_dict()

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "mode": "dry-run",
            "plan_id": self.plan_id,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "lifecycle_state": self.lifecycle_state,
            "target": self.target,
            "adapter": self.adapter,
            "inputs": [field.as_dict() for field in self.inputs],
            "outputs": [field.as_dict() for field in self.outputs],
            "artifacts": [artifact.as_dict() for artifact in self.artifacts],
            "requirements": self.requirements_dict(),
            "steps": [step.as_dict() for step in self.steps],
            "safety": _safety_status(),
        }
        if self.environment_requirements is not None:
            payload["target_environment"] = self.environment_requirements.as_dict()
        if self.bundle_verification is not None:
            payload["bundle_verification"] = self.bundle_verification.as_dict()
        if self.platform_inputs:
            payload["platform_inputs"] = [dict(field) for field in self.platform_inputs]
            payload["input_roles"] = {
                "inputs": "algorithm-features-after-serving",
                "platform_inputs": "mlflow-request-signature",
            }
        return payload

    def as_text(self) -> str:
        lines = [
            f"{self.model_id}: DRY-RUN",
            f"Plan: {self.plan_id}",
            f"Model version: {self.model_version}",
            f"Lifecycle state: {self.lifecycle_state}",
            f"Target adapter: {self.adapter}",
            f"Algorithm inputs: {', '.join(field.as_text() for field in self.inputs)}",
            f"Outputs: {', '.join(field.as_text() for field in self.outputs)}",
            (
                "Requirements: "
                + json.dumps(
                    self.requirements_dict(), ensure_ascii=False, sort_keys=True
                )
            ),
            "Safety: no credentials, remote clients, or remote writes",
        ]
        lines.extend(
            f"Artifact: {artifact.name} [{artifact.kind}] {artifact.public_uri} sha256={artifact.sha256}"
            for artifact in self.artifacts
        )
        lines.extend(
            f"{step.sequence}. {step.action} [{step.effect}; planned-only]"
            for step in self.steps
        )
        if self.environment_requirements is not None:
            lines.append(
                "Target environment: "
                + json.dumps(
                    self.environment_requirements.as_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        if self.bundle_verification is not None:
            lines.append(
                "Bundle verification: "
                + ("passed" if self.bundle_verification.valid else "blocked")
            )
        if self.platform_inputs:
            lines.append("Platform inputs (MLflow request signature):")
            for field in self.platform_inputs:
                kind = str(field["type"])
                items = field.get("items")
                if kind == "array" and isinstance(items, Mapping):
                    kind = f"Array({items.get('type', 'unknown')})"
                requirement = "required" if field["required"] else "optional"
                lines.append(f"- {field['name']}: {kind} ({requirement})")
        return "\n".join(lines)


@dataclass(frozen=True)
class PublishRequest:
    target: str | None
    experiment: str | None
    owner: str | None
    authorized: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "target": self.target,
            "experiment": self.experiment,
            "owner": self.owner,
            "publish_authorized": self.authorized,
        }


@dataclass(frozen=True)
class PublishGateResult:
    status: str
    reason: str
    message: str
    model_id: str | None
    target: str | None
    request: PublishRequest

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "message": self.message,
            "model_id": self.model_id,
            "target": self.target,
            "request": self.request.as_dict(),
            "safety": _safety_status(),
        }

    def as_text(self) -> str:
        model = self.model_id or "<not-inspected>"
        target = self.target or "<missing>"
        return "\n".join(
            (
                f"{model}: PUBLISH {self.status.upper()}",
                f"Reason: {self.reason}",
                f"Message: {self.message}",
                f"Target: {target}",
                "Safety: no credentials, remote clients, or remote writes",
            )
        )


def build_dry_run_plan(path: str | Path) -> DryRunPlan:
    validation, asset = load_validated_asset(path)
    if asset is None:
        raise ContractViolationError(validation)

    summary = asset.summary
    if summary.lifecycle_state not in _DRY_RUN_LIFECYCLE_STATES:
        allowed = ", ".join(
            state.value for state in _DRY_RUN_LIFECYCLE_STATES
        )
        raise ManifestError(
            "dry_run_lifecycle_not_eligible",
            (
                "dry-run requires lifecycle state "
                f"{allowed}; got {summary.lifecycle_state.value}"
            ),
        )
    if any(
        artifact.sha256 == _PLACEHOLDER_SHA256
        for artifact in asset.artifacts
    ):
        raise ManifestError(
            "dry_run_placeholder_artifact_digest",
            "dry-run requires a non-placeholder SHA-256 digest for every artifact",
        )
    if asset.pending_archive_paths:
        unresolved = ", ".join(asset.pending_archive_paths)
        raise ManifestError(
            "archive_incomplete",
            f"dry-run requires resolved archive records: {unresolved}",
        )

    adapter = _TARGET_ADAPTERS.get(summary.target)
    if adapter is None:
        raise ManifestError(
            "dry_run_target_not_enabled",
            f"dry-run adapter is not enabled for target: {summary.target}",
        )

    bundle_verification = None
    platform_inputs: tuple[dict[str, object], ...] = ()
    if asset.environment_requirements is not None:
        manifest_path = Path(path)
        bundle_path = manifest_path.parent / "release-bundle" / "service"
        bundle_verification = verify_mlflow_bundle(
            manifest_path,
            bundle_path,
        )
        if not bundle_verification.valid:
            first = bundle_verification.issues[0]
            raise ManifestError(first.code, first.message)
        local_bundles = tuple(
            artifact for artifact in asset.artifacts
            if artifact.kind == "mlflow-pyfunc" and artifact.uri.startswith("artifact://local/")
        )
        if local_bundles:
            try:
                actual_digest = _tree_digest(bundle_path)
            except (BundleBuildError, OSError) as error:
                raise ManifestError("unsafe_mlflow_bundle", str(error)) from error
            if any(artifact.sha256 != actual_digest for artifact in local_bundles):
                raise ManifestError(
                    "bundle_artifact_digest_mismatch",
                    "recorded MLflow service digest differs from the current release-bundle/service",
                )
        platform_inputs = _platform_inputs_from_mlmodel(bundle_path)

    steps = adapter.plan_steps()
    bundle_plan_evidence = bundle_verification.as_dict() if bundle_verification is not None else None
    if bundle_plan_evidence is not None:
        # The absolute checkout path is diagnostic, not part of release identity.
        bundle_plan_evidence.pop("bundle_path", None)
    plan_payload = {
        "mode": "dry-run",
        "model_id": summary.model_id,
        "model_version": summary.model_version,
        "lifecycle_state": summary.lifecycle_state.value,
        "target": summary.target,
        "adapter": adapter.kind,
        "inputs": [field.as_dict() for field in summary.inputs],
        "outputs": [field.as_dict() for field in summary.outputs],
        "artifacts": [artifact.as_dict() for artifact in asset.artifacts],
        "requirements": (
            asset.runtime_requirements.as_dict()
            if asset.runtime_requirements is not None
            else {"accelerator": "cpu"}
        ),
        "target_environment": (
            asset.environment_requirements.as_dict()
            if asset.environment_requirements is not None
            else None
        ),
        "bundle_verification": bundle_plan_evidence,
        "platform_inputs": platform_inputs,
        "steps": [step.as_dict() for step in steps],
    }
    plan_seed = json.dumps(
        plan_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    plan_id = hashlib.sha256(plan_seed).hexdigest()
    return DryRunPlan(
        plan_id=plan_id,
        model_id=summary.model_id,
        model_version=summary.model_version,
        lifecycle_state=summary.lifecycle_state.value,
        target=summary.target,
        adapter=adapter.kind,
        inputs=summary.inputs,
        outputs=summary.outputs,
        artifacts=asset.artifacts,
        runtime_requirements=asset.runtime_requirements,
        steps=steps,
        environment_requirements=asset.environment_requirements,
        bundle_verification=bundle_verification,
        platform_inputs=platform_inputs,
    )


def evaluate_publish_gate(
    path: str | Path,
    *,
    target: str | None,
    experiment: str | None,
    owner: str | None,
    authorized: bool,
) -> PublishGateResult:
    request = PublishRequest(
        target=target,
        experiment=experiment,
        owner=owner,
        authorized=authorized,
    )
    required_values = (
        ("experiment", experiment),
        ("owner", owner),
    )
    for field, value in required_values:
        if value is None or not value.strip():
            return PublishGateResult(
                status="denied",
                reason=f"missing_{field}",
                message=f"publish requires an explicit {field}",
                model_id=None,
                target=target,
                request=request,
            )
    if not authorized:
        return PublishGateResult(
            status="denied",
            reason="missing_publish_authorization",
            message="publish requires --authorize-publish",
            model_id=None,
            target=target,
            request=request,
        )

    validation, asset = load_validated_asset(path)
    if asset is None:
        return PublishGateResult(
            status="denied",
            reason="manifest_contract_invalid",
            message="manifest violates the asset contract",
            model_id=validation.model_id,
            target=target,
            request=request,
        )
    summary = asset.summary
    resolved_target = summary.target if target is None else target
    resolved_request = PublishRequest(
        target=resolved_target,
        experiment=experiment,
        owner=owner,
        authorized=authorized,
    )
    if asset.pending_archive_paths:
        return PublishGateResult(
            status="denied",
            reason="archive_incomplete",
            message="publish requires all archive records to be resolved",
            model_id=summary.model_id,
            target=resolved_target,
            request=resolved_request,
        )
    if resolved_target != summary.target:
        return PublishGateResult(
            status="denied",
            reason="target_mismatch",
            message=f"requested target {resolved_target!r} does not match {summary.target!r}",
            model_id=summary.model_id,
            target=resolved_target,
            request=resolved_request,
        )
    return PublishGateResult(
        status="ready",
        reason="authorization_complete",
        message="发布授权与目标已齐；远端上传须显式 --apply，并先通过实包与环境检查",
        model_id=summary.model_id,
        target=resolved_target,
        request=resolved_request,
    )
