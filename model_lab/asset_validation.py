from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any

from model_lab.baseline import BaselineRunner, get_baseline_runner
from model_lab.manifest import (
    ManifestError,
    ValidationCheck,
    ValidationIssue,
    _MLFLOW_EXPERIMENT,
    _MLFLOW_RUN_ID,
    _PLATFORM_DEPLOYMENT_STATUSES,
    _SEMANTIC_VERSION,
    _load_manifest,
)


_MAX_BASELINE_BYTES = 1_000_000
_MAX_LOCAL_TEXT_BYTES = 10_000_000


def _issue(
    code: str,
    path: str,
    message: str,
    suggestion: str,
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        path=path,
        message=message,
        suggestion=suggestion,
    )


def _resolve_reference(
    manifest_path: Path,
    reference: str,
    field_path: str,
) -> tuple[Path | None, ValidationIssue | None]:
    candidate = Path(reference)
    if candidate.is_absolute():
        return None, _issue(
            "invalid_local_reference",
            field_path,
            "local asset references must be relative to the model manifest",
            f"replace {field_path} with a portable relative path",
        )
    try:
        resolved = (manifest_path.parent / candidate).resolve()
    except (OSError, RuntimeError, ValueError):
        return None, _issue(
            "invalid_local_reference",
            field_path,
            "local asset reference cannot be resolved safely",
            f"replace {field_path} with a valid relative path",
        )
    manifest_resolved = manifest_path.resolve()
    repository_root = manifest_resolved.parent
    for parent in manifest_resolved.parents:
        if parent.name == "models":
            repository_root = parent.parent
            break
    try:
        resolved.relative_to(repository_root)
    except ValueError:
        return None, _issue(
            "reference_outside_repository",
            field_path,
            "local asset reference resolves outside the trusted repository root",
            f"move the referenced file inside the repository and update {field_path}",
        )
    return resolved, None


def _sha256_utf8_lf(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _validate_file_digest(
    manifest_path: Path,
    reference: str,
    expected_digest: str,
    reference_path: str,
    digest_path: str,
    max_bytes: int = _MAX_LOCAL_TEXT_BYTES,
) -> tuple[str | None, list[ValidationIssue]]:
    resolved, reference_issue = _resolve_reference(
        manifest_path, reference, reference_path
    )
    if reference_issue is not None:
        return None, [reference_issue]
    assert resolved is not None
    try:
        with resolved.open("rb") as stream:
            content = stream.read(max_bytes + 1)
    except FileNotFoundError:
        return None, [
            _issue(
                "referenced_file_missing",
                reference_path,
                "referenced asset file does not exist",
                f"restore the file referenced by {reference_path}",
            )
        ]
    except OSError:
        return None, [
            _issue(
                "referenced_file_unreadable",
                reference_path,
                "referenced asset file cannot be read",
                f"make the file referenced by {reference_path} readable",
            )
        ]
    if len(content) > max_bytes:
        return None, [
            _issue(
                "referenced_file_too_large",
                reference_path,
                f"referenced text file exceeds the {max_bytes}-byte safety limit",
                "replace it with a small sanitized text asset",
            )
        ]
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None, [
            _issue(
                "referenced_file_unreadable",
                reference_path,
                "referenced asset file must be UTF-8 text",
                f"replace the file referenced by {reference_path} with UTF-8 text",
            )
        ]
    if _sha256_utf8_lf(text) != expected_digest:
        return None, [
            _issue(
                "reference_digest_mismatch",
                digest_path,
                "referenced file does not match its recorded SHA-256 digest",
                "restore the released file or create a new model version",
            )
        ]
    return text, []


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json_document(
    text: str | None,
    field_path: str,
) -> tuple[object | None, list[ValidationIssue]]:
    if text is None:
        return None, []
    try:
        if len(text.encode("utf-8")) > _MAX_BASELINE_BYTES:
            return None, [
                _issue(
                    "baseline_document_too_large",
                    field_path,
                    "baseline document exceeds the 1 MB offline-fixture limit",
                    "replace it with a small sanitized fixture",
                )
            ]
        document = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ):
        return None, [
            _issue(
                "baseline_document_invalid",
                field_path,
                "baseline document must be strict UTF-8 JSON with unique keys",
                "restore the released sanitized JSON fixture",
            )
        ]
    return document, []


def _compare_snapshot_mapping(
    actual: object,
    expected: Mapping[str, object],
    path: str,
) -> list[ValidationIssue]:
    if not isinstance(actual, Mapping):
        return [
            _issue(
                "release_snapshot_mismatch",
                path,
                "release snapshot section must be an object",
                "restore the current release snapshot",
            )
        ]

    issues: list[ValidationIssue] = []
    actual_keys = set(actual)
    expected_keys = set(expected)
    for field in sorted(expected_keys - actual_keys):
        field_path = f"{path}.{field}"
        issues.append(
            _issue(
                "release_snapshot_mismatch",
                field_path,
                "release snapshot field is missing",
                "restore the field from the current model asset manifest",
            )
        )
    for field in sorted(actual_keys - expected_keys, key=str):
        field_path = f"{path}.{field}"
        issues.append(
            _issue(
                "release_snapshot_mismatch",
                field_path,
                "release snapshot contains an untracked field",
                "remove the field or create a new model release contract",
            )
        )
    for field in sorted(expected_keys & actual_keys):
        field_path = f"{path}.{field}"
        expected_value = expected[field]
        actual_value = actual[field]
        if isinstance(expected_value, Mapping):
            issues.extend(
                _compare_snapshot_mapping(
                    actual_value,
                    expected_value,
                    field_path,
                )
            )
        elif not _strict_equal(actual_value, expected_value):
            issues.append(
                _issue(
                    "release_snapshot_mismatch",
                    field_path,
                    "release snapshot value differs from the current asset record",
                    "restore the released value or create a new model version",
                )
            )
    return issues


def _strict_equal(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, Mapping) and isinstance(expected, Mapping):
        return set(actual) == set(expected) and all(
            _strict_equal(actual[key], expected[key]) for key in actual
        )
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _strict_equal(actual_item, expected_item)
            for actual_item, expected_item in zip(actual, expected)
        )
    return actual == expected


def _snapshot_issues(
    snapshot: object,
    document: Mapping[str, Any],
    baseline_details: Mapping[str, object] | None,
) -> list[ValidationIssue]:
    if not isinstance(snapshot, Mapping):
        return [
            _issue(
                "release_snapshot_mismatch",
                "release.current",
                "release snapshot must be a YAML object",
                "restore the current release snapshot",
            )
        ]

    captured_on = snapshot.get("captured_on")
    captured_on_valid = False
    if isinstance(captured_on, str):
        try:
            date.fromisoformat(captured_on)
        except ValueError:
            pass
        else:
            captured_on_valid = True

    expected = {
        "snapshot_version": "1.0",
        "model_id": document["model"]["id"],
        "model_version": document["model"]["version"],
        "lifecycle_state": document["model"]["lifecycle_state"],
        "captured_on": captured_on,
        "target": dict(document["target"]),
        "contract": dict(document["contract"]),
        "source": dict(document["source"]),
        "platform": dict(document["platform"]),
        "migration": dict(document["migration"]),
    }
    artifacts = document["artifacts"]
    if len(artifacts) == 1:
        expected["artifact"] = dict(artifacts[0])
    else:
        expected["artifacts"] = [dict(artifact) for artifact in artifacts]

    baseline = document.get("baseline")
    if isinstance(baseline, Mapping):
        baseline_snapshot = dict(baseline)
        if baseline_details is not None:
            baseline_snapshot.update(
                {
                    "aggregation": baseline_details["aggregation"],
                    "parameters": baseline_details["parameters"],
                    "rounding": baseline_details["rounding"],
                }
            )
        expected["baseline"] = baseline_snapshot
    issues = _compare_snapshot_mapping(snapshot, expected, "release.current")
    if not captured_on_valid:
        issues.append(
            _issue(
                "release_snapshot_mismatch",
                "release.current.captured_on",
                "captured_on must be an ISO calendar date",
                "record the date when this release snapshot was captured",
            )
        )
    return issues


def _historical_snapshot_issues(
    snapshot: object,
    document: Mapping[str, Any],
    reference: str,
    field_path: str,
) -> tuple[list[ValidationIssue], dict[str, str] | None]:
    if not isinstance(snapshot, Mapping):
        return (
            [
                _issue(
                    "historical_release_snapshot_invalid",
                    field_path,
                    "historical release snapshot must be a YAML object",
                    "restore the versioned historical release snapshot",
                )
            ],
            None,
        )

    expected_values = {
        "snapshot_version": "1.0",
        "model_id": document["model"]["id"],
        "model_version": Path(reference).stem,
    }
    issues: list[ValidationIssue] = []
    for field, expected in expected_values.items():
        if snapshot.get(field) != expected:
            issues.append(
                _issue(
                    "historical_release_snapshot_mismatch",
                    f"{field_path}.{field}",
                    "historical release identity does not match its manifest reference",
                    "restore the historical snapshot or correct release.history",
                )
            )
    lifecycle_state = snapshot.get("lifecycle_state")
    if lifecycle_state not in {"released", "retired"}:
        issues.append(
            _issue(
                "historical_release_snapshot_mismatch",
                f"{field_path}.lifecycle_state",
                "historical release must record a released or retired lifecycle",
                "restore the historical release lifecycle record",
            )
        )

    platform = snapshot.get("platform")
    platform_identity: dict[str, str] | None = None
    platform_path = f"{field_path}.platform"
    if not isinstance(platform, Mapping):
        issues.append(
            _issue(
                "historical_release_snapshot_mismatch",
                platform_path,
                "historical release must record its platform identity",
                "restore the historical platform record",
            )
        )
    else:
        provider = platform.get("provider")
        experiment = platform.get("experiment")
        run_id = platform.get("run_id")
        deployment_status = platform.get("deployment_status")
        platform_valid = True
        if provider != "mlflow":
            platform_valid = False
            issues.append(
                _issue(
                    "historical_release_snapshot_mismatch",
                    f"{platform_path}.provider",
                    "historical platform provider is not supported",
                    "restore the MLflow historical platform record",
                )
            )
        if not isinstance(experiment, str) or _MLFLOW_EXPERIMENT.fullmatch(
            experiment
        ) is None:
            platform_valid = False
            issues.append(
                _issue(
                    "historical_release_snapshot_mismatch",
                    f"{platform_path}.experiment",
                    "historical MLflow experiment ID is not canonical",
                    "restore the historical MLflow experiment ID",
                )
            )
        run_pending = run_id == "pending-archive"
        if not isinstance(run_id, str) or (
            not run_pending and _MLFLOW_RUN_ID.fullmatch(run_id) is None
        ):
            platform_valid = False
            issues.append(
                _issue(
                    "historical_release_snapshot_mismatch",
                    f"{platform_path}.run_id",
                    "historical MLflow run ID is not canonical",
                    "restore the historical run ID or pending-archive marker",
                )
            )
        if deployment_status not in _PLATFORM_DEPLOYMENT_STATUSES:
            platform_valid = False
            issues.append(
                _issue(
                    "historical_release_snapshot_mismatch",
                    f"{platform_path}.deployment_status",
                    "historical deployment status is not supported",
                    "restore the historical deployment status",
                )
            )
        if run_pending != (deployment_status == "pending-archive"):
            platform_valid = False
            issues.append(
                _issue(
                    "historical_release_snapshot_mismatch",
                    platform_path,
                    "historical run and deployment archive states disagree",
                    "restore a coherent historical platform record",
                )
            )
        if platform_valid:
            assert isinstance(provider, str)
            assert isinstance(experiment, str)
            assert isinstance(run_id, str)
            platform_identity = {
                "path": field_path,
                "provider": provider,
                "experiment": experiment,
                "run_id": run_id,
            }
    return issues, platform_identity


def _runner_contract_issues(
    document: Mapping[str, Any],
    runner: BaselineRunner,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if document["target"].get("kind") != runner.target_kind:
        issues.append(
            _issue(
                "behavior_baseline_contract_mismatch",
                "target.kind",
                "model target does not match the registered baseline runner",
                f"restore target.kind to {runner.target_kind} or create a new baseline kind",
            )
        )
    expected_contract = runner.expected_contract()
    contract = document["contract"]
    for direction in ("inputs", "outputs"):
        if contract.get(direction) != expected_contract[direction]:
            issues.append(
                _issue(
                    "behavior_baseline_contract_mismatch",
                    f"contract.{direction}",
                    "model contract differs from the registered behavior baseline",
                    "restore the released contract or create a new model version",
                )
            )
    return issues


def validate_asset_records(
    document: Mapping[str, Any],
    manifest_path: Path,
) -> tuple[tuple[ValidationIssue, ...], tuple[ValidationCheck, ...]]:
    issues: list[ValidationIssue] = []
    checks: list[ValidationCheck] = []

    source = document.get("source")
    if isinstance(source, Mapping):
        for reference_field, digest_field in (
            ("entrypoint", "entrypoint_sha256"),
            ("notebook", "notebook_sha256"),
            ("documentation", "documentation_sha256"),
        ):
            _, file_issues = _validate_file_digest(
                manifest_path,
                source[reference_field],
                source[digest_field],
                f"source.{reference_field}",
                f"source.{digest_field}",
            )
            issues.extend(file_issues)

    input_document: object | None = None
    expected_document: object | None = None
    replay_details: dict[str, object] | None = None
    replay_passed = False
    baseline = document.get("baseline")
    if isinstance(baseline, Mapping):
        input_text, input_issues = _validate_file_digest(
            manifest_path,
            baseline["input"],
            baseline["input_sha256"],
            "baseline.input",
            "baseline.input_sha256",
            _MAX_BASELINE_BYTES,
        )
        expected_text, expected_issues = _validate_file_digest(
            manifest_path,
            baseline["expected"],
            baseline["expected_sha256"],
            "baseline.expected",
            "baseline.expected_sha256",
            _MAX_BASELINE_BYTES,
        )
        issues.extend(input_issues)
        issues.extend(expected_issues)
        input_document, input_load_issues = _load_json_document(
            input_text, "baseline.input"
        )
        expected_document, expected_load_issues = _load_json_document(
            expected_text, "baseline.expected"
        )
        issues.extend(input_load_issues)
        issues.extend(expected_load_issues)

        runner = get_baseline_runner(str(baseline["kind"]))
        if runner is None:
            issues.append(
                _issue(
                    "unsupported_behavior_baseline",
                    "baseline.kind",
                    "no offline runner supports this behavior baseline kind",
                    "use a supported baseline runner or add one before release",
                )
            )
        else:
            issues.extend(_runner_contract_issues(document, runner))
        if (
            runner is not None
            and input_document is not None
            and expected_document is not None
        ):
            replay = runner.replay(input_document, expected_document)
            issues.extend(
                _issue(
                    problem.code,
                    problem.path,
                    problem.message,
                    problem.suggestion,
                )
                for problem in replay.problems
            )
            replay_details = dict(replay.check_details())
            replay_passed = not replay.problems

    release = document.get("release")
    if isinstance(release, Mapping):
        snapshot_path, path_issue = _resolve_reference(
            manifest_path,
            release["current"],
            "release.current",
        )
        snapshot: object | None = None
        snapshot_problem_count = len(issues)
        if path_issue is not None:
            issues.append(path_issue)
        elif snapshot_path is None or not snapshot_path.is_file():
            issues.append(
                _issue(
                    "referenced_file_missing",
                    "release.current",
                    "current release snapshot does not exist",
                    "restore the versioned release snapshot",
                )
            )
        else:
            try:
                snapshot = _load_manifest(snapshot_path)
            except ManifestError:
                issues.append(
                    _issue(
                        "release_snapshot_invalid",
                        "release.current",
                        "current release snapshot is not strict UTF-8 YAML",
                        "restore the versioned release snapshot",
                    )
                )
            else:
                if not isinstance(source, Mapping):
                    issues.append(
                        _issue(
                            "release_snapshot_incomplete_asset",
                            "release.current",
                            "release snapshots require source records",
                            "complete the model asset record",
                        )
                    )
                elif not isinstance(document.get("platform"), Mapping) or not isinstance(
                    document.get("migration"), Mapping
                ):
                    issues.append(
                        _issue(
                            "release_snapshot_incomplete_asset",
                            "release.current",
                            "release snapshots require platform and migration records",
                            "complete the model asset record",
                        )
                    )
                elif not document["artifacts"]:
                    issues.append(
                        _issue(
                            "release_snapshot_incomplete_asset",
                            "release.current.artifacts",
                            "release snapshots require at least one model artifact",
                            "record the current model artifacts",
                        )
                    )
                else:
                    issues.extend(
                        _snapshot_issues(snapshot, document, replay_details)
                    )
        if len(issues) == snapshot_problem_count and snapshot is not None:
            checks.append(
                ValidationCheck(
                    name="release_snapshot",
                    status="passed",
                    details=(
                        ("path", release["current"]),
                        ("model_version", document["model"]["version"]),
                    ),
                )
            )

        history = release.get("history", [])
        history_problem_count = len(issues)
        historical_platform_identities: list[dict[str, str]] = []
        seen_snapshot_paths: set[Path] = set()
        seen_release_versions = {str(document["model"]["version"])}
        if snapshot_path is not None:
            seen_snapshot_paths.add(snapshot_path)
        for index, reference in enumerate(history):
            field_path = f"release.history[{index}]"
            release_version = Path(reference).stem
            reference_valid = True
            if _SEMANTIC_VERSION.fullmatch(release_version) is None:
                reference_valid = False
                issues.append(
                    _issue(
                        "invalid_historical_release_version",
                        field_path,
                        "historical release version is not canonical SemVer",
                        "use a path such as releases/1.2.3.yaml",
                    )
                )
            if reference != f"releases/{release_version}.yaml":
                reference_valid = False
                issues.append(
                    _issue(
                        "invalid_historical_release_path",
                        field_path,
                        "historical releases must use releases/<version>.yaml",
                        "move the snapshot under the model releases directory",
                    )
                )
            if release_version in seen_release_versions:
                reference_valid = False
                issues.append(
                    _issue(
                        "duplicate_release_version",
                        field_path,
                        "release version is already declared by this model",
                        "keep one immutable snapshot for each release version",
                    )
                )
            elif _SEMANTIC_VERSION.fullmatch(release_version) is not None:
                seen_release_versions.add(release_version)
            if not reference_valid:
                continue
            historical_path, historical_path_issue = _resolve_reference(
                manifest_path,
                reference,
                field_path,
            )
            if historical_path_issue is not None:
                issues.append(historical_path_issue)
                continue
            assert historical_path is not None
            if historical_path in seen_snapshot_paths:
                issues.append(
                    _issue(
                        "duplicate_release_snapshot",
                        field_path,
                        "release snapshot is listed more than once",
                        "keep each current or historical snapshot reference unique",
                    )
                )
                continue
            seen_snapshot_paths.add(historical_path)
            if not historical_path.is_file():
                issues.append(
                    _issue(
                        "referenced_file_missing",
                        field_path,
                        "historical release snapshot does not exist",
                        "restore the versioned historical release snapshot",
                    )
                )
                continue
            try:
                historical_snapshot = _load_manifest(historical_path)
            except ManifestError:
                issues.append(
                    _issue(
                        "historical_release_snapshot_invalid",
                        field_path,
                        "historical release snapshot is not strict UTF-8 YAML",
                        "restore the versioned historical release snapshot",
                    )
                )
                continue
            snapshot_issues, platform_identity = _historical_snapshot_issues(
                historical_snapshot,
                document,
                reference,
                field_path,
            )
            issues.extend(snapshot_issues)
            if not snapshot_issues and platform_identity is not None:
                historical_platform_identities.append(platform_identity)
        if history and len(issues) == history_problem_count:
            checks.append(
                ValidationCheck(
                    name="historical_release_snapshots",
                    status="passed",
                    details=(
                        ("count", len(history)),
                        ("snapshots", tuple(historical_platform_identities)),
                    ),
                )
            )

    if replay_passed and replay_details is not None:
        checks.append(
            ValidationCheck(
                name="behavior_baseline",
                status="passed",
                details=tuple(replay_details.items()),
            )
        )
    return tuple(issues), tuple(checks)
