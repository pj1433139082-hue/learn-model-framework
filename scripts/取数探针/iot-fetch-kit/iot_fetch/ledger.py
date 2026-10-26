"""自控信号台账（xlsx）→ 取数节点清单 / Markdown 点表。

逐条移植自旧脚本 `iot_ledger_to_nodes.py`：列名常量、`--match` 模糊匹配、
`--column` 选择匹配列、`--signals` 过滤与未知信号警告、点位/信号为空的行跳过、
`(点位, 信号)` 去重、`--out` JSON、`--md` 四列点表、`--list-devices`。

退出码：0 正常；匹配到 0 行返回 3；台账/参数问题走 `sys.exit(消息)`（Python 语义即退出码 1）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "COL_DEVICE",
    "COL_FACILITY",
    "COL_POINT",
    "COL_SIGNAL",
    "REQUIRED",
    "ledger_to_nodes",
    "main",
    "match_mask",
    "read_ledger",
    "run_ledger",
    "select_rows",
]

COL_FACILITY = "设施名称"
COL_DEVICE = "设备名称"
COL_POINT = "数采点位编号"
COL_SIGNAL = "数采信号名称"
REQUIRED = (COL_FACILITY, COL_DEVICE, COL_POINT, COL_SIGNAL)


def read_ledger(path: Any):
    """读台账 xlsx（缺 pandas/openpyxl 或缺列时直接退出，与旧脚本一致）。"""
    try:
        import pandas as pd
    except ImportError:  # pragma: no cover - 依赖环境
        sys.exit("缺少 pandas：请 pip install 'iot-fetch-kit[ledger]'")
    target = Path(path)
    if not target.is_file():
        sys.exit("台账不存在：{0}".format(target))
    df = pd.read_excel(target)
    missing = [column for column in REQUIRED if column not in df.columns]
    if missing:
        sys.exit(
            "台账缺少必需列：{0}（实际列：{1}...）".format(
                missing, list(df.columns)[:12]
            )
        )
    return df.fillna("")


def match_mask(df: Any, keyword: str, column: str):
    """按关键词做「包含」匹配，返回布尔掩码；关键词为空返回 `None`（= 全选）。"""
    word = (keyword or "").strip()
    if not word:
        return None
    columns = [COL_FACILITY, COL_DEVICE] if column in ("", "any", "任一") else [column]
    mask = None
    for name in columns:
        if name not in df.columns:
            sys.exit("没有这一列：{0}".format(name))
        matched = df[name].astype(str).str.contains(word, case=False, regex=False)
        mask = matched if mask is None else (mask | matched)
    return mask


def select_rows(
    df: Any,
    *,
    match: str = "",
    column: str = "any",
    signals: str = "",
) -> Optional[List[Dict[str, str]]]:
    """过滤台账，返回 `[{facility, device, namespace, id, full_id}]`；无匹配返回 `None`。"""
    mask = match_mask(df, match, column)
    subset = df if mask is None else df[mask]
    if subset.empty:
        print("匹配到 0 行：--match {0!r}".format(match), file=sys.stderr)
        return None
    if signals:
        wanted = [item.strip() for item in signals.split(",") if item.strip()]
        got = {str(item) for item in subset[COL_SIGNAL]}
        unknown = [item for item in wanted if item not in got]
        if unknown:
            print(
                "警告：台账里没有这些信号 {0}；台账有此设备族的信号：{1}".format(
                    unknown, sorted(got)
                ),
                file=sys.stderr,
            )
        subset = subset[subset[COL_SIGNAL].astype(str).isin(wanted)]
        if subset.empty:
            print("按 --signals 过滤后为空", file=sys.stderr)
            return None
    # 点位编号/信号名都不能空（少尾缀接口会静默返回 null）
    bad = subset[
        (subset[COL_POINT].astype(str).str.strip() == "")
        | (subset[COL_SIGNAL].astype(str).str.strip() == "")
    ]
    if len(bad):
        print(
            "警告：{0} 行缺少点位编号或信号名，已跳过".format(len(bad)), file=sys.stderr
        )
        subset = subset.drop(bad.index)
    rows: List[Dict[str, str]] = []
    seen = set()
    for _, record in subset.iterrows():
        point = str(record[COL_POINT]).strip()
        signal = str(record[COL_SIGNAL]).strip()
        key = (point, signal)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "facility": str(record[COL_FACILITY]).strip(),
                "device": str(record[COL_DEVICE]).strip(),
                "namespace": point,
                "id": signal,
                "full_id": "{0}-{1}".format(point, signal),
            }
        )
    return rows


def ledger_to_nodes(
    ledger_path: Any,
    *,
    match: str = "",
    column: str = "any",
    signals: str = "",
) -> List[Dict[str, str]]:
    """库用法：台账 xlsx → `[{"namespace": 点位编号, "id": 信号名}]`。

    匹配到 0 行或过滤后为空时抛 `ValueError`（不像 CLI 那样返回 3）。
    """
    rows = select_rows(read_ledger(ledger_path), match=match, column=column, signals=signals)
    if rows is None:
        raise ValueError("台账里没有匹配的节点：--match {0!r}".format(match))
    return [{"namespace": row["namespace"], "id": row["id"]} for row in rows]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="iot-fetch ledger",
        description="自控信号台账 → 取数节点清单（点表）",
    )
    parser.add_argument(
        "--ledger",
        required=True,
        help="台账 xlsx 路径（建议 data/台账/<厂>-自控信号台账-<hash8>.xlsx）",
    )
    parser.add_argument("--match", default="", help="关键词，匹配设施名称或设备名称，例如 曝气风机")
    parser.add_argument("--column", default="any", help="匹配列：any/设施名称/设备名称，默认 any")
    parser.add_argument(
        "--signals",
        default="",
        help="只保留这些信号，逗号分隔，例如 V_IF,V_OP,V_P,V_ITIT",
    )
    parser.add_argument("--out", default="", help="输出 JSON 节点清单（给 iot-fetch --nodes-file）")
    parser.add_argument("--md", default="", help="输出 Markdown 四列点表")
    parser.add_argument("--list-devices", action="store_true", help="只列出台账里的设备名称，不产出文件")
    return parser


def run_ledger(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    path = Path(args.ledger)
    df = read_ledger(path)

    if args.list_devices:
        names = sorted({str(item) for item in df[COL_DEVICE] if str(item).strip()})
        print("台账 {0}：{1} 行，{2} 个设备名称".format(path.name, len(df), len(names)))
        for name in names:
            print("  -", name)
        return 0

    rows = select_rows(df, match=args.match, column=args.column, signals=args.signals)
    if rows is None:
        return 3

    nodes = [{"namespace": row["namespace"], "id": row["id"]} for row in rows]
    machines = sorted({row["namespace"] for row in rows})
    print(
        "匹配 {0} 个节点，覆盖 {1} 个点位编号（设备）：".format(len(rows), len(machines))
    )
    for machine in machines:
        devices = sorted({row["device"] for row in rows if row["namespace"] == machine})
        print("  {0}  {1}".format(machine, "/".join(devices)))

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(nodes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print("已写 {0}（{1} 个节点）".format(out, len(nodes)))

    if args.md:
        markdown = Path(args.md)
        markdown.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "| 设施名称 | 设备名称 | 数采点位编号 | 数采信号名称 |",
            "|---|---|---|---|",
        ]
        for row in rows:
            lines.append(
                "| {0} | {1} | `{2}` | `{3}` |".format(
                    row["facility"], row["device"], row["namespace"], row["id"]
                )
            )
        markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("已写 {0}（{1} 行）".format(markdown, len(rows)))

    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    return run_ledger(argv)
