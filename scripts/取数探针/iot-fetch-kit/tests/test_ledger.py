"""台账 xlsx → 节点清单测试（需要 pandas + openpyxl，缺依赖自动跳过）。"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

import support  # noqa: E402

from iot_fetch import ledger  # noqa: E402

try:
    import openpyxl  # noqa: F401
    import pandas as pd

    HAS_LEDGER = True
except ImportError:  # pragma: no cover - 依赖环境
    HAS_LEDGER = False
    pd = None

BACKTICK = chr(96)

ROWS = [
    {
        "设施名称": "某污水厂",
        "设备名称": "1#曝气风机",
        "数采点位编号": "NS-A",
        "数采信号名称": "V_IF",
    },
    {
        "设施名称": "某污水厂",
        "设备名称": "1#曝气风机",
        "数采点位编号": "NS-A",
        "数采信号名称": "V_OP",
    },
    {
        "设施名称": "某污水厂",
        "设备名称": "1#曝气风机",
        "数采点位编号": "NS-A",
        "数采信号名称": "V_OP",
    },
    {
        "设施名称": "另一厂",
        "设备名称": "2#泵",
        "数采点位编号": "NS-B",
        "数采信号名称": "V_IF",
    },
    {"设施名称": "另一厂", "设备名称": "", "数采点位编号": "", "数采信号名称": ""},
]


@unittest.skipIf(not HAS_LEDGER, "需要 pandas + openpyxl")
class LedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_ctx = support.temp_dir()
        self.tmp = self._tmp_ctx.__enter__()
        self.addCleanup(self._tmp_ctx.__exit__, None, None, None)
        self.path = self.tmp / "ledger.xlsx"
        pd.DataFrame(ROWS).to_excel(self.path, index=False)

    def test_read_ledger_columns(self) -> None:
        frame = ledger.read_ledger(self.path)
        for column in ledger.REQUIRED:
            self.assertIn(column, frame.columns)
        self.assertEqual(len(frame), len(ROWS))

    def test_select_rows_dedupes_and_skips_empty(self) -> None:
        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf):
            rows = ledger.select_rows(ledger.read_ledger(self.path))
        self.assertEqual(
            [row["full_id"] for row in rows], ["NS-A-V_IF", "NS-A-V_OP", "NS-B-V_IF"]
        )
        self.assertIn("缺少点位编号或信号名", err_buf.getvalue())

    def test_match_and_zero_match(self) -> None:
        frame = ledger.read_ledger(self.path)
        rows = ledger.select_rows(frame, match="某污水厂")
        self.assertEqual(len(rows), 2)
        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf):
            self.assertIsNone(ledger.select_rows(frame, match="不存在的设备"))
        self.assertIn("匹配到 0 行", err_buf.getvalue())

    def test_match_on_device_column(self) -> None:
        frame = ledger.read_ledger(self.path)
        rows = ledger.select_rows(frame, match="1#曝气风机", column="设备名称")
        self.assertEqual(len(rows), 2)

    def test_signals_filter_warns_unknown(self) -> None:
        frame = ledger.read_ledger(self.path)
        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf):
            rows = ledger.select_rows(frame, signals="V_IF,V_XYZ")
        self.assertEqual(len(rows), 2)
        self.assertIn("没有这些信号", err_buf.getvalue())

    def test_ledger_to_nodes(self) -> None:
        nodes = ledger.ledger_to_nodes(self.path)
        self.assertEqual(
            nodes,
            [
                {"namespace": "NS-A", "id": "V_IF"},
                {"namespace": "NS-A", "id": "V_OP"},
                {"namespace": "NS-B", "id": "V_IF"},
            ],
        )
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(ValueError):
                ledger.ledger_to_nodes(self.path, match="不存在的设备")

    def test_cli_writes_out_and_md(self) -> None:
        out = self.tmp / "nodes.json"
        md = self.tmp / "points.md"
        out_buf = io.StringIO()
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(
            io.StringIO()
        ):
            code = ledger.run_ledger(
                ["--ledger", str(self.path), "--out", str(out), "--md", str(md)]
            )
        self.assertEqual(code, 0)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(data), 3)
        self.assertEqual(data[0], {"namespace": "NS-A", "id": "V_IF"})
        text = md.read_text(encoding="utf-8")
        self.assertIn("| 设施名称 | 设备名称 | 数采点位编号 | 数采信号名称 |", text)
        self.assertIn(BACKTICK + "NS-B" + BACKTICK, text)
        stdout = out_buf.getvalue()
        self.assertIn("匹配 3 个节点", stdout)
        self.assertIn("已写", stdout)

    def test_cli_list_devices(self) -> None:
        out_buf = io.StringIO()
        with contextlib.redirect_stdout(out_buf):
            code = ledger.run_ledger(["--ledger", str(self.path), "--list-devices"])
        self.assertEqual(code, 0)
        stdout = out_buf.getvalue()
        self.assertIn("2 个设备名称", stdout)
        self.assertIn("1#曝气风机", stdout)

    def test_cli_zero_match_returns_three(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            io.StringIO()
        ):
            code = ledger.run_ledger(
                ["--ledger", str(self.path), "--match", "不存在的设备"]
            )
        self.assertEqual(code, 3)

    def test_read_ledger_missing_file_exits(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            ledger.read_ledger(self.tmp / "nope.xlsx")
        self.assertIn("台账不存在", str(ctx.exception.code))
