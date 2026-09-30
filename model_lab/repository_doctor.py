from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import os
import platform
import struct
import sys
import tomllib
from zipfile import BadZipFile, ZipFile

from model_lab.environment import framework_identity, read_local_settings
from model_lab.path_safety import is_link_or_junction


_ACTIVE_TEXT_SUFFIXES = frozenset(
    {".py", ".json", ".yaml", ".yml", ".toml", ".md", ".ipynb"}
)
_ACTIVE_ROOTS = (
    Path("model_lab"),
    Path(".skillshare/skills"),
)
_LEGACY_PYTHON_SUFFIX = "3" + "12"
_LEGACY_MODULE_NAME = "mlops_deploy_iot_armor2_" + _LEGACY_PYTHON_SUFFIX
_LEGACY_ENVIRONMENT_NAME = ".venv-" + _LEGACY_PYTHON_SUFFIX
_LEGACY_MARKERS = (_LEGACY_MODULE_NAME, _LEGACY_ENVIRONMENT_NAME)


@dataclass(frozen=True)
class DoctorObservation:
    python: str
    executable: str

    @classmethod
    def current(cls) -> DoctorObservation:
        return cls(
            python=".".join(str(part) for part in sys.version_info[:3]),
            executable=sys.executable,
        )

    @property
    def minor(self) -> str:
        parts = self.python.split(".")
        return ".".join(parts[:2]) if len(parts) >= 2 else self.python


@dataclass(frozen=True)
class DoctorCheck:
    code: str
    passed: bool
    message: str
    required: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "passed": self.passed,
            "required": self.required,
            "message": self.message,
        }


@dataclass(frozen=True)
class DoctorReport:
    repository_root: str
    observation: DoctorObservation
    checks: tuple[DoctorCheck, ...]
    framework: dict[str, object]
    deployment: dict[str, object]

    @property
    def valid(self) -> bool:
        return all(check.passed for check in self.checks if check.required)

    @property
    def deployment_capability_ready(self) -> bool:
        capability = next(
            check
            for check in self.checks
            if check.code == "deployment_capability_missing"
        )
        return capability.passed

    def as_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "framework_ready": self.valid,
            "deployment_capability_ready": self.deployment_capability_ready,
            "repository_root": self.repository_root,
            "python": {
                "version": self.observation.python,
                "minor": self.observation.minor,
                "executable": self.observation.executable,
            },
            "checks": [check.as_dict() for check in self.checks],
            "framework": self.framework,
            "deployment": self.deployment,
            "safety": {"network_access": False, "remote_writes": False},
        }

    def as_text(self) -> str:
        state = "READY" if self.valid else "NOT READY"
        lines = [
            f"model-lab doctor: {state}",
            f"Python: {self.observation.python}",
            f"Interpreter: {self.observation.executable}",
            f"Framework source: {self.framework['module']}",
            f"Framework: {self.framework['version']} | {self.framework['git_commit'] or 'unknown commit'} | dirty={self.framework.get('git_dirty')}",
        ]
        for check in self.checks:
            marker = "OK" if check.passed else ("ERROR" if check.required else "INFO")
            lines.append(f"[{marker}] {check.message}")
        if not self.deployment_capability_ready:
            lines.append(
                "Company archive check is optional for framework use; packaging and "
                "deployment check the model's archive and target separately."
            )
        return "\n".join(lines)


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _active_legacy_references(root: Path) -> tuple[str, ...]:
    matches: list[str] = []
    for relative_root in _ACTIVE_ROOTS:
        active_root = root / relative_root
        if not active_root.is_dir():
            continue
        for path in sorted(active_root.rglob("*")):
            if (
                not path.is_file()
                or path.suffix.casefold() not in _ACTIVE_TEXT_SUFFIXES
            ):
                continue
            content = _read_text(path)
            if content is None:
                continue
            if any(marker in content for marker in _LEGACY_MARKERS):
                matches.append(path.relative_to(root).as_posix())
    return tuple(matches)


def _external_capability(
    root: Path, current: DoctorObservation, configured: str | Path | None,
    target_platform: str | None,
) -> tuple[bool, str, dict[str, object]]:
    operating_system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in {"amd64", "x64", "x86_64"}:
        machine = "x86_64"
    target = target_platform or f"{operating_system}_{machine}"
    state: dict[str, object] = {"status": "not_configured", "target_platform": target,
                                "network_access": False, "import_executed": False}
    if configured is None:
        try:
            configured = os.environ.get("MODEL_LAB_COMPANY_IOT_ZIP") or read_local_settings(root).get("company_iot_zip")
        except (OSError, ValueError):
            state["status"] = "configuration_invalid"
            return False, "private local configuration could not be read", state
    if not configured:
        return False, "company archive not configured; framework use remains available", state
    archive = Path(configured).expanduser().absolute()
    state["status"] = "invalid"
    state["archive"] = str(archive)
    suffix = ".so" if target == "linux_x86_64" else ".pyd" if target == "windows_x86_64" else None
    if current.minor != "3.11" or suffix is None:
        return False, "company archive check requires Python 3.11 and a supported target platform", state
    if (archive.resolve().is_relative_to(root) or not archive.is_file()
            or any(is_link_or_junction(p) for p in (archive, *archive.parents))
            or archive.stat().st_size > 16 * 1024 * 1024):
        return False, "company archive must be a bounded regular file outside the framework", state
    prefix = "infra_model_deploy/resources/infra/311/"
    entries = (prefix + "mlops_deploy_iot_armor2_311.py",
               prefix + "pyarmor_runtime_000000/__init__.py",
               prefix + f"pyarmor_runtime_000000/{target}/pyarmor_runtime{suffix}")
    try:
        with ZipFile(archive) as bundle:
            names = bundle.namelist()
            if len(names) != len(set(names)):
                return False, "company archive contains duplicate entries", state
            contents: list[bytes] = []
            for name in entries:
                info = bundle.getinfo(name)
                if info.is_dir() or info.file_size > 2 * 1024 * 1024:
                    return False, "company archive component is invalid or too large", state
                contents.append(bundle.read(info))
    except (BadZipFile, OSError, KeyError, RuntimeError, ValueError):
        return False, "company archive is invalid or lacks the requested target runtime", state
    native = contents[-1]
    if target == "linux_x86_64":
        native_valid = (len(native) >= 20 and native[:6] == b"\x7fELF\x02\x01"
                        and struct.unpack_from("<H", native, 18)[0] == 62)
    else:
        native_valid = native.startswith(b"MZ")
    if not native_valid:
        return False, "company archive native file does not match the requested platform format", state
    state.update(status="files_verified", archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    return True, "external archive files match the target; model digests, import and connectivity still need model checks", state


def run_repository_doctor(
    repository_root: str | Path,
    *,
    observation: DoctorObservation | None = None,
    company_package: str | Path | None = None,
    target_platform: str | None = None,
) -> DoctorReport:
    root = Path(repository_root).resolve()
    current = observation or DoctorObservation.current()
    checks: list[DoctorCheck] = []

    checks.append(
        DoctorCheck(
            "python_minor_mismatch",
            current.minor == "3.11",
            (
                "current interpreter is Python 3.11"
                if current.minor == "3.11"
                else f"current interpreter is Python {current.minor}; use Python 3.11"
            ),
        )
    )
    executable_uses_legacy = _LEGACY_ENVIRONMENT_NAME in current.executable.replace(
        "\\", "/"
    )
    checks.append(
        DoctorCheck(
            "legacy_interpreter_selected",
            not executable_uses_legacy,
            (
                f"selected interpreter is not the legacy {_LEGACY_ENVIRONMENT_NAME}"
                if not executable_uses_legacy
                else f"selected interpreter comes from {_LEGACY_ENVIRONMENT_NAME}"
            ),
        )
    )

    python_version = _read_text(root / ".python-version")
    checks.append(
        DoctorCheck(
            "python_version_contract",
            python_version is not None and python_version.strip() == "3.11",
            "repository .python-version declares Python 3.11",
        )
    )

    pyproject_text = _read_text(root / "pyproject.toml")
    pyproject_ok = False
    if pyproject_text is not None:
        try:
            project = tomllib.loads(pyproject_text).get("project", {})
            pyproject_ok = project.get("requires-python") == ">=3.11,<3.12"
        except (tomllib.TOMLDecodeError, AttributeError):
            pyproject_ok = False
    checks.append(
        DoctorCheck(
            "project_python_contract",
            pyproject_ok,
            "pyproject.toml requires Python >=3.11,<3.12",
        )
    )

    editor_text = _read_text(root / ".vscode" / "settings.json")
    editor_ok = (
        editor_text is not None
        and ".venv-311" in editor_text
        and _LEGACY_ENVIRONMENT_NAME not in editor_text
    )
    checks.append(
        DoctorCheck(
            "editor_interpreter_mismatch",
            editor_ok,
            "VS Code selects the project .venv-311 interpreter",
        )
    )

    legacy_environment = root / _LEGACY_ENVIRONMENT_NAME
    checks.append(
        DoctorCheck(
            "legacy_environment_present",
            not legacy_environment.exists(),
            (
                f"legacy {_LEGACY_ENVIRONMENT_NAME} is absent from the active project"
                if not legacy_environment.exists()
                else f"legacy {_LEGACY_ENVIRONMENT_NAME} is still present in the active project"
            ),
        )
    )
    build_directory = root / "build"
    checks.append(
        DoctorCheck(
            "stale_build_present",
            not build_directory.exists(),
            (
                "stale build directory is absent"
                if not build_directory.exists()
                else "stale build directory is present and may contain Python 3.12 output"
            ),
        )
    )

    legacy_module = root / "framework" / f"{_LEGACY_MODULE_NAME}.py"
    checks.append(
        DoctorCheck(
            "legacy_runtime_present",
            not legacy_module.exists(),
            (
                "legacy Python 3.12 deployment module is absent from the active framework"
                if not legacy_module.exists()
                else "legacy Python 3.12 deployment module is present in the active framework"
            ),
        )
    )

    legacy_references = _active_legacy_references(root)
    checks.append(
        DoctorCheck(
            "active_python312_reference",
            not legacy_references,
            (
                "active framework and new-model paths contain no Python 3.12 entrypoints"
                if not legacy_references
                else "active Python 3.12 references: "
                + ", ".join(legacy_references[:5])
            ),
        )
    )

    capability_ready, capability_message, deployment = _external_capability(
        root, current, company_package, target_platform,
    )
    checks.append(
        DoctorCheck(
            "deployment_capability_missing",
            capability_ready,
            capability_message,
            required=False,
        )
    )

    return DoctorReport(
        repository_root=str(root),
        observation=current,
        checks=tuple(checks),
        framework=framework_identity(),
        deployment=deployment,
    )


__all__ = [
    "DoctorCheck",
    "DoctorObservation",
    "DoctorReport",
    "run_repository_doctor",
]
