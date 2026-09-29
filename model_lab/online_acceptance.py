"""Bind model-owned real-IoT evidence to a fixed bundle without model formulas."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from model_lab.path_safety import is_link_or_junction


RECEIPT = Path("validation/online-acceptance.json")
PROFILE = "model-iot-v1"
_MAX_RECEIPT_BYTES = 32_768


def _sha256(path: Path) -> str:
    if is_link_or_junction(path) or not path.is_file():
        raise ValueError(f"missing or linked file: {path.name}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("event timestamp must be a string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("event timestamp requires an explicit offset")
    return parsed


def _finite(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("output must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("output must be finite")
    return number


def _binding_codes(binding: dict[str, Any], fields: list[str]) -> dict[str, list[str]]:
    entries = binding.get("bindings")
    if not isinstance(entries, list):
        raise ValueError("point binding entries are missing")
    mapping: dict[str, list[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("input"), str):
            raise ValueError("point binding entry is invalid")
        points = entry.get("points")
        if not isinstance(points, list) or not points:
            raise ValueError("point binding has no points")
        codes = [point.get("code") for point in points if isinstance(point, dict)]
        if len(codes) != len(points) or any(not isinstance(code, str) or not code for code in codes):
            raise ValueError("point binding code is invalid")
        mapping[entry["input"]] = codes
    if set(mapping) != set(fields):
        raise ValueError("point binding differs from model fields")
    return mapping


def verify_online_acceptance(workspace: Path, manifest: dict[str, Any]) -> tuple[bool, str]:
    """Check receipt identity, digests, request shape, and numeric oracle parity.

    This is consistency of a private test's evidence, not an attestation from
    the company IoT platform. The private test must establish actual origin.
    """
    try:
        receipt_path = workspace / RECEIPT
        if is_link_or_junction(receipt_path) or not receipt_path.is_file():
            raise ValueError("model-specific online acceptance receipt is missing")
        if receipt_path.stat().st_size > _MAX_RECEIPT_BYTES:
            raise ValueError("online acceptance receipt is too large")
        evidence = json.loads(receipt_path.read_text(encoding="utf-8"))
        if not isinstance(evidence, dict) or evidence.get("schema_version") != "1.0" or evidence.get("profile") != PROFILE:
            raise ValueError("online acceptance profile is unsupported")
        if evidence.get("online_iot_fake") is not False:
            raise ValueError("private test has not claimed real IoT data")
        model = manifest["model"]
        serving = manifest["serving"]
        if serving.get("mode") != "point-code":
            raise ValueError("online acceptance requires a point-code model")
        provider = serving["provider"]
        if (evidence.get("model_id") != model["id"]
                or evidence.get("version") != model["version"]
                or evidence.get("provider_archive_sha256") != provider["archive_sha256"]):
            raise ValueError("model, version, or provider differs from the receipt")
        artifacts = [item for item in manifest["artifacts"] if item.get("kind") == "mlflow-pyfunc"]
        if len(artifacts) != 1 or evidence.get("bundle_sha256") != artifacts[0]["sha256"]:
            raise ValueError("receipt is not bound to the declared service bundle")
        binding_path = workspace / serving["binding"]
        if evidence.get("binding_sha256") != _sha256(binding_path):
            raise ValueError("point binding changed after online acceptance")
        binding = yaml.safe_load(binding_path.read_text(encoding="utf-8"))
        if not isinstance(binding, dict) or evidence.get("factory_code") != binding.get("factory_code"):
            raise ValueError("factory differs from the point binding")
        fields = serving["point_fields"]
        if not isinstance(fields, list) or not fields or not all(isinstance(field, str) for field in fields):
            raise ValueError("model point fields are invalid")
        codes = _binding_codes(binding, fields)
        if evidence.get("point_codes") != codes:
            raise ValueError("receipt point codes differ from the binding")
        request = evidence.get("request_row")
        controls = serving.get("control_fields", [])
        required = {serving["factory_field"], serving["start_field"], *fields, *controls}
        if not isinstance(request, dict) or set(request) != required:
            raise ValueError("recorded platform request differs from the serving contract")
        if request[serving["factory_field"]] != binding["factory_code"]:
            raise ValueError("recorded request has the wrong factory")
        if not isinstance(request[serving["start_field"]], str) or not request[serving["start_field"]]:
            raise ValueError("recorded request has no time anchor")
        for field in fields:
            if request[field] != codes[field]:
                raise ValueError("recorded request has the wrong point array")
        for field in controls:
            _finite(request[field])
        outputs = manifest["contract"]["outputs"]
        output_fields = {item["name"] for item in outputs}
        prediction = evidence.get("prediction_outputs")
        oracle = evidence.get("oracle_outputs")
        if not output_fields or not isinstance(prediction, dict) or not isinstance(oracle, dict) or set(prediction) != output_fields or set(oracle) != output_fields:
            raise ValueError("accepted outputs differ from the model contract")
        for field in output_fields:
            if abs(_finite(prediction[field]) - _finite(oracle[field])) > 1e-6:
                raise ValueError("private test prediction differs from its oracle")
        raw_rows, window_rows = evidence.get("raw_rows"), evidence.get("window_rows")
        if type(raw_rows) is not int or type(window_rows) is not int or not 0 < window_rows <= raw_rows:
            raise ValueError("receipt does not record usable IoT rows")
        requested = _timestamp(evidence.get("requested_at_utc"))
        received = _timestamp(evidence.get("received_at_utc"))
        if received < requested:
            raise ValueError("receipt event times are inconsistent")
        test_path = workspace / "tests/test_real_bundle.py"
        if evidence.get("test_sha256") != _sha256(test_path):
            raise ValueError("private online test changed after receipt was written")
    except (OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError,
            TypeError, KeyError, IndexError, AttributeError, ValueError) as error:
        return False, f"point_online_unverified: {error}"
    return True, "model-specific IoT receipt is bound to the fixed service, point arrays, private test, and oracle"
