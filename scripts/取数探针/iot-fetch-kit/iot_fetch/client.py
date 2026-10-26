"""取数客户端：单窗口取数、上限探测、窗口换算、自然月切分。

这里实现的每一条都是旧脚本 `fetch_window` / `probe_cap` / `parse_signal_window` /
`month_slices` 的逐条移植，契约要点：

* 请求体 `{"detail": {"returnBounds": False, "endTime": str(ms), "maxSizePerNode": n,
  "startTime": str(ms)}, "nodes": [{"namespace": ..., "id": ...}]}`；
* 请求头必须含 `Content-Type: application/json` 与 `X-Tenant-Id`（缺租户头直接 401）；
* **HTTP 200 且响应 JSON 的 `code == 0`** 才算成功，`code != 0` 抛错进重试；
* `401` 清令牌缓存后重试（新一轮会自动换新令牌）；
* `results[i]` 非 dict 或 `historyData` 为空 → 记 missing，**不抛错**；
* 列名 = `namespace + "-" + id`。
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import requests

from .auth import TokenProvider
from .config import BJ_TZ, IotConfig

__all__ = [
    "FetchStats",
    "IotClient",
    "ms_to_bj",
    "ensure_bj",
    "month_slices",
    "parse_signal_window",
    "slice_windows",
]

WindowCols = List[Tuple[str, Dict[int, Any]]]


# ---------------------------------------------------------------------------
# 时间工具
# ---------------------------------------------------------------------------
def ensure_bj(value: datetime) -> datetime:
    """把 naive datetime 视为北京时间；aware 原样返回。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=BJ_TZ)
    return value


def ms_to_bj(ms: int) -> datetime:
    """毫秒时间戳 → 北京时间 datetime（用于控制台打印）。"""
    return datetime.fromtimestamp(int(ms) / 1000.0, BJ_TZ)


def _ms(value: datetime) -> int:
    """datetime → 毫秒整数时间戳（与旧脚本一致：int(dt.timestamp() * 1000)）。"""
    return int(value.timestamp() * 1000)


def parse_signal_window(cap: int, step_s: float = 15.0) -> float:
    """按每测点上限反推窗口小时数，取整数小时且不超上限。

    ```
    parse_signal_window(2000)      # 8.0   2000 * 15 / 3600 = 8.33 → 8
    parse_signal_window(1500)      # 6.0   1500 * 15 / 3600 = 6.25 → 6
    parse_signal_window(1500, 60)  # 25.0  采样周期 60s 时同样的点数覆盖 25 小时
    parse_signal_window(0)         # 6.0   探测失败时的保守默认
    ```
    """
    if cap <= 0:
        return 6.0
    raw_hours = cap * float(step_s) / 3600.0
    return float(max(1, int(raw_hours)))


def month_slices(start: datetime, end: datetime) -> List[Tuple[int, int, datetime, datetime]]:
    """按北京时间的自然月切分区间，返回 `[(年, 月, slice_start, slice_end)]`。

    首尾裁剪到 `[start, end)`；结束时刻落在月首时不会多出一个空月。
    """
    start = ensure_bj(start)
    end = ensure_bj(end)
    year, month = start.year, start.month
    out: List[Tuple[int, int, datetime, datetime]] = []
    while True:
        month_start = datetime(year, month, 1, tzinfo=BJ_TZ)
        next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
        month_end = datetime(next_year, next_month, 1, tzinfo=BJ_TZ)
        if month_start >= end:
            break
        out.append(
            (
                year,
                month,
                max(month_start, start),
                min(month_end, end),
            )
        )
        if month_end >= end:
            break
        year, month = next_year, next_month
    return out


def slice_windows(start: datetime, end: datetime, hours: float) -> List[datetime]:
    """把 `[start, end)` 按 `hours` 小时切成窗口起点列表（与旧脚本同一个 while 循环）。"""
    start = ensure_bj(start)
    end = ensure_bj(end)
    wins: List[datetime] = []
    cursor = start
    step = timedelta(hours=float(hours))
    while cursor < end:
        wins.append(cursor)
        cursor += step
    return wins


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------
@dataclass
class FetchStats:
    """窗口级统计：成功 / 重试后成功 / 彻底失败（与旧脚本 `_STAT` 同义）。"""

    ok: int = 0
    retried: int = 0
    fail: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {"ok": self.ok, "retried": self.retried, "fail": self.fail}


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------
class IotClient:
    """直连 REST 的取数客户端。

    线程模型与旧脚本一致：**每个线程一个 Session**（`threading.local`），
    `trust_env = False`（不读系统代理），连接池 `pool_connections=8, pool_maxsize=16,
    max_retries=0`（重试由本类显式控制，避免双重重试）。

    ```
    cfg = IotConfig.from_env(client_secret="...")
    with IotClient(cfg) as client:
        data = client.fetch(nodes, start, hours=6)   # {测点名: {毫秒: 值}}
    ```
    """

    def __init__(
        self,
        config: IotConfig,
        *,
        retries: int = 4,
        tokens: Optional[TokenProvider] = None,
        session_factory: Optional[Callable[[], requests.Session]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.config = config
        self.retries = int(retries)
        self.stats = FetchStats()
        self.missing: Dict[str, int] = {}
        self._lock = threading.Lock()
        self._local = threading.local()
        self._session_factory = session_factory
        self._clock = clock or time.time
        self._tokens = tokens if tokens is not None else TokenProvider(config)
        self._closed = False

    # -- 生命周期 ---------------------------------------------------------
    def __enter__(self) -> "IotClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        """关闭当前线程的会话；其他线程的会话随线程结束回收。"""
        session = getattr(self._local, "session", None)
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响退出
                pass
            self._local.session = None
        self._tokens.close()
        self._closed = True

    # -- 令牌 / 会话 ------------------------------------------------------
    @property
    def tokens(self) -> TokenProvider:
        return self._tokens

    def session(self) -> requests.Session:
        """返回当前线程的 Session（首次调用时创建）。"""
        if self._session_factory is not None:
            return self._session_factory()
        session = getattr(self._local, "session", None)
        if session is None:
            session = self._build_session()
            self._local.session = session
        return session

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        # 关键：不吃环境代理。Clash 等本地代理会把 HTTPS 变成 HTML 错误页（JSONDecodeError）。
        session.trust_env = self.config.use_env_proxy
        session.mount(
            "https://",
            requests.adapters.HTTPAdapter(
                pool_connections=self.config.pool_connections,
                pool_maxsize=self.config.pool_maxsize,
                max_retries=0,
            ),
        )
        return session

    def _sleep(self, seconds: float) -> None:
        delay = float(seconds) * float(self.config.sleep_scale)
        if delay > 0:
            time.sleep(delay)

    # -- 统计 -------------------------------------------------------------
    def reset_stats(self) -> None:
        with self._lock:
            self.stats = FetchStats()
            self.missing = {}

    def _note_missing(self, name: str) -> None:
        with self._lock:
            self.missing[name] = self.missing.get(name, 0) + 1

    # -- 单窗口取数 -------------------------------------------------------
    def fetch_window(
        self,
        nodes: Sequence[Dict[str, Any]],
        start: datetime,
        hours: float,
        max_size: int = 100000,
        retries: Optional[int] = None,
    ) -> Tuple[datetime, Optional[WindowCols]]:
        """取一个窗口。

        返回 `(start, cols)`：`cols` 为 `[(测点名, {毫秒: 值}), ...]`（顺序同 nodes），
        重试耗尽时返回 `(start, None)`。这是旧脚本 `fetch_window` 的等价签名。
        """
        attempt_limit = self.retries if retries is None else int(retries)
        start = ensure_bj(start)
        end = start + timedelta(hours=float(hours))
        body = {
            "detail": {
                "returnBounds": False,
                "endTime": str(_ms(end)),
                "maxSizePerNode": int(max_size),
                "startTime": str(_ms(start)),
            },
            "nodes": list(nodes),
        }
        base_headers = {
            "Content-Type": "application/json",
            "X-Tenant-Id": self.config.tenant,
        }
        for attempt in range(attempt_limit):
            try:
                headers = dict(
                    base_headers,
                    Authorization="Bearer {0}".format(self._tokens.get()),
                )
                response = self.session().post(
                    self.config.api_url,
                    headers=headers,
                    json=body,
                    timeout=self.config.timeout,
                )
                if response.status_code == 401:
                    # 令牌可能刚好过期：清缓存，下一轮重试自动换新令牌（旧脚本同款）。
                    self._tokens.invalidate()
                    raise RuntimeError("401 令牌失效")
                response.raise_for_status()
                payload = response.json()
                if payload.get("code") != 0:
                    raise RuntimeError(
                        "code={0} {1}".format(payload.get("code"), payload.get("message"))
                    )
                results = payload.get("results") or []
                cols: WindowCols = []
                for node, result in zip(nodes, results):
                    name = "{0}-{1}".format(node["namespace"], node["id"])
                    if not isinstance(result, dict):
                        self._note_missing(name)
                        continue
                    history = result.get("historyData") or []
                    if not history:
                        self._note_missing(name)
                        continue
                    cols.append((name, {int(point["t"]): point.get("v") for point in history}))
                with self._lock:
                    self.stats.ok += 1
                    if attempt:
                        self.stats.retried += 1
                return start, cols
            except Exception:  # noqa: BLE001 - 与旧脚本一致：所有异常都算可重试
                if attempt == attempt_limit - 1:
                    with self._lock:
                        self.stats.fail += 1
                    return start, None
                self._sleep(0.4 * (attempt + 1) + random.random() * 0.4)
        return start, None

    # -- 库级便捷接口 -----------------------------------------------------
    def fetch(
        self,
        nodes: Sequence[Dict[str, Any]],
        start: datetime,
        hours: Optional[float] = None,
        *,
        end: Optional[datetime] = None,
        max_size: int = 100000,
        retries: Optional[int] = None,
    ) -> Dict[str, Dict[int, Any]]:
        """取一个窗口并返回 `{测点名: {毫秒: 值}}`；窗口彻底失败时抛 `RuntimeError`。"""
        if hours is None:
            if end is None:
                raise ValueError("fetch() 需要 hours 或 end 之一")
            hours = (ensure_bj(end) - ensure_bj(start)).total_seconds() / 3600.0
        _, cols = self.fetch_window(
            nodes, start, hours, max_size=max_size, retries=retries
        )
        if cols is None:
            raise RuntimeError(
                "取数失败（已重试 {0} 次）：start={1:%Y-%m-%d %H:%M} hours={2:g}".format(
                    self.retries if retries is None else int(retries),
                    ensure_bj(start),
                    float(hours),
                )
            )
        return {name: dict(values) for name, values in cols}

    def probe_cap(
        self,
        nodes: Sequence[Dict[str, Any]],
        when: datetime,
        retries: Optional[int] = None,
    ) -> int:
        """探某时间段的每测点上限：给 8 小时窗口 + `maxSizePerNode=100000`，看被钳到多少。"""
        _, cols = self.fetch_window(
            nodes, when, hours=8, max_size=100000, retries=retries
        )
        if not cols:
            return 0
        return max(len(values) for _, values in cols)

    # -- 与模块函数同名的方法（方便 `client.parse_signal_window(...)`）-----
    @staticmethod
    def parse_signal_window(cap: int, step_s: float = 15.0) -> float:
        return parse_signal_window(cap, step_s)

    @staticmethod
    def month_slices(start: datetime, end: datetime) -> List[Tuple[int, int, datetime, datetime]]:
        return month_slices(start, end)

    @staticmethod
    def slice_windows(start: datetime, end: datetime, hours: float) -> List[datetime]:
        return slice_windows(start, end, hours)

