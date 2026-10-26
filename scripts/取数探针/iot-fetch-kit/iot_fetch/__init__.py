"""iot-fetch-kit：直连 REST 的 IoT 历史数据取数工具包。

三行用法：

```
from iot_fetch import IotConfig, IotClient, make_nodes

cfg = IotConfig.from_env(client_secret="...")
with IotClient(cfg) as client:
    data = client.fetch(make_nodes("PS37217-02", "-BE_GFJF_0001-C_003_", ["0001"], ["V_IF"]),
                        start, hours=6)
# data: {测点名: {毫秒: 值}}
```
"""

from __future__ import annotations

from .auth import TokenProvider
from .client import (
    FetchStats,
    IotClient,
    ensure_bj,
    month_slices,
    ms_to_bj,
    parse_signal_window,
    slice_windows,
)
from .config import BJ_TZ, IotConfig
from .nodes import load_nodes, make_nodes, node_name, save_nodes, validate_nodes
from .pipeline import FetchOptions, fetch_range, run_fetch
from .writers import MonthWriter, normalize_frame, resolve_format

__version__ = "1.0.0"

__all__ = [
    "__version__",
    "BJ_TZ",
    "FetchOptions",
    "FetchStats",
    "IotClient",
    "IotConfig",
    "MonthWriter",
    "TokenProvider",
    "ensure_bj",
    "fetch_range",
    "load_nodes",
    "make_nodes",
    "month_slices",
    "ms_to_bj",
    "node_name",
    "normalize_frame",
    "parse_signal_window",
    "resolve_format",
    "run_fetch",
    "save_nodes",
    "slice_windows",
    "validate_nodes",
]
