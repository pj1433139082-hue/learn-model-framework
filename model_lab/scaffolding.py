from __future__ import annotations

from dataclasses import dataclass
import html
from importlib.resources import files
from importlib.resources.abc import Traversable
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from typing import Final
import unicodedata

from packaging.specifiers import InvalidSpecifier, SpecifierSet
import yaml  # type: ignore[import-untyped]

from model_lab.manifest import ManifestError, validate_manifest
from model_lab.environment import framework_identity
from model_lab.path_safety import is_link_or_junction
from model_lab.runtime_contract import (
    ACTIVE_RUNTIME_PROFILE_IDS,
    DEFAULT_RUNTIME_PROFILE_ID,
    RuntimeProfile,
    get_runtime_profile,
)

TEMPLATE_VERSION: Final = "new-model-v2"
WORKFLOW_ID: Final = "model-lifecycle-v1"
_MODEL_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_TOKEN = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")
_TARGETS: Final = frozenset({"cpu-iot", "gpu", "chronos"})
_ARTIFACT_FORMATS: Final = frozenset({"onnx", "safetensors", "torchscript"})
_WINDOWS_RESERVED: Final = frozenset(
    {
        "aux",
        "clock$",
        "con",
        "nul",
        "prn",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)

_TEMPLATE_FILES: Final = {
    PurePosixPath("README.md"): PurePosixPath("base/README.md.tmpl"),
    PurePosixPath("decisions.yaml"): PurePosixPath("base/decisions.yaml.tmpl"),
    PurePosixPath("model.yaml"): PurePosixPath("base/model.yaml.tmpl"),
    PurePosixPath("model-lab.yaml"): PurePosixPath("base/model-lab.yaml.tmpl"),
    PurePosixPath("model-spec.md"): PurePosixPath("base/model-spec.md.tmpl"),
    PurePosixPath("CHECKLIST.md"): PurePosixPath("base/CHECKLIST.md.tmpl"),
    PurePosixPath("src/model.py"): PurePosixPath("base/src/model.py.tmpl"),
    PurePosixPath("notebooks/model.ipynb"): PurePosixPath(
        "base/notebooks/model.ipynb.tmpl"
    ),
    PurePosixPath("tests/model_test_template.py"): PurePosixPath(
        "base/tests/model_test_template.py.tmpl"
    ),
    PurePosixPath("validation/validation-report.md"): PurePosixPath(
        "base/validation/validation-report.md.tmpl"
    ),
    PurePosixPath("baselines/README.md"): PurePosixPath(
        "base/baselines/README.md.tmpl"
    ),
    PurePosixPath("releases/README.md"): PurePosixPath("base/releases/README.md.tmpl"),
    PurePosixPath("runtime/requirements.lock.txt"): PurePosixPath(
        "base/runtime/requirements.lock.txt.tmpl"
    ),
    PurePosixPath("runtime/README.md"): PurePosixPath(
        "base/runtime/README.md.tmpl"
    ),
    PurePosixPath("plots/.gitkeep"): PurePosixPath("base/plots/gitkeep.tmpl"),
}


class WorkspaceError(ValueError):
    """模型工作区请求不安全或无法完成。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ScaffoldRequest:
    model_id: str
    name: str
    owner: str
    target: str = "cpu-iot"
    serving_mode: str = "numeric"
    runtime_profile: str = DEFAULT_RUNTIME_PROFILE_ID
    gpu_compute_capability: str | None = None
    gpu_memory_gb_min: float | None = None
    cuda_version: str | None = None
    framework_name: str | None = None
    framework_version: str | None = None
    artifact_format: str | None = None


@dataclass(frozen=True)
class ScaffoldReport:
    status: str
    model_id: str
    target: str
    serving_mode: str
    destination: str
    files: tuple[str, ...]
    runtime_profile: str
    template_version: str = TEMPLATE_VERSION

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "model_id": self.model_id,
            "target": self.target,
            "serving_mode": self.serving_mode,
            "destination": self.destination,
            "files": list(self.files),
            "runtime_profile": self.runtime_profile,
            "template_version": self.template_version,
            "safety": {
                "existing_files_overwritten": False,
                "remote_access": False,
                "remote_writes": False,
            },
        }

    def as_text(self) -> str:
        verb = "CREATE PLAN" if self.status == "planned" else "CREATED"
        lines = [
            f"{self.model_id}: {verb}",
            f"Target: {self.target}",
            f"Serving mode: {self.serving_mode}",
            f"Runtime profile: {self.runtime_profile}",
            f"Destination: {self.destination}",
            f"Template: {self.template_version}",
            "Files:",
        ]
        lines.extend(f"- {path}" for path in self.files)
        lines.append("Safety: no overwrite, network access, or remote writes")
        return "\n".join(lines)


@dataclass(frozen=True)
class _NormalizedRequest:
    model_id: str
    name: str
    owner: str
    target: str
    serving_mode: str
    runtime_profile: RuntimeProfile
    gpu_compute_capability: str | None
    gpu_memory_gb_min: float | None
    cuda_version: str | None
    framework_name: str | None
    framework_version: str | None
    artifact_format: str | None


def _clean_text(value: object, field: str, *, maximum: int = 200) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkspaceError(f"invalid_{field}", f"{field} must be a non-empty string")
    cleaned = value.strip()
    if len(cleaned) > maximum or any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
        for character in cleaned
    ):
        raise WorkspaceError(
            f"invalid_{field}",
            f"{field} must be at most {maximum} characters without control characters",
        )
    return cleaned


def _version_constraint(value: object, field: str) -> str:
    cleaned = _clean_text(value, field)
    try:
        SpecifierSet(cleaned)
    except InvalidSpecifier as error:
        raise WorkspaceError(
            f"invalid_{field}",
            f"{field} must be a version range such as >=1.0,<2.0",
        ) from error
    return cleaned


def _normalize_request(request: ScaffoldRequest) -> _NormalizedRequest:
    model_id = _clean_text(request.model_id, "model_id", maximum=63)
    if _MODEL_ID.fullmatch(model_id) is None:
        raise WorkspaceError(
            "invalid_model_id",
            "model_id must use lowercase ASCII kebab-case",
        )
    if model_id.casefold() in _WINDOWS_RESERVED:
        raise WorkspaceError(
            "invalid_model_id", "model_id is reserved by the local filesystem"
        )

    name = _clean_text(request.name, "name")
    owner = _clean_text(request.owner, "owner")
    target = _clean_text(request.target, "target")
    if target not in _TARGETS:
        raise WorkspaceError(
            "unsupported_target",
            f"target must be one of: {', '.join(sorted(_TARGETS))}",
        )
    serving_mode = _clean_text(request.serving_mode, "serving_mode")
    if serving_mode not in {"numeric", "point-code"}:
        raise WorkspaceError(
            "unsupported_serving_mode", "serving_mode must be numeric or point-code"
        )
    if serving_mode == "point-code" and target != "cpu-iot":
        raise WorkspaceError(
            "unsupported_serving_mode", "point-code serving requires cpu-iot target"
        )
    runtime_profile_id = _clean_text(request.runtime_profile, "runtime_profile")
    try:
        runtime_profile = get_runtime_profile(runtime_profile_id)
    except KeyError as error:
        raise WorkspaceError(
            "unsupported_runtime_profile",
            f"runtime profile is not supported: {runtime_profile_id}",
        ) from error
    if runtime_profile_id not in ACTIVE_RUNTIME_PROFILE_IDS:
        raise WorkspaceError(
            "inactive_runtime_profile",
            "historical runtime profiles cannot create or rebuild model workspaces",
        )

    runtime_values = (
        request.gpu_compute_capability,
        request.gpu_memory_gb_min,
        request.cuda_version,
        request.framework_name,
        request.framework_version,
        request.artifact_format,
    )
    if target == "cpu-iot":
        if any(value is not None for value in runtime_values):
            raise WorkspaceError(
                "unexpected_runtime_option",
                "cpu-iot workspaces must not declare GPU runtime options",
            )
        return _NormalizedRequest(
            model_id=model_id,
            name=name,
            owner=owner,
            target=target,
            serving_mode=serving_mode,
            runtime_profile=runtime_profile,
            gpu_compute_capability=None,
            gpu_memory_gb_min=None,
            cuda_version=None,
            framework_name=None,
            framework_version=None,
            artifact_format=None,
        )

    missing = [
        field
        for field, value in (
            ("gpu_compute_capability", request.gpu_compute_capability),
            ("gpu_memory_gb_min", request.gpu_memory_gb_min),
            ("cuda_version", request.cuda_version),
            ("framework_version", request.framework_version),
            ("artifact_format", request.artifact_format),
        )
        if value is None
    ]
    framework_name = request.framework_name
    if target == "chronos" and framework_name is None:
        framework_name = "pytorch"
    if framework_name is None:
        missing.append("framework_name")
    if missing:
        raise WorkspaceError(
            "missing_runtime_option",
            "GPU targets require: " + ", ".join(sorted(missing)),
        )

    memory = request.gpu_memory_gb_min
    if (
        isinstance(memory, bool)
        or not isinstance(memory, (int, float))
        or memory <= 0
        or not math.isfinite(float(memory))
    ):
        raise WorkspaceError(
            "invalid_gpu_memory_gb_min",
            "gpu_memory_gb_min must be a positive finite number",
        )
    compute = _version_constraint(
        request.gpu_compute_capability, "gpu_compute_capability"
    )
    cuda = _version_constraint(request.cuda_version, "cuda_version")
    framework_version = _version_constraint(
        request.framework_version, "framework_version"
    )
    normalized_framework = _clean_text(framework_name, "framework_name")
    if target == "chronos" and normalized_framework != "pytorch":
        raise WorkspaceError(
            "unsupported_framework", "Chronos workspaces require pytorch"
        )
    artifact_format = _clean_text(request.artifact_format, "artifact_format")
    if artifact_format not in _ARTIFACT_FORMATS:
        raise WorkspaceError(
            "unsupported_artifact_format",
            "artifact_format must be one of: " + ", ".join(sorted(_ARTIFACT_FORMATS)),
        )
    return _NormalizedRequest(
        model_id=model_id,
        name=name,
        owner=owner,
        target=target,
        serving_mode=serving_mode,
        runtime_profile=runtime_profile,
        gpu_compute_capability=compute,
        gpu_memory_gb_min=float(memory),
        cuda_version=cuda,
        framework_name=normalized_framework,
        framework_version=framework_version,
        artifact_format=artifact_format,
    )


def _json_scalar(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def ensure_local_filesystem_path(path: str | Path, label: str) -> Path:
    """Return a local path while rejecting network and device namespaces."""

    raw = os.fspath(path)
    normalized = raw.replace("\\", "/")
    if "\0" in raw or normalized.startswith("//"):
        raise WorkspaceError(
            "unsafe_workspace_path",
            f"{label} must use a local filesystem path",
        )
    candidate = Path(path)
    if os.name == "nt":
        if candidate.drive and not candidate.is_absolute():
            raise WorkspaceError(
                "unsafe_workspace_path",
                f"{label} must not use a drive-relative path",
            )
        absolute = candidate if candidate.is_absolute() else Path.cwd() / candidate
        anchor = absolute.anchor
        if anchor:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            drive_type = int(kernel32.GetDriveTypeW(anchor))
            if drive_type in {0, 1, 4}:
                raise WorkspaceError(
                    "unsafe_workspace_path",
                    f"{label} must be on an available local drive",
                )
    return candidate


def _template_root() -> Traversable:
    return files("model_lab").joinpath("templates", "new-model")


def _read_template(relative: PurePosixPath) -> str:
    resource = _template_root()
    for part in relative.parts:
        resource = resource.joinpath(part)
    try:
        return resource.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as error:
        raise WorkspaceError(
            "template_unavailable", f"template resource is unavailable: {relative}"
        ) from error


def _render(template: str, values: dict[str, str], source: str) -> str:
    required = set(_TOKEN.findall(template))
    missing = sorted(required - set(values))
    if missing:
        raise WorkspaceError(
            "template_value_missing",
            f"template {source} requires: {', '.join(missing)}",
        )
    rendered = _TOKEN.sub(lambda match: values[match.group(1)], template)
    remaining = sorted(set(_TOKEN.findall(rendered)))
    if remaining:
        raise WorkspaceError(
            "template_render_incomplete",
            f"template {source} still contains: {', '.join(remaining)}",
        )
    return rendered.replace("\r\n", "\n").replace("\r", "\n")


def _render_files(request: _NormalizedRequest) -> dict[PurePosixPath, str]:
    framework = framework_identity()
    values = {
        "MODEL_ID": request.model_id,
        "MODEL_ID_PY": request.model_id.replace("-", "_"),
        "MODEL_ID_JSON": _json_scalar(request.model_id),
        "MODEL_LAB_VERSION_JSON": _json_scalar(framework["version"]),
        "MODEL_LAB_COMMIT_JSON": _json_scalar(framework["git_commit"]),
        "MODEL_LAB_DIRTY_JSON": _json_scalar(framework["git_dirty"]),
        "MODEL_NAME_MD": html.escape(request.name, quote=True),
        "MODEL_NAME_JSON": _json_scalar(request.name),
        "NOTEBOOK_SOURCE_JSON": _json_scalar(
            [
                f"# {html.escape(request.name, quote=True)}\n",
                "\n",
                (f"模型 ID：`{request.model_id}`；目标：`{request.target}`。\n"),
                "\n",
                "<!-- MODEL-LAB-TODO:notebook -->\n",
                "按环境、契约、取数、数据质量、模型、验证、可视化和结论的顺序补齐。",
            ]
        ),
        "NOTEBOOK_PYTHON_MINOR_JSON": _json_scalar(
            request.runtime_profile.python_minor
        ),
        "OWNER_JSON": _json_scalar(request.owner),
        "TARGET_KIND": request.target,
        "RUNTIME_PROFILE_ID": request.runtime_profile.profile_id,
        "RUNTIME_PROFILE_ID_JSON": _json_scalar(request.runtime_profile.profile_id),
        "ENVIRONMENT_OPERATING_SYSTEM_JSON": _json_scalar(
            request.runtime_profile.operating_system
        ),
        "ENVIRONMENT_ARCHITECTURE_JSON": _json_scalar(
            request.runtime_profile.architecture
        ),
        "ENVIRONMENT_PYTHON_JSON": _json_scalar(request.runtime_profile.python),
        "ENVIRONMENT_MLFLOW_JSON": _json_scalar(request.runtime_profile.mlflow),
        "MLFLOW_VERSION": request.runtime_profile.pin("mlflow"),
        "CLOUDPICKLE_VERSION": request.runtime_profile.pin("cloudpickle"),
        "NUMPY_VERSION": request.runtime_profile.pin("numpy"),
        "PANDAS_VERSION": request.runtime_profile.pin("pandas"),
        "TEMPLATE_VERSION_JSON": _json_scalar(TEMPLATE_VERSION),
        "WORKFLOW_ID_JSON": _json_scalar(WORKFLOW_ID),
    }
    if request.target != "cpu-iot":
        accelerator_recommendation = (
            f"目标 {request.target}；算力 {request.gpu_compute_capability}；"
            f"显存至少 {request.gpu_memory_gb_min:g} GB；"
            f"CUDA {request.cuda_version}；"
            f"{request.framework_name} {request.framework_version}；"
            f"制品格式 {request.artifact_format}。"
        )
        values.update(
            {
                "ACCELERATOR_RECOMMENDATION_JSON": _json_scalar(
                    accelerator_recommendation
                ),
                "GPU_COMPUTE_CAPABILITY_JSON": _json_scalar(
                    request.gpu_compute_capability
                ),
                "GPU_MEMORY_GB_MIN": _json_scalar(request.gpu_memory_gb_min),
                "CUDA_VERSION_JSON": _json_scalar(request.cuda_version),
                "FRAMEWORK_NAME_JSON": _json_scalar(request.framework_name),
                "FRAMEWORK_VERSION_JSON": _json_scalar(request.framework_version),
                "ARTIFACT_FORMAT_JSON": _json_scalar(request.artifact_format),
            }
        )

    target_template_path = PurePosixPath("targets") / (f"{request.target}.yaml.tmpl")
    target_block = _render(
        _read_template(target_template_path),
        values,
        target_template_path.as_posix(),
    ).rstrip("\n")
    values["TARGET_BLOCK"] = target_block
    if request.serving_mode == "point-code":
        serving_path = PurePosixPath("targets/point-code-serving.yaml.tmpl")
        values["SERVING_BLOCK"] = _render(
            _read_template(serving_path), values, serving_path.as_posix()
        ).rstrip("\n")
    else:
        values["SERVING_BLOCK"] = ""
    if request.target == "cpu-iot":
        values["TARGET_DECISIONS_BLOCK"] = ""
    else:
        target_decisions_path = PurePosixPath("targets") / (
            f"{request.target}-decisions.yaml.tmpl"
        )
        values["TARGET_DECISIONS_BLOCK"] = _render(
            _read_template(target_decisions_path),
            values,
            target_decisions_path.as_posix(),
        ).rstrip("\n")

    rendered: dict[PurePosixPath, str] = {}
    for output, source in _TEMPLATE_FILES.items():
        content = _render(
            _read_template(source),
            values,
            source.as_posix(),
        )
        rendered[output] = content
    if request.serving_mode == "point-code":
        binding_path = PurePosixPath("targets/point-code-bindings.yaml.tmpl")
        rendered[PurePosixPath("input-bindings.yaml")] = _render(
            _read_template(binding_path), values, binding_path.as_posix()
        )

    manifest = yaml.safe_load(rendered[PurePosixPath("model.yaml")])
    if not isinstance(manifest, dict):
        raise WorkspaceError(
            "template_invalid", "rendered model.yaml is not a YAML object"
        )
    return rendered


def _drafts_root(models_root: Path) -> Path:
    return models_root / "_drafts"


def _refuse_symlink(path: Path, label: str) -> None:
    candidate = path
    while True:
        if is_link_or_junction(candidate):
            raise WorkspaceError(
                "unsafe_workspace_path",
                f"{label} must not pass through a link or junction: {candidate}",
            )
        parent = candidate.parent
        if parent == candidate:
            return
        candidate = parent


def _existing_model_ids(models_root: Path) -> dict[str, Path]:
    if not models_root.exists():
        return {}
    manifests: list[Path] = []
    try:
        children = tuple(models_root.iterdir())
    except OSError as error:
        raise WorkspaceError(
            "model_inventory_unreadable", "existing model inventory cannot be read"
        ) from error
    for child in children:
        if is_link_or_junction(child):
            raise WorkspaceError(
                "unsafe_workspace_path",
                f"model inventory must not contain a link or junction: {child}",
            )
        if not child.is_dir():
            continue
        if child.name == "_drafts":
            try:
                draft_children = tuple(child.iterdir())
            except OSError as error:
                raise WorkspaceError(
                    "model_inventory_unreadable",
                    "draft model inventory cannot be read",
                ) from error
            for draft in draft_children:
                if is_link_or_junction(draft):
                    raise WorkspaceError(
                        "unsafe_workspace_path",
                        f"draft inventory must not contain a link or junction: {draft}",
                    )
                if not draft.is_dir():
                    continue
                manifest = draft / "model.yaml"
                if is_link_or_junction(manifest):
                    raise WorkspaceError(
                        "unsafe_workspace_path",
                        f"draft manifest must not be a link or junction: {manifest}",
                    )
                if not manifest.is_file():
                    raise WorkspaceError(
                        "model_inventory_invalid",
                        f"draft workspace has no model.yaml: {draft}",
                    )
                manifests.append(manifest)
            continue
        manifest = child / "model.yaml"
        if is_link_or_junction(manifest):
            raise WorkspaceError(
                "unsafe_workspace_path",
                f"model manifest must not be a link or junction: {manifest}",
            )
        if manifest.is_file():
            manifests.append(manifest)

    inventory: dict[str, list[Path]] = {}
    for manifest_path in manifests:
        try:
            report = validate_manifest(manifest_path)
        except ManifestError as error:
            raise WorkspaceError(
                "model_inventory_invalid",
                f"existing model manifest cannot be parsed: {manifest_path}",
            ) from error
        if not report.valid or report.model_id == "<unknown>":
            raise WorkspaceError(
                "model_inventory_invalid",
                f"existing model manifest violates the asset contract: {manifest_path}",
            )
        identity = report.model_id.casefold()
        inventory.setdefault(identity, []).append(manifest_path)
    identities: dict[str, Path] = {}
    for identity, locations in inventory.items():
        if len(locations) == 1:
            identities[identity] = locations[0]
            continue
        active = [path for path in locations if path.parent.parent == models_root]
        drafts = [path for path in locations if path.parent.parent == _drafts_root(models_root)]
        if len(active) == 1 and len(drafts) == len(locations) - 1:
            from model_lab.maintenance import valid_maintenance_fork_set

            if valid_maintenance_fork_set(active[0], drafts):
                identities[identity] = active[0]
                continue
        if not active and len(drafts) == len(locations):
            from model_lab.draft_upgrade import valid_draft_upgrade_fork_set

            if valid_draft_upgrade_fork_set(drafts):
                identities[identity] = next(path for path in drafts if path.parent.name == identity)
                continue
        raise WorkspaceError(
            "model_inventory_duplicate_id",
            f"existing model ID is duplicated: {identity}",
        )
    return identities


def _check_destination(models_root: Path, model_id: str) -> Path:
    _refuse_symlink(models_root, "models root")
    if models_root.exists() and not models_root.is_dir():
        raise WorkspaceError(
            "invalid_models_root", f"models root is not a directory: {models_root}"
        )
    drafts_root = _drafts_root(models_root)
    _refuse_symlink(drafts_root, "drafts root")
    destination = drafts_root / model_id
    if is_link_or_junction(destination) or destination.exists():
        raise WorkspaceError(
            "workspace_exists", f"workspace already exists: {destination}"
        )
    if drafts_root.is_dir():
        try:
            collision = next(
                (
                    child
                    for child in drafts_root.iterdir()
                    if child.name.casefold() == model_id.casefold()
                ),
                None,
            )
        except OSError as error:
            raise WorkspaceError(
                "model_inventory_unreadable",
                "draft model inventory cannot be inspected safely",
            ) from error
        if collision is not None:
            raise WorkspaceError(
                "workspace_exists", f"workspace name collides with: {collision}"
            )
    identity = _existing_model_ids(models_root).get(model_id.casefold())
    if identity is not None:
        raise WorkspaceError(
            "model_id_exists", f"model_id is already declared by: {identity}"
        )
    return destination


def _write_workspace(
    destination: Path,
    rendered: dict[PurePosixPath, str | bytes],
) -> None:
    drafts_root = destination.parent
    temporary: Path | None = None
    try:
        drafts_root.mkdir(parents=True, exist_ok=True)
        _refuse_symlink(drafts_root, "drafts root")
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}-", dir=str(drafts_root))
        )
        for relative, content in rendered.items():
            target = temporary.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                with target.open("xb") as handle:
                    handle.write(content)
            else:
                with target.open("x", encoding="utf-8", newline="\n") as handle:
                    handle.write(content)
        _refuse_symlink(drafts_root, "drafts root")
        if is_link_or_junction(destination) or destination.exists():
            raise WorkspaceError(
                "workspace_exists", f"workspace already exists: {destination}"
            )
        temporary.rename(destination)
    except WorkspaceError:
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        raise
    except OSError as error:
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        code = (
            "workspace_exists"
            if isinstance(error, FileExistsError)
            else "workspace_write_failed"
        )
        raise WorkspaceError(
            code, f"workspace could not be created: {error}"
        ) from error


def scaffold_model(
    request: ScaffoldRequest,
    models_root: str | Path,
    *,
    apply: bool = False,
) -> ScaffoldReport:
    """Plan or atomically create one draft model workspace."""

    normalized = _normalize_request(request)
    root = ensure_local_filesystem_path(models_root, "models root")
    framework_root = Path(__file__).resolve().parents[1]
    if root.resolve() == framework_root or root.resolve().is_relative_to(framework_root):
        raise WorkspaceError(
            "models_root_inside_framework",
            "private models root must be outside the shared framework checkout",
        )
    try:
        destination = _check_destination(root, normalized.model_id)
    except OSError as error:
        raise WorkspaceError(
            "model_inventory_unreadable",
            "model inventory cannot be inspected safely",
        ) from error
    rendered = _render_files(normalized)
    relative_files = tuple(sorted(path.as_posix() for path in rendered))
    if apply:
        _write_workspace(destination, rendered)
        status = "created"
    else:
        status = "planned"
    return ScaffoldReport(
        status=status,
        model_id=normalized.model_id,
        target=normalized.target,
        serving_mode=normalized.serving_mode,
        destination=str(destination.resolve(strict=False)),
        files=relative_files,
        runtime_profile=normalized.runtime_profile.profile_id,
    )
