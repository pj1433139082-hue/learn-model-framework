"""Local framework identity and private workspace path configuration.

This module deliberately keeps user-specific paths in an ignored, local JSON
file. It never creates a model workspace and never reads model contents.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from functools import lru_cache
from typing import Any

from model_lab import __version__
from model_lab.path_safety import (
    first_link_or_junction_component,
    is_link_or_junction,
)


LOCAL_SETTINGS_NAME = ".model-lab.local.json"
_ALLOWED_SETTINGS_KEYS = frozenset({"version", "models_root", "company_iot_zip"})
_NETWORK_FILESYSTEMS = frozenset(
    {
        "9p",
        "afs",
        "cifs",
        "ceph",
        "davfs",
        "glusterfs",
        "gpfs",
        "lustre",
        "nfs",
        "nfs4",
        "smb3",
    }
)


class EnvironmentError(ValueError):
    """Unsafe or invalid machine-local model-lab configuration."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def framework_root() -> Path:
    """Return the source checkout that owns the imported ``model_lab`` package."""

    return Path(__file__).resolve().parents[1]


def _git_value(root: Path, *arguments: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _git_dirty(root: Path) -> bool:
    """Return dirty when Git cannot prove that the source tree is clean."""

    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "status",
                "--porcelain=v1",
                "--untracked-files=normal",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return result.returncode != 0 or bool(result.stdout.strip())


def framework_identity() -> dict[str, object]:
    """Return JSON-safe package, source, and current Git identity facts."""

    root = framework_root()
    return {
        "version": __version__,
        "root": str(root),
        "module": str(Path(__file__).resolve()),
        "git_commit": _git_value(root, "rev-parse", "HEAD"),
        "git_branch": _git_value(root, "symbolic-ref", "--short", "-q", "HEAD"),
        "git_dirty": _git_dirty(root),
    }


def _duplicate_checked_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EnvironmentError(
                "local_settings_invalid", f"duplicate local setting: {key}"
            )
        result[key] = value
    return result


@lru_cache(maxsize=32)
def _git_root(root: Path) -> Path | None:
    value = _git_value(root, "rev-parse", "--show-toplevel")
    return Path(value).resolve() if value else None


def _git_path_state(root: Path, relative_path: str) -> tuple[bool, bool]:
    """Return (tracked, ignored) for a path relative to *root* when in Git."""

    if _git_root(root) is None:
        return False, False
    tracked = False
    ignored = False
    try:
        tracked_result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--error-unmatch",
                "--",
                relative_path,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=3,
        )
        tracked = tracked_result.returncode == 0
        ignored_result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "check-ignore",
                "--quiet",
                "--no-index",
                "--",
                relative_path,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=3,
        )
        ignored = ignored_result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False, False
    return tracked, ignored


def _assert_ignored_private_file(root: Path, relative_path: str) -> None:
    tracked, ignored = _git_path_state(root, relative_path)
    if tracked:
        raise EnvironmentError(
            "local_settings_tracked",
            f"refusing to write user-specific data to tracked path: {relative_path}",
        )
    if not ignored:
        raise EnvironmentError(
            "local_settings_not_ignored",
            f"refusing to write user-specific data because Git does not ignore {relative_path}",
        )


def _mount_type(path: Path) -> str | None:
    """Find the Linux mount type containing *path*, if mountinfo is available."""

    if not sys.platform.startswith("linux"):
        return None
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    resolved = path.resolve(strict=False)
    matches: list[tuple[int, str]] = []
    for line in lines:
        left, separator, right = line.partition(" - ")
        if not separator:
            continue
        fields = left.split()
        filesystem_fields = right.split()
        if len(fields) < 5 or not filesystem_fields:
            continue
        mountpoint_text = re.sub(
            r"\\([0-7]{3})",
            lambda match: chr(int(match.group(1), 8)),
            fields[4],
        )
        mountpoint = Path(mountpoint_text)
        try:
            if resolved == mountpoint or resolved.is_relative_to(mountpoint):
                matches.append((len(mountpoint.parts), filesystem_fields[0].casefold()))
        except (OSError, ValueError):
            continue
    return max(matches)[1] if matches else None


def _reject_nonlocal_path(path: Path, raw: str) -> None:
    normalized = raw.replace("\\", "/")
    if "\0" in raw or normalized.startswith("//"):
        raise EnvironmentError(
            "unsafe_workspace_path",
            "workspace paths must use a local filesystem path, not a network or device namespace",
        )

    if os.name == "nt":
        if path.drive and not path.is_absolute():
            raise EnvironmentError(
                "unsafe_workspace_path",
                "drive-relative workspace paths are not allowed",
            )
        anchor = path.anchor
        if anchor:
            try:
                import ctypes

                drive_type = int(ctypes.windll.kernel32.GetDriveTypeW(anchor))  # type: ignore[attr-defined]
            except (AttributeError, OSError):
                drive_type = 0
            if drive_type in {0, 1, 4}:
                raise EnvironmentError(
                    "unsafe_workspace_path",
                    "workspace must be on an available local filesystem",
                )
    elif _mount_type(path) in _NETWORK_FILESYSTEMS:
        raise EnvironmentError(
            "unsafe_workspace_path", "workspace must not be on a network filesystem"
        )


def _absolute_path_without_links(path: str | Path) -> Path:
    raw_value = os.fspath(path)
    if not isinstance(raw_value, str):
        raise EnvironmentError("unsafe_workspace_path", "workspace path must be text")
    try:
        expanded = Path(raw_value).expanduser()
    except (OSError, RuntimeError) as error:
        raise EnvironmentError(
            "unsafe_workspace_path", "workspace path cannot be expanded safely"
        ) from error
    absolute = expanded if expanded.is_absolute() else Path.cwd() / expanded
    _reject_nonlocal_path(absolute, raw_value)

    component = first_link_or_junction_component(absolute)
    if component is not None:
        raise EnvironmentError(
            "unsafe_workspace_path",
            f"workspace path must not pass through a link or junction: {component}",
        )
    normalized = Path(os.path.abspath(absolute))
    if is_link_or_junction(normalized):
        raise EnvironmentError(
            "unsafe_workspace_path",
            f"workspace path must not be a link or junction: {normalized}",
        )
    return normalized


def _is_same_or_child(path: Path, parent: Path) -> bool:
    try:
        return path == parent or path.is_relative_to(parent)
    except (OSError, ValueError):
        return False


def _ensure_outside_framework(path: Path) -> Path:
    canonical = path.resolve(strict=False)
    framework = framework_root().resolve(strict=False)
    if _is_same_or_child(canonical, framework):
        raise EnvironmentError(
            "models_root_inside_framework",
            "private model workspaces must be outside the framework checkout",
        )
    return canonical


def ensure_external_workspace(path: str | Path) -> Path:
    """Validate a local workspace or ``model.yaml`` and return its directory.

    Nonexistent destination directories are allowed so callers can validate a
    future workspace path without creating it. Existing path components may not
    be symlinks/junctions or lie on a known network filesystem.
    """

    candidate = _absolute_path_without_links(path)
    if candidate.name.casefold() == "model.yaml":
        if candidate.exists() and (
            not candidate.is_file() or is_link_or_junction(candidate)
        ):
            raise EnvironmentError(
                "unsafe_workspace_path", "model.yaml must be a regular local file"
            )
        workspace = candidate.parent
    else:
        if candidate.exists() and not candidate.is_dir():
            raise EnvironmentError(
                "unsafe_workspace_path",
                "workspace must be a directory or a model.yaml path",
            )
        workspace = candidate

    return _ensure_outside_framework(workspace)


def read_local_settings(root: Path | None = None) -> dict[str, object]:
    """Read and validate the ignored local configuration, or return ``{}``."""

    base = (root or framework_root()).resolve(strict=False)
    path = base / LOCAL_SETTINGS_NAME
    if not path.exists() and not is_link_or_junction(path):
        return {}
    if is_link_or_junction(path) or not path.is_file():
        raise EnvironmentError(
            "local_settings_unsafe", "local settings must be a regular file, not a link"
        )
    tracked, ignored = _git_path_state(base, LOCAL_SETTINGS_NAME)
    if tracked or (ignored is False and _git_root(base) is not None):
        raise EnvironmentError(
            "local_settings_not_private",
            "local settings must remain untracked and Git-ignored",
        )
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_duplicate_checked_object,
        )
    except EnvironmentError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EnvironmentError(
            "local_settings_invalid", "local settings are unreadable or invalid JSON"
        ) from error
    if not isinstance(document, dict) or set(document) - _ALLOWED_SETTINGS_KEYS:
        raise EnvironmentError(
            "local_settings_invalid",
            "local settings allow only version, models_root, and company_iot_zip",
        )
    if type(document.get("version")) is not int or document["version"] != 1:
        raise EnvironmentError(
            "local_settings_invalid", "local settings version must be integer 1"
        )
    if (
        not isinstance(document.get("models_root"), str)
        or not document["models_root"].strip()
    ):
        raise EnvironmentError(
            "local_settings_invalid", "local settings require a non-empty models_root"
        )

    result: dict[str, object] = {"version": 1}
    result["models_root"] = str(ensure_external_workspace(document["models_root"]))
    if "company_iot_zip" in document:
        zip_path = document["company_iot_zip"]
        if not isinstance(zip_path, str) or not zip_path.strip():
            raise EnvironmentError(
                "local_settings_invalid", "company_iot_zip must be a non-empty path"
            )
        external_zip = _absolute_path_without_links(zip_path)
        if external_zip.exists() and not external_zip.is_file():
            raise EnvironmentError(
                "local_settings_invalid", "company_iot_zip must point to a regular file"
            )
        result["company_iot_zip"] = str(_ensure_outside_framework(external_zip))
    return result


def configured_models_root(value: str | None = None) -> Path:
    """Resolve the models root by flag, environment, then ignored local config."""

    selected: str | None
    if value is not None:
        selected = value
        source = "--models-root"
    elif "MODEL_LAB_MODELS_ROOT" in os.environ:
        selected = os.environ["MODEL_LAB_MODELS_ROOT"]
        source = "MODEL_LAB_MODELS_ROOT"
    else:
        settings = read_local_settings()
        setting = settings.get("models_root")
        selected = setting if isinstance(setting, str) else None
        source = "local settings"

    if selected is None or not selected.strip():
        raise EnvironmentError(
            "models_root_required",
            "choose an external local models directory with --models-root or MODEL_LAB_MODELS_ROOT",
        )
    try:
        return ensure_external_workspace(selected)
    except EnvironmentError as error:
        if error.code == "models_root_inside_framework":
            raise EnvironmentError(
                error.code,
                f"{source} points inside the framework checkout; private models must stay outside it",
            ) from error
        raise


__all__ = [
    "EnvironmentError",
    "LOCAL_SETTINGS_NAME",
    "configured_models_root",
    "ensure_external_workspace",
    "framework_identity",
    "framework_root",
    "read_local_settings",
]
