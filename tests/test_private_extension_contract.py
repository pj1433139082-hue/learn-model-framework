"""Synthetic checks for private baseline and online-acceptance extension seams."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from model_lab.baseline import BaselineReplay, BaselineRunner, get_baseline_runner
from model_lab.online_acceptance import verify_online_acceptance


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _SyntheticEntryPoint:
    name = "synthetic-addition-v1"

    def load(self) -> BaselineRunner:
        return BaselineRunner(
            kind=self.name,
            target_kind="cpu-iot",
            inputs=(("a", "number", "1"), ("b", "number", "1")),
            outputs=(("sum", "number", "1"),),
            replay=lambda _inputs, _expected: BaselineReplay(
                (), 1, self.name, "single-row", {}, {},
            ),
        )


class PrivateExtensionContractTests(unittest.TestCase):
    def test_baseline_runner_is_loaded_only_from_private_entry_point(self) -> None:
        with patch("model_lab.baseline.entry_points", return_value=[_SyntheticEntryPoint()]):
            runner = get_baseline_runner("synthetic-addition-v1")
            self.assertIsNotNone(runner)
            assert runner is not None
            self.assertEqual(runner.expected_contract()["outputs"][0]["name"], "sum")
            self.assertIsNone(get_baseline_runner("another-private-formula"))

    def test_generic_online_receipt_requires_bound_oracle_and_test(self) -> None:
        with tempfile.TemporaryDirectory(prefix="generic-iot-receipt-") as directory:
            workspace = Path(directory)
            (workspace / "tests").mkdir()
            (workspace / "validation").mkdir()
            test_file = workspace / "tests" / "test_real_bundle.py"
            test_file.write_text("# synthetic private test fixture\n", encoding="utf-8")
            binding_file = workspace / "input-bindings.yaml"
            binding_file.write_text(
                "factory_code: EXAMPLE_SITE\n"
                "bindings:\n"
                "  - input: flow\n"
                "    points:\n"
                "      - code: EXAMPLE_FLOW_A\n",
                encoding="utf-8",
            )
            manifest = {
                "model": {"id": "synthetic-addition", "version": "0.1.0"},
                "serving": {
                    "mode": "point-code", "provider": {"archive_sha256": "a" * 64},
                    "binding": "input-bindings.yaml", "factory_field": "factory_code",
                    "start_field": "start_time", "point_fields": ["flow"],
                    "control_fields": ["setpoint"],
                },
                "artifacts": [{"kind": "mlflow-pyfunc", "sha256": "b" * 64}],
                "contract": {"outputs": [{"name": "result", "type": "number", "unit": "1"}]},
            }
            receipt = {
                "schema_version": "1.0", "profile": "model-iot-v1",
                "online_iot_fake": False,
                "model_id": "synthetic-addition", "version": "0.1.0",
                "provider_archive_sha256": "a" * 64,
                "bundle_sha256": "b" * 64,
                "binding_sha256": _digest(binding_file),
                "test_sha256": _digest(test_file),
                "factory_code": "EXAMPLE_SITE",
                "point_codes": {"flow": ["EXAMPLE_FLOW_A"]},
                "request_row": {
                    "factory_code": "EXAMPLE_SITE", "start_time": "2026-01-01 00:00:00",
                    "flow": ["EXAMPLE_FLOW_A"], "setpoint": 10.0,
                },
                "prediction_outputs": {"result": 12.0},
                "oracle_outputs": {"result": 12.0},
                "raw_rows": 100, "window_rows": 60,
                "requested_at_utc": "2026-01-01T01:00:00+00:00",
                "received_at_utc": "2026-01-01T01:00:01+00:00",
            }
            receipt_path = workspace / "validation" / "online-acceptance.json"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            valid, message = verify_online_acceptance(workspace, manifest)
            self.assertTrue(valid, message)

            receipt["oracle_outputs"]["result"] = 13.0
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            valid, message = verify_online_acceptance(workspace, manifest)
            self.assertFalse(valid)
            self.assertIn("oracle", message)


if __name__ == "__main__":
    unittest.main()
