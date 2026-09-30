from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

from packaging.version import InvalidVersion, Version
import yaml  # type: ignore[import-untyped]

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


MAINTENANCE_WORKFLOW_ID = "maintenance-lifecycle-v1"
CONTRACT_UPGRADE_WORKFLOW_ID = "contract-upgrade-lifecycle-v1"
CHANGE_KINDS = {"behavior-unchanged", "contract-upgrade"}


def _object(document: object, key: str) -> Mapping[str, Any]:
    value = document.get(key) if isinstance(document, Mapping) else None
    if not isinstance(value, Mapping):
        raise WorkspaceError("maintenance_parent_invalid", f"parent {key} is missing")
    return value


def _sha256(path: Path) -> str:
    try:
        canonical = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except (OSError, UnicodeError) as error:
        raise WorkspaceError("maintenance_parent_unreadable", f"cannot read {path}") from error


def _safe_child(root: Path, relative: object, label: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise WorkspaceError("maintenance_parent_invalid", f"{label} is invalid")
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).is_absolute() or any(part in {".", ".."} for part in parts):
        raise WorkspaceError("maintenance_parent_invalid", f"{label} is unsafe")
    candidate = root.joinpath(*parts)
    _refuse_symlink(candidate, label)
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise WorkspaceError("maintenance_parent_invalid", f"{label} is unsafe") from error
    return candidate


def _load_released(manifest: Path) -> dict[str, Any]:
    try:
        validation = validate_manifest(manifest)
    except ManifestError as error:
        raise WorkspaceError("maintenance_parent_invalid", str(error)) from error
    if not validation.valid:
        first = validation.issues[0]
        raise WorkspaceError(
            "maintenance_parent_invalid", f"parent manifest is invalid: {first.code}"
        )
    try:
        document = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise WorkspaceError("maintenance_parent_unreadable", "parent manifest cannot be read") from error
    if not isinstance(document, dict):
        raise WorkspaceError("maintenance_parent_invalid", "parent manifest is not an object")
    model = _object(document, "model")
    if model.get("lifecycle_state") != "released":
        raise WorkspaceError("maintenance_parent_not_released", "maintenance parent must be released")
    return document


def _version_pair(base: object, requested: str, change_kind: str = "behavior-unchanged") -> tuple[str, str]:
    try:
        old = Version(str(base))
        new = Version(requested)
    except InvalidVersion as error:
        raise WorkspaceError("maintenance_version_invalid", "model versions must be semantic versions") from error
    if old.is_prerelease or new.is_prerelease or new <= old:
        raise WorkspaceError("maintenance_version_invalid", "new version must follow the released version")
    if change_kind == "behavior-unchanged" and new.release[:2] != old.release[:2]:
        raise WorkspaceError(
            "maintenance_version_invalid",
            "behavior-unchanged maintenance requires a newer patch version",
        )
    if change_kind == "contract-upgrade" and (
        len(new.release) != 3
        or new.release[0] != old.release[0]
        or new.release[1] != old.release[1] + 1
        or new.release[2] != 0
    ):
        raise WorkspaceError(
            "maintenance_version_invalid",
            "contract upgrade requires the next minor version with patch zero",
        )
    if change_kind not in CHANGE_KINDS:
        raise WorkspaceError("maintenance_change_kind_invalid", "unsupported maintenance change kind")
    return str(old), str(new)


def _snapshot(parent: Path, document: Mapping[str, Any]) -> Path:
    release = _object(document, "release")
    snapshot = _safe_child(parent, release.get("current"), "release snapshot")
    if not snapshot.is_file():
        raise WorkspaceError("maintenance_parent_invalid", "current release snapshot is missing")
    return snapshot


@dataclass(frozen=True)
class MaintenanceReport:
    status: str
    change_kind: str
    model_id: str
    base_version: str
    version: str
    destination: str
    files: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "change_kind": self.change_kind,
            "model_id": self.model_id,
            "base_version": self.base_version,
            "version": self.version,
            "destination": self.destination,
            "files": list(self.files),
            "safety": {"existing_files_overwritten": False, "remote_access": False, "remote_writes": False},
        }

    def as_text(self) -> str:
        verb = "PLAN" if self.status == "planned" else "CREATED"
        return (
            f"{self.model_id}: {self.change_kind.upper()} {verb}\n"
            f"Version: {self.base_version} -> {self.version}\n"
            f"Destination: {self.destination}\n"
            "Safety: released parent unchanged; no remote writes"
        )


def fork_maintenance(
    path: str | Path, version: str, *, change_kind: str = "behavior-unchanged", apply: bool = False
) -> MaintenanceReport:
    candidate = ensure_local_filesystem_path(path, "released model")
    candidate_workspace = candidate if candidate.is_dir() else candidate.parent
    # This remains lazy to keep import-time dependencies small and lets the
    # environment boundary own framework-root detection in one place.
    from model_lab.environment import ensure_external_workspace

    try:
        parent = ensure_external_workspace(candidate_workspace)
    except ValueError as error:
        raise WorkspaceError(getattr(error, "code", "unsafe_workspace_path"), str(error)) from error
    manifest = parent / "model.yaml"
    models_root = parent.parent
    _refuse_symlink(manifest, "released model")
    if parent.name == "_drafts" or models_root.name == "_drafts" or not manifest.is_file():
        raise WorkspaceError("maintenance_parent_invalid", "select one released workspace outside a _drafts directory")
    document = _load_released(manifest)
    model = _object(document, "model")
    base_version, new_version = _version_pair(model.get("version"), version, change_kind)
    model_id = str(model["id"])
    target = _object(document, "target")
    if target.get("kind") != "cpu-iot":
        raise WorkspaceError("maintenance_target_unsupported", "current maintenance fork supports released cpu-iot assets")
    if "serving" in document:
        raise WorkspaceError(
            "maintenance_serving_unsupported",
            "maintenance fork cannot infer a released model's online serving adapter",
        )
    destination = models_root / "_drafts" / f"{model_id}-{new_version}"
    _refuse_symlink(destination, "maintenance destination")
    if destination.exists() or is_link_or_junction(destination):
        raise WorkspaceError("workspace_exists", f"maintenance workspace already exists: {destination}")
    drafts = models_root / "_drafts"
    if drafts.is_dir():
        for child in drafts.iterdir():
            if is_link_or_junction(child):
                raise WorkspaceError("unsafe_workspace_path", "draft inventory contains a link")
            other = child / "model.yaml"
            if child.is_dir() and other.is_file():
                try:
                    other_report = validate_manifest(other)
                except ManifestError as error:
                    raise WorkspaceError("model_inventory_invalid", "draft inventory is invalid") from error
                if other_report.model_id.casefold() == model_id.casefold():
                    valid, _, parent_manifest = validate_maintenance_lineage(child)
                    if not valid or parent_manifest is None or parent_manifest.resolve() != manifest.resolve():
                        raise WorkspaceError("maintenance_draft_exists", "another draft has invalid lineage for this model")
                    other_metadata = yaml.safe_load((child / "model-lab.yaml").read_text(encoding="utf-8"))
                    other_kind = (
                        "contract-upgrade" if other_metadata["template"]["id"] == "contract-upgrade-model"
                        else "behavior-unchanged"
                    )
                    if other_kind == change_kind:
                        raise WorkspaceError("maintenance_draft_exists", f"an open {change_kind} draft already exists")
    request = _normalize_request(
        ScaffoldRequest(model_id=model_id, name=str(model["name"]), owner=str(model["owner"]), target="cpu-iot")
    )
    rendered: dict[PurePosixPath, str | bytes] = _render_files(request)
    new_manifest = yaml.safe_load(rendered[PurePosixPath("model.yaml")])
    new_manifest["model"]["version"] = new_version
    new_manifest["contract"] = document["contract"]
    if "baseline" in document:
        baseline = dict(_object(document, "baseline"))
        for field in ("input", "expected"):
            source = _safe_child(parent, baseline.get(field), f"baseline {field}")
            if not source.is_file():
                raise WorkspaceError("maintenance_parent_invalid", f"baseline {field} is missing")
            relative = PurePosixPath("baselines") / "base" / f"{field}.json"
            rendered[relative] = source.read_bytes()
            baseline[field] = relative.as_posix()
        if change_kind == "behavior-unchanged":
            new_manifest["baseline"] = baseline
    rendered[PurePosixPath("model.yaml")] = yaml.safe_dump(new_manifest, allow_unicode=True, sort_keys=False)
    decisions = [
                {
                    "id": "maintenance-behavior-scope",
                    "stage": "design",
                    "blocks_transition": "specified",
                    "level": "CONFIRM",
                    "required": True,
                    "question": "是否以父版实际行为为唯一迁移基线，只改 Python 3.11 运行时和打包？",
                    "recommendation": (
                        f"保持 {model_id} {base_version} 的实际公式、阈值、输入输出、测点和控制动作不变；"
                        "仅建立 Python 3.11 维护补丁，名称与文档冲突另列行为升级。"
                    ),
                    "status": "open",
                    "answer": None,
                    "confirmed_by": None,
                    "confirmed_at": None,
                    "evidence": "model-spec.md#维护范围",
                },
                {
                    "id": "validation-outcome",
                    "stage": "validation",
                    "blocks_transition": "validated",
                    "level": "CONFIRM",
                    "required": True,
                    "question": "新版本的固定输入行为回放、指标和可视化是否满足父版基线？",
                    "recommendation": None,
                    "status": "open",
                    "answer": None,
                    "confirmed_by": None,
                    "confirmed_at": None,
                    "evidence": "validation/validation-report.md",
                },
                {
                    "id": "release-readiness",
                    "stage": "release",
                    "blocks_transition": "releasable",
                    "level": "CONFIRM",
                    "required": True,
                    "question": "是否接受本版本的部署、回滚和运行方式？一次性任务可明确不调度；定时运营另行确认。",
                    "recommendation": None,
                    "status": "open",
                    "answer": None,
                    "confirmed_by": None,
                    "confirmed_at": None,
                    "evidence": "model-spec.md#部署计划",
                },
    ]
    if change_kind == "contract-upgrade":
        decisions[0].update({
            "id": "contract-online-input",
            "question": "是否将在线入参升级为测点编码数组，历史取数失败直接报错？",
            "recommendation": "沿用父版算法目标；在线使用测点编码数组，历史窗口由模型显式定义，生产取数失败报错，不使用 mock 兜底。",
            "evidence": "model-spec.md#在线契约升级",
        })
        decisions.insert(1, {
            "id": "historical-window-contract",
            "stage": "design",
            "blocks_transition": "specified",
            "level": "CONFIRM",
            "required": True,
            "question": "该模型的测点、单位、起止时间、时区、取数窗口、重采样和缺失策略是否已逐项核对？",
            "recommendation": None,
            "status": "open",
            "answer": None,
            "confirmed_by": None,
            "confirmed_at": None,
            "evidence": "model-spec.md#历史窗口与特征行为",
        })
        decisions.insert(2, {
            "id": "online-output-grain",
            "stage": "design",
            "blocks_transition": "specified",
            "level": "CONFIRM",
            "required": True,
            "question": "在线一次请求应返回哪个完整聚合周期的结果，输出是单行、计划还是事件列表？",
            "recommendation": None,
            "status": "open",
            "answer": None,
            "confirmed_by": None,
            "confirmed_at": None,
            "evidence": "model-spec.md#在线输出粒度",
        })
    rendered[PurePosixPath("decisions.yaml")] = yaml.safe_dump(
        {
            "schema_version": "1.0",
            "model_id": model_id,
            "owner": model["owner"],
            "decisions": decisions,
        },
        allow_unicode=True,
        sort_keys=False,
    )
    baseline_note = (
        "父版已锁定固定输入行为基线；原始文件按字节复制到 baselines/base/。"
        if "baseline" in document
        else "父版尚无可复验的固定输入行为基线；在构建前必须从旧版实际行为补齐，不能由新算法自造预期。"
    )
    rendered[PurePosixPath("model-spec.md")] = (
        f"# {model['name']} · {new_version} 行为不变维护\n\n"
        "### 维护范围\n\n"
        f"父版 {base_version} 保持 released；本补丁只迁移 Linux x86_64 / Python 3.11 运行时。"
        "公式、阈值、字段、测点及控制动作以父版实际行为为准；名称或文档冲突不在本次暗改。\n\n"
        "### 父版行为基线\n\n"
        f"{baseline_note}\n\n"
        "### 风险与缺口\n\n"
        "旧版部署入口不得执行；旧制品和平台 Run 只作为历史证据，新版本须独立构建与核验。\n\n"
        "### 数据质量结论\n\n<!-- MODEL-LAB-TODO:data-quality -->\n\n"
        "### 构建证据\n\n<!-- MODEL-LAB-TODO:build-evidence -->\n\n"
        "### 部署计划\n\n<!-- MODEL-LAB-TODO:deployment-plan -->\n\n"
        "### 回滚计划\n\n<!-- MODEL-LAB-TODO:rollback-plan -->\n\n"
        "### 调度、监测、告警与后续维护\n\n<!-- MODEL-LAB-TODO:operations-plan -->\n"
    )
    if change_kind == "contract-upgrade":
        rendered[PurePosixPath("model-spec.md")] = (
            f"# {model['name']} · {new_version} 在线契约升级\n\n"
            "### 在线契约升级\n\n"
            f"父版 {base_version} 不变；本版计划把平台输入从即时数值改为测点编码数组，并显式定义历史窗口。"
            "当前仅继承父版契约作为对照，不代表已确定新契约；不得直接部署。\n\n"
            "### 历史窗口与特征行为\n\n"
            "<!-- MODEL-LAB-TODO:per-model-window-and-feature-parity -->\n\n"
            "### 在线输出粒度\n\n"
            "<!-- MODEL-LAB-TODO:per-model-output-grain -->\n\n"
            "### 父版行为基线\n\n"
            f"{baseline_note}\n\n"
            "### 风险与缺口\n\n"
            "取数接口、测点映射、时间口径、缺失策略、历史重采样与父版逐模型核验；生产取数失败不得 mock 降级。\n\n"
            "### 数据质量结论\n\n<!-- MODEL-LAB-TODO:data-quality -->\n\n"
            "### 构建证据\n\n<!-- MODEL-LAB-TODO:build-evidence -->\n\n"
            "### 部署计划\n\n<!-- MODEL-LAB-TODO:deployment-plan -->\n\n"
            "### 回滚计划\n\n<!-- MODEL-LAB-TODO:rollback-plan -->\n\n"
            "### 调度、监测、告警与后续维护\n\n<!-- MODEL-LAB-TODO:operations-plan -->\n"
        )
    snapshot = _snapshot(parent, document)
    metadata = {
        "schema_version": "1.0",
        "template": {"id": "contract-upgrade-model" if change_kind == "contract-upgrade" else "maintenance-model", "version": TEMPLATE_VERSION},
        "workflow": {"id": CONTRACT_UPGRADE_WORKFLOW_ID if change_kind == "contract-upgrade" else MAINTENANCE_WORKFLOW_ID},
        "maintenance": {
            "base_directory": parent.name,
            "base_version": base_version,
            "base_manifest_sha256": _sha256(manifest),
            "base_snapshot_sha256": _sha256(snapshot),
            "behavior_policy": "algorithm-unchanged-contract-upgrade" if change_kind == "contract-upgrade" else "unchanged",
        },
    }
    rendered[PurePosixPath("model-lab.yaml")] = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
    if apply:
        _write_workspace(destination, rendered)
    return MaintenanceReport(
        status="created" if apply else "planned",
        change_kind=change_kind,
        model_id=model_id,
        base_version=base_version,
        version=new_version,
        destination=str(destination.resolve(strict=False)),
        files=tuple(sorted(path.as_posix() for path in rendered)),
    )


def validate_maintenance_lineage(workspace: Path) -> tuple[bool, str, Path | None]:
    metadata_path = workspace / "model-lab.yaml"
    if is_link_or_junction(metadata_path) or not metadata_path.is_file():
        return False, "maintenance metadata is missing or unsafe", None
    try:
        metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
        draft = yaml.safe_load((workspace / "model.yaml").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return False, "maintenance metadata cannot be parsed", None
    if not isinstance(metadata, Mapping) or not isinstance(draft, Mapping):
        return False, "maintenance metadata is invalid", None
    template = metadata.get("template")
    lineage = metadata.get("maintenance")
    workflow = metadata.get("workflow")
    template_id = template.get("id") if isinstance(template, Mapping) else None
    change_kind = "contract-upgrade" if template_id == "contract-upgrade-model" else "behavior-unchanged"
    expected_workflow = CONTRACT_UPGRADE_WORKFLOW_ID if change_kind == "contract-upgrade" else MAINTENANCE_WORKFLOW_ID
    expected_policy = "algorithm-unchanged-contract-upgrade" if change_kind == "contract-upgrade" else "unchanged"
    if (
        metadata.get("schema_version") != "1.0"
        or not isinstance(template, Mapping)
        or template_id not in {"maintenance-model", "contract-upgrade-model"}
        or template.get("version") != TEMPLATE_VERSION
        or not isinstance(workflow, Mapping)
        or workflow.get("id") != expected_workflow
        or not isinstance(lineage, Mapping)
    ):
        return False, "maintenance lineage is missing", None
    base_directory = lineage.get("base_directory")
    if not isinstance(base_directory, str) or base_directory in {"", ".", "..", "_drafts"} or "/" in base_directory or "\\" in base_directory or ":" in base_directory:
        return False, "maintenance base directory is invalid", None
    parent = workspace.parent.parent / base_directory
    try:
        _refuse_symlink(parent / "model.yaml", "maintenance parent")
        base = _load_released(parent / "model.yaml")
        snapshot = _snapshot(parent, base)
        if _sha256(parent / "model.yaml") != lineage.get("base_manifest_sha256") or _sha256(snapshot) != lineage.get("base_snapshot_sha256"):
            return False, "released parent changed after maintenance fork", None
        base_model = _object(base, "model")
        draft_model = _object(draft, "model")
        base_version, new_version = _version_pair(base_model.get("version"), str(draft_model.get("version")), change_kind)
    except (WorkspaceError, OSError, KeyError):
        return False, "maintenance parent or version is invalid", None
    if (
        lineage.get("base_version") != base_version
        or lineage.get("behavior_policy") != expected_policy
        or draft_model.get("id") != base_model.get("id")
        or draft_model.get("lifecycle_state") not in {"draft", "specified", "built", "validated", "releasable"}
        or workspace.name != f"{draft_model.get('id')}-{new_version}"
        or draft_model.get("name") != base_model.get("name")
        or draft_model.get("owner") != base_model.get("owner")
        or not isinstance(draft.get("contract"), Mapping)
        or draft["contract"].get("outputs") != _object(base, "contract").get("outputs")
        or (change_kind == "behavior-unchanged" and draft.get("contract") != base.get("contract"))
        or (change_kind == "behavior-unchanged" and draft.get("serving") != base.get("serving"))
        or not isinstance(draft.get("target"), Mapping)
        or draft["target"].get("kind") != _object(base, "target").get("kind")
    ):
        return False, "maintenance identity, behavior policy, or state is inconsistent", None
    old_baseline = base.get("baseline")
    new_baseline = draft.get("baseline")
    if isinstance(old_baseline, Mapping):
        if change_kind == "behavior-unchanged":
            if not isinstance(new_baseline, Mapping) or any(
                new_baseline.get(field) != old_baseline.get(field)
                for field in ("kind", "input_sha256", "expected_sha256")
            ):
                return False, "frozen parent behavior baseline was changed", None
        else:
            for field in ("input", "expected"):
                copied = workspace / "baselines" / "base" / f"{field}.json"
                try:
                    original = _safe_child(parent, old_baseline.get(field), f"baseline {field}")
                    intact = copied.is_file() and copied.read_bytes() == original.read_bytes()
                except (WorkspaceError, OSError):
                    intact = False
                if is_link_or_junction(copied) or not intact:
                    return False, "archived parent behavior baseline was changed", None
            if isinstance(new_baseline, Mapping) and any(
                str(new_baseline.get(field, "")).startswith("baselines/base/")
                for field in ("input", "expected")
            ):
                return False, "new online contract must use a new behavior baseline", None
    return True, "released parent and versioned maintenance lineage are intact", parent / "model.yaml"


def valid_maintenance_fork_set(active: Path, drafts: list[Path]) -> bool:
    """Allow only one verified patch and one verified contract upgrade per active asset."""
    if not drafts or len(drafts) > 2:
        return False
    kinds: set[str] = set()
    for draft_manifest in drafts:
        valid, _, parent_manifest = validate_maintenance_lineage(draft_manifest.parent)
        if not valid or parent_manifest is None or parent_manifest.resolve() != active.resolve():
            return False
        try:
            metadata = yaml.safe_load((draft_manifest.parent / "model-lab.yaml").read_text(encoding="utf-8"))
            kind = metadata["template"]["id"]
        except (OSError, UnicodeError, yaml.YAMLError, KeyError, TypeError):
            return False
        if kind in kinds:
            return False
        kinds.add(kind)
    return True
