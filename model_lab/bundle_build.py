"""Build and verify one local MLflow service bundle from one model workspace.

This module has no remote publish path. Applying the build executes only the
explicit ``src/model.py`` and never scans the checkout for other Python files.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib.metadata import version
import json
from pathlib import Path, PurePosixPath
import runpy
import shutil
from tempfile import TemporaryDirectory
import yaml  # type: ignore[import-untyped]

from model_lab.decisions import load_decision_register
from model_lab.manifest import LifecycleState, load_validated_asset
from model_lab.path_safety import is_link_or_junction
from model_lab.point_serving import parse_point_serving, parse_provider_spec
from model_lab.provider_archive import ProviderArchiveError, read_provider_archive
from model_lab.runtime_contract import check_build_environment, verify_mlflow_bundle
from model_lab.environment import ensure_external_workspace


_MAX_SOURCE_BYTES = 1024 * 1024
_MAX_SAMPLE_BYTES = 20 * 1024 * 1024


class BundleBuildError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BundleBuildReport:
    status: str
    model_id: str
    bundle_path: str
    source_sha256: str
    sample_sha256: str
    bundle_sha256: str | None
    sample_rows: int | None
    serving_mode: str = "numeric"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "model_id": self.model_id,
            "bundle_path": self.bundle_path,
            "source_sha256": self.source_sha256,
            "sample_sha256": self.sample_sha256,
            "bundle_sha256": self.bundle_sha256,
            "sample_rows": self.sample_rows,
            "serving_mode": self.serving_mode,
            "online_iot_verified": False if self.serving_mode == "point-code" else "not-applicable",
            "safety": {
                "source_scan": "src/model.py only",
                "model_code_executed": self.status == "built",
                "model_code_side_effects": "not sandboxed",
                "framework_remote_writes": False,
            },
        }

    def as_text(self) -> str:
        online_note = (
            "\nPoint-code mode: online IoT not verified; not releasable"
            if self.serving_mode == "point-code" else ""
        )
        return (
            f"{self.model_id}: bundle {self.status}\n"
            f"Bundle: {self.bundle_path}\n"
            f"Source SHA-256: {self.source_sha256}\n"
            f"Bundle SHA-256: {self.bundle_sha256 or '<not built>'}\n"
            "Safety: framework does not publish; model source runs on --apply"
            f"{online_note}"
        )


def _regular_file(path: Path, *, maximum: int, label: str) -> bytes:
    if is_link_or_junction(path) or not path.is_file():
        raise BundleBuildError("unsafe_input", f"{label} must be a regular local file: {path}")
    if path.stat().st_size > maximum:
        raise BundleBuildError("input_too_large", f"{label} exceeds {maximum} bytes: {path}")
    return path.read_bytes()


def _portable_mlmodel(bundle: Path) -> None:
    """Rewrite MLflow's recorded artifact locations into bundle-relative POSIX paths.

    MLflow records native separators and the saving host's own source paths. A bundle built on
    Windows then carries ``artifacts\\logic_bundle.pkl``, which the Linux runtime cannot resolve,
    and the recorded ``uri`` leaks the build machine's directory layout into the published
    artifact. Only recorded locations that normalize to a safe relative path are rewritten;
    anything absolute, escaping, or malformed is left untouched so bundle verification reports it.
    """
    path = bundle / "MLmodel"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        return
    flavors = document.get("flavors")
    python_function = flavors.get("python_function") if isinstance(flavors, dict) else None
    mappings = (
        python_function.get("artifacts") if isinstance(python_function, dict) else None
    )
    if isinstance(mappings, dict):
        entries: list[object] = list(mappings.values())
    elif isinstance(mappings, list):
        entries = list(mappings)
    else:
        return
    changed = False
    for value in entries:
        if not isinstance(value, dict):
            continue
        recorded = value.get("path")
        if not isinstance(recorded, str) or not recorded:
            continue
        portable = PurePosixPath(recorded.replace("\\", "/"))
        if (
            portable.is_absolute()
            or ":" in recorded
            or any(part in {"", ".", ".."} for part in portable.parts)
        ):
            continue
        safe = portable.as_posix()
        if recorded != safe:
            value["path"] = safe
            changed = True
        if value.get("uri") != safe:
            value["uri"] = safe
            changed = True
    if not changed:
        return
    path.write_text(
        yaml.safe_dump(
            document, allow_unicode=True, sort_keys=False, default_flow_style=False
        ),
        encoding="utf-8",
        newline="\n",
    )



def _tree_digest(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(
        directory.rglob("*"), key=lambda item: item.relative_to(directory).as_posix()
    ):
        if is_link_or_junction(path):
            raise BundleBuildError("unsafe_bundle", f"bundle contains unsafe path: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise BundleBuildError("unsafe_bundle", f"bundle contains unsafe path: {path}")
        relative = path.relative_to(directory).as_posix()
        if (
            relative.startswith("code/")
            and "/__pycache__/" in relative
            and path.suffix.casefold() in {".pyc", ".pyo"}
        ):
            continue
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def build_bundle(
    manifest_path: str | Path,
    sample_csv: str | Path,
    *,
    apply: bool = False,
    iot_package_zip: str | Path | None = None,
) -> BundleBuildReport:
    """Plan or build a CPU-IoT pyfunc bundle, refusing to overwrite an existing one."""

    manifest = Path(manifest_path)
    if manifest.is_dir():
        manifest /= "model.yaml"
    if apply:
        try:
            ensure_external_workspace(manifest)
        except ValueError as error:
            raise BundleBuildError(getattr(error, "code", "unsafe_workspace_path"), str(error)) from error
    validation, asset = load_validated_asset(manifest)
    if asset is None:
        first = validation.issues[0]
        raise BundleBuildError(first.code, first.message)
    if asset.summary.target != "cpu-iot" or asset.environment_requirements is None:
        raise BundleBuildError("unsupported_target", "bundle-build currently supports CPU-IoT models with a target environment")
    inputs = [field.name for field in asset.summary.inputs]
    outputs = [field.name for field in asset.summary.outputs]
    document = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    serving = parse_point_serving(document, manifest)
    provider_spec = parse_provider_spec(document)
    if provider_spec is not None:
        if iot_package_zip is None:
            raise BundleBuildError("point_provider_archive_required", "pass --iot-package-zip with the reviewed company Python 3.11 archive")
        try:
            provider_files = read_provider_archive(iot_package_zip, provider_spec)
        except ProviderArchiveError as error:
            raise BundleBuildError(error.code, str(error)) from error
    else:
        if iot_package_zip is not None:
            raise BundleBuildError("point_provider_unexpected", "this model has no company IoT provider contract")
        provider_files = None
    if not inputs or not outputs:
        raise BundleBuildError("empty_contract", "model.yaml must declare input and output fields")

    workspace = manifest.parent
    source = workspace / "src" / "model.py"
    if (
        is_link_or_junction(workspace / "src")
        or not source.resolve(strict=False).is_relative_to(workspace.resolve(strict=False))
    ):
        raise BundleBuildError("unsafe_source", "model source must remain inside the workspace")
    source_bytes = _regular_file(source, maximum=_MAX_SOURCE_BYTES, label="model source")
    sample = Path(sample_csv)
    sample_bytes = _regular_file(sample, maximum=_MAX_SAMPLE_BYTES, label="sample CSV")
    source_digest = hashlib.sha256(source_bytes).hexdigest()
    sample_digest = hashlib.sha256(sample_bytes).hexdigest()
    destination = workspace / "release-bundle" / "service"
    if destination.exists() or is_link_or_junction(destination):
        raise BundleBuildError("bundle_exists", f"existing bundle will not be overwritten: {destination}")

    report = BundleBuildReport(
        status="planned",
        model_id=asset.summary.model_id,
        bundle_path=str(destination),
        source_sha256=source_digest,
        sample_sha256=sample_digest,
        bundle_sha256=None,
        sample_rows=None,
        serving_mode="point-code" if serving else "numeric",
    )
    if not apply:
        return report

    if asset.summary.lifecycle_state == LifecycleState.DRAFT:
        raise BundleBuildError(
            "lifecycle_not_specified",
            "confirm the model contract and advance beyond draft before building",
        )
    if workspace.parent.name == "_drafts":
        decisions = load_decision_register(
            workspace, expected_model_id=asset.summary.model_id, required=True
        )
        if not decisions.valid:
            decision_issue = decisions.issues[0]
            raise BundleBuildError(decision_issue.code, decision_issue.message)
        unresolved = [
            item.decision_id
            for item in decisions.decisions
            if item.blocks_transition == "specified" and item.status == "open"
        ]
        if unresolved:
            raise BundleBuildError(
                "requirements_unconfirmed",
                f"required design decisions remain open: {unresolved}",
            )

    environment = check_build_environment(manifest)
    if not environment.valid:
        first = environment.issues[0]
        raise BundleBuildError(first.code, first.message)

    # The control-plane package intentionally does not install MLflow. Import it
    # only for an explicitly applied build on the exact target interpreter.
    import cloudpickle
    import mlflow.models
    import mlflow.pyfunc
    from mlflow.models.signature import ModelSignature
    from mlflow.types.schema import Array, ColSpec, DataType, Schema
    import pandas as pd
    from model_lab_service_runtime import SourceModel

    pins = dict(environment.dependency_pins)
    mismatched = {
        name: (pin, version(name)) for name, pin in pins.items() if version(name) != pin
    }
    if mismatched:
        raise BundleBuildError("installed_dependency_mismatch", f"installed packages differ from the model lock: {mismatched}")

    data = pd.read_csv(sample)
    if data.empty:
        raise BundleBuildError("empty_sample", "sample CSV has no rows")
    missing = [name for name in inputs if name not in data.columns]
    if missing:
        raise BundleBuildError("sample_columns_missing", f"sample CSV lacks model inputs: {missing}")
    example = data.loc[:, inputs].head(8).copy()
    for field in asset.summary.inputs:
        if field.field_type == "number":
            # A sample CSV containing only whole numbers must not create an
            # MLflow int64 signature that rejects later float64 requests.
            example[field.name] = pd.to_numeric(example[field.name], errors="raise").astype("float64")
    namespace = runpy.run_path(str(source), run_name="model_lab_build_model")
    predictor = namespace.get("predict")
    if not callable(predictor):
        raise BundleBuildError("predict_missing", "src/model.py must expose predict(inputs)")
    if serving and serving.feature_adapter == "source-history-v1" and not callable(namespace.get("prepare_features")):
        raise BundleBuildError("history_adapter_missing", "source-history-v1 requires prepare_features(raw, request) in src/model.py")
    expected = predictor(example.copy())
    if not isinstance(expected, pd.DataFrame) or tuple(expected.columns) != tuple(outputs) or len(expected) != len(example):
        raise BundleBuildError("output_contract_mismatch", "model predict output does not match model.yaml")
    service_example = pd.DataFrame([serving.example_row()]) if serving else example.head(1)
    if serving:
        service_signature = ModelSignature(
            inputs=Schema([
                ColSpec(DataType.string, serving.factory_field),
                *[ColSpec(Array(DataType.string), name) for name in serving.point_fields],
                ColSpec(DataType.string, serving.start_field, required=serving.time_strategy != "latest-complete"),
                *[ColSpec(DataType.double, name) for name in serving.control_fields],
                *[ColSpec(DataType.double, name, required=False) for name in serving.evaluation_fields],
            ]),
            outputs=mlflow.models.infer_signature(example.head(1), expected.head(1)).outputs,
        )
    else:
        service_signature = mlflow.models.infer_signature(service_example, expected.head(1))

    bundle_root = destination.parent
    if is_link_or_junction(bundle_root):
        raise BundleBuildError("unsafe_bundle_root", "release-bundle must not be a link")
    bundle_root.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix=".bundle-build-", dir=bundle_root) as temporary:
        stage = Path(temporary)
        payload = stage / "payload"
        payload.mkdir()
        logic = payload / "logic_bundle.pkl"
        custom_sources = payload / "custom_sources.pkl"
        with logic.open("wb") as stream:
            cloudpickle.dump(
                {
                    "source_sha256": source_digest,
                    "inputs": inputs,
                    "outputs": outputs,
                    "serving": serving.as_runtime_dict() if serving else None,
                },
                stream,
            )
        with custom_sources.open("wb") as stream:
            cloudpickle.dump({"model": source_bytes.decode("utf-8")}, stream)

        # Copy only the reviewed runtime file; MLflow must never walk the repo.
        runtime_source = Path(__file__).resolve().parents[1] / "model_lab_service_runtime" / "__init__.py"
        code_directory = stage / "code-src" / "model_lab_service_runtime"
        code_directory.mkdir(parents=True)
        shutil.copyfile(runtime_source, code_directory / "__init__.py")
        code_paths = [str(code_directory)]
        artifacts = {
            "logic_bundle": str(logic),
            "custom_sources": str(custom_sources),
            "model_file": str(source),
        }
        if provider_spec is not None and provider_files is not None:
            provider_module = stage / "code-src" / "mlops_deploy_iot_armor2_311.py"
            provider_module.write_bytes(provider_files["module"])
            pyarmor_package = stage / "code-src" / "pyarmor_runtime_000000"
            native_directory = pyarmor_package / "linux_x86_64"
            native_directory.mkdir(parents=True)
            (pyarmor_package / "__init__.py").write_bytes(provider_files["runtime_init"])
            native_file = native_directory / "pyarmor_runtime.so"
            native_file.write_bytes(provider_files["native"])
            provenance = payload / "company_iot_provenance.json"
            provenance.write_text(json.dumps({
                "source": "infra_model_deploy.zip:resources/infra/311",
                "archive_sha256": provider_spec.archive_sha256,
                "module_sha256": provider_spec.module_sha256,
                "runtime_init_sha256": provider_spec.runtime_init_sha256,
                "native_sha256": provider_spec.native_sha256,
                "python_abi": "cp311",
                "platform": "linux_x86_64",
            }, sort_keys=True), encoding="utf-8")
            code_paths.extend((str(provider_module), str(pyarmor_package)))
            artifacts["company_iot_native"] = str(native_file)
            artifacts["company_iot_provenance"] = str(provenance)

        staged_bundle = stage / "service"
        mlflow.pyfunc.save_model(
            path=str(staged_bundle),
            python_model=SourceModel(),
            artifacts=artifacts,
            code_paths=code_paths,
            signature=service_signature,
            input_example=None if serving else example.head(1),
            pip_requirements=[f"{name}=={pin}" for name, pin in pins.items()],
        )
        _portable_mlmodel(staged_bundle)
        if serving is None:
            loaded = mlflow.pyfunc.load_model(str(staged_bundle))
            actual = loaded.predict(example)
            pd.testing.assert_frame_equal(
                actual.reset_index(drop=True),
                expected.reset_index(drop=True),
                check_dtype=False,
                rtol=1e-10,
                atol=1e-10,
            )
        preflight = verify_mlflow_bundle(manifest, staged_bundle)
        if not preflight.valid:
            first = preflight.issues[0]
            raise BundleBuildError(first.code, first.message)
        if destination.exists():
            raise BundleBuildError("bundle_exists", f"bundle appeared during build: {destination}")
        staged_bundle.rename(destination)

    return BundleBuildReport(
        status="built",
        model_id=asset.summary.model_id,
        bundle_path=str(destination),
        source_sha256=source_digest,
        sample_sha256=sample_digest,
        bundle_sha256=_tree_digest(destination),
        sample_rows=len(data),
        serving_mode="point-code" if serving else "numeric",
    )


__all__ = ["BundleBuildError", "BundleBuildReport", "build_bundle"]
