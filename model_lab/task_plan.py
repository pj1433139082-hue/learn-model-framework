"""Task-scoped interaction mode and resumption record for one model workspace."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata
import yaml  # type: ignore[import-untyped]

from model_lab.scaffolding import _refuse_symlink
from model_lab.workflow_state import manifest_path


SCHEMA_VERSION = "1.0"
MODES = frozenset({"full-auto", "assisted", "manual-review"})
INTENTS = frozenset({"status", "read", "new", "maintain", "build", "publish", "data-audit"})
_TASK_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_DECISION_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_WINDOWS_RESERVED = frozenset({
    "aux", "clock$", "con", "nul", "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
})
_CREDENTIAL_TEXT = re.compile(
    r"(?i)(?:secret|password|passwd|api[ _-]?key|(?:access|auth)[_ -]?token|\btoken)[ \t]*[:=][ \t]*[^ \t]+"
    r"|bearer[ \t]+[A-Za-z0-9._~+/=-]{8,}"
    r"|https?://[^ /@:]+:[^ /@]+@"
    r"|[?&](?:token|secret|password|api[_-]?key)=[^&# \t]+"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|gh[pousr]_[A-Za-z0-9]{24,}|sk-[A-Za-z0-9]{24,}"
)


class TaskPlanError(ValueError):
    """A task-plan request or stored record is invalid or unsafe."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _task_id(value: object) -> str:
    if not isinstance(value, str) or not _TASK_ID.fullmatch(value) or value.endswith("."):
        raise TaskPlanError("task_id_invalid", "task_id must be a lowercase filesystem-safe identifier")
    if value.split(".", 1)[0] in _WINDOWS_RESERVED:
        raise TaskPlanError("task_id_invalid", "task_id is reserved by the local filesystem")
    return value


def _plain_text(value: object, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskPlanError("task_plan_invalid", f"{field} must be a non-empty string")
    cleaned = value.strip()
    if len(cleaned) > maximum or any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
        for character in cleaned
    ):
        raise TaskPlanError("task_plan_invalid", f"{field} is too long or contains control characters")
    if field == "next_action" and _CREDENTIAL_TEXT.search(cleaned):
        raise TaskPlanError(
            "task_plan_sensitive_text",
            "next_action must not embed credentials or credential-bearing URLs",
        )
    return cleaned


def _canonical_workspace(value: str | Path) -> Path:
    from model_lab.environment import ensure_external_workspace

    try:
        _refuse_symlink(Path(value), "model workspace")
        manifest = manifest_path(value)
        workspace = ensure_external_workspace(manifest.parent)
        _refuse_symlink(workspace, "model workspace")
        return workspace
    except (OSError, yaml.YAMLError) as error:
        raise TaskPlanError("task_workspace_invalid", "model workspace cannot be read safely") from error


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TaskPlanError("task_plan_invalid", f"task plan contains duplicate key: {key}")
        result[key] = value
    return result


def _model_identity(workspace: Path) -> dict[str, str]:
    manifest = manifest_path(workspace)
    try:
        document = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise TaskPlanError("task_workspace_invalid", "model manifest cannot be read safely") from error
    model = document.get("model") if isinstance(document, Mapping) else None
    if not isinstance(model, Mapping):
        raise TaskPlanError("task_workspace_invalid", "model manifest identity is missing")
    model_id, version = model.get("id"), model.get("version")
    if not isinstance(model_id, str) or not model_id.strip() or not isinstance(version, str) or not version.strip():
        raise TaskPlanError("task_workspace_invalid", "model manifest ID and version are required")
    return {"id": model_id, "version": version}


def _plan_directories(workspace: Path, *, create: bool) -> tuple[Path, Path]:
    metadata = workspace / ".model-lab"
    tasks = metadata / "tasks"
    for path, label in ((workspace, "model workspace"), (metadata, "task metadata directory"), (tasks, "task directory")):
        _refuse_symlink(path, label)
    if create:
        try:
            metadata.mkdir(exist_ok=True)
            _refuse_symlink(metadata, "task metadata directory")
            if not metadata.is_dir():
                raise TaskPlanError("task_plan_path_unsafe", "task metadata path is not a directory")
            tasks.mkdir(exist_ok=True)
            _refuse_symlink(tasks, "task directory")
        except OSError as error:
            raise TaskPlanError("task_plan_write_failed", "task directory could not be created") from error
    elif metadata.exists() and not metadata.is_dir() or tasks.exists() and not tasks.is_dir():
        raise TaskPlanError("task_plan_path_unsafe", "task metadata path is not a directory")
    return metadata, tasks


def _validated_record(value: object, task_id: str, identity: dict[str, str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "task_id", "mode", "intent", "next_action",
        "pending_decision_ids", "model", "framework",
    }:
        raise TaskPlanError("task_plan_invalid", "task plan has an unsupported schema")
    if value.get("schema_version") != SCHEMA_VERSION or value.get("task_id") != task_id:
        raise TaskPlanError("task_plan_invalid", "task plan schema or task ID does not match")
    if (
        not isinstance(value.get("mode"), str)
        or value.get("mode") not in MODES
        or not isinstance(value.get("intent"), str)
        or value.get("intent") not in INTENTS
    ):
        raise TaskPlanError("task_plan_invalid", "task plan mode or intent is unsupported")
    _plain_text(value.get("next_action"), "next_action", maximum=500)
    pending = value.get("pending_decision_ids")
    if not isinstance(pending, list) or any(
        not isinstance(item, str) or not _DECISION_ID.fullmatch(item) for item in pending
    ) or len(pending) != len(set(pending)):
        raise TaskPlanError("task_plan_invalid", "pending_decision_ids must contain unique safe IDs")
    model = value.get("model")
    if not isinstance(model, dict) or set(model) != {"id", "version"} or model != identity:
        raise TaskPlanError("task_plan_stale", "task plan belongs to a different model ID or version")
    framework = value.get("framework")
    if not isinstance(framework, dict) or set(framework) != {"commit", "version", "dirty"}:
        raise TaskPlanError("task_plan_invalid", "task plan framework identity is invalid")
    if framework["commit"] is not None and not isinstance(framework["commit"], str):
        raise TaskPlanError("task_plan_invalid", "task plan framework commit is invalid")
    if not isinstance(framework["version"], str) or not framework["version"]:
        raise TaskPlanError("task_plan_invalid", "task plan framework version is invalid")
    if not isinstance(framework["dirty"], bool):
        raise TaskPlanError("task_plan_invalid", "task plan framework dirty state is invalid")
    return value


def read_task_plan(workspace: str | Path, task_id: str) -> dict[str, object] | None:
    """Read one task record without creating directories or files."""

    safe_task_id = _task_id(task_id)
    canonical = _canonical_workspace(workspace)
    identity = _model_identity(canonical)
    _, tasks = _plan_directories(canonical, create=False)
    if not tasks.exists():
        return None
    target = tasks / f"{safe_task_id}.json"
    _refuse_symlink(target, "task plan")
    if not target.exists():
        return None
    if not target.is_file() or target.stat().st_nlink > 1:
        raise TaskPlanError("task_plan_path_unsafe", "task plan must be a regular single-link file")
    try:
        value = json.loads(
            target.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TaskPlanError("task_plan_invalid", "task plan cannot be read as JSON") from error
    return _validated_record(value, safe_task_id, identity)


def write_task_plan(
    workspace: str | Path,
    task_id: str,
    *,
    mode: str,
    intent: str,
    next_action: str,
    pending_decisions: Iterable[str] = (),
) -> Path:
    """Atomically persist only resumable task intent and interaction state."""

    safe_task_id = _task_id(task_id)
    if not isinstance(mode, str) or mode not in MODES:
        raise TaskPlanError("task_mode_invalid", "mode must be full-auto, assisted, or manual-review")
    if not isinstance(intent, str) or intent not in INTENTS:
        raise TaskPlanError("task_intent_invalid", "intent is not a supported model-lab task")
    cleaned_action = _plain_text(next_action, "next_action", maximum=500)
    if isinstance(pending_decisions, (str, bytes)):
        raise TaskPlanError("task_plan_invalid", "pending_decisions must be a collection of IDs, not text")
    try:
        pending = list(pending_decisions)
    except TypeError as error:
        raise TaskPlanError("task_plan_invalid", "pending_decisions must be an iterable of IDs") from error
    if any(not isinstance(item, str) or not _DECISION_ID.fullmatch(item) for item in pending):
        raise TaskPlanError("task_plan_invalid", "pending_decisions must contain safe decision IDs")
    if len(pending) != len(set(pending)):
        raise TaskPlanError("task_plan_invalid", "pending_decisions must not contain duplicates")

    canonical = _canonical_workspace(workspace)
    identity = _model_identity(canonical)
    from model_lab.environment import framework_identity

    environment = framework_identity()
    framework_version = environment.get("version")
    framework_commit = environment.get("git_commit")
    framework_dirty = environment.get("git_dirty")
    if not isinstance(framework_version, str) or not framework_version:
        raise TaskPlanError("task_plan_invalid", "framework version is unavailable")
    if framework_commit is not None and not isinstance(framework_commit, str):
        raise TaskPlanError("task_plan_invalid", "framework commit is invalid")
    if not isinstance(framework_dirty, bool):
        raise TaskPlanError("task_plan_invalid", "framework dirty state is unavailable")
    record: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "task_id": safe_task_id,
        "mode": mode,
        "intent": intent,
        "next_action": cleaned_action,
        "pending_decision_ids": sorted(pending),
        "model": identity,
        "framework": {
            "commit": framework_commit,
            "version": framework_version,
            "dirty": framework_dirty,
        },
    }
    _validated_record(record, safe_task_id, identity)

    _, tasks = _plan_directories(canonical, create=True)
    destination = tasks / f"{safe_task_id}.json"
    _refuse_symlink(destination, "task plan")
    if destination.exists() and (not destination.is_file() or destination.stat().st_nlink > 1):
        raise TaskPlanError("task_plan_path_unsafe", "existing task plan is not a regular single-link file")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=tasks,
            prefix=f".{safe_task_id}-", suffix=".tmp", delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            output.flush()
            os.fsync(output.fileno())
        _refuse_symlink(destination, "task plan")
        _refuse_symlink(temporary, "temporary task plan")
        os.replace(temporary, destination)
    except (OSError, ValueError) as error:
        raise TaskPlanError("task_plan_write_failed", "task plan could not be written atomically") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


__all__ = ["TaskPlanError", "read_task_plan", "write_task_plan"]
