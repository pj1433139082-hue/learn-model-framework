"""配置对象：端点、身份、超时与网络开关。

所有取值都可以用环境变量覆盖。默认值与旧脚本
```scripts/取数探针/iot_bulk_fetch.py``` 完全一致：

=======================  ==========================================  ===========================
环境变量                 含义                                        默认
=======================  ==========================================  ===========================
`IOT_TOKEN_URL`          令牌端点                                    Keycloak（saas-iam.bewg.net.cn）
`IOT_API_URL`            历史取数端点                                be-iot-saas.bewg.net.cn
`IOT_DATA_URL`           同上（公司框架别名，`IOT_API_URL` 优先）     —
`IOT_TENANT`             租户（请求头 X-Tenant-Id）                  bewg
`IOT_TENANT_ID`          同上（公司框架别名，`IOT_TENANT` 优先）      —
`IOT_CLIENT_ID`          OAuth client_id                             ai-console
`IOT_CLIENT_SECRET`      OAuth client_secret                         空（必须由调用方提供）
`IOT_CREDENTIALS_JSON`   本地凭据 JSON 路径（见 load_credentials_file） 空
`IOT_USE_ENV_PROXY`      1=尊重系统 HTTP(S)_PROXY                    0（不读环境代理）
`IOT_FETCH_FORCE_JSONL`  1=强制 JSONL 落盘                           0
`IOT_FETCH_SLEEP_SCALE`  重试退避倍率（秒），主要给测试用            1.0
=======================  ==========================================  ===========================

取值优先级（高 → 低）：显式参数（CLI/from_env(client_secret=...)）> 环境变量 >
凭据 JSON（--credentials-json 或 IOT_CREDENTIALS_JSON）> 内置默认值。
公司框架 resources/infra/mlops_deploy_iot2.py 用 IOT_TENANT_ID / IOT_DATA_URL
等名字，本包把 kit 名放前面、公司名当别名，两套环境变量都能直接用。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields, replace
from datetime import timedelta, timezone
from typing import Any, Dict, Optional, Tuple

__all__ = [
    "BJ_TZ",
    "CREDENTIAL_FIELDS",
    "DEFAULT_API_URL",
    "DEFAULT_CLIENT_ID",
    "DEFAULT_RETRIES",
    "DEFAULT_SIGNALS",
    "DEFAULT_STEP_SECONDS",
    "DEFAULT_TENANT",
    "DEFAULT_TOKEN_URL",
    "DEFAULT_WORKERS",
    "ENV_ALIASES",
    "IotConfig",
    "NO_PROXY_HOSTS",
    "credentials_from_mapping",
    "env_flag",
    "env_float",
    "load_credentials_file",
    "prepare_network_env",
]

DEFAULT_TOKEN_URL = "https://saas-iam.bewg.net.cn/realms/bewg/protocol/openid-connect/token"
DEFAULT_API_URL = "https://be-iot-saas.bewg.net.cn/ts/v3/datapoint/history/rawvalue"
DEFAULT_CLIENT_ID = "ai-console"
DEFAULT_TENANT = "bewg"
DEFAULT_SIGNALS = "V_IF,V_OP,V_P,V_ITIT,V_I,S_RUN"
DEFAULT_WORKERS = 32
DEFAULT_RETRIES = 4
DEFAULT_STEP_SECONDS = 15.0

# 公司域名必须绕过本地代理，否则 Clash 之类会把 HTTPS 变成 HTML 错误页（JSONDecodeError）。
NO_PROXY_HOSTS = ("bewg.net.cn", ".bewg.net.cn")

# 北京时间固定 UTC+8，无夏令时。
BJ_TZ = timezone(timedelta(hours=8))

_TRUTHY = frozenset({"1", "true", "yes", "on", "y", "t"})

# 公司框架（infra_model_deploy: resources/infra/mlops_deploy_iot2.py）用的环境变量名，
# 作为本包 kit 名的别名：kit 名优先，公司名次之。
ENV_ALIASES = {
    "token_url": ("IOT_TOKEN_URL",),
    "api_url": ("IOT_API_URL", "IOT_DATA_URL"),
    "tenant": ("IOT_TENANT", "IOT_TENANT_ID"),
    "client_id": ("IOT_CLIENT_ID",),
    "client_secret": ("IOT_CLIENT_SECRET",),
}

# 凭据 JSON 的字段名：kit 风格优先，兼容公司 enclave 载荷里的同名同义字段。
CREDENTIAL_FIELDS = {
    "token_url": ("token_url", "iot_token_url"),
    "api_url": ("api_url", "iot_data_url"),
    "tenant": ("tenant", "iot_tenant_id"),
    "client_id": ("client_id", "iot_client_id"),
    "client_secret": ("client_secret", "iot_client_secret"),
}


def _first_env(env, names):
    """按顺序取第一个非空环境变量（去空白）。"""
    for name in names:
        value = (env.get(name) or "").strip()
        if value:
            return value
    return ""


def credentials_from_mapping(data):
    """把任意风格的凭据字典归一成 IotConfig 字段名。

    同时接受 kit 风格（client_secret 等）与公司 enclave 风格
    （iot_client_secret 等）；同一字段两种都出现时 kit 风格优先。
    只保留非空字符串，其他类型/空值直接忽略。
    """
    normalized = {}
    for key, aliases in CREDENTIAL_FIELDS.items():
        for name in aliases:
            value = data.get(name)
            if isinstance(value, str) and value.strip():
                normalized[key] = value.strip()
                break
    return normalized


def load_credentials_file(path):
    """读本地凭据 JSON，返回归一化后的字段字典（见 credentials_from_mapping）。

    文件不存在、读不了、JSON 非法或顶层不是对象时抛 ValueError（中文消息）。
    报错只包含路径与解析器位置，绝不带文件内容，避免把 secret 打进日志。
    """
    text_path = str(path)
    try:
        with open(text_path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        raise ValueError("凭据文件不存在：{0}".format(text_path))
    except OSError as exc:
        raise ValueError("凭据文件读取失败：{0}（{1}）".format(text_path, exc))
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError("凭据文件不是合法 JSON：{0}（{1}）".format(text_path, exc))
    if not isinstance(data, dict):
        raise ValueError("凭据文件顶层必须是 JSON 对象：{0}".format(text_path))
    return credentials_from_mapping(data)


def env_flag(name: str, default: bool = False) -> bool:
    """读布尔环境变量：1/true/yes/on/y/t 为真，空或未设置为 default。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    text = raw.strip().lower()
    if not text:
        return default
    return text in _TRUTHY


def env_float(name: str, default: float) -> float:
    """读浮点环境变量；解析失败或为空时返回 default（不抛错，避免拖崩 CLI）。"""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def prepare_network_env(use_env_proxy: bool = False) -> None:
    """把公司域名追加进 NO_PROXY（幂等）。

    旧脚本在模块导入时就做这件事，本包保留同样的时机（`import iot_fetch.config` 即生效），
    并在构造 :class:`IotConfig` 时再确认一次。

    当 `IOT_USE_ENV_PROXY=1` 时**不追加**：此时完全尊重调用方的代理配置，
    原样的 NO_PROXY 生效（适用于必须经代理才能到达端点的分支环境）。
    """
    if use_env_proxy:
        return
    current = [v.strip() for v in (os.environ.get("NO_PROXY") or "").split(",") if v.strip()]
    merged = list(current)
    for host in NO_PROXY_HOSTS:
        if host not in merged:
            merged.append(host)
    if merged != current:
        os.environ["NO_PROXY"] = ",".join(merged)


@dataclass(frozen=True)
class IotConfig:
    """取数所需的全部配置（端点、身份、超时、网络开关）。

    ```
    cfg = IotConfig.from_env(client_secret="...")   # 端点走环境变量/默认值
    cfg = IotConfig(api_url="http://127.0.0.1:8000/x", client_secret="...")  # 测试用
    ```
    """

    token_url: str = DEFAULT_TOKEN_URL
    api_url: str = DEFAULT_API_URL
    tenant: str = DEFAULT_TENANT
    client_id: str = DEFAULT_CLIENT_ID
    # repr=False：对象被打印/记日志时绝不带出密钥（凭据不落日志）
    client_secret: str = field(default="", repr=False)
    # (连接超时, 读取超时)，秒；与旧脚本的 timeout=(5, 30) / (5, 90) 对应
    timeout: Tuple[float, float] = (5.0, 90.0)
    token_timeout: Tuple[float, float] = (5.0, 30.0)
    # 令牌缓存秒数：旧脚本 2400 秒（平台令牌本身 30 分钟有效）
    token_ttl: float = 2400.0
    use_env_proxy: bool = False
    sleep_scale: float = 1.0
    pool_connections: int = 8
    pool_maxsize: int = 16

    def __post_init__(self) -> None:
        prepare_network_env(self.use_env_proxy)
        if not str(self.api_url or "").strip():
            raise ValueError("api_url 不能为空")
        if not str(self.token_url or "").strip():
            raise ValueError("token_url 不能为空")
        if self.sleep_scale < 0:
            raise ValueError("sleep_scale 不能为负")

    # -- 便捷属性 ---------------------------------------------------------
    @property
    def has_secret(self) -> bool:
        return bool(self.client_secret)

    @property
    def endpoints(self) -> Dict[str, str]:
        return {"token_url": self.token_url, "api_url": self.api_url}

    # -- 构造 -------------------------------------------------------------
    @classmethod
    def from_env(
        cls,
        client_secret: Optional[str] = None,
        *,
        credentials: Optional[Dict[str, Any]] = None,
        credentials_json: Optional[str] = None,
        **overrides: Any
    ) -> "IotConfig":
        """从环境变量 / 本地凭据 JSON 构造配置。

        取值优先级（高 → 低）：显式参数（client_secret、overrides）> 环境变量 >
        凭据 JSON（credentials_json 或 IOT_CREDENTIALS_JSON）> 内置默认值。
        环境变量里 kit 名优先于公司框架名（IOT_TENANT > IOT_TENANT_ID、
        IOT_API_URL > IOT_DATA_URL），没有 kit 名时自动使用公司名。

        参数
        ----
        client_secret
            显式凭据；None 表示依次读环境变量与凭据 JSON。
        credentials
            已读好的凭据字典（字段名同 JSON），优先级高于本次读出的 JSON 文件。
        credentials_json
            本地凭据 JSON 路径；None 时读 IOT_CREDENTIALS_JSON 环境变量。
        overrides
            任意字段覆盖（api_url=...、sleep_scale=0 之类）。
        """
        env = os.environ
        path = credentials_json
        if path is None:
            path = (env.get("IOT_CREDENTIALS_JSON") or "").strip() or None
        creds: Dict[str, str] = {}
        if path:
            creds.update(load_credentials_file(path))
        if credentials:
            creds.update(credentials_from_mapping(credentials))
        kwargs: Dict[str, Any] = {
            "token_url": _first_env(env, ENV_ALIASES["token_url"])
            or creds.get("token_url")
            or DEFAULT_TOKEN_URL,
            "api_url": _first_env(env, ENV_ALIASES["api_url"])
            or creds.get("api_url")
            or DEFAULT_API_URL,
            "tenant": _first_env(env, ENV_ALIASES["tenant"])
            or creds.get("tenant")
            or DEFAULT_TENANT,
            "client_id": _first_env(env, ENV_ALIASES["client_id"])
            or creds.get("client_id")
            or DEFAULT_CLIENT_ID,
            "client_secret": _first_env(env, ENV_ALIASES["client_secret"])
            or creds.get("client_secret")
            or "",
            "use_env_proxy": env_flag("IOT_USE_ENV_PROXY", False),
            "sleep_scale": env_float("IOT_FETCH_SLEEP_SCALE", 1.0),
        }
        if client_secret is not None:
            kwargs["client_secret"] = client_secret
        kwargs.update(overrides)
        return cls(**kwargs)

    def replace(self, **changes: Any) -> "IotConfig":
        """返回改了若干字段的新配置（`IotConfig` 是 frozen 的）。"""
        return replace(self, **changes)

    def require_secret(self) -> "IotConfig":
        """缺凭据时抛出与旧 CLI 一致的中文错误信息。"""
        if not self.has_secret:
            raise ValueError("缺少 --client-secret（或设置环境变量 IOT_CLIENT_SECRET）")
        return self

    def field_names(self) -> Tuple[str, ...]:
        return tuple(f.name for f in fields(self))


# 导入即生效：与旧脚本"模块导入时补 NO_PROXY"的时机一致。
prepare_network_env(env_flag("IOT_USE_ENV_PROXY", False))
