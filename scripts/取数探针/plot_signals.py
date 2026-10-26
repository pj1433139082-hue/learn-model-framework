"""取用参数逐条出图（SOP ③ 的「参数时序图集」）

对 `iot_bulk_fetch.py` 落入的 parquet 目录，把**每个参与计算的信号**画成时序线图，
每台设备一条线，图题带单位。产物进 `plots/`，可被 Notebook 内嵌或直接贴进分析报告。

只画参与计算的信号
------------------
SOP ③ 要求「所有取用参数逐条出图」。实际取数时会顺手拉一些辅助信号（三相电流、
运行状态、电机温度等），它们不参与公式、画出来只会淹没有效信息。默认按 `--signals`
白名单只画白名单内的；`--signals all` 才全画。

用法
----
    python plot_signals.py \
        --data "data/fan-efficiency-taierzhuang/raw" \
        --signals V_IF,V_OP,V_P,V_ITIT \
        --out plots --resample 1h

    # 更细：30 分钟重采样，只画二污
    python plot_signals.py --data <dir> --signals V_IF --match PS37217-02 --resample 30min

约定
----
* parquet 列名形如 `<数采点位编号>-<数采信号名称>`（`iot_bulk_fetch.py` 的写出格式）
* 时间列优先 `data_time_bj`，退 `data_time_utc`
* 重采样默认 1h 均值；线条是「原始点太密画不动」的降采样，不改原数据
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

UNITS = {
    "V_IF": "m³/min",
    "V_OP": "kPa",
    "V_P": "kW",
    "V_I": "A",
    "V_IA": "A",
    "V_IB": "A",
    "V_IC": "A",
    "V_ITIT": "℃",
    "V_OTIT": "℃",
    "V_MTIT": "℃",
    "V_SIT": "%",
    "V_TRT": "h",
    "S_RUN": "0/1",
    "S_AUT": "0/1",
    "S_REM": "0/1",
    "A_FLT": "0/1",
    "A_SAG": "0/1",
}

LABELS = {
    "V_IF": "进口风量",
    "V_OP": "出口压力（表压）",
    "V_P": "功率",
    "V_I": "电流",
    "V_ITIT": "进口温度",
    "V_MTIT": "电机温度",
    "V_OTIT": "出口温度",
    "S_RUN": "运行状态",
    "S_AUT": "自动/手动",
    "S_REM": "远程/就地",
    "A_FLT": "故障",
    "A_SAG": "喘振报警",
    "V_TRT": "运行时间",
    "V_SIT": "就地设定",
}


def _setup_fonts():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans SC", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="取用参数逐条出图")
    ap.add_argument("--data", required=True, help="parquet 目录（iot_bulk_fetch.py 的 --out）")
    ap.add_argument("--signals", default="", help="要画的信号，逗号分隔；留空或 all 表示全画")
    ap.add_argument("--match", default="", help="只画点位编号含该子串的设备，例如 PS37217-02")
    ap.add_argument("--out", default="plots", help="输出目录，默认 plots/")
    ap.add_argument("--resample", default="1h", help="重采样周期（pandas 频率），默认 1h；none 表示不重采样")
    ap.add_argument("--clip-pct", type=float, default=99.0,
                    help="按分位数裁剪 y 轴（默认 99，即取 p0.5~p99.5）；0 表示不裁剪、显示完整量程")
    ap.add_argument("--start", default="", help="起始时间 YYYY-MM-DD，可选")
    ap.add_argument("--end", default="", help="结束时间 YYYY-MM-DD，可选")
    args = ap.parse_args(argv)

    try:
        import pandas as pd
    except ImportError:  # pragma: no cover
        sys.exit("缺少 pandas：请用项目 .venv-311 运行")
    try:
        import pyarrow  # noqa: F401
    except ImportError:  # pragma: no cover
        sys.exit("缺少 pyarrow（读 parquet 需要）")

    plt = _setup_fonts()

    data_dir = Path(args.data)
    files = sorted(data_dir.rglob("*.parquet")) if data_dir.is_dir() else []
    if not files:
        sys.exit(f"目录下没有 parquet：{data_dir}")
    print(f"读取 {len(files)} 个 parquet ...")

    frames = []
    for f in files:
        df = pd.read_parquet(f)
        if args.match:
            # 时间列不含设备号，必须一并保留，否则后面找不到时间轴
            keep = [c for c in df.columns if args.match in c or c in ("data_time_bj", "data_time_utc")]
            df = df[keep]
        frames.append(df)
    # iot_bulk_fetch.py 按月落盘，每个 parquet 是「一行一个时间戳、一列一个测点」的宽表，
    # 所以月份之间是纵向拼接（axis=0），不是横向。
    df = pd.concat(frames, axis=0, ignore_index=True) if len(frames) > 1 else frames[0]
    df = df.loc[:, ~df.columns.duplicated()]

    tcol = "data_time_bj" if "data_time_bj" in df.columns else ("data_time_utc" if "data_time_utc" in df.columns else None)
    if tcol is None:
        sys.exit(f"找不到时间列（data_time_bj / data_time_utc）：{list(df.columns)[:10]}")

    if args.start:
        df = df[df[tcol] >= pd.Timestamp(args.start)]
    if args.end:
        df = df[df[tcol] < pd.Timestamp(args.end)]
    if df.empty:
        sys.exit("按 --start/--end 过滤后没有数据")

    want = [s.strip() for s in args.signals.split(",") if s.strip()]
    time_columns = {"data_time_bj", "data_time_utc"}
    cols = [c for c in df.columns if c not in time_columns]
    if want and want != ["all"]:
        cols = [c for c in cols if c.rsplit("-", 1)[-1] in want]
    if not cols:
        sys.exit("没有匹配的信号列，检查 --signals")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    df = df[[tcol] + cols].set_index(tcol).sort_index()
    rs = args.resample.strip().lower()
    if rs not in ("", "none", "0"):
        df = df.resample(args.resample).mean()

    made = []
    for c in cols:
        sig = c.rsplit("-", 1)[-1]
        unit = UNITS.get(sig, "")
        label = LABELS.get(sig, sig)
        fig, ax = plt.subplots(figsize=(11, 3.4), constrained_layout=True)
        ax.plot(df.index, df[c], lw=0.8, color="#1f5f8b")
        ax.set_title(f"{label}（{sig}）" + (f" · 单位 {unit}" if unit else ""), fontsize=11, loc="left")
        ax.set_xlabel("北京时间")
        ax.set_ylabel(f"{sig}" + (f" / {unit}" if unit else ""))
        ax.grid(True, ls=":", lw=0.5, alpha=0.6)

        note = ""
        if args.clip_pct and 50 < args.clip_pct < 100:
            tail = (100 - args.clip_pct) / 2
            lo = float(df[c].quantile(tail / 100))
            hi = float(df[c].quantile(1 - tail / 100))
            if hi > lo:
                pad = (hi - lo) * 0.08
                ax.set_ylim(lo - pad, hi + pad)
                note = f"· y 轴裁到 p{tail:.1f}~p{args.clip_pct - tail:.1f}（坏值块不入图，--clip-pct 0 看全量程）"
                ax.set_title(f"{label}（{sig}）" + (f" · 单位 {unit}" if unit else "") + "  " + note,
                             fontsize=9.5, loc="left")

        dev = c.rsplit("-", 1)[0]
        ax.text(0.995, 0.02, dev, transform=ax.transAxes, ha="right", va="bottom", fontsize=7, color="#6b7280")
        name = f"{sig}__{dev}.png"
        fig.savefig(out_dir / name, dpi=140)
        plt.close(fig)
        made.append(name)

    print(f"已出图 {len(made)} 张 → {out_dir}")
    for n in made:
        print("  ", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
