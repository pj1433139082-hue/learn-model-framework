"""示例：用 iot_fetch 当库用，取自定义点表的数据并算点统计。

运行前设置环境变量 IOT_CLIENT_SECRET；或者直接把凭据作为参数传进来（不要写进源码）：

    python examples/fetch_custom_nodes.py --nodes examples/nodes.example.json \
        --start "2026-03-01" --hours 6

这个脚本演示三件事：
1. 从 JSON/CSV 文件读点表（load_nodes）；
2. 用 IotConfig.from_env() + IotClient 直接取数，得到 {测点名: {毫秒: 值}}；
3. 不落盘、在内存里做一次粗统计（每个测点的点数与均值）。
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from iot_fetch import IotClient, IotConfig, load_nodes  # noqa: E402


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="用 iot_fetch 取自定义点表的历史数据")
    parser.add_argument("--nodes", default=str(Path(__file__).with_name("nodes.example.json")),
                        help="点表文件：JSON 数组 [{\"namespace\",\"id\"}] 或 CSV（表头 namespace,id）")
    parser.add_argument("--start", default="", help="起始时间 YYYY-MM-DD[ HH:MM]，默认 1 天前")
    parser.add_argument("--hours", type=float, default=6.0, help="取多少小时（默认 6）")
    parser.add_argument("--step-seconds", type=float, default=15.0, help="采样周期秒数（默认 15）")
    return parser.parse_args(argv)


def parse_start(text: str) -> datetime:
    # 与 CLI 一致：只给日期时用北京时间零点。
    from iot_fetch import ensure_bj

    if not text:
        return ensure_bj(datetime.now()) - timedelta(days=1)
    fmt = "%Y-%m-%d %H:%M" if len(text) > 10 else "%Y-%m-%d"
    return datetime.strptime(text, fmt).replace(tzinfo=ensure_bj(datetime.now()).tzinfo)


def main(argv=None) -> int:
    args = parse_args(argv)
    nodes = load_nodes(args.nodes)
    start = parse_start(args.start)

    # 端点和身份都可以用环境变量覆盖；client_secret 不传则读 IOT_CLIENT_SECRET。
    config = IotConfig.from_env()
    config.require_secret()

    print("点表 {0} 个测点，起始 {1:%Y-%m-%d %H:%M}，取 {2:g}h".format(len(nodes), start, args.hours))
    with IotClient(config) as client:
        data = client.fetch(nodes, start, hours=args.hours, step_seconds=args.step_seconds)

    if not data:
        print("没有取到任何数据")
        return 1

    print("{0:<28} {1:>6}  {2:>14}".format("测点", "点数", "均值"))
    for name in sorted(data):
        series = data[name]
        values = [v for v in series.values() if v is not None]
        mean = statistics.fmean(values) if values else float("nan")
        print("{0:<28} {1:>6}  {2:>14.4f}".format(name, len(series), mean))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
