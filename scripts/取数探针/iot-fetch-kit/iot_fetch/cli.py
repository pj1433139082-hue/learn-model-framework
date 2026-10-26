"""命令行入口：`iot-fetch <取数参数>` / `iot-fetch doctor` / `iot-fetch ledger`。

取数部分的参数、默认值、报错文案、退出码与旧脚本 `iot_bulk_fetch.py` 一一对应；
新增 `--step-seconds`、`--format`、`--version` 与 `doctor`、`ledger` 两个子命令。
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

from . import __version__
from .config import (
    BJ_TZ,
    DEFAULT_CLIENT_ID,
    DEFAULT_RETRIES,
    DEFAULT_SIGNALS,
    DEFAULT_STEP_SECONDS,
    DEFAULT_WORKERS,
    IotConfig,
)
from .nodes import load_nodes, make_nodes
from .pipeline import FetchOptions, run_fetch

__all__ = ["build_parser", "main", "parse_time_args", "run_fetch_cli"]

SUBCOMMANDS = ("doctor", "ledger")


def parse_time_args(
    start_text: str,
    end_text: str,
    now: Optional[datetime] = None,
) -> Tuple[datetime, datetime]:
    """解析 `--start/--end`，逐字保留旧脚本的时间解析链。

    * 带时分（长度 > 10）按 `%Y-%m-%d %H:%M`；
    * 只带日期按 `%Y-%m-%d`；
    * 都为空：`end = 现在`、`start = end - 30 天`；
    * 一律补上北京时间 tzinfo。
    """
    now = now or datetime.now(BJ_TZ)
    end = (
        datetime.strptime(end_text, "%Y-%m-%d %H:%M").replace(tzinfo=BJ_TZ)
        if len(end_text) > 10
        else datetime.strptime(end_text, "%Y-%m-%d").replace(tzinfo=BJ_TZ)
        if end_text
        else now
    )
    start = (
        datetime.strptime(start_text, "%Y-%m-%d %H:%M").replace(tzinfo=BJ_TZ)
        if len(start_text) > 10
        else datetime.strptime(start_text, "%Y-%m-%d").replace(tzinfo=BJ_TZ)
        if start_text
        else end - timedelta(days=30)
    )
    return start, end


def build_parser() -> argparse.ArgumentParser:
    """取数子命令（默认子命令）的参数表：旧参数逐字保留 + 三个新参数。"""
    parser = argparse.ArgumentParser(
        prog="iot-fetch",
        description="IoT 历史数据批量取数（直连 REST，无需 VPN）",
    )
    parser.add_argument("--factory-code", required=True, help="厂站码，例如 PS37217-02")
    parser.add_argument(
        "--machine-prefix",
        default="",
        help="机台命名空间前缀，例如 -BE_GFJF_0001-C_003_",
    )
    parser.add_argument("--machine", default="", help="机台序号，逗号分隔，例如 0001,0002,0003")
    parser.add_argument(
        "--signals",
        default=DEFAULT_SIGNALS,
        help="信号名，逗号分隔（默认 {0}）".format(DEFAULT_SIGNALS),
    )
    parser.add_argument(
        "--nodes-file",
        help="可选：JSON 数组 [{\"namespace\",\"id\"}] 或 CSV（表头 namespace,id）",
    )
    parser.add_argument("--start", default="", help="起 YYYY-MM-DD[ HH:MM]，默认 30 天前")
    parser.add_argument("--end", default="", help="止 YYYY-MM-DD[ HH:MM]，默认现在")
    parser.add_argument("--out", default="raw", help="输出目录（每月每厂一个 parquet）")
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help="并发（实测 32 稳定，48 也 OK）",
    )
    parser.add_argument(
        "--window-hours",
        type=float,
        default=0,
        help="强制窗口小时数，0=按上限自动",
    )
    parser.add_argument(
        "--max-size",
        type=int,
        default=0,
        help="强制 maxSizePerNode，0=按探针结果",
    )
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--probe", action="store_true", help="只探上限与延迟，不写数据")
    parser.add_argument("--client-id", default=None, help="客户端 ID（默认读 IOT_CLIENT_ID，再缺省 ai-console）")
    parser.add_argument(
        "--client-secret",
        default=os.environ.get("IOT_CLIENT_SECRET", ""),
        help="客户端密钥（默认读环境变量 IOT_CLIENT_SECRET）",
    )
    parser.add_argument(
        "--credentials-json",
        default=None,
        metavar="PATH",
        help="本地凭据 JSON 路径（默认读 IOT_CREDENTIALS_JSON）；优先级：CLI > 环境变量 > JSON > 默认值",
    )
    parser.add_argument(
        "--step-seconds",
        type=float,
        default=DEFAULT_STEP_SECONDS,
        help="采样周期秒数，用于把每测点上限换算成窗口（默认 15）",
    )
    parser.add_argument(
        "--format",
        default="auto",
        choices=("auto", "parquet", "jsonl"),
        help="落盘格式：auto=有 pandas+pyarrow 就 parquet，否则 jsonl",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="iot-fetch-kit {0}".format(__version__),
    )
    return parser


def run_fetch_cli(argv: Sequence[str]) -> int:
    """执行取数子命令，返回退出码（0 正常 / 1 一行没取到）。"""
    parser = build_parser()
    args = parser.parse_args(list(argv))

    try:
        config = IotConfig.from_env(credentials_json=args.credentials_json)
    except ValueError as exc:
        sys.exit("凭据文件错误：{0}".format(exc))
    # 显式 CLI > 环境变量 > 凭据 JSON > 默认值
    if args.client_secret:
        config = config.replace(client_secret=args.client_secret)
    if args.client_id:
        config = config.replace(client_id=args.client_id)
    if not config.has_secret:
        sys.exit("缺少 --client-secret（或设置环境变量 IOT_CLIENT_SECRET）")

    start, end = parse_time_args(args.start, args.end)

    if args.nodes_file:
        try:
            nodes = load_nodes(args.nodes_file)
        except ValueError as exc:
            sys.exit("节点清单错误：{0}".format(exc))
    else:
        machines = [m.strip() for m in args.machine.split(",") if m.strip()]
        signals = [s.strip() for s in args.signals.split(",") if s.strip()]
        nodes = make_nodes(args.factory_code, args.machine_prefix, machines, signals)

    options = FetchOptions(
        nodes=nodes,
        start=start,
        end=end,
        factory=args.factory_code,
        out_dir=Path(args.out),
        workers=args.workers,
        window_hours=args.window_hours,
        max_size=args.max_size,
        retries=args.retries,
        step_seconds=args.step_seconds,
        probe_only=args.probe,
        fmt=args.format,
    )
    return run_fetch(config, options)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """统一入口：首个词是 `doctor`/`ledger` 时转对应子命令，否则按取数参数解析。"""
    args_list: List[str] = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] == "doctor":
        from .doctor import main as doctor_main

        return doctor_main(args_list[1:])
    if args_list and args_list[0] == "ledger":
        from .ledger import main as ledger_main

        return ledger_main(args_list[1:])
    return run_fetch_cli(args_list)
