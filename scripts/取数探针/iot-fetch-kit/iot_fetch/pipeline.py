"""逐月编排：探上限 → 切窗 → 并发取 → 重排失败窗 → 逐月落盘 → 统计。

`run_fetch()` 是旧脚本 `main()` 取数段的等价实现（连中文打印一起搬过来）；
`fetch_range()` 是给库用户用的版本：给区间，直接拿 `{测点: {毫秒: 值}}`。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .client import (
    FetchStats,
    IotClient,
    ensure_bj,
    month_slices,
    ms_to_bj,
    parse_signal_window,
    slice_windows,
)
from .config import BJ_TZ, DEFAULT_STEP_SECONDS, IotConfig
from .writers import MonthWriter, normalize_frame

__all__ = ["FetchOptions", "fetch_range", "run_fetch"]

RETRY_ROUNDS = 3
"""失败窗整轮重排的总轮数（旧脚本同款：首轮 + 2 次重排，轮 1 前等 2s、轮 2 前等 4s）。"""

WindowCols = List[Tuple[str, Dict[int, Any]]]


@dataclass
class FetchOptions:
    """一次取数任务的参数（字段与旧 CLI 一一对应）。

    `max_size=0` 表示「由探针决定」；`window_hours=0` 表示「按探针换算」。
    """

    nodes: Sequence[Dict[str, Any]]
    start: datetime
    end: datetime
    factory: str
    out_dir: Path = Path("raw")
    workers: int = 16
    window_hours: float = 0.0
    max_size: int = 0
    retries: int = 4
    step_seconds: float = DEFAULT_STEP_SECONDS
    probe_only: bool = False
    fmt: str = "auto"
    log: Callable[[str], None] = field(default=print)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _sleep(seconds: float, config: IotConfig) -> None:
    """按 `sleep_scale` 缩放等待（测试里设为 0 可以把重试退避变成空转）。"""
    delay = float(seconds) * float(config.sleep_scale)
    if delay > 0:
        time.sleep(delay)


def _merge_into(table: Dict[int, Dict[str, Any]], cols: Sequence[Tuple[str, Dict[int, Any]]]) -> None:
    for name, values in cols:
        for stamp, value in values.items():
            table.setdefault(int(stamp), {})[name] = value


def _persist(
    writer: MonthWriter,
    table: Dict[int, Dict[str, Any]],
    year: int,
    month: int,
) -> Tuple[int, datetime, datetime]:
    """落盘一个月，返回 `(行数, 首条北京时间, 末条北京时间)`。"""
    if writer.fmt == "parquet":
        import pandas as pd

        df = pd.DataFrame.from_dict(table, orient="index")
        df.index = pd.Index(df.index).astype("int64")
        df = normalize_frame(df)
        writer.write_parquet(df, year, month)
        sp = df["data_time_bj"]
        return len(df), sp.iloc[0].to_pydatetime(), sp.iloc[-1].to_pydatetime()
    writer.write_jsonl(table, year, month)
    stamps = sorted(table)
    return len(stamps), ms_to_bj(stamps[0]), ms_to_bj(stamps[-1])


def _run_windows(
    client: IotClient,
    nodes: Sequence[Dict[str, Any]],
    wins: Sequence[datetime],
    window: float,
    max_size: int,
    retries: int,
    workers: int,
) -> Dict[datetime, Optional[WindowCols]]:
    """并发取一批窗口，返回 `{窗口起点: cols|None}`（`None` = 重试耗尽）。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not wins:
        return {}
    limit = max(1, min(int(workers), len(wins)))
    results: Dict[datetime, Optional[WindowCols]] = {}
    with ThreadPoolExecutor(max_workers=limit) as pool:
        futures = {
            pool.submit(client.fetch_window, nodes, start, window, max_size, retries): start
            for start in wins
        }
        for future in as_completed(futures):
            start = futures[future]
            try:
                _, cols = future.result()
            except Exception:  # noqa: BLE001 - fetch_window 自己不抛，这里只做保护
                cols = None
            results[start] = cols
    return results


def _fetch_month(
    client: IotClient,
    config: IotConfig,
    nodes: Sequence[Dict[str, Any]],
    wins: Sequence[datetime],
    window: float,
    max_size: int,
    retries: int,
    workers: int,
    year: int,
    month: int,
    log: Callable[[str], None],
) -> Tuple[Dict[int, Dict[str, Any]], List[datetime]]:
    """并发取一个月，失败窗整轮重排；返回 `(表, 最终失败的窗口)`。

    轮次与旧脚本一致：首轮取全部窗口，之后至多 2 次重排（轮 1 前等 2s、轮 2 前等 4s），
    每轮按窗口顺序合并，最后仍失败的窗口只提示一次。
    """
    table: Dict[int, Dict[str, Any]] = {}
    pending: List[datetime] = list(wins)
    for rnd in range(RETRY_ROUNDS):
        if not pending:
            break
        if rnd:
            _sleep(2 * rnd, config)  # 轮 1 前 2s，轮 2 前 4s
        batch = list(pending)
        results = _run_windows(client, nodes, batch, window, max_size, retries, workers)
        pending = []
        order = list(wins) if rnd == 0 else batch
        for start in order:  # 按窗口顺序合并，保证覆盖顺序与串行取数一致
            cols = results.get(start)
            if cols is None:
                pending.append(start)
            else:
                _merge_into(table, cols)
    if pending:
        head = ", ".join("{0:%m-%d %H:%M}".format(win) for win in pending[:5])
        log(
            "  ⚠ {0}-{1:02d} 仍有 {2} 个窗口失败：{3}".format(
                year, month, len(pending), head
            )
        )
    return table, pending


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_fetch(config: IotConfig, options: FetchOptions, *, client: Optional[IotClient] = None) -> int:
    """执行一次取数任务，返回退出码（0 = 正常，1 = 一行都没取到）。

    打印与退出行为对齐旧脚本：探针/汇总/失败窗提示都保留中文原样。
    """
    log = options.log
    nodes = list(options.nodes)
    start = ensure_bj(options.start)
    end = ensure_bj(options.end)
    factory = str(options.factory)
    out_dir = Path(options.out_dir)
    step = float(options.step_seconds)
    own_client = client is None
    client = client or IotClient(config, retries=options.retries)
    stats: FetchStats = client.stats
    began_all = time.monotonic()
    grand = 0
    try:
        log(
            "测点 {0} 个 × 窗口内采样；区间 {1:%Y-%m-%d %H:%M} → {2:%Y-%m-%d %H:%M}".format(
                len(nodes), start, end
            )
        )
        # 探「每测点上限」：近端用 end-1 天（近期上限更高，保守地决定窗口）
        cap = int(options.max_size) or client.probe_cap(
            nodes, end - timedelta(days=1), retries=options.retries
        )
        if options.probe_only:
            probe_at = end - timedelta(days=1)
            cap_old = client.probe_cap(
                nodes, start + timedelta(days=1), retries=options.retries
            )
            log("近期（{0:%Y-%m-%d}）每测点上限 = {1}".format(probe_at, cap))
            log("起点附近（{0:%Y-%m-%d}）每测点上限 = {1}".format(start, cap_old))
            log(
                "建议窗口：近期 {0:g}h、远期 {1:g}h（@{2:g}s）".format(
                    parse_signal_window(cap, step),
                    parse_signal_window(cap_old, step),
                    step,
                )
            )
            return 0
        if cap <= 0:
            raise SystemExit("上限探测失败（该时间段无数据或鉴权失败）")
        window = float(options.window_hours) or parse_signal_window(cap, step)
        log(
            "每测点上限 {0}，窗口 {1:g}h，并发 {2}".format(
                cap, window, int(options.workers)
            )
        )
        writer = MonthWriter(out_dir, factory, options.fmt, log=log)
        if writer.fmt != "parquet":
            log("⚠ 落盘格式 jsonl：{0}".format(writer.reason))
        max_size = int(options.max_size) or 100000
        months = month_slices(start, end)
        for index, (year, month, month_start, month_end) in enumerate(months, start=1):
            if not options.window_hours:
                # 远期月份上限更低：每个月在区间中点重探一次（旧脚本同款，
                # 即使给了 --max-size 也照探；给了 --window-hours 才完全跳过探针）
                cap_month = client.probe_cap(
                    nodes,
                    month_start + (month_end - month_start) / 2,
                    retries=options.retries,
                )
                if cap_month:
                    window = parse_signal_window(cap_month, step)
            month_window = window
            wins = slice_windows(month_start, month_end, month_window)
            if not wins:
                continue
            began = time.monotonic()
            table, pending = _fetch_month(
                client,
                config,
                nodes,
                wins,
                month_window,
                max_size,
                options.retries,
                int(options.workers),
                year,
                month,
                log,
            )
            elapsed = time.monotonic() - began
            fail = len(pending)
            if not table:
                log(
                    "[{0:2d}] {1} {2}-{3:02d}: 无数据（失败 {4}/{5}）".format(
                        index, factory, year, month, fail, len(wins)
                    )
                )
                continue
            rows, first_bj, last_bj = _persist(writer, table, year, month)
            log(
                "[{0:2d}] {1} {2}-{3:02d}: {4:7d} 行 ({5}/{6} 窗@{7:g}h, 失败 {8}) "
                "{9:%m-%d %H:%M}→{10:%m-%d %H:%M} {11:.1f}s".format(
                    index,
                    factory,
                    year,
                    month,
                    rows,
                    len(wins) - fail,
                    len(wins),
                    month_window,
                    fail,
                    first_bj,
                    last_bj,
                    elapsed,
                )
            )
            grand += rows
    finally:
        if own_client:
            client.close()

    log(
        "\n完成：成功 {0}（重试成功 {1}），彻底失败 {2}，总行数 {3}，{4:.0f}s → {5}".format(
            stats.ok,
            stats.retried,
            stats.fail,
            grand,
            time.monotonic() - began_all,
            out_dir,
        )
    )
    if client.missing:
        log("空测点 {0} 个（前 10）：".format(len(client.missing)))
        for name, count in sorted(
            client.missing.items(), key=lambda item: (-item[1], item[0])
        )[:10]:
            log("  {0}: {1} 窗无数据".format(name, count))
    if grand == 0:
        return 1
    if stats.fail:
        log("⚠ 有 {0} 个窗口重试耗尽（重跑同命令会整月覆盖重写）".format(stats.fail))
    return 0


# ---------------------------------------------------------------------------
# 库用法
# ---------------------------------------------------------------------------
def fetch_range(
    config: IotConfig,
    nodes: Sequence[Dict[str, Any]],
    start: datetime,
    end: datetime,
    *,
    step_seconds: float = DEFAULT_STEP_SECONDS,
    window_hours: float = 0.0,
    max_size: int = 100000,
    workers: int = 16,
    retries: int = 4,
    probe: bool = True,
) -> Dict[str, Dict[int, Any]]:
    """取任意区间，返回 `{测点名: {毫秒: 值}}`。

    `window_hours=0` 时按每测点上限自动换算窗口（`probe=True` 先探一次，
    `probe=False` 用保守的 6 小时）。任一窗口重试耗尽会抛 `RuntimeError`，
    不静默丢数据。

    ```
    data = fetch_range(cfg, nodes, start, end)          # 几个月也行
    len(data["PS37217-02-BE_GFJF_0001-C_003_0001-V_IF"])
    ```
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    start = ensure_bj(start)
    end = ensure_bj(end)
    nodes = list(nodes)
    client = IotClient(config, retries=retries)
    try:
        window = float(window_hours)
        if not window:
            cap = client.probe_cap(nodes, start + (end - start) / 2, retries=retries) if probe else 0
            window = parse_signal_window(cap, step_seconds)
        wins: List[datetime] = []
        for _, _, month_start, month_end in month_slices(start, end):
            wins.extend(slice_windows(month_start, month_end, window))
        results: Dict[datetime, Optional[WindowCols]] = {}
        if wins:
            with ThreadPoolExecutor(max_workers=max(1, min(int(workers), len(wins)))) as pool:
                futures = {
                    pool.submit(client.fetch_window, nodes, win, window, max_size, retries): win
                    for win in wins
                }
                for future in as_completed(futures):
                    win = futures[future]
                    try:
                        results[win] = future.result()[1]
                    except Exception:  # noqa: BLE001
                        results[win] = None
        merged: Dict[str, Dict[int, Any]] = {}
        failed: List[datetime] = []
        for win in wins:
            cols = results.get(win)
            if cols is None:
                failed.append(win)
                continue
            for name, values in cols:
                bucket = merged.setdefault(name, {})
                for stamp, value in values.items():
                    bucket[int(stamp)] = value
        if failed:
            raise RuntimeError(
                "有 {0} 个窗口重试耗尽，首个：{1:%Y-%m-%d %H:%M}".format(len(failed), failed[0])
            )
        return merged
    finally:
        client.close()
