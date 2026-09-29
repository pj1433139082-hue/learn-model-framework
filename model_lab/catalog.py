from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import html
from pathlib import Path
from typing import Any, cast
import unicodedata
from urllib.parse import quote

from model_lab.manifest import (
    ManifestError,
    ValidationReport,
    _load_manifest,
    _validate_loaded_document,
)


@dataclass(frozen=True)
class CatalogIssue:
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


@dataclass(frozen=True)
class CatalogOutput:
    name: str
    field_type: str
    unit: str

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "type": self.field_type,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class CatalogEntry:
    directory: str
    model_id: str
    name: str
    version: str
    lifecycle_state: str
    maintenance_status: str
    target: str
    platform_id: str
    archive_status: str
    manifest_path: str
    release_path: str
    historical_release_paths: tuple[str, ...]
    maintenance_path: str
    baseline_status: str
    outputs: tuple[CatalogOutput, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "directory": self.directory,
            "model_id": self.model_id,
            "name": self.name,
            "version": self.version,
            "lifecycle_state": self.lifecycle_state,
            "maintenance_status": self.maintenance_status,
            "target": self.target,
            "platform_id": self.platform_id,
            "archive_status": self.archive_status,
            "manifest_path": self.manifest_path,
            "release_path": self.release_path,
            "historical_release_paths": list(self.historical_release_paths),
            "maintenance_path": self.maintenance_path,
            "baseline_status": self.baseline_status,
            "outputs": [output.as_dict() for output in self.outputs],
        }


@dataclass(frozen=True)
class CatalogReport:
    models_root: str
    expected_count: int
    discovered_count: int
    entries: tuple[CatalogEntry, ...]
    issues: tuple[CatalogIssue, ...]

    @property
    def valid(self) -> bool:
        return not self.issues

    @property
    def release_snapshot_count(self) -> int:
        return sum(
            1 + len(entry.historical_release_paths) for entry in self.entries
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "models_root": self.models_root,
            "expected_model_count": self.expected_count,
            "model_count": self.discovered_count,
            "release_snapshot_count": self.release_snapshot_count,
            "entries": [entry.as_dict() for entry in self.entries],
            "issues": [issue.as_dict() for issue in self.issues],
        }

    def as_text(self) -> str:
        state = "VALID" if self.valid else "INVALID"
        lines = [
            f"catalog: {state}",
            f"Models: {self.discovered_count}/{self.expected_count}",
        ]
        for issue in self.issues:
            lines.append(f"- [{issue.code}] {issue.path}: {issue.message}")
        return "\n".join(lines)


def _relative_path(path: Path, repository_root: Path) -> str:
    return path.resolve().relative_to(repository_root.resolve()).as_posix()


def _identity_values(
    document: object,
) -> tuple[str | None, str | None, str | None]:
    if not isinstance(document, Mapping):
        return None, None, None
    model = document.get("model")
    platform = document.get("platform")
    model_id = model.get("id") if isinstance(model, Mapping) else None
    provider = platform.get("provider") if isinstance(platform, Mapping) else None
    experiment = (
        platform.get("experiment") if isinstance(platform, Mapping) else None
    )
    run_id = platform.get("run_id") if isinstance(platform, Mapping) else None
    normalized_model_id = model_id if isinstance(model_id, str) else None
    platform_id = (
        f"{provider}:{experiment}"
        if isinstance(provider, str) and isinstance(experiment, str)
        else None
    )
    resolved_run_id = (
        f"{provider}:{run_id}"
        if isinstance(provider, str)
        and isinstance(run_id, str)
        and run_id != "pending-archive"
        else None
    )
    return normalized_model_id, platform_id, resolved_run_id


def _duplicate_issues(
    identities: Mapping[str, list[str]],
    *,
    code: str,
    label: str,
) -> list[CatalogIssue]:
    issues: list[CatalogIssue] = []
    for locations in identities.values():
        if len(locations) < 2:
            continue
        issues.append(
            CatalogIssue(
                code=code,
                path=", ".join(sorted(locations)),
                message=f"{label} must be unique across the active catalog",
                suggestion="assign a unique identity and create a new release snapshot",
            )
        )
    return issues


def _historical_platform_identities(
    validation: ValidationReport,
) -> tuple[tuple[str, str, str | None], ...]:
    identities: list[tuple[str, str, str | None]] = []
    for check in validation.checks:
        if check.name != "historical_release_snapshots":
            continue
        snapshots = dict(check.details).get("snapshots")
        if not isinstance(snapshots, (list, tuple)):
            continue
        for snapshot in snapshots:
            if not isinstance(snapshot, Mapping):
                continue
            path = snapshot.get("path")
            provider = snapshot.get("provider")
            experiment = snapshot.get("experiment")
            run_id = snapshot.get("run_id")
            if not all(isinstance(value, str) for value in (path, provider, experiment)):
                continue
            assert isinstance(path, str)
            assert isinstance(provider, str)
            assert isinstance(experiment, str)
            resolved_run_id = (
                f"{provider}:{run_id}"
                if isinstance(run_id, str) and run_id != "pending-archive"
                else None
            )
            identities.append(
                (path, f"{provider}:{experiment}", resolved_run_id)
            )
    return tuple(identities)


def _catalog_entry(
    document: Mapping[str, Any],
    manifest_path: Path,
    repository_root: Path,
) -> CatalogEntry:
    model = cast(Mapping[str, Any], document["model"])
    target = cast(Mapping[str, Any], document["target"])
    platform = cast(Mapping[str, Any], document["platform"])
    source = cast(Mapping[str, Any], document["source"])
    release = cast(Mapping[str, Any], document["release"])
    contract = cast(Mapping[str, Any], document["contract"])
    artifacts = cast(list[Mapping[str, Any]], document["artifacts"])

    release_path = manifest_path.parent / str(release["current"])
    historical_release_paths = tuple(
        _relative_path(manifest_path.parent / str(reference), repository_root)
        for reference in cast(list[str], release.get("history", []))
    )
    maintenance_path = manifest_path.parent / str(source["documentation"])
    outputs = tuple(
        CatalogOutput(
            name=str(field["name"]),
            field_type=str(field["type"]),
            unit=str(field["unit"]),
        )
        for field in cast(list[Mapping[str, Any]], contract["outputs"])
    )
    pending = (
        platform["deployment_status"] == "pending-archive"
        or any(
            artifact["archive_status"] == "pending-archive"
            for artifact in artifacts
        )
    )
    return CatalogEntry(
        directory=manifest_path.parent.name,
        model_id=str(model["id"]),
        name=str(model["name"]),
        version=str(model["version"]),
        lifecycle_state=str(model["lifecycle_state"]),
        maintenance_status=str(model["maintenance_status"]),
        target=str(target["kind"]),
        platform_id=f"{platform['provider']}:{platform['experiment']}",
        archive_status="pending" if pending else "resolved",
        manifest_path=_relative_path(manifest_path, repository_root),
        release_path=_relative_path(release_path, repository_root),
        historical_release_paths=historical_release_paths,
        maintenance_path=_relative_path(maintenance_path, repository_root),
        baseline_status="locked" if "baseline" in document else "pending",
        outputs=outputs,
    )


def build_catalog(
    models_root: str | Path,
    *,
    expected_count: int | None = None,
) -> CatalogReport:
    root = Path(models_root).resolve()
    repository_root = root.parent
    manifest_paths = sorted(
        root.glob("*/model.yaml"),
        key=lambda path: path.parent.name.casefold(),
    )
    issues: list[CatalogIssue] = []
    entries: list[CatalogEntry] = []
    model_ids: defaultdict[str, list[str]] = defaultdict(list)
    platform_ids: defaultdict[str, list[str]] = defaultdict(list)
    platform_runs: defaultdict[str, list[str]] = defaultdict(list)

    if expected_count is None:
        expected_count = len(manifest_paths)
    elif len(manifest_paths) != expected_count:
        issues.append(
            CatalogIssue(
                code="model_count_mismatch",
                path="models",
                message="discovered model count differs from the catalog contract",
                suggestion=f"restore exactly {expected_count} active model manifests",
            )
        )

    for manifest_path in manifest_paths:
        try:
            manifest_relative = _relative_path(manifest_path, repository_root)
        except ValueError:
            issues.append(
                CatalogIssue(
                    code="manifest_outside_models_root",
                    path=manifest_path.name,
                    message="model manifest resolves outside the repository root",
                    suggestion="replace the symlink with a repository-local manifest",
                )
            )
            continue

        try:
            document = _load_manifest(manifest_path)
        except ManifestError as error:
            issues.append(
                CatalogIssue(
                    code=error.code,
                    path=manifest_relative,
                    message="model manifest cannot be read safely",
                    suggestion="repair the manifest and run model-lab validate",
                )
            )
            continue

        model_id, platform_id, resolved_run_id = _identity_values(document)
        if model_id is not None:
            model_ids[model_id].append(manifest_relative)
        if platform_id is not None:
            platform_ids[platform_id].append(manifest_relative)
        if resolved_run_id is not None:
            platform_runs[resolved_run_id].append(manifest_relative)

        model = document.get("model") if isinstance(document, Mapping) else None
        inactive = (
            isinstance(model, Mapping)
            and model.get("lifecycle_state") != "released"
        )
        if inactive:
            issues.append(
                CatalogIssue(
                    code="inactive_model_in_catalog",
                    path=f"{manifest_relative}:model.lifecycle_state",
                    message="the active catalog only accepts released models",
                    suggestion=(
                        "move retired assets out of the active inventory or "
                        "restore the released lifecycle record"
                    ),
                )
            )

        validation = _validate_loaded_document(document, manifest_path)
        if not validation.valid:
            issues.extend(
                CatalogIssue(
                    code=issue.code,
                    path=f"{manifest_relative}:{issue.path}",
                    message="model manifest violates the asset contract",
                    suggestion="run model-lab validate for field-level remediation",
                )
                for issue in validation.issues
            )
            continue

        if inactive:
            continue

        for history_path, history_platform_id, history_run_id in (
            _historical_platform_identities(validation)
        ):
            location = f"{manifest_relative}:{history_path}"
            platform_ids[history_platform_id].append(location)
            if history_run_id is not None:
                platform_runs[history_run_id].append(location)

        if not isinstance(document, Mapping):
            continue
        entries.append(
            _catalog_entry(
                cast(Mapping[str, Any], document),
                manifest_path,
                repository_root,
            )
        )

    issues.extend(
        _duplicate_issues(
            model_ids,
            code="duplicate_model_id",
            label="model ID",
        )
    )
    issues.extend(
        _duplicate_issues(
            platform_ids,
            code="duplicate_platform_id",
            label="platform experiment identity",
        )
    )
    issues.extend(
        _duplicate_issues(
            platform_runs,
            code="duplicate_platform_run_id",
            label="resolved platform run identity",
        )
    )
    return CatalogReport(
        models_root=_relative_path(root, repository_root),
        expected_count=expected_count,
        discovered_count=len(manifest_paths),
        entries=tuple(entries),
        issues=tuple(issues),
    )


def _markdown_text(value: str) -> str:
    normalized = "".join(
        " "
        if unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
        else character
        for character in value
    )
    escaped = html.escape(normalized, quote=True)
    return escaped.replace("|", "&#124;")


def _markdown_code(value: str) -> str:
    return f"<code>{_markdown_text(value)}</code>"


def _catalog_link(path: str, label: str) -> str:
    encoded_path = quote(f"../../{path}", safe="/._-")
    safe_label = _markdown_text(label).replace("[", "&#91;").replace("]", "&#93;")
    return f"[{safe_label}]({encoded_path})"


def render_catalog_markdown(report: CatalogReport) -> str:
    if not report.valid:
        raise ValueError("cannot render an invalid model catalog")

    lines = [
        "<!-- generated by model-lab catalog; do not edit manually -->",
        "# 在役模型资产目录",
        "",
        (
            "> 本页完全由 `models/*/model.yaml` 生成。"
            "修改模型后请重新生成，不要手工维护表格。"
        ),
        "",
        f"- 在役模型：**{report.discovered_count}**",
        "- 当前清单：全部通过资产契约与发布快照校验",
        (
            f"- 发布快照：**{report.release_snapshot_count}**（"
            f"{report.discovered_count} 个当前快照 + "
            f"{report.release_snapshot_count - report.discovered_count} 个历史快照）"
        ),
        "",
        (
            "| # | 模型 | 当前版本 | 目标 | 平台标识 | 归档 | "
            "发布快照 | 行为基线 | 输出 Schema | 后续维护入口 |"
        ),
        "|---:|---|---:|---|---|---|---|---|---|---|",
    ]
    for index, entry in enumerate(report.entries, start=1):
        archive_status = "待归档" if entry.archive_status == "pending" else "完整"
        baseline_status = (
            "已锁定" if entry.baseline_status == "locked" else "待补齐"
        )
        outputs = "<br>".join(
            _markdown_code(
                f"{output.name}: {output.field_type} [{output.unit}]"
            )
            for output in entry.outputs
        )
        model_link = _catalog_link(
            entry.manifest_path,
            f"{entry.name} ({entry.model_id})",
        )
        release_link = _catalog_link(entry.release_path, entry.version)
        historical_links = [
            _catalog_link(path, f"历史 {Path(path).stem}")
            for path in entry.historical_release_paths
        ]
        release_links = "<br>".join((release_link, *historical_links))
        maintenance_link = _catalog_link(entry.maintenance_path, "规则文档")
        lines.append(
            "| "
            + " | ".join(
                (
                    str(index),
                    model_link,
                    _markdown_code(entry.version),
                    _markdown_code(entry.target),
                    _markdown_code(entry.platform_id),
                    archive_status,
                    release_links,
                    baseline_status,
                    outputs,
                    f"{maintenance_link}<br>{_markdown_code(entry.maintenance_status)}",
                )
            )
            + " |"
        )

    lines.extend(
        (
            "",
            "## 生成与校验",
            "",
            "```powershell",
            (
                "model-lab catalog <models-root> --expected-count <N>"
            ),
            (
                "model-lab catalog <models-root> --expected-count <N> "
                "--check <private-catalog.md>"
            ),
            "```",
            "",
        )
    )
    return "\n".join(lines)


def catalog_digest(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def catalog_is_in_sync(report: CatalogReport, path: str | Path) -> bool:
    if not report.valid:
        return False
    try:
        current = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return current == render_catalog_markdown(report)
