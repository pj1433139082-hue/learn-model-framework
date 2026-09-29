"""Read-only Codex hook: restore a session's model focus and route minimal docs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any

import yaml  # type: ignore[import-untyped]

from model_lab.workflow_state import model_manifests, task_context


def _session_file(repository_root: Path, session_id: str, cache_root: Path | None) -> Path:
    root = cache_root or (Path(tempfile.gettempdir()) / "model-lab-codex-sessions")
    repository_key = hashlib.sha256(str(repository_root.resolve()).encode("utf-8")).hexdigest()[:20]
    session_key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return root / repository_key / f"{session_key}.json"


def _models(repository_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    configured = os.environ.get("MODEL_LAB_MODELS_ROOT")
    if not configured:
        return entries
    models_root = Path(configured).expanduser().resolve()
    if models_root == repository_root or models_root.is_relative_to(repository_root):
        return entries
    for path in model_manifests(models_root):
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            model = document["model"]
            model_id, version = model["id"], model["version"]
            if isinstance(model_id, str) and isinstance(version, str):
                aliases: set[str] = set()
                platform = document.get("platform")
                if isinstance(platform, dict) and isinstance(platform.get("experiment"), str):
                    aliases.add(platform["experiment"])
                receipts = path.parent / "validation" / "publications"
                if receipts.is_dir():
                    for receipt_path in receipts.glob("*.json"):
                        try:
                            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                            if (receipt.get("model_id") == model_id
                                    and receipt.get("model_version") == version
                                    and isinstance(receipt.get("experiment"), str)):
                                aliases.add(receipt["experiment"])
                        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
                            pass
                entries.append({"id": model_id, "version": version, "path": str(path), "aliases": sorted(aliases)})
        except (OSError, UnicodeError, yaml.YAMLError, KeyError, TypeError):
            continue
    return entries


def _mentioned(prompt: str, entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    alias_matches = list({entry["path"]: entry for entry in entries for alias in entry["aliases"]
                          if re.search(rf"(?<![A-Za-z0-9_-]){re.escape(alias)}(?![A-Za-z0-9_-])", prompt, re.IGNORECASE)}.values())
    if alias_matches:
        return alias_matches, True
    candidates: list[dict[str, Any]] = []
    for entry in entries:
        model_id = entry["id"]
        if re.search(rf"(?<![A-Za-z0-9-]){re.escape(model_id)}(?![A-Za-z0-9-])", prompt, re.IGNORECASE):
            candidates.append(entry)
    if not candidates:
        return [], False
    version_tokens = set(re.findall(r"(?<![0-9])\d+\.\d+\.\d+(?![0-9])", prompt))
    if version_tokens:
        candidates = [entry for entry in candidates if entry["version"] in version_tokens]
    return candidates, True


def _intent(prompt: str) -> str:
    if re.search(r"发布|上传|publish|MLflow", prompt, re.IGNORECASE):
        return "publish"
    if re.search(r"维护|补丁|迁移|maintain", prompt, re.IGNORECASE):
        return "maintain"
    if re.search(r"构建|验证|测试|build|test", prompt, re.IGNORECASE):
        return "build"
    if re.search(r"新建|创建模型|new model", prompt, re.IGNORECASE):
        return "new"
    return "status"


def handle_event(event: dict[str, Any], repository_root: Path, *, cache_root: Path | None = None) -> dict[str, object]:
    name = event.get("hook_event_name")
    if name not in {"SessionStart", "UserPromptSubmit"}:
        return {}
    session_id = event.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return {}
    cache = _session_file(repository_root, session_id, cache_root)
    saved: dict[str, str] = {}
    pending_key: str | None = None
    if cache.is_file():
        try:
            raw = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                if isinstance(raw.get("id"), str) and isinstance(raw.get("version"), str):
                    saved = {"id": raw["id"], "version": raw["version"]}
                if isinstance(raw.get("pending"), str):
                    pending_key = raw["pending"]
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    entries = _models(repository_root)
    selected: dict[str, Any] | None = None
    prompt = event.get("prompt", "") if name == "UserPromptSubmit" else ""
    if not isinstance(prompt, str):
        prompt = ""
    mentioned, named_known_model = _mentioned(prompt, entries)
    named_unknown_experiment = re.search(r"\bMLModel-\d+_V\d+\b", prompt, re.IGNORECASE) is not None
    if len(mentioned) == 1:
        selected = mentioned[0]
    elif len(mentioned) > 1 and saved:
        matching_saved = [entry for entry in mentioned if entry["id"] == saved["id"] and entry["version"] == saved["version"]]
        if len(matching_saved) == 1 and len({entry["id"] for entry in mentioned}) == 1:
            selected = matching_saved[0]
    elif not named_known_model and not named_unknown_experiment and saved:
        selected = next((entry for entry in entries if entry["id"] == saved["id"] and entry["version"] == saved["version"]), None)
    if selected is None:
        if named_known_model or named_unknown_experiment:
            key_text = "|".join(sorted(f"{item['id']}@{item['version']}" for item in mentioned))
            if named_unknown_experiment:
                key_text += "|" + "|".join(sorted(re.findall(r"MLModel-\d+_V\d+", prompt, re.IGNORECASE)))
            current_pending = hashlib.sha256(key_text.encode("utf-8")).hexdigest()
            if pending_key == current_pending:
                detail = "本会话已提示过同一模型版本歧义；不要重复提问。若用户尚未回答，只做不依赖版本的只读工作。"
            else:
                detail = "模型版本未唯一定位；请向用户只问一次准确的模型 ID 与版本，不要猜或设置仓库级当前模型。"
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps({"pending": current_pending}), encoding="utf-8")
        elif pending_key:
            detail = "本会话的模型版本仍未唯一定位；不要重复询问，先做不依赖版本的只读工作。"
        else:
            detail = "尚无唯一模型版本；框架任务先读 AGENTS.md 与 docs/00-入口层/00-模型开发总览.md。若任务必须定位模型，只问一次 ID 与版本。"
    else:
        if saved != {"id": selected["id"], "version": selected["version"]}:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"id": selected["id"], "version": selected["version"]}), encoding="utf-8")
        context = task_context(selected["path"], task=_intent(prompt))
        snapshot = context["snapshot"]
        assert isinstance(snapshot, dict)
        model = snapshot["model"]
        publication = snapshot["publication"]
        assert isinstance(model, dict) and isinstance(publication, dict)
        docs = ", ".join(str(item) for item in context["docs"])
        drift = "快照漂移：运行 model-lab context <工作区> --write；hook 不会自动改文件。" if not context["snapshot_in_sync"] else "快照一致。"
        detail = (
            f"本会话焦点：{selected['id']} {selected['version']}；本地阶段={model['lifecycle_state']}；"
            f"仓库发布记录={publication['status']}（不是实时平台状态）。{drift} "
            f"按本任务最小加载：{docs}。具体事实仍读该工作区 model.yaml、decisions.yaml、input-bindings.yaml 及回执。"
        )
    return {"hookSpecificOutput": {"hookEventName": name, "additionalContext": detail}}


def main() -> int:
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            return 0
        repository_root = Path(__file__).resolve().parents[1]
        result = handle_event(event, repository_root)
        if result:
            print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        # Advisory hook failures must never block the AGENTS.md + CLI fallback.
        print(f"model-lab hook unavailable: {type(error).__name__}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
