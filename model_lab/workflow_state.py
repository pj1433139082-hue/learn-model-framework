"""Deterministic, repository-owned view of one model version's workflow facts.

The snapshot is a generated index, not a second editable source of truth and not
a live query of the company's platform.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

import yaml  # type: ignore[import-untyped]

from model_lab.decisions import load_decision_register
from model_lab.path_safety import (
    first_link_or_junction_component,
    is_link_or_junction,
)


SNAPSHOT_NAME = "workflow-state.json"
_RUN_ID = re.compile(r"[0-9a-f]{32}")
_POINT_METADATA = ("facility_name", "equipment_name", "acquisition_signal_name")


def manifest_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_dir():
        candidate /= "model.yaml"
    if candidate.name != "model.yaml" or is_link_or_junction(candidate):
        raise ValueError("select a regular model.yaml or its model workspace")
    return candidate.resolve(strict=True)


def _text_digest(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_text(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _binding_state(workspace: Path, serving: object) -> dict[str, object]:
    if not isinstance(serving, dict) or serving.get("mode") != "point-code":
        return {"mode": "numeric", "point_metadata": "not_applicable", "binding": None}
    binding = serving.get("binding")
    base: dict[str, object] = {
        "mode": "point-code", "point_metadata": "unknown", "binding": binding,
        "point_fields": serving.get("point_fields", [serving.get("point_field")] if serving.get("point_field") else []),
    }
    if (not isinstance(binding, str) or "\\" in binding
            or Path(binding).is_absolute() or ".." in Path(binding).parts):
        return base
    path = workspace / binding
    if not path.is_file() or is_link_or_junction(path):
        return base
    base["binding_sha256"] = _text_digest(path)
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        base["point_metadata"] = "invalid"
        return base
    if not isinstance(document, dict):
        base["point_metadata"] = "invalid"
        return base
    rows = document.get("bindings")
    if not isinstance(rows, list) or not rows:
        base["point_metadata"] = "incomplete"
        return base
    points = [point for row in rows if isinstance(row, dict)
              for point in (row.get("points") if isinstance(row.get("points"), list) else [])
              if isinstance(point, dict)]
    complete = (
        bool(points)
        and all(isinstance(row, dict) and row.get("expected_signal")
                and isinstance(row.get("points"), list) and row["points"]
                and all(isinstance(point, dict) for point in row["points"])
                for row in rows)
        and all(all(isinstance(point.get(key), str) and point[key].strip()
                    for key in ("code", *_POINT_METADATA)) for point in points)
    )
    base["binding_count"] = len(rows)
    base["point_count"] = len(points)
    base["point_metadata"] = (
        "declared" if complete and all(point.get("point_id") and point.get("signal_code") for point in points)
        else "legacy_code_only" if complete else "incomplete"
    )
    return base


def _publication_state(workspace: Path, document: dict[str, Any], model: dict[str, Any]) -> dict[str, object]:
    artifacts = document.get("artifacts")
    bundle_digest = next((item.get("sha256") for item in artifacts
                          if isinstance(item, dict) and item.get("kind") == "mlflow-pyfunc"), None) if isinstance(artifacts, list) else None
    receipt_dir = workspace / "validation" / "publications"
    accepted: list[tuple[str, str, dict[str, object], Path]] = []
    if receipt_dir.is_dir() and not is_link_or_junction(receipt_dir):
        for path in receipt_dir.glob("*.json"):
            if is_link_or_junction(path) or not _RUN_ID.fullmatch(path.stem):
                continue
            try:
                receipt = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(receipt, dict):
                continue
            verification = receipt.get("verification")
            if (
                receipt.get("schema_version") == "1.0"
                and receipt.get("status") == "verified"
                and receipt.get("model_id") == model.get("id")
                and receipt.get("model_version") == model.get("version")
                and receipt.get("run_id") == path.stem
                and receipt.get("bundle_sha256") == bundle_digest
                and isinstance(receipt.get("experiment"), str)
                and isinstance(verification, dict)
                and all(verification.get(key) is True for key in
                        ("run_finished", "downloaded_digest_matched", "platform_predict_matched"))
            ):
                accepted.append((str(receipt.get("verified_at_utc", "")), path.stem, receipt, path))
    if accepted:
        _, _, receipt, path = sorted(accepted)[-1]
        return {
            "status": "verified_repository_receipt", "live_platform_status": "not_queried",
            "experiment": receipt["experiment"], "run_id": receipt["run_id"],
            "bundle_sha256": bundle_digest,
            "evidence": str(path.relative_to(workspace)).replace("\\", "/"),
            "receipt_sha256": _text_digest(path),
        }
    platform = document.get("platform")
    if isinstance(platform, dict) and _RUN_ID.fullmatch(str(platform.get("run_id", ""))):
        return {
            "status": "recorded_legacy_unverified", "live_platform_status": "not_queried",
            "experiment": platform.get("experiment"), "run_id": platform["run_id"],
            "evidence": "model.yaml#platform",
        }
    return {"status": "unknown", "live_platform_status": "not_queried", "experiment": None, "run_id": None, "evidence": None}


def build_snapshot(path: str | Path) -> dict[str, object]:
    manifest = manifest_path(path)
    workspace = manifest.parent
    document = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("model"), dict):
        raise ValueError(f"invalid model.yaml: {manifest}")
    model = document["model"]
    model_id = model.get("id")
    version = model.get("version")
    if not isinstance(model_id, str) or not isinstance(version, str):
        raise ValueError(f"model identity is incomplete: {manifest}")
    register = load_decision_register(workspace, expected_model_id=model_id, required=False)
    decisions_path = workspace / "decisions.yaml"
    decisions: dict[str, object] = {
        "status": "recorded" if decisions_path.is_file() else "not_recorded",
        "summary": register.summary() if decisions_path.is_file() else None,
        "open_ids": sorted(item.decision_id for item in register.decisions if item.status == "open"),
    }
    if decisions_path.is_file() and not is_link_or_junction(decisions_path):
        decisions["source_sha256"] = _text_digest(decisions_path)
    target = document.get("target")
    return {
        "schema_version": "1.0",
        "scope": "generated_repository_snapshot_not_live_platform",
        "model": {
            "id": model_id, "name": model.get("name"), "version": version,
            "lifecycle_state": model.get("lifecycle_state"), "owner": model.get("owner"),
            "target": target.get("kind") if isinstance(target, dict) else None,
        },
        "serving": _binding_state(workspace, document.get("serving")),
        "decisions": decisions,
        "publication": _publication_state(workspace, document, model),
        "sources": {"model.yaml": _text_digest(manifest)},
    }


def snapshot_in_sync(path: str | Path) -> bool:
    manifest = manifest_path(path)
    snapshot = manifest.parent / SNAPSHOT_NAME
    return snapshot.is_file() and not is_link_or_junction(snapshot) and snapshot.read_text(encoding="utf-8") == _json_text(build_snapshot(manifest))


def write_snapshot(path: str | Path) -> Path:
    from model_lab.environment import EnvironmentError, ensure_external_workspace

    component = first_link_or_junction_component(Path(path))
    if component is not None:
        raise EnvironmentError(
            "unsafe_workspace_path",
            f"model workspace must not pass through a link or junction: {component}",
        )

    workspace = ensure_external_workspace(path)
    manifest = manifest_path(workspace)
    destination = manifest.parent / SNAPSHOT_NAME
    if destination.exists() and (not destination.is_file() or is_link_or_junction(destination)):
        raise ValueError(f"unsafe snapshot destination: {destination}")
    rendered = _json_text(build_snapshot(manifest))
    if destination.is_file() and destination.read_text(encoding="utf-8") == rendered:
        return destination
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=manifest.parent,
                                         prefix=".workflow-state-", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            output.write(rendered)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def write_publication_receipt(
    manifest: str | Path, *, experiment: str, experiment_id: str,
    run_id: str, owner: str, bundle_sha256: str, plan_id: str,
) -> Path:
    """Record a finished, downloaded and predicted Run; never replace a receipt."""
    from model_lab.environment import EnvironmentError, ensure_external_workspace

    component = first_link_or_junction_component(Path(manifest))
    if component is not None:
        raise EnvironmentError(
            "unsafe_workspace_path",
            f"model workspace must not pass through a link or junction: {component}",
        )

    path = manifest_path(ensure_external_workspace(manifest))
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("invalid MLflow run ID")
    model = yaml.safe_load(path.read_text(encoding="utf-8"))["model"]
    validation = path.parent / "validation"
    directory = validation / "publications"
    if is_link_or_junction(validation) or is_link_or_junction(directory):
        raise ValueError("publication receipt directory must not be a link or junction")
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{run_id}.json"
    if destination.exists():
        raise FileExistsError(f"publication receipt already exists: {destination}")
    receipt: dict[str, object] = {
        "schema_version": "1.0", "status": "verified",
        "model_id": model["id"], "model_version": model["version"],
        "experiment": experiment, "experiment_id": str(experiment_id),
        "run_id": run_id, "owner": owner, "bundle_sha256": bundle_sha256,
        "plan_id": plan_id,
        "verified_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "verification": {
            "run_finished": True, "downloaded_digest_matched": True,
            "platform_predict_matched": True,
        },
        "scope": "MLflow_publication_only_not_business_platform_or_schedule",
    }
    with destination.open("x", encoding="utf-8", newline="\n") as output:
        output.write(_json_text(receipt))
    return destination


def model_manifests(models_root: str | Path) -> tuple[Path, ...]:
    models = Path(models_root)
    candidates = list(models.glob("*/model.yaml")) + list(models.glob("_drafts/*/model.yaml"))
    return tuple(sorted((path for path in candidates if path.is_file() and not is_link_or_junction(path)), key=str))


def task_context(path: str | Path, task: str = "status") -> dict[str, object]:
    """Minimal task-specific pointers; recompute facts even if JSON drifted."""
    snapshot = build_snapshot(path)
    model = snapshot["model"]
    assert isinstance(model, dict)
    lifecycle = model["lifecycle_state"]
    docs = [
        "docs/00-入口层/00-模型开发总览.md",
        "docs/00-入口层/03-模型任务分流与加载矩阵.md",
    ]
    if task not in {"status", "read"}:
        docs.append("docs/00-入口层/06-模型任务问答与交互档位.md")
    if lifecycle == "released" or task == "maintain":
        docs.append("docs/10-流程层/11-新模型开发SOP.md")
        docs.append("docs/adr/0015-在役模型维护版必须安全分叉.md")
    elif lifecycle in {"draft", "specified"}:
        docs.append("docs/10-流程层/11-新模型开发SOP.md")
    elif lifecycle in {"built", "validated"}:
        docs.append("docs/30-触发层/30-部署前检查清单.md")
    elif lifecycle == "releasable":
        docs.append("docs/10-流程层/10-部署教程-集团MLflow.md")
    serving = snapshot["serving"]
    assert isinstance(serving, dict)
    if serving["mode"] == "point-code":
        docs.append("docs/10-流程层/15-多字段测点绑定.md")
    if task == "publish":
        docs.append("docs/adr/0017-发布验收与运营边界分离.md")
    relative_docs = list(dict.fromkeys(docs))
    from model_lab.environment import framework_identity

    framework = framework_identity()
    root = Path(str(framework["root"]))
    doc_paths = [str((root / relative).resolve(strict=False)) for relative in relative_docs]
    return {
        "snapshot": snapshot,
        "snapshot_in_sync": snapshot_in_sync(path),
        "docs": relative_docs,
        "doc_paths": doc_paths,
        "framework": framework,
    }
