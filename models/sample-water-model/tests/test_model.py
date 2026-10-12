"""标准水厂前馈投药量样板模型单测。"""

from __future__ import annotations

import sys
from pathlib import Path
import unittest
import pandas as pd

_MODEL_ROOT = Path(__file__).resolve().parents[1]
if str(_MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_MODEL_ROOT))

from src.model import predict


class SampleWaterModelTests(unittest.TestCase):
    def test_predict_standard_conditions(self) -> None:
        inputs = pd.DataFrame([{
            "influent_flow": 2000.0,
            "influent_cod": 200.0,
            "influent_nh3n": 30.0,
        }])
        outputs = predict(inputs)
        self.assertEqual(len(outputs), 1)
        # unit_coagulant = 25 + 0.05*200 = 35 mg/L
        # coagulant = 2000 * 35 / 1000 = 70.0 kg/h
        self.assertAlmostEqual(outputs["coagulant_dosage"].iloc[0], 70.0, places=2)
        # needed_cod = 4.5*30 - 0.3*200 = 135 - 60 = 75 mg/L
        # carbon = 2000 * 75 / 1200 = 125.0 kg/h
        self.assertAlmostEqual(outputs["carbon_source_dosage"].iloc[0], 125.0, places=2)

    def test_predict_zero_flow(self) -> None:
        inputs = pd.DataFrame([{
            "influent_flow": 0.0,
            "influent_cod": 150.0,
            "influent_nh3n": 20.0,
        }])
        outputs = predict(inputs)
        self.assertEqual(outputs["coagulant_dosage"].iloc[0], 0.0)
        self.assertEqual(outputs["carbon_source_dosage"].iloc[0], 0.0)

    def test_predict_excess_cod_no_carbon_needed(self) -> None:
        inputs = pd.DataFrame([{
            "influent_flow": 1000.0,
            "influent_cod": 300.0,
            "influent_nh3n": 10.0,
        }])
        outputs = predict(inputs)
        self.assertEqual(outputs["carbon_source_dosage"].iloc[0], 0.0)
        self.assertGreater(outputs["coagulant_dosage"].iloc[0], 0.0)

    def test_missing_column_raises_value_error(self) -> None:
        inputs = pd.DataFrame([{"influent_flow": 1000.0}])
        with self.assertRaises(ValueError):
            predict(inputs)


if __name__ == "__main__":
    unittest.main()
