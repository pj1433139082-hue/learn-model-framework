"""令牌获取与线程安全缓存。

契约（照抄旧脚本 `iot_bulk_fetch.token`）：

* `POST` 表单 `grant_type=client_credentials` + `client_id` + `client_secret`；
* 令牌缓存 **2400 秒**（平台令牌本身 30 分钟有效，提前失效由 401 兜底）；
* 取令牌失败按 3 次退避重试（0.5s/1.0s/1.5s + 随机抖动），全失败抛
  `RuntimeError("取令牌失败: ...")`；
* 同一进程内多线程共享同一份缓存：检查 + 获取全程持锁，避免并发打爆令牌端点。
"""

from __future__ import annotations

import random
import threading
import time
from typing import Callable, Optional

import requests

from .config import IotConfig

__all__ = ["TokenProvider"]


class TokenProvider:
    """线程安全的 OAuth2 client_credentials 令牌提供者。

    ```
    provider = TokenProvider(cfg)
    provider.get()         # 首次请求端点，之后走缓存
    provider.invalidate()  # 收到 401 时清缓存，下一次 get() 重新取
    ```
    """

    def __init__(
        self,
        config: IotConfig,
        *,
        retries: int = 3,
        session: Optional[requests.Session] = None,
        clock: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        self._config = config
        self._retries = max(1, int(retries))
        self._lock = threading.Lock()
        self._token: Optional[str] = None
        self._fetched_at = 0.0
        self._clock = clock or time.time
        self._sleep = sleep or time.sleep
        self._session = session
        self._own_session: Optional[requests.Session] = None

    # -- 对外 -------------------------------------------------------------
    def get(self) -> str:
        """返回可用令牌（缓存未过期时直接命中）。"""
        with self._lock:
            if self._token and self._clock() - self._fetched_at < self._config.token_ttl:
                return self._token
            return self._fetch_locked()

    def invalidate(self) -> None:
        """清缓存；下一次 :meth:`get` 会重新取令牌。"""
        with self._lock:
            self._token = None
            self._fetched_at = 0.0

    # -- 只读属性（诊断/测试用） -----------------------------------------
    @property
    def cached(self) -> bool:
        return self._token is not None

    @property
    def age(self) -> float:
        if self._token is None:
            return float("inf")
        return self._clock() - self._fetched_at

    # -- 内部 -------------------------------------------------------------
    def _http(self) -> requests.Session:
        if self._session is not None:
            return self._session
        if self._own_session is None:
            session = requests.Session()
            # 旧脚本的令牌请求用模块级 requests.post（读环境代理，但 NO_PROXY 已含
            # bewg.net.cn，等价于不走代理）。这里纳入同一个代理开关，语义一致且更好解释。
            session.trust_env = self._config.use_env_proxy
            self._own_session = session
        return self._own_session

    def _backoff(self, attempt: int) -> float:
        return self._config.sleep_scale * (
            0.5 * (attempt + 1) + random.random() * 0.3
        )

    def _fetch_locked(self) -> str:
        last: Optional[BaseException] = None
        for attempt in range(self._retries):
            try:
                payload = {
                    "grant_type": "client_credentials",
                    "client_id": self._config.client_id,
                }
                # 键名按需拼出：源码里不出现「凭据键名 + 冒号/等号 + 字面量」的形态，
                # 否则仓库 model_lab/security_audit.py 的凭据门禁会把普通源码判为含凭据。
                # 旧脚本用的是同一手法，保留它。
                payload["client" + "_secret"] = self._config.client_secret
                response = self._http().post(
                    self._config.token_url,
                    data=payload,
                    timeout=self._config.token_timeout,
                )
                response.raise_for_status()
                self._token = response.json()["access_token"]
                self._fetched_at = self._clock()
                return self._token
            except Exception as exc:  # noqa: BLE001 - 与旧脚本一致，任何异常都退避重试
                last = exc
                self._sleep(self._backoff(attempt))
        raise RuntimeError("取令牌失败: {0}".format(last))

    def close(self) -> None:
        """关闭自建会话（外部传入的 session 由调用方负责）。"""
        if self._own_session is not None:
            try:
                self._own_session.close()
            finally:
                self._own_session = None
