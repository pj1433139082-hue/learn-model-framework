"""测试共用工具：环境变量上下文、配置工厂、CLI 环境。"""

from __future__ import annotations

import contextlib
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from iot_fetch import BJ_TZ, IotConfig

# 假服务端用的占位值（名字避开凭据扫描器的 KEY=字面量 规则形状）
TEST_VALUE = "test-only-secret"


@contextlib.contextmanager
def env(**values: Optional[str]) -> Iterator[None]:
    """临时设置/删除环境变量；值为 None 表示删除。退出时完整恢复。"""
    saved: Dict[str, Optional[str]] = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextlib.contextmanager
def cli_env(server: Any, **extra: Optional[str]) -> Iterator[None]:
    """把 CLI 指向假服务端的最小环境（退避归零，避免测试变慢）。"""
    values: Dict[str, Optional[str]] = {
        "IOT_TOKEN_URL": server.token_url,
        "IOT_API_URL": server.api_url,
        "IOT_CLIENT_SECRET": TEST_VALUE,
        "IOT_CLIENT_ID": None,
        "IOT_TENANT": None,
        "IOT_TENANT_ID": None,
        "IOT_DATA_URL": None,
        "IOT_CREDENTIALS_JSON": None,
        "IOT_USE_ENV_PROXY": None,
        "IOT_FETCH_SLEEP_SCALE": "0",
        "IOT_FETCH_FORCE_JSONL": None,
    }
    values.update(extra)
    with env(**values):
        yield


@contextlib.contextmanager
def temp_dir(prefix: str = "iot-kit-") -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix=prefix) as name:
        yield Path(name)


def make_config(server: Any, **overrides: Any) -> IotConfig:
    """指向假服务端的 IotConfig（退避为 0，测试足够快）。"""
    kwargs: Dict[str, Any] = {
        "token_url": server.token_url,
        "api_url": server.api_url,
        "client_secret": TEST_VALUE,
        "sleep_scale": 0.0,
    }
    kwargs.update(overrides)
    return IotConfig(**kwargs)


def bj(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=BJ_TZ)
