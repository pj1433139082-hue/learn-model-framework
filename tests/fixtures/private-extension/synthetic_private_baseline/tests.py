"""Unittest module installed with the synthetic private extension example."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from model_lab.baseline import get_baseline_runner

from .baseline import KIND


def _load_example(name: str) -> object:
    path = Path(__file__).parent / "examples" / name
    return json.loads(path.read_text(encoding="utf-8"))


class SyntheticPrivateExtensionTests(unittest.TestCase):
    def test_installed_entry_point_replays_independent_expected_values(self) -> None:
        runner = get_baseline_runner(KIND)
        self.assertIsNotNone(runner)
        assert runner is not None

        result = runner.replay(
            _load_example("input.json"),
            _load_example("expected.json"),
        )
        self.assertEqual(result.problems, ())
        self.assertEqual(result.case_count, 3)
        self.assertEqual(result.kind, KIND)
        self.assertEqual(runner.expected_contract()["outputs"][0]["name"], "total")

    def test_missing_runner_and_bad_expected_values_fail_closed(self) -> None:
        self.assertIsNone(get_baseline_runner("synthetic-unregistered-v1"))

        runner = get_baseline_runner(KIND)
        self.assertIsNotNone(runner)
        assert runner is not None
        expected = _load_example("expected.json")
        assert isinstance(expected, dict)
        mismatched = copy.deepcopy(expected)
        mismatched["cases"][1]["total"] = 99

        result = runner.replay(_load_example("input.json"), mismatched)
        self.assertEqual(len(result.problems), 1)
        self.assertEqual(result.problems[0].code, "synthetic_sum_mismatch")
        self.assertEqual(result.problems[0].path, "cases[1].total")

    def test_malformed_input_fails_closed(self) -> None:
        runner = get_baseline_runner(KIND)
        self.assertIsNotNone(runner)
        assert runner is not None

        result = runner.replay(
            {"cases": [{"left": True, "right": 1}]},
            {"cases": [{"total": 2}]},
        )
        self.assertEqual(len(result.problems), 1)
        self.assertEqual(result.problems[0].code, "synthetic_non_finite_number")


if __name__ == "__main__":
    unittest.main()
