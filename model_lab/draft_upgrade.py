"""Safely fork an unreleased numeric draft into a point-code contract version."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml  # type: ignore[import-untyped]

from model_lab.maintenance import _sha256, _version_pair
from model_lab.manifest import ManifestError, validate_manifest
from model_lab.path_safety import is_link_or_junction
from model_lab.scaffolding import (
    TEMPLATE_VERSION,
    ScaffoldRequest,
    WorkspaceError,
    _normalize_request,
    _refuse_symlink,
    _render_files,
    _write_workspace,
    ensure_local_filesystem_path,
)


DRAFT_UPGRADE_TEMPLATE_ID = "draft-contract-upgrade-model"
DRAFT_UPGRADE_WORKFLOW_ID = "model-lifecycle-v1"
_FROZEN_FILES = (
    "model.yaml",
    "model-lab.yaml",
    "decisions.yaml",
    "model-spec.md",
    "validation/validation-report.md",
    "notebooks/model.ipynb",
    "runtime/requirements.lock.txt",
    "src/model.py",
    "tests/test_model.py",
)
_INHERITED_DECISIONS = {
    "requirements-context",
    "design-model-logic",
    "design-exception-strategy",
}
_INHERITED_SECTIONS = {
    "一句话用途": "purpose",
    "使用者": "users",
    "执行频率": "frequency",
    "范围与非目标": "boundaries",
    "公式或模型逻辑": "formula",
    "缺失、异常和边界策略": "exception-strategy",
}


@dataclass(frozen=True)
class DraftUpgradeReport:
    status: str
    model_id: str
    base_version: str
    version: str
    destination: str
    files: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "change_kind": "contract-upgrade",
            "model_id": self.model_id,
            "base_version": self.base_version,
            "version": self.version,
            "destination": self.destination,
            "files": list(self.files),
            "safety": {"existing_files_overwritten": False, "remote_access": False, "remote_writes": False},
        }

    def as_text(self) -> str:
        verb = "CREATED" if self.status == "created" else "PLAN"
        return (
            f"{self.model_id}: DRAFT CONTRACT UPGRADE {verb}\n"
            f"Version: {self.base_version} -> {self.version}\n"
            f"Destination: {self.destination}\n"
            "Safety: parent draft unchanged; no remote writes"
        )


def _document(path: Path, label: str) -> dict[str, Any]:
    _refuse_symlink(path, label)
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise WorkspaceError("draft_upgrade_unreadable", f"cannot read {label}") from error
    if not isinstance(value, dict):
        raise WorkspaceError("draft_upgrade_invalid", f"{label} must be a YAML object")
    return value


def _parent_files(parent: Path) -> dict[str, str]:
    digests: dict[str, str] = {}
    for relative in _FROZEN_FILES:
        path = parent.joinpath(*PurePosixPath(relative).parts)
        _refuse_symlink(path, f"parent {relative}")
        if not path.is_file():
            raise WorkspaceError("draft_upgrade_parent_invalid", f"parent {relative} is missing")
        digests[relative] = _sha256(path)
    return digests


def _verified_parent(parent: Path) -> tuple[dict[str, Any], dict[str, str]]:
    manifest = parent / "model.yaml"
    try:
        report = validate_manifest(manifest)
    except ManifestError as error:
        raise WorkspaceError("draft_upgrade_parent_invalid", str(error)) from error
    if not report.valid:
        raise WorkspaceError("draft_upgrade_parent_invalid", f"parent manifest: {report.issues[0].code}")
    document = _document(manifest, "parent manifest")
    metadata = _document(parent / "model-lab.yaml", "parent metadata")
    model = document.get("model")
    target = document.get("target")
    template = metadata.get("template")
    workflow = metadata.get("workflow")
    if (
        not isinstance(model, Mapping)
        or model.get("lifecycle_state") != "releasable"
        or not isinstance(target, Mapping)
        or target.get("kind") != "cpu-iot"
        or "serving" in document
        or not isinstance(template, Mapping)
        or template.get("id") != "new-model"
        or not isinstance(workflow, Mapping)
        or workflow.get("id") != DRAFT_UPGRADE_WORKFLOW_ID
    ):
        raise WorkspaceError(
            "draft_upgrade_parent_invalid",
            "parent must be a releasable numeric cpu-iot new-model draft",
        )
    digests = _parent_files(parent)
    source = document.get("source")
    if not isinstance(source, Mapping) or source.get("entrypoint_sha256") != digests["src/model.py"]:
        raise WorkspaceError("draft_upgrade_parent_invalid", "parent source digest is not current")
    decisions = _document(parent / "decisions.yaml", "parent decisions").get("decisions")
    if not isinstance(decisions, list) or any(
        not isinstance(item, Mapping)
        or (item.get("required") is True and item.get("status") != "confirmed")
        for item in decisions
    ):
        raise WorkspaceError("draft_upgrade_parent_invalid", "parent required decisions are not confirmed")
    return document, digests


def _section(markdown: str, heading: str) -> str | None:
    marker = f"### {heading}\n"
    start = markdown.find(marker)
    if start < 0:
        return None
    content = markdown[start + len(marker):].split("\n### ", 1)[0].split("\n## ", 1)[0].strip()
    return content if content and "MODEL-LAB-TODO" not in content else None


def _carry_forward_spec(template: str, parent: str, model_id: str, base_version: str) -> str:
    for heading, token in _INHERITED_SECTIONS.items():
        content = _section(parent, heading)
        if content is not None:
            template = template.replace(f"<!-- MODEL-LAB-TODO:{token} -->", content)
    return template + (
        "\n### 父草稿与升级边界\n\n"
        f"数值契约父草稿：`{model_id}` {base_version}。父版算法源码和测试已按字节"
        "留存在 `baselines/base/`；当前新版只规划平台测点编码数组输入，不将"
        "父版数值 MLflow 包冒充在线测点制品。新版测点、时间和验收问题需单独关闭。\n"
    )


def _carry_forward_decisions(template: str, parent: dict[str, Any]) -> str:
    register = yaml.safe_load(template)
    inherited = {item.get("id"): item for item in parent.get("decisions", []) if isinstance(item, dict)}
    for item in register["decisions"]:
        old = inherited.get(item.get("id"))
        if item["id"] in _INHERITED_DECISIONS and old and old.get("status") == "confirmed":
            for field in ("recommendation", "answer", "status", "confirmed_by", "confirmed_at"):
                item[field] = old.get(field)
        elif item["id"] == "requirements-io-contract":
            item["recommendation"] = (
                "算法仍接收两台进水瞬时流量数值并输出两者之和；平台改为两个"
                "Array(string) 测点编码字段，由公司 3.11 IoT provider 取同一时间点数值。"
            )
        elif item["id"] == "design-parameters-measurements":
            item["recommendation"] = (
                "先核对目标设施的进水瞬时流量点位、m3/h 单位、"
                "显式北京时间起点、窗口长度和失败直接报错规则。"
            )
    return yaml.safe_dump(register, allow_unicode=True, sort_keys=False)


def fork_draft_contract(path: str | Path, version: str, *, apply: bool = False) -> DraftUpgradeReport:
    candidate = ensure_local_filesystem_path(path, "draft model")
    candidate_workspace = candidate if candidate.is_dir() else candidate.parent
    # Keep framework-boundary discovery centralized in the environment module.
    from model_lab.environment import ensure_external_workspace

    try:
        parent = ensure_external_workspace(candidate_workspace)
    except ValueError as error:
        raise WorkspaceError(getattr(error, "code", "unsafe_workspace_path"), str(error)) from error
    _refuse_symlink(parent, "draft parent")
    drafts = parent.parent
    if drafts.name != "_drafts" or not parent.is_dir():
        raise WorkspaceError("draft_upgrade_parent_invalid", "select an external _drafts/<model-id> workspace")
    document, digests = _verified_parent(parent)
    model = document["model"]
    model_id = str(model["id"])
    if parent.name != model_id:
        raise WorkspaceError("draft_upgrade_parent_invalid", "select the original unversioned draft")
    base_version, new_version = _version_pair(model["version"], version, "contract-upgrade")
    destination = drafts / f"{model_id}-{new_version}"
    _refuse_symlink(destination, "draft upgrade destination")
    if destination.exists() or is_link_or_junction(destination):
        raise WorkspaceError("workspace_exists", f"draft upgrade destination exists: {destination}")
    for sibling in drafts.iterdir():
        if is_link_or_junction(sibling):
            raise WorkspaceError("unsafe_workspace_path", "draft inventory contains a link")
        other = sibling / "model.yaml"
        if sibling != parent and sibling.is_dir() and other.is_file():
            other_report = validate_manifest(other)
            if other_report.model_id.casefold() == model_id.casefold():
                raise WorkspaceError("draft_upgrade_exists", "another draft version already uses this model ID")

    request = _normalize_request(ScaffoldRequest(
        model_id=model_id, name=str(model["name"]), owner=str(model["owner"]),
        target="cpu-iot", serving_mode="point-code",
    ))
    rendered: dict[PurePosixPath, str | bytes] = _render_files(request)
    new_manifest = yaml.safe_load(rendered[PurePosixPath("model.yaml")])
    new_manifest["model"]["version"] = new_version
    new_manifest["target"] = document["target"]
    new_manifest["contract"] = document["contract"]
    rendered[PurePosixPath("model.yaml")] = yaml.safe_dump(new_manifest, allow_unicode=True, sort_keys=False)
    bindings = yaml.safe_load(rendered[PurePosixPath("input-bindings.yaml")])
    bindings["model_version"] = new_version
    rendered[PurePosixPath("input-bindings.yaml")] = yaml.safe_dump(bindings, allow_unicode=True, sort_keys=False)
    parent_decisions = _document(parent / "decisions.yaml", "parent decisions")
    rendered[PurePosixPath("decisions.yaml")] = _carry_forward_decisions(
        rendered[PurePosixPath("decisions.yaml")], parent_decisions
    )
    parent_spec = (parent / "model-spec.md").read_text(encoding="utf-8")
    rendered[PurePosixPath("model-spec.md")] = _carry_forward_spec(
        rendered[PurePosixPath("model-spec.md")], parent_spec, model_id, base_version
    )
    for relative in ("src/model.py", "tests/test_model.py"):
        content = (parent / relative).read_bytes()
        rendered[PurePosixPath(relative)] = content
        rendered[PurePosixPath("baselines/base") / PurePosixPath(relative).name] = content
    metadata = {
        "schema_version": "1.0",
        "template": {"id": DRAFT_UPGRADE_TEMPLATE_ID, "version": TEMPLATE_VERSION},
        "workflow": {"id": DRAFT_UPGRADE_WORKFLOW_ID},
        "draft_upgrade": {
            "base_directory": parent.name,
            "base_version": base_version,
            "base_digests": digests,
            "behavior_policy": "algorithm-unchanged-point-contract-upgrade",
        },
    }
    rendered[PurePosixPath("model-lab.yaml")] = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
    if apply:
        _write_workspace(destination, rendered)
    return DraftUpgradeReport(
        status="created" if apply else "planned", model_id=model_id,
        base_version=base_version, version=new_version,
        destination=str(destination.resolve(strict=False)),
        files=tuple(sorted(path.as_posix() for path in rendered)),
    )


def validate_draft_upgrade_lineage(workspace: Path) -> tuple[bool, str, Path | None]:
    try:
        metadata = _document(workspace / "model-lab.yaml", "draft upgrade metadata")
        draft = _document(workspace / "model.yaml", "draft upgrade manifest")
        template = metadata.get("template")
        workflow = metadata.get("workflow")
        lineage = metadata.get("draft_upgrade")
        if (
            not isinstance(template, Mapping)
            or template.get("id") != DRAFT_UPGRADE_TEMPLATE_ID
            or template.get("version") != TEMPLATE_VERSION
            or not isinstance(workflow, Mapping)
            or workflow.get("id") != DRAFT_UPGRADE_WORKFLOW_ID
            or not isinstance(lineage, Mapping)
            or lineage.get("behavior_policy") != "algorithm-unchanged-point-contract-upgrade"
        ):
            return False, "draft upgrade metadata is invalid", None
        base_directory = lineage.get("base_directory")
        if not isinstance(base_directory, str) or not base_directory or any(
            character in base_directory for character in "/\\:"
        ) or base_directory in {".", "..", "_drafts"}:
            return False, "draft upgrade base directory is unsafe", None
        parent = workspace.parent / base_directory
        if parent == workspace or is_link_or_junction(parent):
            return False, "draft upgrade parent is unsafe", None
        base, digests = _verified_parent(parent)
        if digests != lineage.get("base_digests"):
            return False, "parent draft changed after the fork", None
        base_model = base["model"]
        draft_model = draft.get("model")
        if not isinstance(draft_model, Mapping):
            return False, "draft model identity is missing", None
        base_version, new_version = _version_pair(
            base_model["version"], str(draft_model.get("version")), "contract-upgrade"
        )
        if (
            parent.name != base_model.get("id")
            or lineage.get("base_version") != base_version
            or workspace.name != f"{base_model['id']}-{new_version}"
            or draft_model.get("id") != base_model.get("id")
            or draft_model.get("name") != base_model.get("name")
            or draft_model.get("owner") != base_model.get("owner")
            or draft_model.get("lifecycle_state") not in {"draft", "specified", "built", "validated", "releasable"}
            or draft.get("contract", {}).get("outputs") != base.get("contract", {}).get("outputs")
            or draft.get("target", {}).get("kind") != base.get("target", {}).get("kind")
        ):
            return False, "draft upgrade identity or output contract changed", None
        for relative in ("src/model.py", "tests/test_model.py"):
            archived = workspace / "baselines" / "base" / Path(relative).name
            original = parent / relative
            if is_link_or_junction(archived) or not archived.is_file() or archived.read_bytes() != original.read_bytes():
                return False, "archived parent behavior baseline changed", None
        return True, "parent draft and versioned contract-upgrade lineage are intact", parent / "model.yaml"
    except (WorkspaceError, OSError, KeyError, TypeError, ValueError, AttributeError):
        return False, "draft upgrade lineage cannot be verified", None


def valid_draft_upgrade_fork_set(drafts: list[Path]) -> bool:
    """Allow exactly one original numeric draft and one verified next-minor fork."""
    if len(drafts) != 2:
        return False
    try:
        identities = {
            path: _document(path, "draft manifest")["model"]["id"]
            for path in drafts
        }
    except (WorkspaceError, KeyError, TypeError):
        return False
    base = next((path for path, model_id in identities.items() if path.parent.name == model_id), None)
    if base is None or any(model_id != identities[base] for model_id in identities.values()):
        return False
    child = next((path for path in drafts if path != base), None)
    if child is None:
        return False
    valid, _, parent_manifest = validate_draft_upgrade_lineage(child.parent)
    return valid and parent_manifest is not None and parent_manifest.resolve() == base.resolve()
