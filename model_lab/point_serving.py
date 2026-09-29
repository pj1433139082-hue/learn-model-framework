"""Opt-in, single-point serving contract for the first IoT vertical slice."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any
import unicodedata
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml  # type: ignore[import-untyped]

from model_lab.manifest import ValidationIssue, _UniqueKeySafeLoader
from model_lab.path_safety import is_link_or_junction


_SERVING_FIELDS = frozenset(
    {"mode", "factory_field", "point_field", "start_field", "rolling_window", "interval", "binding"}
)
_MULTI_SERVING_FIELDS = frozenset(
    {"mode", "factory_field", "point_fields", "start_field", "rolling_window", "interval", "binding"}
)
_BINDING_FIELDS = frozenset(
    {"schema_version", "model_id", "model_version", "factory_code", "point_field", "point_code"}
)
_MULTI_BINDING_FIELDS = frozenset(
    {"schema_version", "model_id", "model_version", "factory_code", "bindings"}
)
_LOGICAL_FIELDS = frozenset(
    {"input", "expected_signal", "expected_signal_kind", "unit", "aggregation", "points"}
)
_POINT_FIELDS = frozenset(
    {"code", "facility_name", "equipment_name", "acquisition_signal_name", "signal_kind", "unit"}
)
_POINT_FIELDS_21 = _POINT_FIELDS | {"point_id", "signal_code"}
_SIGNAL_KINDS = frozenset(
    {"instantaneous", "cumulative", "concentration", "temperature", "status", "quality", "other"}
)
_POINT_CODE = re.compile(r"[A-Za-z0-9_-]+")
_WINDOW = re.compile(r"[1-9][0-9]*(?:min|h|d)")
_INTERVAL = re.compile(r"[1-9][0-9]*(?:s|min|h)")
_TIME_FIELDS = frozenset({"strategy", "timezone", "iot_timestamp_timezone"})
_COMPLETE_TIME_FIELDS = _TIME_FIELDS | {"period"}
_COMPLETE_OPTIONAL_TIME_FIELDS = frozenset({"availability_lag"})
_OPTIONAL_SERVING_FIELDS = frozenset({"control_fields", "evaluation_fields", "provider", "feature_adapter"})
_PROVIDER_FIELDS = frozenset({"kind", "archive_sha256", "module_sha256", "runtime_init_sha256", "native_sha256"})


class PointServingError(ValueError):
    def __init__(self, code: str, path: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.path = path

    def as_issue(self) -> ValidationIssue:
        suggestion = (
            "fill model.yaml serving.point_fields, serving.factory_field and "
            "serving.start_field with distinct names; then fill input-bindings.yaml"
            if self.code == "point_serving_fields" and self.path == "serving.point_fields"
            else "correct the point-serving contract"
        )
        return ValidationIssue(self.code, self.path, str(self), suggestion)


@dataclass(frozen=True)
class ProviderArchiveSpec:
    archive_sha256: str
    module_sha256: str
    runtime_init_sha256: str
    native_sha256: str


def parse_provider_spec(document: object) -> ProviderArchiveSpec | None:
    if not isinstance(document, dict) or not isinstance(document.get("serving"), dict):
        return None
    serving = document["serving"]
    if serving.get("mode") != "point-code":
        return None
    target = document.get("target", {})
    environment = target.get("environment", {}) if isinstance(target, dict) else {}
    linux_target = isinstance(environment, dict) and environment.get("operating_system") == "linux"
    raw = serving.get("provider")
    if raw is None:
        if linux_target:
            raise PointServingError("point_provider_required", "serving.provider", "Linux point-mode builds require a reviewed Python 3.11 company provider archive")
        return None
    provider = _exact_fields(raw, _PROVIDER_FIELDS, "serving.provider")
    if provider["kind"] != "company-iot-311" or not linux_target or environment.get("architecture") != "x86_64":
        raise PointServingError("point_provider_target_mismatch", "serving.provider", "company-iot-311 requires Linux x86_64")
    if environment.get("python") != ">=3.11,<3.12":
        raise PointServingError("point_provider_abi_mismatch", "target.environment.python", "company-iot-311 requires the exact Python range >=3.11,<3.12")
    native = environment.get("native_runtime", {})
    if not isinstance(native, dict) or native != {
        "kind": "pyarmor", "python_abi": "cp311", "platform": "linux_x86_64", "artifact": "company_iot_native"
    }:
        raise PointServingError("point_provider_abi_mismatch", "target.environment.native_runtime", "company-iot-311 requires cp311 Linux x86_64 PyArmor runtime")
    for label in _PROVIDER_FIELDS - {"kind"}:
        digest = provider[label]
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise PointServingError("point_provider_digest_invalid", f"serving.provider.{label}", "record a lowercase SHA-256 digest")
    artifacts = document.get("artifacts", [])
    native_records = [item for item in artifacts if isinstance(item, dict) and item.get("name") == "company_iot_native"] if isinstance(artifacts, list) else []
    if len(native_records) != 1 or native_records[0].get("sha256") != provider["native_sha256"]:
        raise PointServingError("point_provider_native_record_mismatch", "artifacts", "register the Linux native runtime with the same SHA-256")
    return ProviderArchiveSpec(**{name: provider[name] for name in _PROVIDER_FIELDS - {"kind"}})


@dataclass(frozen=True)
class PointServingContract:
    factory_field: str
    point_field: str
    start_field: str
    rolling_window: str
    interval: str
    factory_code: str
    point_code: str
    time_strategy: str
    timezone: str
    iot_timestamp_timezone: str
    control_fields: tuple[str, ...] = ()
    evaluation_fields: tuple[str, ...] = ()
    feature_adapter: str = "latest"
    time_period: str | None = None
    time_lag: str | None = None

    @property
    def point_fields(self) -> tuple[str, ...]:
        return (self.point_field,)

    def as_runtime_dict(self) -> dict[str, object]:
        result = {name: getattr(self, name) for name in self.__dataclass_fields__ if name not in {"time_period", "time_lag"}}
        if self.time_period is not None:
            result["time_period"] = self.time_period
        if self.time_lag is not None:
            result["time_lag"] = self.time_lag
        return result

    def example_row(self) -> dict[str, object]:
        result = {
            self.factory_field: self.factory_code,
            self.point_field: [self.point_code],
            **{name: 0.0 for name in self.control_fields},
        }
        if self.time_strategy != "latest-complete":
            result[self.start_field] = "2026-01-01" if self.time_strategy == "target-date-day" else "2026-01-01 00:00:00"
        return result


@dataclass(frozen=True)
class MultiPointServingContract:
    factory_field: str
    start_field: str
    rolling_window: str
    interval: str
    factory_code: str
    bindings: tuple[tuple[str, tuple[str, ...], str], ...]
    time_strategy: str
    timezone: str
    iot_timestamp_timezone: str
    control_fields: tuple[str, ...] = ()
    evaluation_fields: tuple[str, ...] = ()
    feature_adapter: str = "latest"
    time_period: str | None = None
    time_lag: str | None = None

    @property
    def point_fields(self) -> tuple[str, ...]:
        return tuple(binding[0] for binding in self.bindings)

    def as_runtime_dict(self) -> dict[str, object]:
        result = {
            "factory_field": self.factory_field,
            "start_field": self.start_field,
            "rolling_window": self.rolling_window,
            "interval": self.interval,
            "factory_code": self.factory_code,
            "bindings": [
                {"input": name, "codes": list(codes), "aggregation": aggregation}
                for name, codes, aggregation in self.bindings
            ],
            "time_strategy": self.time_strategy,
            "timezone": self.timezone,
            "iot_timestamp_timezone": self.iot_timestamp_timezone,
            "control_fields": list(self.control_fields),
            "evaluation_fields": list(self.evaluation_fields),
            "feature_adapter": self.feature_adapter,
        }
        if self.time_period is not None:
            result["time_period"] = self.time_period
        if self.time_lag is not None:
            result["time_lag"] = self.time_lag
        return result

    def example_row(self) -> dict[str, object]:
        result = {
            self.factory_field: self.factory_code,
            **{name: list(codes) for name, codes, _ in self.bindings},
            **{name: 0.0 for name in self.control_fields},
        }
        if self.time_strategy != "latest-complete":
            result[self.start_field] = "2026-01-01" if self.time_strategy == "target-date-day" else "2026-01-01 00:00:00"
        return result


def _exact_fields(value: object, expected: frozenset[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PointServingError("point_serving_invalid_shape", path, f"{path} must be an object")
    missing = expected - value.keys()
    extra = value.keys() - expected
    if missing or extra:
        raise PointServingError(
            "point_serving_fields", path,
            f"{path} fields differ: missing={sorted(missing)}, extra={sorted(extra)}",
        )
    return value


def _serving_fields(value: object, required: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PointServingError("point_serving_invalid_shape", "serving", "serving must be an object")
    if "time" not in value:
        raise PointServingError("point_time_required", "serving.time", "point mode requires explicit time strategy and zones")
    missing = required - value.keys()
    extra = value.keys() - required - _OPTIONAL_SERVING_FIELDS - {"time"}
    if missing or extra:
        raise PointServingError("point_serving_fields", "serving", f"serving fields differ: missing={sorted(missing)}, extra={sorted(extra)}")
    if value.get("feature_adapter", "latest") not in {"latest", "source-history-v1"}:
        raise PointServingError("point_feature_adapter_invalid", "serving.feature_adapter", "supported feature adapters are latest and source-history-v1")
    return value


def _history_window_safe(serving: dict[str, Any], window: str) -> None:
    if serving.get("feature_adapter", "latest") != "source-history-v1":
        return
    match = re.fullmatch(r"([1-9][0-9]*)(min|h|d)", window)
    if match is None:
        return
    amount, unit = match.groups()
    minutes = int(amount) * {"min": 1, "h": 60, "d": 1440}[unit]
    if minutes > 7 * 1440:
        raise PointServingError("point_history_window_too_large", "serving.rolling_window", "source-history-v1 windows must not exceed 7 days")


def _time_and_roles(
    serving: dict[str, Any], point_fields: list[str], inputs: list[dict[str, Any]]
) -> tuple[str, str, str, tuple[str, ...], tuple[str, ...], str | None, str | None]:
    raw_time = serving["time"]
    if not isinstance(raw_time, dict):
        raise PointServingError("point_serving_invalid_shape", "serving.time", "serving.time must be an object")
    latest_complete = raw_time.get("strategy") == "latest-complete"
    expected_time_fields = _COMPLETE_TIME_FIELDS if latest_complete else _TIME_FIELDS
    if latest_complete:
        expected_time_fields |= raw_time.keys() & _COMPLETE_OPTIONAL_TIME_FIELDS
    time = _exact_fields(raw_time, expected_time_fields, "serving.time")
    strategy = time["strategy"]
    if not isinstance(strategy, str) or strategy not in {"explicit-start", "target-date-day", "latest-complete"}:
        raise PointServingError("point_time_strategy_invalid", "serving.time.strategy", "supported strategies are explicit-start, target-date-day and latest-complete")
    for label in ("timezone", "iot_timestamp_timezone"):
        zone = time[label]
        if not isinstance(zone, str) or not zone or zone.startswith("/") or ".." in zone:
            raise PointServingError("point_time_zone_invalid", f"serving.time.{label}", "use a valid IANA timezone")
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise PointServingError("point_time_zone_invalid", f"serving.time.{label}", "use a valid IANA timezone") from error
    if strategy == "target-date-day" and serving["rolling_window"] != "1d":
        raise PointServingError("point_time_window_invalid", "serving.rolling_window", "target-date-day requires an explicit 1d window")
    period = time.get("period")
    lag = time.get("availability_lag")
    if strategy == "latest-complete":
        if serving.get("feature_adapter") != "source-history-v1":
            raise PointServingError("point_time_strategy_invalid", "serving.time.strategy", "latest-complete requires source-history-v1")
        match = re.fullmatch(r"([1-9][0-9]*)min", period) if isinstance(period, str) else None
        if match is None:
            raise PointServingError("point_time_period_invalid", "serving.time.period", "period must be a positive whole number of minutes")
        period_minutes = int(match.group(1))
        window_match = re.fullmatch(r"([1-9][0-9]*)(min|h|d)", serving["rolling_window"])
        if window_match is None:
            raise PointServingError("point_time_window_invalid", "serving.rolling_window", "latest-complete requires a valid window")
        amount, unit = window_match.groups()
        window_minutes = int(amount) * {"min": 1, "h": 60, "d": 1440}[unit]
        if 1440 % period_minutes or window_minutes < period_minutes or window_minutes % period_minutes:
            raise PointServingError("point_time_period_invalid", "serving.time.period", "period must divide a day and the rolling window")
        if lag is not None:
            lag_match = re.fullmatch(r"([1-9][0-9]*)min", lag) if isinstance(lag, str) else None
            if lag_match is None or int(lag_match.group(1)) % period_minutes:
                raise PointServingError("point_time_lag_invalid", "serving.time.availability_lag", "availability lag must be a positive whole-period duration in minutes")
    controls = serving.get("control_fields", [])
    evaluations = serving.get("evaluation_fields", [])
    if any(not isinstance(fields, list) or any(not isinstance(name, str) or not name.strip() for name in fields) for fields in (controls, evaluations)):
        raise PointServingError("point_field_roles_invalid", "serving", "control_fields and evaluation_fields must be lists of names")
    all_fields = point_fields + controls + evaluations + [serving["factory_field"], serving["start_field"]]
    if len(all_fields) != len(set(all_fields)):
        raise PointServingError("point_field_roles_invalid", "serving", "serving field roles must not overlap")
    input_names = [item.get("name") for item in inputs]
    if (
        not input_names
        or any(not isinstance(name, str) or not name.strip() for name in input_names)
        or len(input_names) != len(set(input_names))
        or any(item.get("type") != "number" for item in inputs)
        or set(input_names) & set(evaluations + [serving["factory_field"], serving["start_field"]])
    ):
        raise PointServingError("point_serving_feature_mismatch", "contract.inputs", "algorithm inputs must be distinct numeric features and exclude platform metadata or evaluation fields")
    if serving.get("feature_adapter", "latest") != "source-history-v1" and input_names != point_fields + controls:
        raise PointServingError("point_serving_feature_mismatch", "contract.inputs", "latest adapter inputs must equal point_fields followed by control_fields")
    return strategy, time["timezone"], time["iot_timestamp_timezone"], tuple(controls), tuple(evaluations), period, lag


def parse_point_serving(
    document: object, manifest_path: Path
) -> PointServingContract | MultiPointServingContract | None:
    if not isinstance(document, dict) or "serving" not in document:
        return None
    if isinstance(document["serving"], dict) and "point_fields" in document["serving"]:
        return _parse_multi_point_serving(document, manifest_path)
    serving = _serving_fields(document["serving"], _SERVING_FIELDS)
    if serving["mode"] != "point-code":
        raise PointServingError("point_serving_mode", "serving.mode", "only point-code mode is supported")
    names = [serving[name] for name in ("factory_field", "point_field", "start_field")]
    if any(not isinstance(name, str) or not name.strip() for name in names) or len(set(names)) != 3:
        raise PointServingError("point_serving_fields", "serving", "serving field names must be distinct non-empty strings")
    window = serving["rolling_window"]
    if not isinstance(window, str) or _WINDOW.fullmatch(window) is None:
        raise PointServingError("point_serving_window", "serving.rolling_window", "rolling_window must be a positive number followed by min, h, or d")
    _history_window_safe(serving, window)
    interval = serving["interval"]
    if not isinstance(interval, str) or _INTERVAL.fullmatch(interval) is None:
        raise PointServingError("point_serving_interval", "serving.interval", "interval must be a positive number followed by s, min, or h")
    if serving["binding"] != "input-bindings.yaml":
        raise PointServingError("point_binding_path", "serving.binding", "binding must be input-bindings.yaml in this model workspace")

    model = document.get("model")
    contract = document.get("contract")
    if not isinstance(model, dict) or not isinstance(contract, dict):
        raise PointServingError("point_serving_contract", "contract", "model and contract are required")
    inputs = contract.get("inputs")
    if (
        not isinstance(inputs, list)
        or len(inputs) < 1
        or not isinstance(inputs[0], dict)
        or (serving.get("feature_adapter", "latest") != "source-history-v1" and inputs[0].get("name") != serving["point_field"])
        or inputs[0].get("type") != "number"
    ):
        raise PointServingError(
            "point_serving_feature_mismatch", "serving.point_field",
            "point_field must name the model's single numeric algorithm input",
        )
    if any(not isinstance(item, dict) for item in inputs):
        raise PointServingError("point_serving_feature_mismatch", "contract.inputs", "all algorithm inputs must be numeric fields")
    strategy, timezone, iot_zone, controls, evaluations, period, lag = _time_and_roles(serving, [serving["point_field"]], inputs)
    binding_path = manifest_path.parent / "input-bindings.yaml"
    if is_link_or_junction(binding_path) or not binding_path.is_file():
        raise PointServingError("point_binding_missing", "serving.binding", "input-bindings.yaml must be a regular local file")
    if binding_path.stat().st_size > 64 * 1024:
        raise PointServingError("point_binding_too_large", "serving.binding", "input-bindings.yaml exceeds 64 KiB")
    try:
        binding = yaml.load(binding_path.read_text(encoding="utf-8"), Loader=_UniqueKeySafeLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise PointServingError("point_binding_invalid", "serving.binding", f"cannot read binding: {error}") from error
    binding = _exact_fields(binding, _BINDING_FIELDS, "input-bindings.yaml")
    if (
        binding["schema_version"] != "1.0"
        or binding["model_id"] != model.get("id")
        or binding["model_version"] != model.get("version")
        or binding["point_field"] != serving["point_field"]
    ):
        raise PointServingError("point_binding_identity_mismatch", "input-bindings.yaml", "binding version, model, or point field does not match model.yaml")
    factory = binding["factory_code"]
    code = binding["point_code"]
    if not isinstance(factory, str) or not factory.strip() or not isinstance(code, str) or _POINT_CODE.fullmatch(code) is None:
        raise PointServingError("point_binding_invalid", "input-bindings.yaml", "factory_code and point_code must be non-empty valid strings")
    if not code.startswith(factory + "-"):
        raise PointServingError("point_binding_factory_mismatch", "input-bindings.yaml.point_code", f"point_code {code!r} does not belong to factory {factory!r}")
    return PointServingContract(
        factory_field=serving["factory_field"],
        point_field=serving["point_field"],
        start_field=serving["start_field"],
        rolling_window=window,
        interval=interval,
        factory_code=factory,
        point_code=code,
        time_strategy=strategy,
        timezone=timezone,
        iot_timestamp_timezone=iot_zone,
        control_fields=controls,
        evaluation_fields=evaluations,
        feature_adapter=serving.get("feature_adapter", "latest"),
        time_period=period,
        time_lag=lag,
    )


def _parse_multi_point_serving(
    document: dict[str, Any], manifest_path: Path
) -> MultiPointServingContract:
    serving = _serving_fields(document["serving"], _MULTI_SERVING_FIELDS)
    if serving["mode"] != "point-code":
        raise PointServingError("point_serving_mode", "serving.mode", "only point-code mode is supported")
    factory_field = serving["factory_field"]
    start_field = serving["start_field"]
    point_fields = serving["point_fields"]
    if (
        not isinstance(factory_field, str) or not factory_field.strip()
        or not isinstance(start_field, str) or not start_field.strip()
        or not isinstance(point_fields, list) or not point_fields
        or any(not isinstance(name, str) or not name.strip() for name in point_fields)
        or len(set(point_fields + [factory_field, start_field])) != len(point_fields) + 2
    ):
        raise PointServingError(
            "point_serving_fields", "serving.point_fields",
            "fill model.yaml serving.point_fields, serving.factory_field and "
            "serving.start_field with distinct non-empty names",
        )
    window = serving["rolling_window"]
    interval = serving["interval"]
    if not isinstance(window, str) or _WINDOW.fullmatch(window) is None:
        raise PointServingError("point_serving_window", "serving.rolling_window", "invalid rolling_window")
    _history_window_safe(serving, window)
    if not isinstance(interval, str) or _INTERVAL.fullmatch(interval) is None:
        raise PointServingError("point_serving_interval", "serving.interval", "invalid interval")
    if serving["binding"] != "input-bindings.yaml":
        raise PointServingError("point_binding_path", "serving.binding", "binding must be input-bindings.yaml")
    contract = document.get("contract")
    model = document.get("model")
    if not isinstance(contract, dict) or not isinstance(model, dict):
        raise PointServingError("point_serving_contract", "contract", "model and contract are required")
    inputs = contract.get("inputs")
    if not isinstance(inputs, list) or any(not isinstance(item, dict) for item in inputs):
        raise PointServingError("point_serving_feature_mismatch", "contract.inputs", "algorithm inputs must be declared")
    strategy, timezone, iot_zone, controls, evaluations, period, lag = _time_and_roles(serving, point_fields, inputs)
    path = manifest_path.parent / "input-bindings.yaml"
    if is_link_or_junction(path) or not path.is_file():
        raise PointServingError("point_binding_missing", "serving.binding", "input-bindings.yaml must be a regular file")
    if path.stat().st_size > 64 * 1024:
        raise PointServingError("point_binding_too_large", "serving.binding", "input-bindings.yaml exceeds 64 KiB")
    try:
        binding = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeySafeLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise PointServingError("point_binding_invalid", "serving.binding", f"cannot read binding: {error}") from error
    binding = _exact_fields(binding, _MULTI_BINDING_FIELDS, "input-bindings.yaml")
    if (
        binding["schema_version"] not in {"2.0", "2.1"}
        or binding["model_id"] != model.get("id")
        or binding["model_version"] != model.get("version")
    ):
        raise PointServingError("point_binding_identity_mismatch", "input-bindings.yaml", "binding version or model does not match model.yaml")
    factory = binding["factory_code"]
    if not isinstance(factory, str) or not factory.strip():
        raise PointServingError("point_binding_invalid", "input-bindings.yaml.factory_code", "factory_code must be a non-empty string")
    entries = binding["bindings"]
    if not isinstance(entries, list) or len(entries) != len(point_fields):
        raise PointServingError("point_binding_fields_mismatch", "input-bindings.yaml.bindings", "bindings must cover every declared point field exactly once")
    parsed: list[tuple[str, tuple[str, ...], str]] = []
    seen_codes: set[str] = set()
    for index, raw_entry in enumerate(entries):
        if isinstance(raw_entry, dict) and "aggregation" not in raw_entry:
            raise PointServingError(
                "point_aggregation_required",
                f"input-bindings.yaml.bindings[{index}].aggregation",
                "aggregation must be declared explicitly",
            )
        entry = _exact_fields(raw_entry, _LOGICAL_FIELDS, f"input-bindings.yaml.bindings[{index}]")
        name = entry["input"]
        if name != point_fields[index]:
            raise PointServingError("point_binding_fields_mismatch", f"input-bindings.yaml.bindings[{index}].input", "logical input does not match serving.point_fields order")
        for label in ("expected_signal", "unit"):
            if not isinstance(entry[label], str) or not entry[label].strip():
                raise PointServingError("point_binding_metadata_missing", f"input-bindings.yaml.bindings[{index}].{label}", f"{label} is required")
        kind = entry["expected_signal_kind"]
        if not isinstance(kind, str) or kind not in _SIGNAL_KINDS:
            raise PointServingError("point_signal_kind_invalid", f"input-bindings.yaml.bindings[{index}].expected_signal_kind", "unsupported expected signal kind")
        matching_input = next((item for item in inputs if item["name"] == name), None)
        if matching_input is not None and entry["unit"] != matching_input["unit"]:
            raise PointServingError("point_unit_mismatch", f"input-bindings.yaml.bindings[{index}].unit", "binding unit differs from algorithm contract")
        points = entry["points"]
        if not isinstance(points, list) or not points:
            raise PointServingError("point_binding_points_missing", f"input-bindings.yaml.bindings[{index}].points", "at least one point is required")
        aggregation = entry["aggregation"]
        if not isinstance(aggregation, str) or (len(points) == 1 and aggregation != "single") or (len(points) > 1 and aggregation not in {"sum", "mean"}):
            raise PointServingError("point_aggregation_required", f"input-bindings.yaml.bindings[{index}].aggregation", "one point requires single; multiple points require explicit sum or mean")
        codes: list[str] = []
        for point_index, raw_point in enumerate(points):
            point_path = f"input-bindings.yaml.bindings[{index}].points[{point_index}]"
            point = _exact_fields(
                raw_point,
                _POINT_FIELDS_21 if binding["schema_version"] == "2.1" else _POINT_FIELDS,
                point_path,
            )
            for label in ("facility_name", "equipment_name", "acquisition_signal_name", "unit"):
                if not isinstance(point[label], str) or not point[label].strip():
                    raise PointServingError("point_binding_metadata_missing", f"{point_path}.{label}", f"{label} is required")
            expected_name = unicodedata.normalize("NFKC", entry["expected_signal"]).strip().casefold()
            actual_name = unicodedata.normalize("NFKC", point["acquisition_signal_name"]).strip().casefold()
            if expected_name != actual_name:
                raise PointServingError(
                    "point_signal_name_mismatch",
                    f"{point_path}.acquisition_signal_name",
                    f"{name} expects signal {entry['expected_signal']!r}, got {point['acquisition_signal_name']!r}",
                )
            code = point["code"]
            if not isinstance(code, str) or _POINT_CODE.fullmatch(code) is None or not code.startswith(factory + "-"):
                raise PointServingError("point_binding_factory_mismatch", f"{point_path}.code", "point code is invalid or belongs to a different factory")
            if binding["schema_version"] == "2.1":
                point_id = point["point_id"]
                signal_code = point["signal_code"]
                if (
                    not isinstance(point_id, str)
                    or _POINT_CODE.fullmatch(point_id) is None
                    or not point_id.startswith(factory + "-")
                    or not isinstance(signal_code, str)
                    or _POINT_CODE.fullmatch(signal_code) is None
                    or code != f"{point_id}-{signal_code}"
                ):
                    raise PointServingError(
                        "point_code_components_mismatch",
                        f"{point_path}.code",
                        "full IoT code must equal the recorded point_id plus '-' plus signal_code",
                    )
            if code in seen_codes:
                raise PointServingError("point_binding_duplicate_code", f"{point_path}.code", "point code is bound more than once")
            seen_codes.add(code)
            if point["signal_kind"] != kind:
                raise PointServingError("point_signal_kind_mismatch", f"{point_path}.signal_kind", f"{name} expects {kind}, got {point['signal_kind']}")
            if point["unit"] != entry["unit"]:
                raise PointServingError("point_unit_mismatch", f"{point_path}.unit", f"{name} expects unit {entry['unit']}")
            codes.append(code)
        parsed.append((name, tuple(codes), aggregation))
    return MultiPointServingContract(
        factory_field=factory_field,
        start_field=start_field,
        rolling_window=window,
        interval=interval,
        factory_code=factory,
        bindings=tuple(parsed),
        time_strategy=strategy,
        timezone=timezone,
        iot_timestamp_timezone=iot_zone,
        control_fields=controls,
        evaluation_fields=evaluations,
        feature_adapter=serving.get("feature_adapter", "latest"),
        time_period=period,
        time_lag=lag,
    )
