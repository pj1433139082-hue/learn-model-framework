"""One-time, local-only onboarding for a model-lab framework checkout."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

from model_lab.environment import (
    EnvironmentError,
    LOCAL_SETTINGS_NAME,
    _assert_ignored_private_file,
    _git_path_state,
    configured_models_root,
    framework_identity,
    framework_root,
    read_local_settings,
)
from model_lab.path_safety import is_link_or_junction


_SETUP_STATE = Path(".model-lab") / "setup-state.json"
_SKILL_NAMES = (
    "model-build-validate",
    "model-data-audit",
    "model-lab-router",
    "model-maintenance",
    "model-requirements-grill",
    "model-runtime-release",
)


def _safe_tree_hash(directory: Path) -> str:
    if is_link_or_junction(directory) or not directory.is_dir():
        raise EnvironmentError(
            "skill_source_invalid",
            f"skill directory is missing or redirected: {directory.name}",
        )
    digest = hashlib.sha256()
    for path in sorted(
        directory.rglob("*"), key=lambda item: item.relative_to(directory).as_posix()
    ):
        if is_link_or_junction(path):
            raise EnvironmentError(
                "skill_source_invalid",
                "project skills may not contain links or junctions",
            )
        if not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix().encode("utf-8")
        try:
            content_hash = hashlib.sha256(path.read_bytes()).digest()
        except OSError as error:
            raise EnvironmentError(
                "skill_source_invalid", "project skill source cannot be read"
            ) from error
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(content_hash)
    return digest.hexdigest()


def _skill_hashes(root: Path, names: tuple[str, ...]) -> dict[str, str]:
    source_root = root / ".skillshare" / "skills"
    hashes: dict[str, str] = {}
    for name in names:
        source = source_root / name
        if (
            is_link_or_junction(source / "SKILL.md")
            or not (source / "SKILL.md").is_file()
        ):
            raise EnvironmentError(
                "skill_source_missing",
                f"project skill source is missing: {name}/SKILL.md",
            )
        hashes[name] = _safe_tree_hash(source)
    return hashes


def _skill_sync_state(root: Path, source_hashes: dict[str, str]) -> dict[str, object]:
    skillshare = shutil.which("skillshare")
    if skillshare is None:
        return {
            "status": "direct_source_available",
            "tool": None,
            "source_count": len(source_hashes),
            "source_hashes": source_hashes,
            "target_hashes": {},
            "verified": False,
        }

    target_hashes: dict[str, str] = {}
    target_root = root / ".agents" / "skills"
    for name in source_hashes:
        target = target_root / name
        if not target.is_dir() or is_link_or_junction(target):
            continue
        try:
            target_hashes[name] = _safe_tree_hash(target)
        except EnvironmentError:
            continue
    return {
        "status": "synced" if target_hashes == source_hashes else "sync_required",
        "tool": "skillshare",
        "source_count": len(source_hashes),
        "source_hashes": source_hashes,
        "target_hashes": target_hashes,
        "verified": target_hashes == source_hashes,
    }


def _hook_state(root: Path) -> dict[str, object]:
    config = root / ".codex" / "hooks.json"
    directory = root / ".codex" / "hooks"
    found = (config.is_file() and not is_link_or_junction(config)) or (
        directory.is_dir()
        and not is_link_or_junction(directory)
        and any(
            item.is_file() and not is_link_or_junction(item)
            for item in directory.iterdir()
        )
    )
    return {
        "files_present": found,
        "state": "trust_in_client_unverified" if found else "not_present",
        "enabled": None,
    }


def _atomic_json_write(root: Path, target: Path, document: dict[str, object]) -> None:
    relative = target.relative_to(root).as_posix()
    _assert_ignored_private_file(root, relative)
    if is_link_or_junction(target) or (target.exists() and not target.is_file()):
        raise EnvironmentError(
            "local_state_unsafe",
            f"refusing to replace non-regular local state: {relative}",
        )
    private_directory = root / _SETUP_STATE.parent
    if is_link_or_junction(private_directory):
        raise EnvironmentError(
            "local_state_unsafe", ".model-lab must not be a link or junction"
        )
    if private_directory.exists() and not private_directory.is_dir():
        raise EnvironmentError("local_state_unsafe", ".model-lab must be a directory")
    private_directory.mkdir(parents=False, exist_ok=True)
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="setup-", suffix=".tmp", dir=private_directory
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(
                json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, target)
        temporary = None
    except EnvironmentError:
        raise
    except OSError as error:
        raise EnvironmentError(
            "local_state_write_failed", f"could not atomically write {relative}"
        ) from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _read_setup_state(root: Path) -> dict[str, object]:
    path = root / _SETUP_STATE
    if not path.exists() and not is_link_or_junction(path):
        return {}
    if is_link_or_junction(path) or not path.is_file():
        raise EnvironmentError(
            "local_state_unsafe", "setup state must be a regular file, not a link"
        )
    tracked, ignored = _git_path_state(root, _SETUP_STATE.as_posix())
    if tracked or not ignored:
        raise EnvironmentError(
            "local_state_not_private",
            "setup state must remain untracked and Git-ignored",
        )
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EnvironmentError(
            "local_state_invalid", "setup state is invalid JSON"
        ) from error
    if not isinstance(result, dict) or result.get("version") != 1:
        raise EnvironmentError(
            "local_state_invalid", "setup state has an unsupported shape"
        )
    allowed = {"version", "last_setup_commit", "last_update", "recorded_at"}
    if set(result) - allowed:
        raise EnvironmentError(
            "local_state_invalid", "setup state contains unsupported fields"
        )
    return result


def setup_workspace(
    models_root: str | Path | None = None,
    *,
    apply: bool = False,
    update: bool = False,
) -> dict[str, object]:
    """Plan or apply machine-local configuration and project skill sync.

    The framework checkout is the only write root. Model directories and model
    facts are never created or modified by this initializer.
    """

    root = framework_root().resolve()
    identity = framework_identity()
    try:
        settings = read_local_settings(root)
    except EnvironmentError as error:
        return {
            "valid": False,
            "status": error.code,
            "next_action": str(error),
            "framework": identity,
            "error": {"code": error.code, "message": str(error)},
            "safety": {"network_access": False, "remote_writes": False},
        }

    try:
        selected_root = configured_models_root(
            str(models_root) if models_root is not None else None
        )
    except EnvironmentError as error:
        if error.code == "models_root_required":
            selected_root = None
        else:
            return {
                "valid": False,
                "status": error.code,
                "next_action": str(error),
                "framework": identity,
                "error": {"code": error.code, "message": str(error)},
                "safety": {"network_access": False, "remote_writes": False},
            }

    local_config_tracked, local_config_ignored = _git_path_state(
        root, LOCAL_SETTINGS_NAME
    )
    state_tracked, state_ignored = _git_path_state(root, _SETUP_STATE.as_posix())
    private_paths_safe = (
        not local_config_tracked
        and local_config_ignored
        and not state_tracked
        and state_ignored
    )
    try:
        source_hashes = _skill_hashes(root, _SKILL_NAMES)
        source_error: EnvironmentError | None = None
    except EnvironmentError as error:
        source_hashes = {}
        source_error = error

    skill_state = (
        _skill_sync_state(root, source_hashes)
        if source_error is None
        else {
            "status": source_error.code,
            "tool": shutil.which("skillshare") is not None,
            "source_count": len(source_hashes),
            "source_hashes": {},
            "target_hashes": {},
            "verified": False,
        }
    )
    hooks = _hook_state(root)
    framework_ok = (
        identity.get("version") is not None
        and str(identity.get("root")) == str(root)
        and sys.version_info[:2] == (3, 11)
    )
    valid = (
        selected_root is not None
        and private_paths_safe
        and source_error is None
        and framework_ok
    )

    if selected_root is None:
        status = "models_root_required"
        next_action = "Choose an external local models directory and rerun setup with --models-root."
        valid = False
    elif not private_paths_safe:
        status = "local_paths_not_ignored"
        next_action = "Ensure .model-lab.local.json and .model-lab/ are Git-ignored, then rerun setup."
        valid = False
    elif source_error is not None:
        status = source_error.code
        next_action = str(source_error)
        valid = False
    elif not framework_ok:
        status = "framework_environment_invalid"
        next_action = "Use the framework checkout with its Python 3.11 environment."
        valid = False
    elif apply:
        status = "applying"
        next_action = "Local setup is being applied."
    else:
        status = "planned"
        next_action = "Run setup with --apply to save the ignored local settings and synchronize project skills."

    result: dict[str, object] = {
        "valid": valid,
        "status": status,
        "next_action": next_action,
        "framework": identity,
        "models_root": str(selected_root) if selected_root is not None else None,
        "settings": {
            "path": str(root / LOCAL_SETTINGS_NAME),
            "git_ignored": local_config_ignored,
            "would_apply": bool(selected_root is not None and private_paths_safe),
        },
        "skills": skill_state,
        "hooks": hooks,
        "update": None,
        "safety": {"network_access": False, "remote_writes": False},
    }
    if not apply or not valid:
        if not valid and status == "planned":
            result["status"] = "not_ready"
        return result

    try:
        state = _read_setup_state(root)
        local_settings: dict[str, object] = {
            "version": 1,
            "models_root": str(selected_root),
        }
        if "company_iot_zip" in settings:
            local_settings["company_iot_zip"] = settings["company_iot_zip"]
        _atomic_json_write(root, root / LOCAL_SETTINGS_NAME, local_settings)

        if skill_state["tool"] is None:
            final_skills = {
                **skill_state,
                "status": "direct_source_available",
                "verified": False,
            }
            skills_valid = True
        else:
            skillshare = shutil.which("skillshare")
            if skillshare is None:
                final_skills = {
                    **skill_state,
                    "status": "direct_source_available",
                    "tool": None,
                }
                skills_valid = True
            else:
                try:
                    completed = subprocess.run(
                        [skillshare, "sync", "--project", "--quiet"],
                        cwd=root,
                        check=False,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=120,
                    )
                except (OSError, subprocess.SubprocessError):
                    completed = None
                verified_state = _skill_sync_state(root, source_hashes)
                skills_valid = (
                    completed is not None
                    and completed.returncode == 0
                    and verified_state["verified"] is True
                )
                final_skills = {
                    **verified_state,
                    "status": "synced_verified"
                    if skills_valid
                    else "sync_failed_source_available",
                    "tool": "skillshare",
                    "verified": skills_valid,
                }

        current_commit = identity.get("git_commit")
        previous_commit = state.get("last_setup_commit")
        old_update = state.get("last_update")
        if (
            previous_commit == current_commit
            and isinstance(old_update, dict)
            and old_update.get("current_commit") == current_commit
        ):
            # Re-running setup at the same source must not erase the last
            # actual update's recovery point with a meaningless B -> B.
            previous_commit = old_update.get("previous_commit", previous_commit)
        update_record: dict[str, object] | None = None
        if update:
            update_record = {
                "previous_commit": previous_commit,
                "current_commit": current_commit,
                "status": "complete" if skills_valid else "partial",
            }
        state_to_write: dict[str, object] = {
            "version": 1,
            "last_setup_commit": (current_commit if skills_valid else state.get("last_setup_commit")),
            "recorded_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
        }
        if isinstance(old_update, dict):
            state_to_write["last_update"] = old_update
        if update_record is not None:
            state_to_write["last_update"] = update_record
        _atomic_json_write(root, root / _SETUP_STATE, state_to_write)
    except EnvironmentError as error:
        result.update(
            {
                "valid": False,
                "status": error.code,
                "next_action": str(error),
                "error": {"code": error.code, "message": str(error)},
            }
        )
        return result

    result.update(
        {
            "valid": skills_valid,
            "status": "ready" if skills_valid else "partial",
            "next_action": (
                "Run model-lab doctor and the local test profile."
                if skills_valid
                else "Project skill synchronization did not verify; inspect Skillshare, then rerun setup."
            ),
            "skills": final_skills,
            "update": update_record,
        }
    )
    return result


__all__ = ["setup_workspace"]
