"""落盘：每月一份 parquet（无 pandas/pyarrow 时退化为 jsonl）。

输出契约（与旧脚本逐条一致，另补全 jsonl）：

* 文件名 `{factory}-{YYYYMM}.parquet`，`YYYYMM` 是**北京时间**自然月；
* 索引 = 毫秒整数（`int64`）、升序、去重 `keep="first"`；
* 列 = 测点名（`namespace + "-" + id`），外加 `data_time_utc`（tz-aware UTC）与
  `data_time_bj`（Asia/Shanghai）；
* `compression="zstd"`；先写同目录临时文件再 `os.replace` 原子替换，
  中途失败不会留下半个文件。
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

__all__ = [
    "MonthWriter",
    "bj_month_stamp",
    "normalize_frame",
    "pandas_available",
    "resolve_format",
]

FORCE_JSONL_ENV = "IOT_FETCH_FORCE_JSONL"


def pandas_available() -> bool:
    """pandas 在不在（能否建 DataFrame）。"""
    try:
        import pandas  # noqa: F401
    except Exception:  # noqa: BLE001 - 任何导入失败都视为不可用
        return False
    return True


def pyarrow_available() -> bool:
    """pyarrow 在不在（能否写 parquet）。"""
    try:
        import pyarrow  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def resolve_format(fmt: str = "auto") -> Tuple[str, str]:
    """决定落盘格式，返回 `(格式, 原因)`。

    * `fmt="parquet"`：强制 parquet，缺 pandas/pyarrow 直接抛 `RuntimeError`；
    * `fmt="jsonl"`：强制 jsonl；
    * `fmt="auto"`（默认）：环境变量 `IOT_FETCH_FORCE_JSONL=1` 时强制 jsonl，
      否则有 pandas+pyarrow 就用 parquet，缺依赖自动退化成 jsonl。
    """
    fmt = (fmt or "auto").strip().lower()
    if fmt not in ("auto", "parquet", "jsonl"):
        raise ValueError("未知落盘格式：{0}（可选 auto/parquet/jsonl）".format(fmt))
    if fmt == "jsonl":
        return "jsonl", "显式指定 --format jsonl"
    if fmt == "parquet":
        if not pandas_available() or not pyarrow_available():
            raise RuntimeError("指定了 parquet，但缺少 pandas 或 pyarrow（pip install 'iot-fetch-kit[parquet]'）")
        return "parquet", "显式指定 --format parquet"
    forced = os.environ.get(FORCE_JSONL_ENV, "").strip().lower()
    if forced in ("1", "true", "yes", "on"):
        return "jsonl", "环境变量 {0} 要求 jsonl".format(FORCE_JSONL_ENV)
    if pandas_available() and pyarrow_available():
        return "parquet", "pandas + pyarrow 可用"
    missing = "pandas" if not pandas_available() else "pyarrow"
    return "jsonl", "缺少 {0}，自动退化为 jsonl".format(missing)


def bj_month_stamp(year: int, month: int) -> str:
    """`(2026, 1)` → `"202601"`（文件名里的北京时间月份戳）。"""
    return "{0:d}{1:02d}".format(int(year), int(month))


def normalize_frame(df: Any) -> Any:
    """把 `{毫秒: {测点: 值}}` 建出的表整理成落盘契约的形状。

    索引转 `int64` 并升序、重复时间戳保留第一条，再追加两列北京时间/UTC 时间。
    """
    import pandas as pd

    if df is None or len(df) == 0:
        return df
    df = df.copy()
    df.index = pd.Index(df.index).astype("int64")
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="first")]
    utc = pd.to_datetime(df.index, unit="ms", utc=True)
    df["data_time_utc"] = utc
    df["data_time_bj"] = utc.tz_convert("Asia/Shanghai")
    return df


def _jsonize(value: Any) -> Any:
    """把 numpy / NaN / inf 之类变成合法 JSON 值。"""
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, (bool, int, str)):
        return value
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _jsonize(item())
        except Exception:  # noqa: BLE001
            return str(value)
    return value


def _atomic_write_text(path: Path, lines: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=str(path.parent),
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    )
    tmp_name = handle_tmp.name
    try:
        with handle_tmp as stream:
            for line in lines:
                stream.write(line)
                stream.write("\n")
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class MonthWriter:
    """按月落盘。一个实例对应一个输出目录 + 一个厂站码。

    ```
    writer = MonthWriter("raw", "PS37217-02", "auto")
    writer.write_parquet(df, 2026, 1)      # → raw/PS37217-02-202601.parquet
    writer.write_jsonl(table, 2026, 1)     # → raw/PS37217-02-202601.jsonl
    ```
    """

    def __init__(self, out_dir: Any, factory: str, fmt: str = "auto", *, log: Optional[Any] = None) -> None:
        self.out_dir = Path(out_dir)
        self.factory = str(factory)
        self.fmt, self.reason = resolve_format(fmt)
        self.log = log

    def path_for(self, year: int, month: int) -> Path:
        """该月的输出路径（`.parquet` 或 `.jsonl`，取决于解析出的格式）。"""
        suffix = ".parquet" if self.fmt == "parquet" else ".jsonl"
        return self.out_dir / "{0}-{1}{2}".format(self.factory, bj_month_stamp(year, month), suffix)

    # -- parquet ---------------------------------------------------------
    def write_parquet(self, df: Any, year: int, month: int) -> Path:
        """写 parquet（`zstd`，原子替换），返回路径。`df` 应已 `normalize_frame`。"""
        if self.fmt != "parquet":
            raise RuntimeError("当前落盘格式是 {0}，不能写 parquet".format(self.fmt))
        target = self.path_for(year, month)
        target.parent.mkdir(parents=True, exist_ok=True)
        handle_tmp = tempfile.NamedTemporaryFile(
            dir=str(target.parent),
            prefix=target.name + ".",
            suffix=".tmp",
            delete=False,
        )
        tmp_name = handle_tmp.name
        handle_tmp.close()
        try:
            df.to_parquet(tmp_name, compression="zstd", index=True)
            os.replace(tmp_name, str(target))
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return target

    # -- jsonl -----------------------------------------------------------
    def write_jsonl(self, table: Mapping[int, Mapping[str, Any]], year: int, month: int) -> Path:
        """写 jsonl（每行 `{"t": 毫秒, "data_time_utc": iso, "signals": {测点: 值}}`）。"""
        target = self.path_for(year, month)
        from datetime import datetime, timezone

        def _lines() -> Iterable[str]:
            for stamp in sorted(table):
                signals = table[stamp] or {}
                record = {
                    "t": int(stamp),
                    "data_time_utc": datetime.fromtimestamp(
                        int(stamp) / 1000.0, timezone.utc
                    ).isoformat(),
                    "signals": {str(k): _jsonize(v) for k, v in signals.items()},
                }
                yield json.dumps(record, ensure_ascii=False)

        _atomic_write_text(target, _lines())
        return target

    # -- 统一入口 ---------------------------------------------------------
    def write(self, table: Mapping[int, Mapping[str, Any]], year: int, month: int) -> Path:
        """按已解析格式落盘。parquet 分支内部负责建表与整理。"""
        if self.fmt == "parquet":
            import pandas as pd

            df = pd.DataFrame.from_dict(dict(table), orient="index")
            df.index = pd.Index(df.index).astype("int64")
            return self.write_parquet(normalize_frame(df), year, month)
        return self.write_jsonl(table, year, month)
