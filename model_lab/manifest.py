from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import ipaddress
import math
import re
from pathlib import Path, PurePosixPath
import sys
from typing import Any
import unicodedata
from urllib.parse import SplitResult, unquote, urlsplit, urlunsplit

import yaml  # type: ignore[import-untyped]
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

from model_lab import __version__


class DuplicateKeyError(yaml.YAMLError):
    """YAML 映射包含会造成事实歧义的重复键。"""

    def __init__(self, key: object, line: int) -> None:
        super().__init__(f"duplicate YAML key {key!r} at line {line}")


class _UniqueKeySafeLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.Node, deep: bool = False) -> dict[Any, Any]:
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as error:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable key",
                    key_node.start_mark,
                ) from error
            if duplicate:
                raise DuplicateKeyError(key, key_node.start_mark.line + 1)
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


class ManifestError(ValueError):
    """模型资产清单无法用于当前操作。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class LifecycleState(StrEnum):
    DRAFT = "draft"
    SPECIFIED = "specified"
    BUILT = "built"
    VALIDATED = "validated"
    RELEASABLE = "releasable"
    RELEASED = "released"
    RETIRED = "retired"

    @classmethod
    def parse(cls, value: object) -> "LifecycleState":
        try:
            return cls(str(value))
        except ValueError as error:
            allowed = ", ".join(state.value for state in cls)
            raise ManifestError(
                "manifest_invalid_lifecycle_state",
                f"unsupported lifecycle state: {value}; expected one of: {allowed}",
            ) from error


@dataclass(frozen=True)
class ContractField:
    name: str
    field_type: str
    unit: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "type": self.field_type, "unit": self.unit}

    def as_text(self) -> str:
        return f"{self.name} ({self.field_type}, {self.unit})"


@dataclass(frozen=True)
class AssetSummary:
    model_id: str
    name: str
    lifecycle_state: LifecycleState
    model_version: str
    target: str
    inputs: tuple[ContractField, ...]
    outputs: tuple[ContractField, ...]
    maintenance_status: str | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "model_id": self.model_id,
            "name": self.name,
            "lifecycle_state": self.lifecycle_state.value,
            "model_version": self.model_version,
            "target": self.target,
            "inputs": [field.as_dict() for field in self.inputs],
            "outputs": [field.as_dict() for field in self.outputs],
        }
        if self.maintenance_status is not None:
            payload["maintenance_status"] = self.maintenance_status
        return payload

    def as_text(self) -> str:
        lines = [
            f"{self.model_id} — {self.name}",
            f"Lifecycle state: {self.lifecycle_state.value}",
        ]
        if self.maintenance_status is not None:
            lines.append(f"Maintenance status: {self.maintenance_status}")
        lines.extend(
            (
                f"Model version: {self.model_version}",
                f"Target: {self.target}",
                f"Inputs: {', '.join(field.as_text() for field in self.inputs)}",
                f"Outputs: {', '.join(field.as_text() for field in self.outputs)}",
            )
        )
        return "\n".join(lines)


@dataclass(frozen=True)
class ArtifactReference:
    name: str
    kind: str
    uri: str
    sha256: str
    archive_status: str | None = None

    @property
    def public_uri(self) -> str:
        return _redact_artifact_uri(self.uri)

    def as_dict(self) -> dict[str, str]:
        payload = {
            "name": self.name,
            "kind": self.kind,
            "uri": self.public_uri,
            "sha256": self.sha256,
        }
        if self.archive_status is not None:
            payload["archive_status"] = self.archive_status
        return payload


@dataclass(frozen=True)
class ValidatedAsset:
    summary: AssetSummary
    artifacts: tuple[ArtifactReference, ...]
    runtime_requirements: RuntimeRequirements | None
    environment_requirements: EnvironmentRequirements | None
    pending_archive_paths: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeRequirements:
    accelerator: str
    gpu_vendor: str
    gpu_compute_capability: str
    gpu_memory_gb_min: int | float
    cuda_version: str
    framework_name: str
    framework_version: str
    artifact_format: str

    def as_dict(self) -> dict[str, object]:
        return {
            "accelerator": self.accelerator,
            "gpu": {
                "vendor": self.gpu_vendor,
                "compute_capability": self.gpu_compute_capability,
                "memory_gb_min": self.gpu_memory_gb_min,
            },
            "cuda": {"version": self.cuda_version},
            "framework": {
                "name": self.framework_name,
                "version": self.framework_version,
            },
            "artifact": {"format": self.artifact_format},
        }


@dataclass(frozen=True)
class NativeRuntimeRequirements:
    kind: str
    python_abi: str | None = None
    platform: str | None = None
    artifact: str | None = None

    def as_dict(self) -> dict[str, str]:
        payload = {"kind": self.kind}
        if self.python_abi is not None:
            payload["python_abi"] = self.python_abi
        if self.platform is not None:
            payload["platform"] = self.platform
        if self.artifact is not None:
            payload["artifact"] = self.artifact
        return payload


@dataclass(frozen=True)
class EnvironmentRequirements:
    operating_system: str
    architecture: str
    python: str
    mlflow: str
    package_format: str
    dependency_lock: str
    native_runtime: NativeRuntimeRequirements

    def as_dict(self) -> dict[str, object]:
        return {
            "operating_system": self.operating_system,
            "architecture": self.architecture,
            "python": self.python,
            "mlflow": self.mlflow,
            "package_format": self.package_format,
            "dependency_lock": self.dependency_lock,
            "native_runtime": self.native_runtime.as_dict(),
        }


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str
    suggestion: str

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "suggestion": self.suggestion,
        }

    def as_text(self) -> str:
        return (
            f"- [{self.code}] {self.path}: {self.message} "
            f"Fix: {self.suggestion}"
        )


@dataclass(frozen=True)
class ValidationCheck:
    name: str
    status: str
    details: tuple[tuple[str, object], ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status,
            **dict(self.details),
        }


@dataclass(frozen=True)
class ValidationReport:
    model_id: str
    lifecycle_state: LifecycleState | None
    issues: tuple[ValidationIssue, ...] = ()
    checks: tuple[ValidationCheck, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.issues

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "valid": self.valid,
            "model_id": self.model_id,
            "lifecycle_state": (
                self.lifecycle_state.value if self.lifecycle_state is not None else None
            ),
            "issues": [issue.as_dict() for issue in self.issues],
        }
        if self.checks:
            payload["checks"] = [check.as_dict() for check in self.checks]
        return payload

    def as_text(self) -> str:
        lines = [
            f"{self.model_id}: {'VALID' if self.valid else 'INVALID'}",
            (
                f"Lifecycle state: {self.lifecycle_state.value}"
                if self.lifecycle_state is not None
                else "Lifecycle state: unavailable"
            ),
            f"Contract issues: {len(self.issues)}",
        ]
        lines.extend(
            f"Check: {check.name} [{check.status}]" for check in self.checks
        )
        lines.extend(issue.as_text() for issue in self.issues)
        return "\n".join(lines)


def _contract_fields(fields: list[dict[str, Any]]) -> tuple[ContractField, ...]:
    return tuple(
        ContractField(
            name=str(field["name"]),
            field_type=str(field["type"]),
            unit=str(field["unit"]),
        )
        for field in fields
    )


def _load_manifest(path: str | Path) -> Any:
    manifest_path = Path(path)
    try:
        return yaml.load(
            manifest_path.read_text(encoding="utf-8"),
            Loader=_UniqueKeySafeLoader,
        )
    except FileNotFoundError as error:
        raise ManifestError(
            "manifest_not_found", f"manifest not found: {manifest_path}"
        ) from error
    except OSError as error:
        detail = error.strerror or error.__class__.__name__
        raise ManifestError(
            "manifest_unreadable",
            f"manifest cannot be read: {manifest_path} ({detail})",
        ) from error
    except UnicodeDecodeError as error:
        raise ManifestError(
            "manifest_invalid_encoding",
            f"manifest is not UTF-8: {manifest_path}",
        ) from error
    except DuplicateKeyError as error:
        raise ManifestError(
            "manifest_duplicate_key",
            f"{error}: {manifest_path}",
        ) from error
    except RecursionError as error:
        raise ManifestError(
            "manifest_invalid_yaml",
            f"manifest nesting is too deep: {manifest_path}",
        ) from error
    except yaml.YAMLError as error:
        raise ManifestError(
            "manifest_invalid_yaml", f"manifest is not valid YAML: {manifest_path}"
        ) from error


def _inspect_document(document: Any, manifest_path: Path) -> AssetSummary:
    try:
        model = document["model"]
        contract = document["contract"]
        return AssetSummary(
            model_id=str(model["id"]),
            name=str(model["name"]),
            lifecycle_state=LifecycleState.parse(model["lifecycle_state"]),
            model_version=str(model["version"]),
            target=str(document["target"]["kind"]),
            inputs=_contract_fields(contract["inputs"]),
            outputs=_contract_fields(contract["outputs"]),
            maintenance_status=(
                str(model["maintenance_status"])
                if "maintenance_status" in model
                else None
            ),
        )
    except (KeyError, TypeError) as error:
        raise ManifestError(
            "manifest_invalid_contract",
            f"manifest has no inspectable asset contract: {manifest_path}",
        ) from error


def inspect_manifest(path: str | Path) -> AssetSummary:
    manifest_path = Path(path)
    return _inspect_document(_load_manifest(manifest_path), manifest_path)


_REQUIRED_ROOT_FIELDS = frozenset(
    {"schema_version", "compatibility", "model", "target", "contract", "artifacts"}
)
_OPTIONAL_ROOT_FIELDS = frozenset(
    {"source", "platform", "baseline", "release", "migration", "serving"}
)
_ARCHIVED_REQUIRED_ROOT_FIELDS = frozenset(
    {"source", "platform", "release", "migration"}
)
_ARCHIVED_LIFECYCLE_STATES = frozenset({"released", "retired"})
_ROOT_FIELDS = _REQUIRED_ROOT_FIELDS | _OPTIONAL_ROOT_FIELDS
_SECTION_FIELDS = {
    "compatibility": frozenset({"model_lab"}),
    "model": frozenset(
        {"id", "name", "version", "lifecycle_state", "maintenance_status", "owner"}
    ),
    "target": frozenset({"kind", "runtime", "environment"}),
    "contract": frozenset({"inputs", "outputs"}),
    "source": frozenset(
        {
            "digest_mode",
            "entrypoint",
            "entrypoint_sha256",
            "notebook",
            "notebook_sha256",
            "documentation",
            "documentation_sha256",
        }
    ),
    "platform": frozenset(
        {"provider", "experiment", "run_id", "deployment_status"}
    ),
    "baseline": frozenset(
        {
            "digest_mode",
            "kind",
            "input",
            "input_sha256",
            "expected",
            "expected_sha256",
        }
    ),
    "release": frozenset({"current", "history"}),
    "migration": frozenset(
        {"legacy_entrypoint", "strategy", "behavior_change"}
    ),
}
_OPTIONAL_SECTION_FIELDS = {
    "model": frozenset({"maintenance_status"}),
    "target": frozenset({"runtime", "environment"}),
    "release": frozenset({"history"}),
}
_NESTED_OBJECT_FIELDS = {"target": frozenset({"runtime", "environment"})}
_NESTED_ARRAY_FIELDS = {"release": frozenset({"history"})}
_CONTRACT_FIELD_FIELDS = frozenset({"name", "type", "unit"})
_ARTIFACT_FIELDS = frozenset(
    {"name", "kind", "uri", "sha256", "archive_status"}
)
_OPTIONAL_ARTIFACT_FIELDS = frozenset({"archive_status"})
_ARTIFACT_ARCHIVE_STATUSES = frozenset({"pending-archive", "resolved"})
_RUNTIME_FIELDS = frozenset({"accelerator", "gpu", "cuda", "framework", "artifact"})
_RUNTIME_OBJECT_FIELDS = {
    "gpu": frozenset({"vendor", "compute_capability", "memory_gb_min"}),
    "cuda": frozenset({"version"}),
    "framework": frozenset({"name", "version"}),
    "artifact": frozenset({"format"}),
}
_ENVIRONMENT_FIELDS = frozenset(
    {
        "operating_system",
        "architecture",
        "python",
        "mlflow",
        "package_format",
        "dependency_lock",
        "native_runtime",
    }
)
_NATIVE_RUNTIME_FIELDS = frozenset(
    {"kind", "python_abi", "platform", "artifact"}
)
_SEMANTIC_VERSION = re.compile(
    r"^(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_ILLEGAL_URI_CHARACTER = re.compile(r'[\x00-\x20\x7f<>"{}|\\^`]')
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_MLFLOW_EXPERIMENT = re.compile(r"MLModel-[0-9]+_V[0-9]{3}")
_MLFLOW_RUN_ID = re.compile(r"[0-9a-f]{32}")
_PLATFORM_DEPLOYMENT_STATUSES = frozenset({"finished", "pending-archive"})
_PLACEHOLDER_ARTIFACT_SEGMENTS = frozenset(
    {"pending", "pending-archive", "tbd", "todo", "unknown"}
)


def _validate_artifact_host(host: str) -> None:
    if not host.isascii() or len(host) > 253:
        raise ValueError("artifact host must be an ASCII host name or IP address")
    if ":" in host or re.fullmatch(r"[0-9.]+", host):
        ipaddress.ip_address(host)
        return
    labels = host.split(".")
    if any(_DNS_LABEL.fullmatch(label) is None for label in labels):
        raise ValueError("artifact host contains an invalid DNS label")


def _artifact_path_segments(path: str) -> tuple[str, ...]:
    raw_segments = path.split("/")
    if not raw_segments or raw_segments[0] != "":
        raise ValueError("artifact URI requires an absolute path")

    segments: list[str] = []
    for raw_segment in raw_segments[1:]:
        if not raw_segment:
            raise ValueError("artifact URI path contains an empty segment")
        try:
            segment = unquote(raw_segment, encoding="utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("artifact URI path is not valid UTF-8") from error
        if (
            segment in {".", ".."}
            or "/" in segment
            or "\\" in segment
            or "%" in segment
            or segment.casefold() in _PLACEHOLDER_ARTIFACT_SEGMENTS
            or segment.split(".", maxsplit=1)[0].casefold()
            in _PLACEHOLDER_ARTIFACT_SEGMENTS
            or _ILLEGAL_URI_CHARACTER.search(segment)
        ):
            raise ValueError("artifact URI path contains an unsafe segment")
        segments.append(segment)
    if not segments:
        raise ValueError("artifact URI requires a non-empty path")
    return tuple(segments)


def _parse_artifact_uri(uri: str) -> SplitResult:
    if _ILLEGAL_URI_CHARACTER.search(uri) or _INVALID_PERCENT_ESCAPE.search(uri):
        raise ValueError("artifact URI contains illegal characters")
    parsed = urlsplit(uri)
    if not parsed.scheme or not parsed.path:
        raise ValueError("artifact URI requires a scheme and path")
    if parsed.scheme.casefold() != "file":
        if not parsed.hostname:
            raise ValueError("network artifact URI requires a host")
        _validate_artifact_host(parsed.hostname)
        if not parsed.path.startswith("/"):
            raise ValueError("network artifact URI requires an absolute path")
    elif parsed.hostname:
        _validate_artifact_host(parsed.hostname)
    _ = parsed.port
    _artifact_path_segments(parsed.path)
    return parsed


def _is_pending_artifact_uri(uri: str) -> bool:
    try:
        parsed = _parse_artifact_uri(uri)
    except ValueError:
        return False
    return (
        parsed.scheme.casefold() == "artifact"
        and parsed.hostname is not None
        and parsed.hostname.casefold() == "pending-archive"
    )


def _redact_artifact_uri(uri: str) -> str:
    try:
        parsed = _parse_artifact_uri(uri)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return "<invalid-artifact-uri>"
    return urlunsplit(
        (
            parsed.scheme,
            f"{host}{port}",
            parsed.path,
            "",
            "",
        )
    )


def _contains_unicode_surrogate(value: str) -> bool:
    return any(unicodedata.category(character) == "Cs" for character in value)


def _unknown_fields(
    value: object,
    allowed: frozenset[str],
    path: str = "",
) -> list[ValidationIssue]:
    if not isinstance(value, Mapping):
        return []
    issues: list[ValidationIssue] = []
    unknown = sorted(
        set(value) - allowed,
        key=lambda field: (type(field).__name__, repr(field)),
    )
    for field in unknown:
        if not isinstance(field, str):
            field_path = f"{path}[{field!r}]" if path else f"$[{field!r}]"
            issues.append(
                ValidationIssue(
                    code="invalid_field_name",
                    path=field_path,
                    message="mapping field names must be strings",
                    suggestion=f"replace {field_path} with a string field name",
                )
            )
            continue
        if _contains_unicode_surrogate(field):
            field_path = (
                f"{path}.<invalid-unicode-field>"
                if path
                else "$.<invalid-unicode-field>"
            )
            issues.append(
                ValidationIssue(
                    code="invalid_field_name",
                    path=field_path,
                    message="mapping field names must be valid Unicode text",
                    suggestion="replace the invalid mapping field name",
                )
            )
            continue
        field_path = f"{path}.{field}" if path else field
        issues.append(
            ValidationIssue(
                code="unknown_field",
                path=field_path,
                message="field is not defined by schema version 1.0",
                suggestion=f"remove unknown field {field_path}",
            )
        )
    return issues


def _unknown_field_issues(document: object) -> list[ValidationIssue]:
    if not isinstance(document, Mapping):
        return []

    issues = _unknown_fields(document, _ROOT_FIELDS)
    for section, allowed in _SECTION_FIELDS.items():
        issues.extend(_unknown_fields(document.get(section), allowed, section))

    target = document.get("target")
    runtime = target.get("runtime") if isinstance(target, Mapping) else None
    if isinstance(runtime, Mapping):
        issues.extend(
            _unknown_fields(runtime, _RUNTIME_FIELDS, "target.runtime")
        )
        for section, allowed in _RUNTIME_OBJECT_FIELDS.items():
            issues.extend(
                _unknown_fields(
                    runtime.get(section),
                    allowed,
                    f"target.runtime.{section}",
                )
            )

    environment = target.get("environment") if isinstance(target, Mapping) else None
    if isinstance(environment, Mapping):
        issues.extend(
            _unknown_fields(
                environment,
                _ENVIRONMENT_FIELDS,
                "target.environment",
            )
        )
        issues.extend(
            _unknown_fields(
                environment.get("native_runtime"),
                _NATIVE_RUNTIME_FIELDS,
                "target.environment.native_runtime",
            )
        )

    contract = document.get("contract")
    if isinstance(contract, Mapping):
        for direction in sorted(_SECTION_FIELDS["contract"]):
            fields = contract.get(direction)
            if isinstance(fields, list):
                for index, field in enumerate(fields):
                    issues.extend(
                        _unknown_fields(
                            field,
                            _CONTRACT_FIELD_FIELDS,
                            f"contract.{direction}[{index}]",
                        )
                    )

    artifacts = document.get("artifacts")
    if isinstance(artifacts, list):
        for index, artifact in enumerate(artifacts):
            issues.extend(
                _unknown_fields(
                    artifact,
                    _ARTIFACT_FIELDS,
                    f"artifacts[{index}]",
                )
            )
    return issues


def _missing_fields(
    value: object,
    required: frozenset[str],
    path: str = "",
) -> list[ValidationIssue]:
    if not isinstance(value, Mapping):
        return []
    issues: list[ValidationIssue] = []
    for field in sorted(required - set(value)):
        field_path = f"{path}.{field}" if path else field
        issues.append(
            ValidationIssue(
                code="missing_required_field",
                path=field_path,
                message="required field is missing",
                suggestion=f"add {field_path}",
            )
        )
    return issues


def _missing_field_issues(document: object) -> list[ValidationIssue]:
    if not isinstance(document, Mapping):
        return []

    issues = _missing_fields(document, _REQUIRED_ROOT_FIELDS)
    model = document.get("model")
    if (
        isinstance(model, Mapping)
        and model.get("lifecycle_state") in _ARCHIVED_LIFECYCLE_STATES
    ):
        issues.extend(_missing_fields(document, _ARCHIVED_REQUIRED_ROOT_FIELDS))
        issues.extend(
            _missing_fields(
                model,
                frozenset({"maintenance_status"}),
                "model",
            )
        )
    for section, allowed in _SECTION_FIELDS.items():
        if section in document:
            required = allowed - _OPTIONAL_SECTION_FIELDS.get(section, frozenset())
            issues.extend(_missing_fields(document[section], required, section))

    target = document.get("target")
    if isinstance(target, Mapping):
        target_kind = target.get("kind")
        if isinstance(target_kind, str) and target_kind in {"gpu", "chronos"}:
            issues.extend(_missing_fields(target, frozenset({"runtime"}), "target"))
        runtime = target.get("runtime")
        if isinstance(runtime, Mapping):
            issues.extend(
                _missing_fields(runtime, _RUNTIME_FIELDS, "target.runtime")
            )
            for section, required in _RUNTIME_OBJECT_FIELDS.items():
                if section in runtime:
                    issues.extend(
                        _missing_fields(
                            runtime[section],
                            required,
                            f"target.runtime.{section}",
                        )
                    )

        environment = target.get("environment")
        if isinstance(environment, Mapping):
            issues.extend(
                _missing_fields(
                    environment,
                    _ENVIRONMENT_FIELDS,
                    "target.environment",
                )
            )
            native_runtime = environment.get("native_runtime")
            if isinstance(native_runtime, Mapping):
                required_native = frozenset({"kind"})
                if native_runtime.get("kind") == "pyarmor":
                    required_native = _NATIVE_RUNTIME_FIELDS
                issues.extend(
                    _missing_fields(
                        native_runtime,
                        required_native,
                        "target.environment.native_runtime",
                    )
                )

    contract = document.get("contract")
    if isinstance(contract, Mapping):
        for direction in sorted(_SECTION_FIELDS["contract"]):
            fields = contract.get(direction)
            if isinstance(fields, list):
                for index, field in enumerate(fields):
                    issues.extend(
                        _missing_fields(
                            field,
                            _CONTRACT_FIELD_FIELDS,
                            f"contract.{direction}[{index}]",
                        )
                    )

    artifacts = document.get("artifacts")
    if isinstance(artifacts, list):
        model = document.get("model")
        archived = (
            isinstance(model, Mapping)
            and model.get("lifecycle_state") in _ARCHIVED_LIFECYCLE_STATES
        )
        required_artifact_fields = (
            _ARTIFACT_FIELDS
            if archived
            else _ARTIFACT_FIELDS - _OPTIONAL_ARTIFACT_FIELDS
        )
        for index, artifact in enumerate(artifacts):
            issues.extend(
                _missing_fields(
                    artifact,
                    required_artifact_fields,
                    f"artifacts[{index}]",
                )
            )
    return issues


def _string_issue(value: object, path: str) -> ValidationIssue | None:
    if isinstance(value, str) and value.strip():
        if not _contains_unicode_surrogate(value):
            return None
        return ValidationIssue(
            code="invalid_field_value",
            path=path,
            message="value must not contain Unicode surrogate code points",
            suggestion=f"replace {path} with valid Unicode text",
        )
    return ValidationIssue(
        code="invalid_field_value",
        path=path,
        message="value must be a non-empty string",
        suggestion=f"replace {path} with a non-empty string",
    )


def _scalar_issues(document: object) -> list[ValidationIssue]:
    if not isinstance(document, Mapping):
        return []

    issues: list[ValidationIssue] = []

    def check(mapping: object, field: str, path: str) -> None:
        if isinstance(mapping, Mapping) and field in mapping:
            issue = _string_issue(mapping[field], path)
            if issue is not None:
                issues.append(issue)

    check(document, "schema_version", "schema_version")
    for section, schema_fields in _SECTION_FIELDS.items():
        if section == "contract":
            continue
        section_value = document.get(section)
        scalar_fields = schema_fields - _NESTED_OBJECT_FIELDS.get(
            section, frozenset()
        ) - _NESTED_ARRAY_FIELDS.get(section, frozenset())
        for field in sorted(scalar_fields):
            check(section_value, field, f"{section}.{field}")

    release = document.get("release")
    if isinstance(release, Mapping):
        history = release.get("history")
        if isinstance(history, list):
            for index, reference in enumerate(history):
                issue = _string_issue(reference, f"release.history[{index}]")
                if issue is not None:
                    issues.append(issue)

    target = document.get("target")
    runtime = target.get("runtime") if isinstance(target, Mapping) else None
    if isinstance(runtime, Mapping):
        check(runtime, "accelerator", "target.runtime.accelerator")
        for section, schema_fields in _RUNTIME_OBJECT_FIELDS.items():
            section_value = runtime.get(section)
            for field in sorted(schema_fields - {"memory_gb_min"}):
                check(
                    section_value,
                    field,
                    f"target.runtime.{section}.{field}",
                )

    environment = target.get("environment") if isinstance(target, Mapping) else None
    if isinstance(environment, Mapping):
        for field in sorted(_ENVIRONMENT_FIELDS - {"native_runtime"}):
            check(environment, field, f"target.environment.{field}")
        native_runtime = environment.get("native_runtime")
        for field in sorted(_NATIVE_RUNTIME_FIELDS):
            check(
                native_runtime,
                field,
                f"target.environment.native_runtime.{field}",
            )

    contract = document.get("contract")
    if isinstance(contract, Mapping):
        for direction in sorted(_SECTION_FIELDS["contract"]):
            contract_fields = contract.get(direction)
            if not isinstance(contract_fields, list):
                continue
            for index, field_definition in enumerate(contract_fields):
                for field in sorted(_CONTRACT_FIELD_FIELDS):
                    check(
                        field_definition,
                        field,
                        f"contract.{direction}[{index}].{field}",
                    )

    artifacts = document.get("artifacts")
    if isinstance(artifacts, list):
        for index, artifact in enumerate(artifacts):
            for field in sorted(_ARTIFACT_FIELDS):
                check(artifact, field, f"artifacts[{index}].{field}")
    return issues


def _lifecycle_issues(document: object) -> list[ValidationIssue]:
    if not isinstance(document, Mapping):
        return []
    model = document.get("model")
    if not isinstance(model, Mapping) or "lifecycle_state" not in model:
        return []
    value = model["lifecycle_state"]
    if not isinstance(value, str):
        return []
    allowed = ", ".join(state.value for state in LifecycleState)
    if value not in {state.value for state in LifecycleState}:
        return [
            ValidationIssue(
                code="invalid_lifecycle_state",
                path="model.lifecycle_state",
                message=f"{value!r} is not a lifecycle state",
                suggestion=f"use one of: {allowed}",
            )
        ]
    return []


def _maintenance_status_issues(document: object) -> list[ValidationIssue]:
    if not isinstance(document, Mapping):
        return []
    model = document.get("model")
    if not isinstance(model, Mapping) or "maintenance_status" not in model:
        return []
    value = model["maintenance_status"]
    allowed = ("active", "limited", "unmaintained")
    if isinstance(value, str) and value not in allowed:
        return [
            ValidationIssue(
                code="invalid_maintenance_status",
                path="model.maintenance_status",
                message="model maintenance status is not supported",
                suggestion=f"use one of: {', '.join(allowed)}",
            )
        ]
    return []


def _contract_field_issues(document: object) -> list[ValidationIssue]:
    if not isinstance(document, Mapping):
        return []
    contract = document.get("contract")
    if not isinstance(contract, Mapping):
        return []

    allowed_types = ("number", "integer", "string", "boolean", "datetime")
    issues: list[ValidationIssue] = []
    for direction in sorted(_SECTION_FIELDS["contract"]):
        fields = contract.get(direction)
        if not isinstance(fields, list):
            continue
        seen_names: set[str] = set()
        for index, field in enumerate(fields):
            if not isinstance(field, Mapping):
                continue
            field_type = field.get("type")
            if isinstance(field_type, str) and field_type not in allowed_types:
                issues.append(
                    ValidationIssue(
                        code="invalid_contract_field_type",
                        path=f"contract.{direction}[{index}].type",
                        message=f"unsupported field type {field['type']!r}",
                        suggestion=f"use one of: {', '.join(allowed_types)}",
                    )
                )
            name = field.get("name")
            if isinstance(name, str) and name:
                if name in seen_names:
                    issues.append(
                        ValidationIssue(
                            code="duplicate_contract_field",
                            path=f"contract.{direction}[{index}].name",
                            message=f"field name {name!r} is duplicated",
                            suggestion=f"use a unique {direction} field name",
                        )
                    )
                seen_names.add(name)
    return issues


def _artifact_issues(document: object) -> list[ValidationIssue]:
    if not isinstance(document, Mapping):
        return []
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list):
        return []

    issues: list[ValidationIssue] = []
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, Mapping):
            continue
        uri = artifact.get("uri")
        if isinstance(uri, str):
            try:
                parsed_uri = _parse_artifact_uri(uri)
                contains_credentials = (
                    parsed_uri.username is not None
                    or parsed_uri.password is not None
                    or bool(parsed_uri.query)
                    or bool(parsed_uri.fragment)
                )
            except ValueError:
                parsed_uri = None
                contains_credentials = False
            uri_path = f"artifacts[{index}].uri"
            if parsed_uri is None:
                issues.append(
                    ValidationIssue(
                        code="invalid_artifact_uri",
                        path=uri_path,
                        message="artifact URI must include a valid scheme, host, and path",
                        suggestion="use a credential-free artifact URI with a valid location",
                    )
                )
            elif contains_credentials:
                issues.append(
                    ValidationIssue(
                        code="artifact_uri_contains_credentials",
                        path=uri_path,
                        message="artifact identity URI must not contain URI parameters",
                        suggestion=(
                            "remove userinfo, query parameters, and fragment data"
                        ),
                    )
                )

        digest = artifact.get("sha256")
        if (
            isinstance(digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            issues.append(
                ValidationIssue(
                    code="invalid_artifact_digest",
                    path=f"artifacts[{index}].sha256",
                    message="artifact digest is not a canonical SHA-256 value",
                    suggestion="use exactly 64 lowercase hexadecimal characters",
                )
            )

        archive_status = artifact.get("archive_status")
        if (
            isinstance(archive_status, str)
            and archive_status not in _ARTIFACT_ARCHIVE_STATUSES
        ):
            allowed = ", ".join(sorted(_ARTIFACT_ARCHIVE_STATUSES))
            issues.append(
                ValidationIssue(
                    code="invalid_artifact_archive_status",
                    path=f"artifacts[{index}].archive_status",
                    message="artifact archive status is not supported",
                    suggestion=f"use one of: {allowed}",
                )
            )
        if (
            isinstance(archive_status, str)
            and archive_status in _ARTIFACT_ARCHIVE_STATUSES
            and isinstance(uri, str)
            and (
                archive_status == "pending-archive"
            )
            != _is_pending_artifact_uri(uri)
        ):
            issues.append(
                ValidationIssue(
                    code="inconsistent_archive_status",
                    path=f"artifacts[{index}].archive_status",
                    message="artifact archive status does not match its URI",
                    suggestion=(
                        "pair resolved with an MLflow URI or pending-archive "
                        "with artifact://pending-archive/..."
                    ),
                )
            )
    return issues


def _archive_status_issues(document: object) -> list[ValidationIssue]:
    if not isinstance(document, Mapping):
        return []
    platform = document.get("platform")
    if not isinstance(platform, Mapping):
        return []

    issues: list[ValidationIssue] = []
    provider = platform.get("provider")
    if isinstance(provider, str) and provider != "mlflow":
        issues.append(
            ValidationIssue(
                code="invalid_platform_provider",
                path="platform.provider",
                message="platform provider is not supported",
                suggestion="use mlflow or extend the platform schema explicitly",
            )
        )

    experiment = platform.get("experiment")
    if (
        isinstance(experiment, str)
        and provider == "mlflow"
        and _MLFLOW_EXPERIMENT.fullmatch(experiment) is None
    ):
        issues.append(
            ValidationIssue(
                code="invalid_platform_experiment",
                path="platform.experiment",
                message="MLflow experiment is not a canonical model experiment ID",
                suggestion="use a versioned platform experiment name such as EXAMPLE_V001",
            )
        )

    run_id = platform.get("run_id")
    run_pending = run_id == "pending-archive"
    if (
        isinstance(run_id, str)
        and not run_pending
        and _MLFLOW_RUN_ID.fullmatch(run_id) is None
    ):
        issues.append(
            ValidationIssue(
                code="invalid_platform_run_id",
                path="platform.run_id",
                message="platform run ID must be a canonical MLflow run ID",
                suggestion=(
                    "use 32 lowercase hexadecimal characters or pending-archive"
                ),
            )
        )

    deployment_status = platform.get("deployment_status")
    if (
        isinstance(deployment_status, str)
        and deployment_status not in _PLATFORM_DEPLOYMENT_STATUSES
    ):
        allowed = ", ".join(sorted(_PLATFORM_DEPLOYMENT_STATUSES))
        issues.append(
            ValidationIssue(
                code="invalid_platform_deployment_status",
                path="platform.deployment_status",
                message="platform deployment status is not supported",
                suggestion=f"use one of: {allowed}",
            )
        )

    status_pending = deployment_status == "pending-archive"
    if run_pending != status_pending:
        issues.append(
            ValidationIssue(
                code="inconsistent_archive_status",
                path="platform",
                message=(
                    "platform.run_id and platform.deployment_status must both "
                    "use pending-archive until the remote record is resolved"
                ),
                suggestion=(
                    "set both fields to pending-archive or replace both with "
                    "the resolved platform record"
                ),
            )
        )

    artifacts = document.get("artifacts")
    if run_pending and isinstance(artifacts, list):
        for index, artifact in enumerate(artifacts):
            if not isinstance(artifact, Mapping):
                continue
            uri = artifact.get("uri")
            if isinstance(uri, str) and not _is_pending_artifact_uri(uri):
                issues.append(
                    ValidationIssue(
                        code="inconsistent_archive_status",
                        path=f"artifacts[{index}].uri",
                        message=(
                            "an unresolved platform record requires an explicit "
                            "pending-archive artifact location"
                        ),
                        suggestion=(
                            "use artifact://pending-archive/... until the remote "
                            "artifact location is known"
                        ),
                    )
                )
            archive_status = artifact.get("archive_status")
            if (
                isinstance(archive_status, str)
                and archive_status != "pending-archive"
            ):
                issues.append(
                    ValidationIssue(
                        code="inconsistent_archive_status",
                        path=f"artifacts[{index}].archive_status",
                        message=(
                            "an unresolved platform record requires pending "
                            "artifact archive status"
                        ),
                        suggestion="set archive_status to pending-archive",
                    )
                )
    elif (
        provider == "mlflow"
        and isinstance(experiment, str)
        and isinstance(run_id, str)
        and _MLFLOW_EXPERIMENT.fullmatch(experiment) is not None
        and _MLFLOW_RUN_ID.fullmatch(run_id) is not None
        and isinstance(artifacts, list)
    ):
        for index, artifact in enumerate(artifacts):
            if not isinstance(artifact, Mapping):
                continue
            uri = artifact.get("uri")
            if not isinstance(uri, str) or _is_pending_artifact_uri(uri):
                continue
            try:
                parsed = _parse_artifact_uri(uri)
            except ValueError:
                continue
            segments = _artifact_path_segments(parsed.path)
            if (
                parsed.scheme.casefold() != "artifact"
                or parsed.hostname is None
                or parsed.hostname.casefold() != "mlflow"
                or len(segments) != 3
                or segments[0] != experiment
                or segments[1] != run_id
            ):
                issues.append(
                    ValidationIssue(
                        code="inconsistent_archive_status",
                        path=f"artifacts[{index}].uri",
                        message=(
                            "resolved artifact URI does not match its MLflow "
                            "experiment and run ID"
                        ),
                        suggestion=(
                            "use artifact://mlflow/<experiment>/<run_id>/... or "
                            "an explicit pending-archive URI"
                        ),
                    )
                )
    return issues


def _pending_archive_paths(document: Mapping[object, object]) -> tuple[str, ...]:
    paths: list[str] = []
    platform = document.get("platform")
    if isinstance(platform, Mapping):
        for field in ("run_id", "deployment_status"):
            if platform.get(field) == "pending-archive":
                paths.append(f"platform.{field}")

    artifacts = document.get("artifacts")
    if isinstance(artifacts, list):
        for index, artifact in enumerate(artifacts):
            if not isinstance(artifact, Mapping):
                continue
            uri = artifact.get("uri")
            archive_status = artifact.get("archive_status")
            if (
                archive_status == "pending-archive"
                or isinstance(uri, str)
                and _is_pending_artifact_uri(uri)
            ):
                paths.append(f"artifacts[{index}].uri")
    return tuple(paths)


def _reference_digest_issues(document: object) -> list[ValidationIssue]:
    if not isinstance(document, Mapping):
        return []

    digest_fields = {
        "source": (
            "entrypoint_sha256",
            "notebook_sha256",
            "documentation_sha256",
        ),
        "baseline": ("input_sha256", "expected_sha256"),
    }
    issues: list[ValidationIssue] = []
    for section, fields in digest_fields.items():
        value = document.get(section)
        if not isinstance(value, Mapping):
            continue
        digest_mode = value.get("digest_mode")
        if isinstance(digest_mode, str) and digest_mode != "utf8-lf":
            path = f"{section}.digest_mode"
            issues.append(
                ValidationIssue(
                    code="unsupported_reference_digest_mode",
                    path=path,
                    message="local text references require UTF-8/LF canonical digests",
                    suggestion=f"set {path} to utf8-lf",
                )
            )
        for field in fields:
            digest = value.get(field)
            if (
                isinstance(digest, str)
                and re.fullmatch(r"[0-9a-f]{64}", digest) is None
            ):
                path = f"{section}.{field}"
                issues.append(
                    ValidationIssue(
                        code="invalid_reference_digest",
                        path=path,
                        message="file digest is not a canonical SHA-256 value",
                        suggestion=(
                            f"replace {path} with exactly 64 lowercase hexadecimal "
                            "characters"
                        ),
                    )
                )
    return issues


def _target_issues(document: object) -> list[ValidationIssue]:
    if not isinstance(document, Mapping):
        return []
    target = document.get("target")
    if not isinstance(target, Mapping) or "kind" not in target:
        return []
    supported = ("cpu-iot", "gpu", "chronos")
    target_kind = target["kind"]
    if isinstance(target_kind, str) and target_kind not in supported:
        return [
            ValidationIssue(
                code="unsupported_target",
                path="target.kind",
                message=f"no target adapter supports {target_kind!r}",
                suggestion=f"use one of: {', '.join(supported)}",
            )
        ]
    return []


def _runtime_requirement_issues(document: object) -> list[ValidationIssue]:
    if not isinstance(document, Mapping):
        return []
    target = document.get("target")
    if not isinstance(target, Mapping):
        return []
    target_kind = target.get("kind")
    if not isinstance(target_kind, str):
        return []
    if target_kind == "cpu-iot":
        if "runtime" not in target:
            return []
        return [
            ValidationIssue(
                code="unexpected_target_runtime",
                path="target.runtime",
                message="cpu-iot targets must not declare GPU runtime requirements",
                suggestion="remove target.runtime from the cpu-iot manifest",
            )
        ]
    if target_kind not in {"gpu", "chronos"}:
        return []
    runtime = target.get("runtime")
    if not isinstance(runtime, Mapping):
        return []

    issues: list[ValidationIssue] = []
    accelerator = runtime.get("accelerator")
    if isinstance(accelerator, str) and accelerator != "gpu":
        issues.append(
            ValidationIssue(
                code="invalid_gpu_runtime_value",
                path="target.runtime.accelerator",
                message="GPU and Chronos targets require the gpu accelerator",
                suggestion="set target.runtime.accelerator to gpu",
            )
        )

    gpu = runtime.get("gpu")
    if isinstance(gpu, Mapping):
        vendor = gpu.get("vendor")
        if isinstance(vendor, str) and vendor != "nvidia":
            issues.append(
                ValidationIssue(
                    code="unsupported_gpu_vendor",
                    path="target.runtime.gpu.vendor",
                    message=f"GPU vendor {vendor!r} is not supported",
                    suggestion="set target.runtime.gpu.vendor to nvidia",
                )
            )
        memory = gpu.get("memory_gb_min")
        if "memory_gb_min" in gpu and (
            isinstance(memory, bool)
            or not isinstance(memory, (int, float))
            or memory <= 0
            or (isinstance(memory, float) and not math.isfinite(memory))
            or (isinstance(memory, int) and memory > sys.float_info.max)
        ):
            issues.append(
                ValidationIssue(
                    code="invalid_gpu_runtime_value",
                    path="target.runtime.gpu.memory_gb_min",
                    message=(
                        "minimum GPU memory must be a positive finite runtime number"
                    ),
                    suggestion=(
                        "set a positive GPU memory requirement within the runtime "
                        "numeric range"
                    ),
                )
            )

    framework = runtime.get("framework")
    if isinstance(framework, Mapping):
        framework_name = framework.get("name")
        if (
            target_kind == "chronos"
            and isinstance(framework_name, str)
            and framework_name != "pytorch"
        ):
            issues.append(
                ValidationIssue(
                    code="unsupported_framework",
                    path="target.runtime.framework.name",
                    message="Chronos requires the pytorch framework contract",
                    suggestion="set target.runtime.framework.name to pytorch",
                )
            )

    artifact = runtime.get("artifact")
    if isinstance(artifact, Mapping):
        artifact_format = artifact.get("format")
        supported_formats = {"safetensors", "onnx", "torchscript"}
        if (
            isinstance(artifact_format, str)
            and artifact_format not in supported_formats
        ):
            issues.append(
                ValidationIssue(
                    code="unsupported_artifact_format",
                    path="target.runtime.artifact.format",
                    message=f"artifact format {artifact_format!r} is not supported",
                    suggestion=(
                        "use one of: " + ", ".join(sorted(supported_formats))
                    ),
                )
            )

    version_fields = (
        (gpu, "compute_capability", "target.runtime.gpu.compute_capability"),
        (runtime.get("cuda"), "version", "target.runtime.cuda.version"),
        (framework, "version", "target.runtime.framework.version"),
    )
    for section, field, path in version_fields:
        if not isinstance(section, Mapping) or not isinstance(section.get(field), str):
            continue
        value = section[field]
        try:
            SpecifierSet(value)
        except InvalidSpecifier:
            issues.append(
                ValidationIssue(
                    code="invalid_runtime_version_constraint",
                    path=path,
                    message="runtime version requirement is not a valid constraint",
                    suggestion="use a range such as >=1.0,<2.0",
                )
            )
    return issues


def _environment_requirement_issues(document: object) -> list[ValidationIssue]:
    if not isinstance(document, Mapping):
        return []
    target = document.get("target")
    environment = target.get("environment") if isinstance(target, Mapping) else None
    if not isinstance(environment, Mapping):
        return []

    issues: list[ValidationIssue] = []
    for field in ("python", "mlflow"):
        value = environment.get(field)
        if not isinstance(value, str):
            continue
        try:
            SpecifierSet(value)
        except InvalidSpecifier:
            path = f"target.environment.{field}"
            issues.append(
                ValidationIssue(
                    code="invalid_environment_version_constraint",
                    path=path,
                    message="environment version requirement is not a valid constraint",
                    suggestion=f"set {path} to a PEP 440 range",
                )
            )

    operating_system = environment.get("operating_system")
    if isinstance(operating_system, str) and operating_system not in {
        "linux",
        "windows",
    }:
        issues.append(
            ValidationIssue(
                code="unsupported_environment_platform",
                path="target.environment.operating_system",
                message="target operating system is not supported",
                suggestion="use linux or windows",
            )
        )
    architecture = environment.get("architecture")
    if isinstance(architecture, str) and architecture not in {
        "x86_64",
        "aarch64",
    }:
        issues.append(
            ValidationIssue(
                code="unsupported_environment_architecture",
                path="target.environment.architecture",
                message="target architecture is not supported",
                suggestion="use x86_64 or aarch64",
            )
        )
    package_format = environment.get("package_format")
    if isinstance(package_format, str) and package_format != "mlflow-pyfunc":
        issues.append(
            ValidationIssue(
                code="unsupported_package_format",
                path="target.environment.package_format",
                message="target package format is not supported",
                suggestion="use mlflow-pyfunc",
            )
        )

    dependency_lock = environment.get("dependency_lock")
    if isinstance(dependency_lock, str):
        lock_path = PurePosixPath(dependency_lock)
        if (
            lock_path.is_absolute()
            or dependency_lock.startswith(("/", "\\"))
            or "\\" in dependency_lock
            or "//" in dependency_lock
            or ":" in dependency_lock
            or any(part in {"", ".", ".."} for part in lock_path.parts)
        ):
            issues.append(
                ValidationIssue(
                    code="unsafe_dependency_lock_path",
                    path="target.environment.dependency_lock",
                    message="dependency lock must be a safe workspace-relative path",
                    suggestion="use runtime/requirements.lock.txt",
                )
            )

    native_runtime = environment.get("native_runtime")
    if not isinstance(native_runtime, Mapping):
        return issues
    kind = native_runtime.get("kind")
    if isinstance(kind, str) and kind not in {"none", "pyarmor"}:
        issues.append(
            ValidationIssue(
                code="unsupported_native_runtime",
                path="target.environment.native_runtime.kind",
                message="native runtime kind is not supported",
                suggestion="use none or pyarmor",
            )
        )
    if kind == "none":
        for field in sorted(set(native_runtime) - {"kind"}):
            issues.append(
                ValidationIssue(
                    code="unexpected_native_runtime_field",
                    path=f"target.environment.native_runtime.{field}",
                    message="native runtime kind none must not declare binary metadata",
                    suggestion=f"remove target.environment.native_runtime.{field}",
                )
            )
    elif kind == "pyarmor":
        python_abi = native_runtime.get("python_abi")
        if isinstance(python_abi, str) and re.fullmatch(r"cp3[0-9]{2}", python_abi) is None:
            issues.append(
                ValidationIssue(
                    code="invalid_native_runtime_abi",
                    path="target.environment.native_runtime.python_abi",
                    message="PyArmor Python ABI must use canonical cp3xx form",
                    suggestion="use a value such as cp311",
                )
            )
        platform = native_runtime.get("platform")
        expected_platform = None
        if isinstance(operating_system, str) and isinstance(architecture, str):
            expected_platform = f"{operating_system}_{architecture}"
        if isinstance(platform, str) and expected_platform is not None and platform != expected_platform:
            issues.append(
                ValidationIssue(
                    code="native_runtime_platform_mismatch",
                    path="target.environment.native_runtime.platform",
                    message="native runtime platform does not match target environment",
                    suggestion=f"set the native runtime platform to {expected_platform}",
                )
            )
        artifact = native_runtime.get("artifact")
        artifacts = document.get("artifacts")
        artifact_names = {
            item.get("name")
            for item in artifacts
            if isinstance(artifacts, list) and isinstance(item, Mapping)
        } if isinstance(artifacts, list) else set()
        if isinstance(artifact, str) and artifact not in artifact_names:
            issues.append(
                ValidationIssue(
                    code="missing_native_runtime_artifact",
                    path="target.environment.native_runtime.artifact",
                    message="named native runtime is not registered in artifacts",
                    suggestion="add an artifact record with the same name and SHA-256",
                )
            )
    return issues


def _version_issues(document: object) -> list[ValidationIssue]:
    if not isinstance(document, Mapping):
        return []

    issues: list[ValidationIssue] = []
    model = document.get("model")
    if isinstance(model, Mapping) and "version" in model:
        model_version = model["version"]
        if (
            isinstance(model_version, str)
            and _SEMANTIC_VERSION.fullmatch(model_version) is None
        ):
            issues.append(
                ValidationIssue(
                    code="invalid_model_version",
                    path="model.version",
                    message=f"model version {model_version!r} is not semantic",
                    suggestion="use a semantic version such as 1.2.3",
                )
            )

    compatibility = document.get("compatibility")
    if (
        isinstance(compatibility, Mapping)
        and isinstance(compatibility.get("model_lab"), str)
    ):
        required_version = compatibility["model_lab"]
        try:
            compatible = Version(__version__) in SpecifierSet(str(required_version))
        except InvalidSpecifier:
            issues.append(
                ValidationIssue(
                    code="invalid_compatibility_specifier",
                    path="compatibility.model_lab",
                    message=f"invalid version specifier {required_version!r}",
                    suggestion="use a PEP 440 range such as >=0.1.0,<1.0.0",
                )
            )
        else:
            if not compatible:
                issues.append(
                    ValidationIssue(
                        code="incompatible_model_lab_version",
                        path="compatibility.model_lab",
                        message=(
                            f"model-lab {__version__} does not satisfy "
                            f"{required_version!r}"
                        ),
                        suggestion=(
                            f"include model-lab {__version__} in the compatibility range"
                        ),
                    )
                )
    return issues


def _shape_issue(path: str, expected: str) -> ValidationIssue:
    return ValidationIssue(
        code="invalid_field_shape",
        path=path,
        message=f"value must be {expected}",
        suggestion=f"replace {path} with {expected}",
    )


def _shape_issues(document: object) -> list[ValidationIssue]:
    if not isinstance(document, Mapping):
        return [_shape_issue("$", "an object")]

    issues: list[ValidationIssue] = []
    for section in _SECTION_FIELDS:
        if section in document and not isinstance(document[section], Mapping):
            issues.append(_shape_issue(section, "an object"))

    target = document.get("target")
    if isinstance(target, Mapping):
        runtime = target.get("runtime")
        if "runtime" in target and not isinstance(runtime, Mapping):
            issues.append(_shape_issue("target.runtime", "an object"))
        elif isinstance(runtime, Mapping):
            for section in _RUNTIME_OBJECT_FIELDS:
                if section in runtime and not isinstance(runtime[section], Mapping):
                    issues.append(
                        _shape_issue(f"target.runtime.{section}", "an object")
                    )
        environment = target.get("environment")
        if "environment" in target and not isinstance(environment, Mapping):
            issues.append(_shape_issue("target.environment", "an object"))
        elif isinstance(environment, Mapping):
            native_runtime = environment.get("native_runtime")
            if "native_runtime" in environment and not isinstance(
                native_runtime, Mapping
            ):
                issues.append(
                    _shape_issue(
                        "target.environment.native_runtime",
                        "an object",
                    )
                )

    contract = document.get("contract")
    if isinstance(contract, Mapping):
        for direction in sorted(_SECTION_FIELDS["contract"]):
            fields = contract.get(direction)
            path = f"contract.{direction}"
            if direction in contract and not isinstance(fields, list):
                issues.append(_shape_issue(path, "an array"))
            elif isinstance(fields, list):
                for index, field in enumerate(fields):
                    if not isinstance(field, Mapping):
                        issues.append(_shape_issue(f"{path}[{index}]", "an object"))

    artifacts = document.get("artifacts")
    if "artifacts" in document and not isinstance(artifacts, list):
        issues.append(_shape_issue("artifacts", "an array"))
    elif isinstance(artifacts, list):
        for index, artifact in enumerate(artifacts):
            if not isinstance(artifact, Mapping):
                issues.append(_shape_issue(f"artifacts[{index}]", "an object"))

    release = document.get("release")
    if isinstance(release, Mapping):
        history = release.get("history")
        if "history" in release and not isinstance(history, list):
            issues.append(_shape_issue("release.history", "an array"))
    return issues


def _validate_document(document: object) -> ValidationReport:
    root = document if isinstance(document, Mapping) else {}
    model = root.get("model")
    model = model if isinstance(model, Mapping) else {}
    raw_model_id = model.get("id")
    model_id = (
        raw_model_id
        if isinstance(raw_model_id, str) and raw_model_id.strip()
        else "<unknown>"
    )
    raw_lifecycle_state = model.get("lifecycle_state")
    if isinstance(raw_lifecycle_state, str):
        try:
            lifecycle_state = LifecycleState(raw_lifecycle_state)
        except ValueError:
            lifecycle_state = None
    else:
        lifecycle_state = None
    issues: list[ValidationIssue] = []
    schema_version = root.get("schema_version")
    if isinstance(schema_version, str) and schema_version != "1.0":
        issues.append(
            ValidationIssue(
                code="unsupported_schema_version",
                path="schema_version",
                message=(
                    f"unsupported schema version "
                    f"{schema_version!r}"
                ),
                suggestion='use supported schema version "1.0"',
            )
        )
    issues.extend(_unknown_field_issues(document))
    issues.extend(_missing_field_issues(document))
    issues.extend(_shape_issues(document))
    issues.extend(_scalar_issues(document))
    issues.extend(_lifecycle_issues(document))
    issues.extend(_maintenance_status_issues(document))
    issues.extend(_contract_field_issues(document))
    issues.extend(_artifact_issues(document))
    issues.extend(_archive_status_issues(document))
    issues.extend(_reference_digest_issues(document))
    issues.extend(_target_issues(document))
    issues.extend(_runtime_requirement_issues(document))
    issues.extend(_environment_requirement_issues(document))
    issues.extend(_version_issues(document))
    return ValidationReport(
        model_id=model_id,
        lifecycle_state=lifecycle_state,
        issues=tuple(issues),
    )


def _validate_loaded_document(
    document: object,
    manifest_path: Path,
) -> ValidationReport:
    validation = _validate_document(document)
    if not validation.valid or not isinstance(document, Mapping):
        return validation

    from model_lab.point_serving import PointServingError, parse_point_serving, parse_provider_spec

    try:
        serving = parse_point_serving(document, manifest_path)
        parse_provider_spec(document)
    except PointServingError as error:
        return ValidationReport(
            model_id=validation.model_id,
            lifecycle_state=validation.lifecycle_state,
            issues=(error.as_issue(),),
        )

    from model_lab.asset_validation import validate_asset_records

    issues, checks = validate_asset_records(document, manifest_path)
    if serving is not None:
        role_hints = (
            *((name, "point") for name in serving.point_fields),
            *((name, "control") for name in serving.control_fields),
            *((name, "evaluation") for name in serving.evaluation_fields),
        )
        checks = tuple(checks) + tuple(
            ValidationCheck(
                name=f"review _target role: {name} ({role})",
                status="review",
                details=(("field", name), ("declared_role", role), ("action", "confirm field role with model owner; suffix does not infer role")),
            )
            for name, role in role_hints if name.casefold().endswith("_target")
        )
    return ValidationReport(
        model_id=validation.model_id,
        lifecycle_state=validation.lifecycle_state,
        issues=issues,
        checks=checks,
    )


def validate_manifest(path: str | Path) -> ValidationReport:
    manifest_path = Path(path)
    document = _load_manifest(manifest_path)
    return _validate_loaded_document(document, manifest_path)


def load_validated_asset(
    path: str | Path,
) -> tuple[ValidationReport, ValidatedAsset | None]:
    manifest_path = Path(path)
    document = _load_manifest(manifest_path)
    validation = _validate_loaded_document(document, manifest_path)
    if not validation.valid:
        return validation, None

    summary = _inspect_document(document, manifest_path)
    artifacts = tuple(
        ArtifactReference(
            name=artifact["name"],
            kind=artifact["kind"],
            uri=artifact["uri"],
            sha256=artifact["sha256"],
            archive_status=(
                artifact["archive_status"]
                if isinstance(artifact.get("archive_status"), str)
                else None
            ),
        )
        for artifact in document["artifacts"]
    )
    target = document["target"]
    runtime = (
        target.get("runtime")
        if target["kind"] in {"gpu", "chronos"}
        else None
    )
    runtime_requirements = None
    if isinstance(runtime, Mapping):
        runtime_requirements = RuntimeRequirements(
            accelerator=runtime["accelerator"],
            gpu_vendor=runtime["gpu"]["vendor"],
            gpu_compute_capability=runtime["gpu"]["compute_capability"],
            gpu_memory_gb_min=runtime["gpu"]["memory_gb_min"],
            cuda_version=runtime["cuda"]["version"],
            framework_name=runtime["framework"]["name"],
            framework_version=runtime["framework"]["version"],
            artifact_format=runtime["artifact"]["format"],
        )
    environment = target.get("environment")
    environment_requirements = None
    if isinstance(environment, Mapping):
        native_runtime = environment["native_runtime"]
        environment_requirements = EnvironmentRequirements(
            operating_system=environment["operating_system"],
            architecture=environment["architecture"],
            python=environment["python"],
            mlflow=environment["mlflow"],
            package_format=environment["package_format"],
            dependency_lock=environment["dependency_lock"],
            native_runtime=NativeRuntimeRequirements(
                kind=native_runtime["kind"],
                python_abi=native_runtime.get("python_abi"),
                platform=native_runtime.get("platform"),
                artifact=native_runtime.get("artifact"),
            ),
        )
    return validation, ValidatedAsset(
        summary=summary,
        artifacts=artifacts,
        runtime_requirements=runtime_requirements,
        environment_requirements=environment_requirements,
        pending_archive_paths=_pending_archive_paths(document),
    )
