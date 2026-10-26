"""自控信号台账 → 取数节点清单（点表）

用途
----
把权威台账（`data/台账/<厂>-自控信号台账-<hash8>.xlsx`）里某个设备族的所有信号
抽成两类产物：

1. `--out` 的 JSON：`[{"namespace": 数采点位编号, "id": 数采信号名称}, ...]`
   可直接喂给 `iot_bulk_fetch.py --nodes-file`，不用手抄点位。
2. `--md` 的 Markdown 点表：设施名称 / 设备名称 / 数采点位编号 / 数采信号名称
   四列对照，直接贴进对接文档或发给现场核对。

为什么要有它
------------
测点 ID 是「数采点位编号 + '-' + 数采信号名称」拼接（见 `docs/10-流程层/15-多字段测点绑定.md`），
少写尾缀时接口**不报错、静默返回 null**（见 `docs/20-规则层/20-硬性规则速查.md`）。
手抄 6 台 × 17 个信号 = 102 个 ID，必错；脚本从台账机器生成，错不了。

用法
----
    # <plant> 6 台曝气风机，只要 4 个参与计算的信号
    python iot_ledger_to_nodes.py \
        --ledger "data/台账/<plant><plant>-自控信号台账-71a60e01.xlsx" \
        --match 曝气风机 --signals V_IF,V_OP,V_P,V_ITIT \
        --out nodes.json --md 点表.md

    # 先看台账里有哪些设备族
    python iot_ledger_to_nodes.py --ledger <xlsx> --list-devices

退出码：0 正常；2 参数/台账问题；3 匹配到 0 行。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

COL_FACILITY = "设施名称"
COL_DEVICE = "设备名称"
COL_POINT = "数采点位编号"
COL_SIGNAL = "数采信号名称"
REQUIRED = (COL_FACILITY, COL_DEVICE, COL_POINT, COL_SIGNAL)


def _read_ledger(path: Path):
    try:
        import pandas as pd
    except ImportError:  # pragma: no cover
        sys.exit("缺少 pandas：请用项目 .venv-311 运行")
    if not path.is_file():
        sys.exit(f"台账不存在：{path}")
    df = pd.read_excel(path)
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        sys.exit(f"台账缺少必需列：{missing}（实际列：{list(df.columns)[:12]}...）")
    return df.fillna("")


def _match_mask(df, keyword: str, column: str):
    kw = keyword.strip()
    if not kw:
        return None
    cols = [COL_FACILITY, COL_DEVICE] if column in ("", "any", "任一") else [column]
    mask = None
    for c in cols:
        if c not in df.columns:
            sys.exit(f"没有这一列：{c}")
        m = df[c].astype(str).str.contains(kw, case=False, regex=False)
        mask = m if mask is None else (mask | m)
    return mask


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="自控信号台账 → 取数节点清单（点表）")
    ap.add_argument("--ledger", required=True, help="台账 xlsx 路径（建议 data/台账/<厂>-自控信号台账-<hash8>.xlsx）")
    ap.add_argument("--match", default="", help="关键词，匹配设施名称或设备名称，例如 曝气风机")
    ap.add_argument("--column", default="any", help="匹配列：any/设施名称/设备名称，默认 any")
    ap.add_argument("--signals", default="", help="只保留这些信号，逗号分隔，例如 V_IF,V_OP,V_P,V_ITIT")
    ap.add_argument("--out", default="", help="输出 JSON 节点清单（给 iot_bulk_fetch.py --nodes-file）")
    ap.add_argument("--md", default="", help="输出 Markdown 四列点表")
    ap.add_argument("--list-devices", action="store_true", help="只列出台账里的设备名称，不产出文件")
    args = ap.parse_args(argv)

    path = Path(args.ledger)
    df = _read_ledger(path)

    if args.list_devices:
        names = sorted({str(x) for x in df[COL_DEVICE] if str(x).strip()})
        print(f"台账 {path.name}：{len(df)} 行，{len(names)} 个设备名称")
        for n in names:
            print("  -", n)
        return 0

    mask = _match_mask(df, args.match, args.column)
    sub = df if mask is None else df[mask]
    if sub.empty:
        print(f"匹配到 0 行：--match {args.match!r}", file=sys.stderr)
        return 3

    if args.signals:
        want = [s.strip() for s in args.signals.split(",") if s.strip()]
        got = {str(s) for s in sub[COL_SIGNAL]}
        unknown = [s for s in want if s not in got]
        if unknown:
            print(f"警告：台账里没有这些信号 {unknown}；台账有此设备族的信号：{sorted(got)}", file=sys.stderr)
        sub = sub[sub[COL_SIGNAL].astype(str).isin(want)]
        if sub.empty:
            print("按 --signals 过滤后为空", file=sys.stderr)
            return 3

    # 校验：点位编号/信号名都不能空（少尾缀会静默返回 null）
    bad = sub[(sub[COL_POINT].astype(str).str.strip() == "") | (sub[COL_SIGNAL].astype(str).str.strip() == "")]
    if len(bad):
        print(f"警告：{len(bad)} 行缺少点位编号或信号名，已跳过", file=sys.stderr)
        sub = sub.drop(bad.index)

    rows = []
    seen = set()
    for _, r in sub.iterrows():
        point = str(r[COL_POINT]).strip()
        sig = str(r[COL_SIGNAL]).strip()
        key = (point, sig)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "facility": str(r[COL_FACILITY]).strip(),
            "device": str(r[COL_DEVICE]).strip(),
            "namespace": point,
            "id": sig,
            "full_id": f"{point}-{sig}",
        })

    nodes = [{"namespace": x["namespace"], "id": x["id"]} for x in rows]
    machines = sorted({x["namespace"] for x in rows})
    print(f"匹配 {len(rows)} 个节点，覆盖 {len(machines)} 个点位编号（设备）：")
    for m in machines:
        devs = sorted({x["device"] for x in rows if x["namespace"] == m})
        print(f"  {m}  {'/'.join(devs)}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(nodes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已写 {out}（{len(nodes)} 个节点）")

    if args.md:
        md = Path(args.md)
        md.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "| 设施名称 | 设备名称 | 数采点位编号 | 数采信号名称 |",
            "|---|---|---|---|",
        ]
        for x in rows:
            lines.append(f"| {x['facility']} | {x['device']} | `{x['namespace']}` | `{x['id']}` |")
        md.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"已写 {md}（{len(rows)} 行）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
