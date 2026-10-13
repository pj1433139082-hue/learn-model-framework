from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import platform
import sys
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
import yaml  # type: ignore[import-untyped]

from model_lab.manifest import (
    EnvironmentRequirements,
    ManifestError,
    ValidationIssue,
    load_validated_asset,
)
from model_lab.path_safety import is_link_or_junction
from model_lab.point_serving import ProviderArchiveSpec, parse_point_serving, parse_provider_spec


_MAX_METADATA_BYTES = 1024 * 1024
_MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
_REQUIRED_LOCK_PACKAGES = frozenset({"mlflow", "cloudpickle", "numpy", "pandas"})
_REQUIRED_BUNDLE_FILES = (
    "MLmodel",
    "python_env.yaml",
    "conda.yaml",
    "requirements.txt",
    "artifacts/logic_bundle.pkl",
    "artifacts/custom_sources.pkl",
)


@dataclass(frozen=True)
class RuntimeProfile:
    profile_id: str
    operating_system: str
    architecture: str
    python: str
    python_minor: str
    mlflow: str
    dependency_pins: tuple[tuple[str, str], ...]

    def pin(self, package: str) -> str:
        return dict(self.dependency_pins)[package]


DEFAULT_RUNTIME_PROFILE_ID = "linux-x86_64-py311-mlflow222"
_RUNTIME_PROFILES = {
    DEFAULT_RUNTIME_PROFILE_ID: RuntimeProfile(
        profile_id=DEFAULT_RUNTIME_PROFILE_ID,
        operating_system="linux",
        architecture="x86_64",
        python=">=3.11,<3.12",
        python_minor="3.11",
        mlflow="==2.22.2",
        dependency_pins=(
            ("mlflow", "2.22.2"),
            ("cloudpickle", "3.1.2"),
            ("numpy", "2.3.3"),
            ("pandas", "2.3.3"),
        ),
    ),
    "windows-x86_64-py312-legacy": RuntimeProfile(
        profile_id="windows-x86_64-py312-legacy",
        operating_system="windows",
        architecture="x86_64",
        python=">=3.12,<3.13",
        python_minor="3.12",
        mlflow="==2.22.2",
        dependency_pins=(
            ("mlflow", "2.22.2"),
            ("cloudpickle", "3.1.2"),
            ("numpy", "2.5.2"),
            ("pandas", "2.3.3"),
        ),
    ),
}
RUNTIME_PROFILE_IDS = tuple(_RUNTIME_PROFILES)
ACTIVE_RUNTIME_PROFILE_IDS = (DEFAULT_RUNTIME_PROFILE_ID,)


def get_runtime_profile(profile_id: str) -> RuntimeProfile:
    return _RUNTIME_PROFILES[profile_id]


@dataclass(frozen=True)
class RuntimeObservation:
    operating_system: str
    architecture: str
    python: str
    python_abi: str

    @classmethod
    def current(cls) -> RuntimeObservation:
        return cls(
            operating_system=platform.system(),
            architecture=platform.machine(),
            python=platform.python_version(),
            python_abi=f"cp{sys.version_info.major}{sys.version_info.minor}",
        ).normalized()

    def normalized(self) -> RuntimeObservation:
        system = self.operating_system.strip().casefold().replace("-", "_")
        systems = {
            "linux": "linux",
            "windows": "windows",
            "win32": "windows",
        }
        machine = self.architecture.strip().casefold().replace("-", "_")
        architectures = {
            "amd64": "x86_64",
            "x64": "x86_64",
            "x86_64": "x86_64",
            "arm64": "aarch64",
            "aarch64": "aarch64",
        }
        return RuntimeObservation(
            operating_system=systems.get(system, system),
            architecture=architectures.get(machine, machine),
            python=self.python.strip(),
            python_abi=self.python_abi.strip().casefold(),
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "operating_system": self.operating_system,
            "architecture": self.architecture,
            "python": self.python,
            "python_abi": self.python_abi,
        }


@dataclass(frozen=True)
class RuntimeCheckReport:
    model_id: str
    target: EnvironmentRequirements | None
    current: RuntimeObservation
    dependency_pins: tuple[tuple[str, str], ...]
    issues: tuple[ValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return not self.issues

    def as_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "model_id": self.model_id,
            "target": self.target.as_dict() if self.target is not None else None,
            "current": self.current.as_dict(),
            "dependency_pins": dict(self.dependency_pins),
            "issues": [issue.as_dict() for issue in self.issues],
            "safety": {
                "artifacts_executed": False,
                "credentials_read": False,
                "network_access": False,
            },
        }

    def as_text(self) -> str:
        state = "PASS" if self.valid else "BLOCK"
        lines = [f"Runtime environment: {state}"]
        if self.target is not None:
            lines.append(
                "Target: "
                f"{self.target.operating_system}/{self.target.architecture} "
                f"Python {self.target.python}"
            )
        lines.append(
            "Current: "
            f"{self.current.operating_system}/{self.current.architecture} "
            f"Python {self.current.python}"
        )
        lines.extend(issue.as_text() for issue in self.issues)
        return "\n".join(lines)


@dataclass(frozen=True)
class BundleCheckReport:
    model_id: str
    bundle_path: str
    target: EnvironmentRequirements | None
    dependency_pins: tuple[tuple[str, str], ...]
    artifact_paths: tuple[tuple[str, str], ...]
    issues: tuple[ValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return not self.issues

    def as_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "model_id": self.model_id,
            "bundle_path": self.bundle_path,
            "target": self.target.as_dict() if self.target is not None else None,
            "dependency_pins": dict(self.dependency_pins),
            "artifact_paths": dict(self.artifact_paths),
            "issues": [issue.as_dict() for issue in self.issues],
            "safety": {
                "artifacts_executed": False,
                "credentials_read": False,
                "network_access": False,
                "serialized_objects_loaded": False,
            },
        }

    def as_text(self) -> str:
        state = "PASS" if self.valid else "BLOCK"
        lines = [f"MLflow bundle: {state}", f"Path: {self.bundle_path}"]
        lines.extend(issue.as_text() for issue in self.issues)
        return "\n".join(lines)


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
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found a duplicate key",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _issue(code: str, path: str, message: str, suggestion: str) -> ValidationIssue:
    return ValidationIssue(code=code, path=path, message=message, suggestion=suggestion)


def _bounded_text(path: Path, *, label: str) -> tuple[str | None, list[ValidationIssue]]:
    if is_link_or_junction(path):
        return None, [
            _issue(
                "unsafe_dependency_lock",
                label,
                "dependency lock must not be a link or junction",
                "replace it with a regular local file",
            )
        ]
    try:
        status = path.stat()
    except FileNotFoundError:
        return None, [
            _issue(
                "missing_dependency_lock",
                label,
                "declared dependency lock does not exist",
                "create the declared lock file with exact versions",
            )
        ]
    except OSError:
        return None, [
            _issue(
                "dependency_lock_unreadable",
                label,
                "declared dependency lock cannot be inspected",
                "make the regular local file readable",
            )
        ]
    if not path.is_file():
        return None, [
            _issue(
                "dependency_lock_unreadable",
                label,
                "declared dependency lock is not a regular file",
                "replace it with a regular local file",
            )
        ]
    if status.st_size > _MAX_METADATA_BYTES:
        return None, [
            _issue(
                "dependency_lock_too_large",
                label,
                "dependency lock exceeds the 1 MiB metadata limit",
                "remove generated or binary content from the lock file",
            )
        ]
    try:
        return path.read_text(encoding="utf-8"), []
    except (OSError, UnicodeDecodeError):
        return None, [
            _issue(
                "dependency_lock_unreadable",
                label,
                "dependency lock must be readable UTF-8 text",
                "write a UTF-8 requirements lock file",
            )
        ]


def _exact_pin(requirement: Requirement) -> str | None:
    specifiers = tuple(requirement.specifier)
    if (
        requirement.url is not None
        or requirement.marker is not None
        or len(specifiers) != 1
        or specifiers[0].operator != "=="
        or "*" in specifiers[0].version
    ):
        return None
    try:
        return str(Version(specifiers[0].version))
    except InvalidVersion:
        return None


def _parse_pins(
    text: str,
    *,
    label: str,
) -> tuple[dict[str, str], list[ValidationIssue]]:
    pins: dict[str, str] = {}
    issues: list[ValidationIssue] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        path = f"{label}:{line_number}"
        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            issues.append(
                _issue(
                    "invalid_dependency_requirement",
                    path,
                    "dependency line is not a valid PEP 508 requirement",
                    "replace it with package==version",
                )
            )
            continue
        name = canonicalize_name(requirement.name)
        version = _exact_pin(requirement)
        if version is None:
            issues.append(
                _issue(
                    "dependency_not_pinned",
                    path,
                    f"dependency {name} is not pinned to one exact version",
                    f"use {name}==<version>",
                )
            )
            continue
        if name in pins:
            issues.append(
                _issue(
                    "duplicate_dependency",
                    path,
                    f"dependency {name} is declared more than once",
                    "keep exactly one pinned declaration",
                )
            )
            continue
        pins[name] = version
    return pins, issues


def _dependency_lock_report(
    manifest_path: Path,
    environment: EnvironmentRequirements,
) -> tuple[dict[str, str], list[ValidationIssue]]:
    root = manifest_path.parent
    lock = root.joinpath(*environment.dependency_lock.split("/"))
    component = root
    for part in environment.dependency_lock.split("/"):
        component = component / part
        if is_link_or_junction(component):
            return {}, [
                _issue(
                    "unsafe_dependency_lock",
                    "target.environment.dependency_lock",
                    "dependency lock path must not pass through a link or junction",
                    "use a regular file inside the model workspace",
                )
            ]
    text, issues = _bounded_text(
        lock,
        label="target.environment.dependency_lock",
    )
    if text is None:
        return {}, issues
    pins, parse_issues = _parse_pins(
        text,
        label="target.environment.dependency_lock",
    )
    issues.extend(parse_issues)
    for package in sorted(_REQUIRED_LOCK_PACKAGES - set(pins)):
        issues.append(
            _issue(
                "missing_required_dependency",
                "target.environment.dependency_lock",
                f"required baseline dependency {package} is missing",
                f"add an exact {package}==<version> pin",
            )
        )
    mlflow_version = pins.get("mlflow")
    if mlflow_version is not None:
        try:
            compatible = Version(mlflow_version) in SpecifierSet(environment.mlflow)
        except (InvalidSpecifier, InvalidVersion):
            compatible = False
        if not compatible:
            issues.append(
                _issue(
                    "dependency_version_mismatch",
                    "target.environment.dependency_lock",
                    "locked MLflow version does not satisfy the target contract",
                    "pin MLflow to the declared target version",
                )
            )
    return pins, issues


def _bundle_regular_file(
    root: Path,
    relative: str,
    *,
    metadata: bool,
) -> tuple[Path | None, list[ValidationIssue]]:
    path = root
    for part in relative.split("/"):
        path = path / part
        if is_link_or_junction(path):
            return None, [
                _issue(
                    "unsafe_mlflow_bundle_path",
                    f"bundle.{relative}",
                    "bundle path must not pass through a link or junction",
                    "replace linked content with regular local files",
                )
            ]
    try:
        status = path.stat()
    except FileNotFoundError:
        return None, [
            _issue(
                "missing_mlflow_bundle_file",
                f"bundle.{relative}",
                "required MLflow bundle file is missing",
                f"generate {relative} during the packaging step",
            )
        ]
    except OSError:
        return None, [
            _issue(
                "mlflow_bundle_file_unreadable",
                f"bundle.{relative}",
                "MLflow bundle file cannot be inspected",
                "make the regular local file readable",
            )
        ]
    if not path.is_file():
        return None, [
            _issue(
                "mlflow_bundle_file_unreadable",
                f"bundle.{relative}",
                "MLflow bundle entry is not a regular file",
                "replace it with a regular local file",
            )
        ]
    size_limit = _MAX_METADATA_BYTES if metadata else _MAX_ARTIFACT_BYTES
    if status.st_size > size_limit:
        code = "bundle_metadata_too_large" if metadata else "bundle_artifact_too_large"
        return None, [
            _issue(
                code,
                f"bundle.{relative}",
                "MLflow bundle file exceeds its static inspection limit",
                "regenerate a bounded release artifact",
            )
        ]
    return path, []


def _read_bundle_text(path: Path, relative: str) -> tuple[str | None, list[ValidationIssue]]:
    try:
        return path.read_text(encoding="utf-8"), []
    except (OSError, UnicodeDecodeError):
        return None, [
            _issue(
                "mlflow_bundle_file_unreadable",
                f"bundle.{relative}",
                "MLflow metadata must be readable UTF-8 text",
                "regenerate UTF-8 MLflow metadata",
            )
        ]


def _load_bundle_yaml(
    path: Path,
    relative: str,
) -> tuple[object | None, list[ValidationIssue]]:
    text, issues = _read_bundle_text(path, relative)
    if text is None:
        return None, issues
    try:
        return yaml.load(text, Loader=_UniqueKeySafeLoader), []
    except yaml.YAMLError:
        return None, [
            _issue(
                "bundle_yaml_invalid",
                f"bundle.{relative}",
                "MLflow metadata is invalid or contains duplicate keys",
                "regenerate unambiguous YAML metadata",
            )
        ]


def _safe_bundle_artifact_path(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    relative = PurePosixPath(raw)
    if (
        relative.is_absolute()
        or raw.startswith(("/", "\\"))
        or "\\" in raw
        or "//" in raw
        or ":" in raw
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        return None
    return relative.as_posix()


def _artifact_mapping_path(value: object) -> str | None:
    """Resolve a recorded artifact location; native separators are never portable.

    MLflow records the saving host's own separators. A bundle whose recorded path uses
    backslashes cannot be resolved by the Linux runtime, so it is rejected for every declared
    target instead of being normalized: a Windows-built bundle is deployable only when the
    builder already recorded portable paths (see ``bundle_build._portable_mlmodel``).
    """
    raw = value.get("path") if isinstance(value, Mapping) else value
    if not isinstance(raw, str):
        return None
    return _safe_bundle_artifact_path(raw)


def _python_version_matches(value: object, constraint: str) -> bool:
    try:
        return Version(str(value)) in SpecifierSet(constraint)
    except (InvalidSpecifier, InvalidVersion):
        return False


def _conda_versions(document: object) -> tuple[str | None, dict[str, str], list[ValidationIssue]]:
    if not isinstance(document, Mapping):
        return None, {}, [
            _issue(
                "bundle_yaml_invalid",
                "bundle.conda.yaml",
                "conda environment must be a YAML object",
                "regenerate conda.yaml",
            )
        ]
    dependencies = document.get("dependencies")
    if not isinstance(dependencies, list):
        return None, {}, [
            _issue(
                "bundle_conda_invalid",
                "bundle.conda.yaml.dependencies",
                "conda environment has no dependency list",
                "regenerate conda.yaml with Python and pip dependencies",
            )
        ]
    python_version: str | None = None
    pip_lines: list[str] = []
    for dependency in dependencies:
        if isinstance(dependency, str) and dependency.startswith("python="):
            python_version = dependency.split("=", maxsplit=2)[-1]
        elif isinstance(dependency, Mapping):
            pip = dependency.get("pip")
            if isinstance(pip, list):
                pip_lines.extend(item for item in pip if isinstance(item, str))
    direct_lines = [line for line in pip_lines if not line.strip().startswith("-r ")]
    pins, issues = _parse_pins(
        "\n".join(direct_lines),
        label="bundle.conda.yaml.dependencies.pip",
    )
    return python_version, pins, issues


def _provider_bundle_issues(
    bundle: Path, spec: ProviderArchiveSpec, artifact_paths: dict[str, str]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    provider_files = {
        "module": "code/mlops_deploy_iot_armor2_311.py",
        "runtime_init": "code/pyarmor_runtime_000000/__init__.py",
        "native": "code/pyarmor_runtime_000000/linux_x86_64/pyarmor_runtime.so",
    }
    for label, relative in provider_files.items():
        path, file_issues = _bundle_regular_file(bundle, relative, metadata=False)
        issues.extend(file_issues)
        if path is not None and hashlib.sha256(path.read_bytes()).hexdigest() != getattr(spec, f"{label}_sha256"):
            issues.append(_issue(
                "point_provider_component_digest_mismatch", f"bundle.{relative}",
                f"company IoT {label} differs from the approved archive", "rebuild with the approved provider ZIP",
            ))
    native_relative = artifact_paths.get("company_iot_native")
    if native_relative is not None:
        native_path, native_issues = _bundle_regular_file(bundle, native_relative, metadata=False)
        issues.extend(native_issues)
        if native_path is not None and hashlib.sha256(native_path.read_bytes()).hexdigest() != spec.native_sha256:
            issues.append(_issue("point_provider_component_digest_mismatch", f"bundle.{native_relative}", "mapped native runtime SHA-256 differs", "rebuild with the approved provider ZIP"))
    provenance_relative = artifact_paths.get("company_iot_provenance")
    if provenance_relative is not None:
        provenance_path, provenance_issues = _bundle_regular_file(bundle, provenance_relative, metadata=True)
        issues.extend(provenance_issues)
        try:
            provenance = json.loads(provenance_path.read_text(encoding="utf-8")) if provenance_path is not None else None
        except (UnicodeError, ValueError):
            provenance = None
        if not isinstance(provenance, dict) or any(
            provenance.get(label) != getattr(spec, label)
            for label in ("archive_sha256", "module_sha256", "runtime_init_sha256", "native_sha256")
        ) or provenance.get("source") != "infra_model_deploy.zip:resources/infra/311" or provenance.get("python_abi") != "cp311" or provenance.get("platform") != "linux_x86_64":
            issues.append(_issue("point_provider_provenance_mismatch", f"bundle.{provenance_relative}", "company archive provenance differs from model.yaml", "rebuild with the approved provider ZIP"))
    for path in bundle.rglob("*"):
        if path.is_file() and (
            path.suffix.casefold() in {".pyd", ".exe", ".dll"}
            or "windows_x86_64" in path.parts
            or any(part.casefold() in {".venv", "venv", "scripts", "site-packages"} for part in path.parts)
            or path.name.casefold() in {"python", "python3", "python3.11"}
        ):
            issues.append(_issue("point_provider_forbidden_file", f"bundle.{path.relative_to(bundle).as_posix()}", "bundle contains a Windows runtime or interpreter", "remove foreign runtime files and rebuild"))
    return issues


def verify_mlflow_bundle(
    manifest_path: str | Path,
    bundle_path: str | Path,
) -> BundleCheckReport:
    """Statically verify an MLflow pyfunc bundle without importing its code."""

    manifest = Path(manifest_path)
    bundle = Path(bundle_path)
    validation, asset = load_validated_asset(manifest)
    model_id = validation.model_id
    try:
        public_bundle_path = str(bundle.resolve(strict=False))
    except OSError:
        public_bundle_path = str(bundle)
    if asset is None:
        return BundleCheckReport(
            model_id=model_id,
            bundle_path=public_bundle_path,
            target=None,
            dependency_pins=(),
            artifact_paths=(),
            issues=validation.issues,
        )
    environment = asset.environment_requirements
    if environment is None:
        return BundleCheckReport(
            model_id=asset.summary.model_id,
            bundle_path=public_bundle_path,
            target=None,
            dependency_pins=(),
            artifact_paths=(),
            issues=(
                _issue(
                    "missing_target_environment",
                    "target.environment",
                    "model has no target runtime environment contract",
                    "add target.environment before verifying a new bundle",
                ),
            ),
        )

    issues: list[ValidationIssue] = []
    if is_link_or_junction(bundle) or not bundle.is_dir():
        issues.append(
            _issue(
                "missing_mlflow_bundle",
                "bundle",
                "MLflow service bundle is missing or unsafe",
                "generate release-bundle/service as a regular local directory",
            )
        )
        return BundleCheckReport(
            model_id=asset.summary.model_id,
            bundle_path=public_bundle_path,
            target=environment,
            dependency_pins=(),
            artifact_paths=(),
            issues=tuple(issues),
        )

    files: dict[str, Path] = {}
    for relative in _REQUIRED_BUNDLE_FILES:
        path, file_issues = _bundle_regular_file(
            bundle,
            relative,
            metadata=relative in {"MLmodel", "python_env.yaml", "conda.yaml", "requirements.txt"},
        )
        issues.extend(file_issues)
        if path is not None:
            files[relative] = path

    lock_pins, lock_issues = _dependency_lock_report(manifest, environment)
    issues.extend(lock_issues)
    bundle_pins: dict[str, str] = {}
    requirements = files.get("requirements.txt")
    if requirements is not None:
        text, read_issues = _read_bundle_text(requirements, "requirements.txt")
        issues.extend(read_issues)
        if text is not None:
            bundle_pins, pin_issues = _parse_pins(
                text,
                label="bundle.requirements.txt",
            )
            issues.extend(pin_issues)
    for package, locked_version in sorted(lock_pins.items()):
        bundled_version = bundle_pins.get(package)
        if bundled_version != locked_version:
            code = "bundle_mlflow_mismatch" if package == "mlflow" else "bundle_dependency_mismatch"
            issues.append(
                _issue(
                    code,
                    "bundle.requirements.txt",
                    f"bundle dependency {package} does not match the lock file",
                    f"pin {package}=={locked_version}",
                )
            )

    python_env_document: object | None = None
    python_env = files.get("python_env.yaml")
    if python_env is not None:
        python_env_document, yaml_issues = _load_bundle_yaml(
            python_env, "python_env.yaml"
        )
        issues.extend(yaml_issues)
        declared_python = (
            python_env_document.get("python")
            if isinstance(python_env_document, Mapping)
            else None
        )
        if not _python_version_matches(declared_python, environment.python):
            issues.append(
                _issue(
                    "bundle_python_mismatch",
                    "bundle.python_env.yaml.python",
                    "bundle Python does not satisfy the target contract",
                    f"regenerate with Python {environment.python}",
                )
            )

    conda = files.get("conda.yaml")
    if conda is not None:
        conda_document, yaml_issues = _load_bundle_yaml(conda, "conda.yaml")
        issues.extend(yaml_issues)
        if conda_document is not None:
            conda_python, conda_pins, conda_issues = _conda_versions(conda_document)
            issues.extend(conda_issues)
            if not _python_version_matches(conda_python, environment.python):
                issues.append(
                    _issue(
                        "bundle_python_mismatch",
                        "bundle.conda.yaml.dependencies",
                        "conda Python does not satisfy the target contract",
                        f"pin Python within {environment.python}",
                    )
                )
            for package, version in conda_pins.items():
                if package in bundle_pins and bundle_pins[package] != version:
                    code = "bundle_mlflow_mismatch" if package == "mlflow" else "bundle_dependency_mismatch"
                    issues.append(
                        _issue(
                            code,
                            "bundle.conda.yaml.dependencies.pip",
                            f"conda and requirements disagree for {package}",
                            "use the same exact dependency version in both files",
                        )
                    )

    artifact_paths: dict[str, str] = {}
    mlmodel = files.get("MLmodel")
    if mlmodel is not None:
        mlmodel_document, yaml_issues = _load_bundle_yaml(mlmodel, "MLmodel")
        issues.extend(yaml_issues)
        manifest_document = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        serving = parse_point_serving(manifest_document, manifest)
        provider_spec = parse_provider_spec(manifest_document)
        if serving is not None:
            signature = (
                mlmodel_document.get("signature")
                if isinstance(mlmodel_document, Mapping)
                else None
            )
            raw_inputs = signature.get("inputs") if isinstance(signature, Mapping) else None
            try:
                signature_inputs = json.loads(raw_inputs) if isinstance(raw_inputs, str) else None
            except (ValueError, TypeError):
                signature_inputs = None
            declarations: dict[str, Mapping[str, Any]] = {}
            if isinstance(signature_inputs, list):
                for field in signature_inputs:
                    if isinstance(field, Mapping):
                        declared_name = field.get("name")
                        if isinstance(declared_name, str):
                            declarations[declared_name] = field
            expected_fields = {
                serving.factory_field: "string",
                serving.start_field: "string",
                **{field: "array" for field in serving.point_fields},
                **{field: "double" for field in serving.control_fields},
                **{field: "double" for field in serving.evaluation_fields},
            }
            for name, field_type in expected_fields.items():
                declaration = declarations.get(name)
                optional = name in serving.evaluation_fields or (name == serving.start_field and serving.time_strategy == "latest-complete")
                matches = (
                    isinstance(declaration, Mapping)
                    and declaration.get("type") == field_type
                    and declaration.get("required") is (not optional)
                )
                if isinstance(declaration, Mapping) and matches and field_type == "array":
                    items = declaration.get("items")
                    matches = isinstance(items, Mapping) and items.get("type") == "string"
                if not matches:
                    expected_type = "Array(string)" if field_type == "array" else field_type
                    issues.append(_issue(
                        "point_signature_mismatch",
                        f"bundle.MLmodel.signature.inputs.{name}",
                        f"{name} must be {'optional' if optional else 'required'} {expected_type} in the MLflow service signature",
                        f"rebuild the point-mode bundle with {name}: {expected_type}",
                    ))
            for name in declarations.keys() - expected_fields.keys():
                issues.append(_issue(
                    "point_signature_mismatch",
                    f"bundle.MLmodel.signature.inputs.{name}",
                    f"undeclared serving input {name}",
                    "remove the extra service input",
                ))
        flavors = (
            mlmodel_document.get("flavors")
            if isinstance(mlmodel_document, Mapping)
            else None
        )
        pyfunc = flavors.get("python_function") if isinstance(flavors, Mapping) else None
        if not isinstance(pyfunc, Mapping):
            issues.append(
                _issue(
                    "missing_mlflow_pyfunc_flavor",
                    "bundle.MLmodel.flavors.python_function",
                    "MLmodel has no python_function flavor",
                    "save the model with the MLflow pyfunc flavor",
                )
            )
        else:
            python_model = pyfunc.get("python_model")
            if serving is not None and pyfunc.get("loader_module") != "mlflow.pyfunc.model":
                issues.append(
                    _issue(
                        "point_loader_mismatch",
                        "bundle.MLmodel.flavors.python_function.loader_module",
                        "point-mode bundle must use the MLflow PythonModel loader",
                        "rebuild the point-mode bundle",
                    )
                )
            if python_model is not None or pyfunc.get("loader_module") == "mlflow.pyfunc.model" or serving is not None:
                if python_model is None:
                    issues.append(
                        _issue(
                            "missing_mlflow_python_model",
                            "bundle.MLmodel.flavors.python_function.python_model",
                            "MLflow pyfunc flavor does not name its serialized PythonModel",
                            "save a complete MLflow PythonModel bundle",
                        )
                    )
                else:
                    model_relative = _artifact_mapping_path(python_model)
                    if model_relative is None:
                        issues.append(
                            _issue(
                                "unsafe_bundle_artifact_path",
                                "bundle.MLmodel.flavors.python_function.python_model",
                                "PythonModel path is absolute, escaping, or malformed",
                                "use a safe path below the service bundle",
                            )
                        )
                    else:
                        _, model_issues = _bundle_regular_file(
                            bundle, model_relative, metadata=False
                        )
                        issues.extend(model_issues)
            mappings = pyfunc.get("artifacts")
            mappings = mappings if isinstance(mappings, Mapping) else {}
            required_mappings = {
                "logic_bundle": "artifacts/logic_bundle.pkl",
                "custom_sources": "artifacts/custom_sources.pkl",
                "model_file": None,
            }
            native = environment.native_runtime
            if native.kind == "pyarmor" and native.artifact is not None:
                required_mappings[native.artifact] = None
            if provider_spec is not None:
                required_mappings["company_iot_provenance"] = None
            for name, expected in required_mappings.items():
                if name not in mappings:
                    issues.append(
                        _issue(
                            "missing_mlflow_artifact_mapping",
                            f"bundle.MLmodel.flavors.python_function.artifacts.{name}",
                            "required MLflow artifact mapping is missing",
                            f"map {name} to a bundle-local artifact",
                        )
                    )
                    continue
                artifact_relative = _artifact_mapping_path(mappings[name])
                if artifact_relative is None:
                    issues.append(
                        _issue(
                            "unsafe_bundle_artifact_path",
                            f"bundle.MLmodel.flavors.python_function.artifacts.{name}",
                            "artifact mapping is absolute, escaping, or malformed",
                            "use a safe path below the service bundle",
                        )
                    )
                    continue
                artifact_paths[name] = artifact_relative
                if expected is not None and artifact_relative != expected:
                    issues.append(
                        _issue(
                            "mlflow_artifact_mapping_mismatch",
                            f"bundle.MLmodel.flavors.python_function.artifacts.{name}",
                            "artifact mapping does not point to the required file",
                            f"map {name} to {expected}",
                        )
                    )
                _, artifact_issues = _bundle_regular_file(
                    bundle,
                    artifact_relative,
                    metadata=False,
                )
                issues.extend(artifact_issues)
            if native.kind == "pyarmor" and native.artifact is not None:
                native_path = artifact_paths.get(native.artifact)
                expected_suffix = ".so" if environment.operating_system == "linux" else ".pyd"
                if native_path is not None and not native_path.casefold().endswith(expected_suffix):
                    issues.append(
                        _issue(
                            "native_runtime_platform_mismatch",
                            f"bundle.MLmodel.flavors.python_function.artifacts.{native.artifact}",
                            "native runtime extension does not match the target platform",
                            f"use a {expected_suffix} runtime for {environment.operating_system}",
                        )
                    )
            if provider_spec is not None:
                issues.extend(_provider_bundle_issues(bundle, provider_spec, artifact_paths))

    return BundleCheckReport(
        model_id=asset.summary.model_id,
        bundle_path=public_bundle_path,
        target=environment,
        dependency_pins=tuple(sorted(bundle_pins.items())),
        artifact_paths=tuple(sorted(artifact_paths.items())),
        issues=tuple(issues),
    )


def check_build_environment(
    manifest_path: str | Path,
    *,
    observation: RuntimeObservation | None = None,
) -> RuntimeCheckReport:
    """Compare the local build host and dependency lock with the manifest contract."""

    path = Path(manifest_path)
    validation, asset = load_validated_asset(path)
    current = (observation or RuntimeObservation.current()).normalized()
    if asset is None:
        return RuntimeCheckReport(
            model_id=validation.model_id,
            target=None,
            current=current,
            dependency_pins=(),
            issues=validation.issues,
        )
    environment = asset.environment_requirements
    if environment is None:
        return RuntimeCheckReport(
            model_id=asset.summary.model_id,
            target=None,
            current=current,
            dependency_pins=(),
            issues=(
                _issue(
                    "missing_target_environment",
                    "target.environment",
                    "model has no target runtime environment contract",
                    "add target.environment or recreate from the current template",
                ),
            ),
        )

    issues: list[ValidationIssue] = []
    try:
        python_matches = Version(current.python) in SpecifierSet(environment.python)
    except (InvalidSpecifier, InvalidVersion) as error:
        raise ManifestError(
            "invalid_environment_contract",
            "validated environment contract could not be evaluated",
        ) from error
    if not python_matches:
        issues.append(
            _issue(
                "build_python_mismatch",
                "target.environment.python",
                "current Python does not satisfy the target contract",
                f"build with Python {environment.python}",
            )
        )
    if (
        current.operating_system != environment.operating_system
        or current.architecture != environment.architecture
    ):
        issues.append(
            _issue(
                "build_platform_mismatch",
                "target.environment",
                "current OS or architecture does not match the target contract",
                (
                    f"build on {environment.operating_system}/"
                    f"{environment.architecture}"
                ),
            )
        )
    native = environment.native_runtime
    if native.kind == "pyarmor" and native.python_abi != current.python_abi:
        issues.append(
            _issue(
                "native_runtime_abi_mismatch",
                "target.environment.native_runtime.python_abi",
                "current Python ABI does not match the native runtime contract",
                f"build with ABI {native.python_abi}",
            )
        )
    pins, lock_issues = _dependency_lock_report(path, environment)
    issues.extend(lock_issues)
    return RuntimeCheckReport(
        model_id=asset.summary.model_id,
        target=environment,
        current=current,
        dependency_pins=tuple(sorted(pins.items())),
        issues=tuple(issues),
    )


__all__ = [
    "ACTIVE_RUNTIME_PROFILE_IDS",
    "BundleCheckReport",
    "DEFAULT_RUNTIME_PROFILE_ID",
    "RUNTIME_PROFILE_IDS",
    "RuntimeCheckReport",
    "RuntimeObservation",
    "RuntimeProfile",
    "check_build_environment",
    "get_runtime_profile",
    "verify_mlflow_bundle",
]
