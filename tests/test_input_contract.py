from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from model_lab.input_contract import validate_inputs


class InputContractTests(unittest.TestCase):
    def test_non_iot_input_requires_supply_and_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "inputs.md").write_text(
                "| 输入 | 类型/单位 | 来源 | 供数方式 | 时间口径/质量 |\n"
                "| --- | --- | --- | --- | --- |\n"
                "| Q_in | m3/h | IoT | 请求直接传 | 当前分钟 |\n"
                "| COD_in | mg/L | 化验 |  |  |\n",
                encoding="utf-8",
            )
            report = validate_inputs(workspace)
        self.assertFalse(report.valid)
        self.assertTrue(any(issue.code == "non_iot_supply_missing" for issue in report.issues))
        self.assertTrue(any(issue.code == "non_iot_timing_missing" for issue in report.issues))

    def test_valid_mixed_sources_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "inputs.md").write_text(
                "| 输入 | 类型/单位 | 来源 | 供数方式 | 时间口径/质量 |\n"
                "| --- | --- | --- | --- | --- |\n"
                "| Q_in | m3/h | IoT | 请求直接传 | 当前分钟 |\n"
                "| COD_in | mg/L | 化验 | 请求直接传 | 采样时间 + 24h stale |\n"
                "| NH4_in | mg/L | 人工 | 请求直接传 | 班次时间 + 8h stale |\n",
                encoding="utf-8",
            )
            report = validate_inputs(workspace)
        self.assertTrue(report.valid, report.as_dict())
        self.assertEqual(report.rows, 3)


if __name__ == "__main__":
    unittest.main()
