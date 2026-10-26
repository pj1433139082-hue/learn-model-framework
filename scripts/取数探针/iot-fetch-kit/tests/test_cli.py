"""CLI 端到端测试：进程内调 cli.main(argv)，全程指向离线假服务端。

断言的是旧脚本的对外契约：文件名、索引、列集合、时区、退出码、两种 --nodes-file、
JSONL 回退，以及 --probe 的「只打印不取数」。
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

import fake_server  # noqa: E402
import support  # noqa: E402

try:
    import pandas as pd
except ImportError:  # pragma: no cover - 依赖环境
    pd = None

try:
    import pyarrow  # noqa: F401

    HAS_PYARROW = True
except ImportError:  # pragma: no cover - 依赖环境
    HAS_PYARROW = False

from iot_fetch import cli  # noqa: E402

HAS_PARQUET = pd is not None and HAS_PYARROW

FACTORY = "TESTFAC"
NODES = [
    {"namespace": "NS-A", "id": "V_IF"},
    {"namespace": "NS-B", "id": "V_OP"},
]
NAMES = [node["namespace"] + "-" + node["id"] for node in NODES]
COLUMNS = set(NAMES + ["data_time_utc", "data_time_bj"])


def ms_of(moment) -> int:
    return int(moment.timestamp() * 1000)


class CliFetchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = fake_server.FakeIotServer()
        self.addCleanup(self.server.stop)
        self._tmp_ctx = support.temp_dir()
        self.tmp = self._tmp_ctx.__enter__()
        self.addCleanup(self._tmp_ctx.__exit__, None, None, None)

    # -- 工具 -------------------------------------------------------------
    def write_nodes(self, kind: str = "json") -> Path:
        if kind == "json":
            path = self.tmp / "nodes.json"
            path.write_text(json.dumps(NODES), encoding="utf-8")
            return path
        path = self.tmp / "nodes.csv"
        lines = ["namespace,id"] + [
            node["namespace"] + "," + node["id"] for node in NODES
        ]
        path.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
        return path

    def build_argv(self, out, *, nodes_path=None, end="2026-03-01 02:00", extra=None):
        argv = ["--factory-code", FACTORY]
        if nodes_path is not None:
            argv.extend(["--nodes-file", str(nodes_path)])
        argv.extend(
            [
                "--start",
                "2026-03-01",
                "--end",
                end,
                "--out",
                str(out),
                "--window-hours",
                "1",
                "--workers",
                "2",
                "--retries",
                "1",
                "--step-seconds",
                "15",
            ]
        )
        argv.extend(extra or [])
        return argv

    def run_cli(self, argv, **env_extra):
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with support.cli_env(self.server, **env_extra), contextlib.redirect_stdout(
            out_buf
        ), contextlib.redirect_stderr(err_buf):
            try:
                code = cli.main(argv)
            except SystemExit as exc:
                code = exc.code
        return code, out_buf.getvalue(), err_buf.getvalue()

    # -- 落盘契约 ---------------------------------------------------------
    @unittest.skipIf(not HAS_PARQUET, "需要 pandas + pyarrow")
    def test_fetch_writes_month_parquet(self) -> None:
        nodes_path = self.write_nodes()
        raw = self.tmp / "raw"
        code, stdout, stderr = self.run_cli(self.build_argv(raw, nodes_path=nodes_path))
        self.assertEqual(code, 0, stderr)
        path = raw / "TESTFAC-202603.parquet"
        self.assertTrue(path.is_file(), "缺少 {0}".format(path.name))
        frame = pd.read_parquet(path)
        self.assertEqual(len(frame), 480)
        self.assertEqual(set(frame.columns), COLUMNS)
        self.assertEqual(str(frame.index.dtype), "int64")
        self.assertTrue(frame.index.is_unique)
        self.assertTrue(frame.index.is_monotonic_increasing)
        self.assertEqual(str(frame["data_time_utc"].dt.tz), "UTC")
        self.assertEqual(
            frame["data_time_bj"].iloc[0].utcoffset(), timedelta(hours=8)
        )
        self.assertEqual(int(frame.index[0]), ms_of(support.bj(2026, 3, 1)) + 15000)
        self.assertIn("480 行", stdout)
        self.assertIn("完成：", stdout)
        self.assertIn("每测点上限 1920", stdout)
        self.assertIn("并发 2", stdout)
        self.assertEqual(self.server.api_calls, 3)  # 1 次探针 + 2 个窗口
        self.assertEqual(len(self.server.token_requests()), 1)

    @unittest.skipIf(not HAS_PARQUET, "需要 pandas + pyarrow")
    def test_nodes_csv_file(self) -> None:
        nodes_path = self.write_nodes("csv")
        raw = self.tmp / "raw_csv"
        code, stdout, stderr = self.run_cli(
            self.build_argv(raw, nodes_path=nodes_path, end="2026-03-01 01:00")
        )
        self.assertEqual(code, 0, stderr)
        frame = pd.read_parquet(raw / "TESTFAC-202603.parquet")
        self.assertEqual(len(frame), 240)
        self.assertEqual(set(frame.columns), COLUMNS)

    @unittest.skipIf(not HAS_PARQUET, "需要 pandas + pyarrow")
    def test_nodes_built_from_machine(self) -> None:
        raw = self.tmp / "raw_machine"
        argv = [
            "--factory-code",
            "PS1",
            "--machine-prefix=-BE_",
            "--machine",
            "0001",
            "--signals",
            "V_IF",
            "--start",
            "2026-03-01",
            "--end",
            "2026-03-01 01:00",
            "--out",
            str(raw),
            "--window-hours",
            "1",
            "--max-size",
            "2000",
            "--workers",
            "1",
            "--retries",
            "1",
        ]
        code, stdout, stderr = self.run_cli(argv)
        self.assertEqual(code, 0, stderr)
        frame = pd.read_parquet(raw / "PS1-202603.parquet")
        self.assertEqual(
            set(frame.columns),
            {"PS1-BE_0001-V_IF", "data_time_utc", "data_time_bj"},
        )
        self.assertEqual(len(frame), 240)

    def test_force_jsonl_fallback(self) -> None:
        nodes_path = self.write_nodes()
        raw = self.tmp / "raw_jsonl"
        code, stdout, stderr = self.run_cli(
            self.build_argv(raw, nodes_path=nodes_path), IOT_FETCH_FORCE_JSONL="1"
        )
        self.assertEqual(code, 0, stderr)
        path = raw / "TESTFAC-202603.jsonl"
        self.assertTrue(path.is_file(), "缺少 {0}".format(path.name))
        self.assertFalse((raw / "TESTFAC-202603.parquet").exists())
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 480)
        record = json.loads(lines[0])
        self.assertEqual(set(record), {"t", "data_time_utc", "signals"})
        self.assertEqual(set(record["signals"]), set(NAMES))
        self.assertTrue(record["data_time_utc"].endswith("+00:00"))
        self.assertIn("JSONL", stdout)

    # -- 探针与失败路径 ---------------------------------------------------
    def test_probe_only_does_not_fetch(self) -> None:
        nodes_path = self.write_nodes()
        raw = self.tmp / "probe_raw"
        code, stdout, stderr = self.run_cli(
            self.build_argv(raw, nodes_path=nodes_path, extra=["--probe"])
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("每测点上限", stdout)
        self.assertIn("建议窗口", stdout)
        self.assertFalse(raw.exists())
        self.assertEqual(self.server.api_calls, 2)  # 近期 + 起点附近各探一次

    def test_empty_history_returns_one(self) -> None:
        self.server.empty_history = True
        nodes_path = self.write_nodes()
        raw = self.tmp / "empty_raw"
        argv = self.build_argv(raw, nodes_path=nodes_path, extra=["--max-size", "2000"])
        code, stdout, stderr = self.run_cli(argv)
        self.assertEqual(code, 1, stderr)
        self.assertIn("无数据", stdout)
        self.assertFalse(raw.exists())

    def test_probe_failure_exits(self) -> None:
        self.server.empty_history = True
        nodes_path = self.write_nodes()
        code, stdout, stderr = self.run_cli(
            self.build_argv(self.tmp / "none_raw", nodes_path=nodes_path)
        )
        self.assertIn("上限探测失败", str(code))

    def test_missing_secret_exits(self) -> None:
        code, stdout, stderr = self.run_cli(
            ["--factory-code", FACTORY], IOT_CLIENT_SECRET=None
        )
        self.assertIn("缺少 --client-secret", str(code))

    def test_nodes_file_missing(self) -> None:
        argv = [
            "--factory-code",
            FACTORY,
            "--nodes-file",
            str(self.tmp / "nope.json"),
            "--start",
            "2026-03-01",
            "--end",
            "2026-03-01 01:00",
        ]
        code, stdout, stderr = self.run_cli(argv)
        self.assertIn("节点清单错误", str(code))

    def test_version(self) -> None:
        code, stdout, stderr = self.run_cli(["--version"])
        self.assertEqual(code, 0)
        self.assertIn("iot-fetch-kit 1.0.0", stdout)


class CliDefaultsTest(unittest.TestCase):
    """参数默认值就是旧脚本的对外契约，逐条钉住。"""

    def test_parser_defaults_match_legacy(self) -> None:
        args = cli.build_parser().parse_args(["--factory-code", "X"])
        self.assertEqual(args.machine_prefix, "")
        self.assertEqual(args.machine, "")
        self.assertEqual(args.signals, "V_IF,V_OP,V_P,V_ITIT,V_I,S_RUN")
        self.assertIsNone(args.nodes_file)
        self.assertEqual(args.start, "")
        self.assertEqual(args.end, "")
        self.assertEqual(args.out, "raw")
        self.assertEqual(args.workers, 32)
        self.assertEqual(args.window_hours, 0.0)
        self.assertEqual(args.max_size, 0)
        self.assertEqual(args.retries, 4)
        self.assertFalse(args.probe)
        self.assertEqual(args.step_seconds, 15.0)
        self.assertEqual(args.format, "auto")
        self.assertIsNone(args.client_id)

    def test_factory_code_required(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.build_parser().parse_args([])
        self.assertEqual(ctx.exception.code, 2)
