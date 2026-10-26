"""`iot-fetch doctor`：不发任何凭据的连通性体检。

检查两件事：

1. **网络路径**：DNS 解析、TLS 握手（含证书链校验）、HTTP 能否拿到响应；
2. **接口是否活着**：未认证时应当得到 `401`，说明请求真的走到了网关而不是
   被代理/WAF 挡掉。取数端点应带 `WWW-Authenticate: Bearer`；令牌端点
   （Keycloak）未认证时回 `401` + JSON `{"error":"invalid_client"}` 且
   **不带** `WWW-Authenticate`，同样算可达。

故意不带 `Authorization`、`X-Tenant-Id`，也不读环境里的密钥，
可以放心在任意机器上先跑一遍。
"""

from __future__ import annotations

import argparse
import json
import socket
import ssl
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlsplit

import requests

from .config import DEFAULT_API_URL, DEFAULT_TOKEN_URL, IotConfig

__all__ = ["Check", "check_endpoint", "main", "render_table", "run_doctor"]

TOKEN_PROBE_BODY: Dict[str, Any] = {"grant_type": "client_credentials"}
API_PROBE_BODY: Dict[str, Any] = {
    "detail": {
        "returnBounds": False,
        "endTime": "0",
        "maxSizePerNode": 1,
        "startTime": "0",
    },
    "nodes": [],
}
# Keycloak 令牌端点的 401 响应体里 error 可能是这几种（表示凭据被正确拒绝）
TOKEN_401_ERRORS = frozenset({"invalid_client", "invalid_grant", "unauthorized_client"})


@dataclass
class Check:
    """一个端点的体检结果。`level` 取 `ok` / `warn` / `fail`。"""

    name: str
    url: str
    host: str
    port: int
    dns: str = ""
    tls: str = ""
    http: str = ""
    status: Optional[int] = None
    bearer: str = ""
    level: str = "ok"
    note: str = ""
    ips: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _resolve(host: str, port: int, timeout: float) -> List[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def _tls_probe(host: str, port: int, timeout: float) -> tuple:
    """返回 `(描述, 是否可达, 是否证书问题)`。先校验，失败再不校验确认能否握手。"""
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                return "OK ({0})".format(tls.version() or "?"), True, False
    except ssl.SSLCertVerificationError as exc:
        cert_error = str(exc)
    except Exception as exc:  # noqa: BLE001 - DNS/连接/握手失败都归为不可达
        return "失败（{0}）".format(type(exc).__name__), False, False
    # 证书校验失败时再试一次（不校验），用来区分「证书问题」与「根本连不上」
    try:
        context = ssl._create_unverified_context()
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                return "证书校验失败（TLS 可握手 {0}）".format(tls.version() or "?"), True, True
    except Exception:  # noqa: BLE001
        return "证书校验失败（{0}）".format(cert_error.splitlines()[0][:40]), False, True


def _probe_error(response: requests.Response) -> str:
    """读响应体前 2KB，取 JSON 里的 error 字段；不是 JSON / 读不到就返回空串。"""
    try:
        raw = response.content[:2048]
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 - 响应体不可解析就当没有 error
        return ""
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    return error if isinstance(error, str) else ""


def _http_probe(
    check: Check,
    body: Dict[str, Any],
    timeout: float,
    trust_env: bool,
    as_form: bool = False,
    token_endpoint: bool = False,
) -> None:
    """发一次不带凭证的 POST，按状态码判定端点是否可达。

    `as_form=True` 时用 `data=` 表单编码（Keycloak 令牌端点只吃
    `application/x-www-form-urlencoded`，用 JSON 会得到 415）。
    `token_endpoint=True` 时额外把「401 + JSON error=invalid_client 等」判为可达：
    Keycloak 未认证时本来就不发 `WWW-Authenticate`。
    """
    session = requests.Session()
    session.trust_env = trust_env
    try:
        if as_form:
            response = session.post(check.url, data=body, timeout=timeout)
        else:
            response = session.post(check.url, json=body, timeout=timeout)
    except requests.exceptions.Timeout:
        check.http = "超时"
        check.level = "fail"
        check.note = "HTTP 请求超时（{0:g}s）".format(timeout)
        return
    except requests.RequestException as exc:
        check.http = "连接失败"
        check.level = "fail"
        check.note = "HTTP 请求失败：{0}".format(type(exc).__name__)
        return
    finally:
        session.close()
    check.status = int(response.status_code)
    check.bearer = response.headers.get("WWW-Authenticate", "")
    status = check.status
    check.http = str(status)
    if status == 401:
        error = _probe_error(response) if token_endpoint else ""
        if error in TOKEN_401_ERRORS:
            # Keycloak 令牌端点的正常拒绝：401 + JSON error，不带 WWW-Authenticate
            check.level = "ok"
            check.note = "401 {0}（表单探针，凭据是假的）".format(error)
        elif "bearer" in check.bearer.lower():
            check.level = "ok"
            check.http = "401 Bearer"
            check.note = "未认证被正确拒绝（401 + WWW-Authenticate: Bearer）"
        else:
            check.level = "warn"
            check.note = "401 但缺少 WWW-Authenticate: Bearer（可能被网关改写）"
    elif status == 415:
        check.level = "warn"
        check.note = "415：请求体编码不被接受（令牌端点要 application/x-www-form-urlencoded）"
    elif status in (400, 403, 405):
        check.level = "warn"
        check.note = "{0}：端点在线，但匿名请求被参数/权限挡住".format(status)
    elif 200 <= status < 300:
        check.level = "warn"
        check.note = "{0}：未认证却放行，确认是否白名单环境".format(status)
    elif status >= 500:
        check.level = "fail"
        check.note = "{0}：服务端或 WAF 异常".format(status)
    else:
        check.level = "warn"
        check.note = "HTTP {0}".format(status)


def check_endpoint(
    name: str,
    url: str,
    *,
    body: Dict[str, Any],
    timeout: float = 8.0,
    trust_env: bool = False,
    as_form: bool = False,
    token_endpoint: bool = False,
) -> Check:
    """体检单个端点：DNS → TLS → HTTP。"""
    parts = urlsplit(url)
    host = parts.hostname or ""
    scheme = (parts.scheme or "https").lower()
    port = parts.port or (443 if scheme == "https" else 80)
    check = Check(name=name, url=url, host=host, port=int(port))
    if not host:
        check.level = "fail"
        check.dns = "URL 无主机名"
        check.note = "URL 解析失败：{0}".format(url)
        return check
    try:
        check.ips = _resolve(host, int(port), timeout)
        check.dns = "OK"
    except Exception as exc:  # noqa: BLE001 - DNS 失败就是网络失败
        check.dns = "失败"
        check.level = "fail"
        check.note = "DNS 解析失败：{0}".format(exc)
        return check
    if scheme == "https":
        description, reachable, cert_fail = _tls_probe(host, int(port), timeout)
        check.tls = description
        if not reachable:
            check.level = "fail"
            check.note = "TLS 握手失败"
            return check
        if cert_fail:
            check.level = "fail"
            check.note = "证书链校验失败（检查系统 CA / 是否被代理劫持）"
            return check
    else:
        check.tls = "跳过（http）"
    _http_probe(check, body, timeout, trust_env, as_form, token_endpoint)
    if check.level == "ok" and check.dns != "OK":
        check.level = "warn"
    return check


def _display_width(text: str) -> int:
    import unicodedata

    total = 0
    for char in text:
        total += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return total


def _pad(text: str, width: int) -> str:
    pad = width - _display_width(text)
    return text + " " * pad if pad > 0 else text


def render_table(checks: Sequence[Check]) -> str:
    """把检查结果排成等宽表格（中文按两格宽对齐）。"""
    headers = ["端点", "主机", "DNS", "TLS", "HTTP", "结论"]
    rows: List[List[str]] = []
    for check in checks:
        mark = {"ok": "✅ 可达", "warn": "⚠ 存疑", "fail": "❌ 不可达"}.get(check.level, check.level)
        rows.append(
            [
                check.name,
                check.host,
                check.dns,
                check.tls,
                check.http or "-",
                mark,
            ]
        )
    widths = [max([_display_width(headers[i])] + [_display_width(row[i]) for row in rows]) for i in range(6)]
    lines = ["  ".join(_pad(headers[i], widths[i]) for i in range(6)).rstrip()]
    lines.append("  ".join("-" * widths[i] for i in range(6)))
    for row in rows:
        lines.append("  ".join(_pad(row[i], widths[i]) for i in range(6)).rstrip())
    return "\n".join(lines)


def run_doctor(argv: Optional[Sequence[str]] = None) -> int:
    """执行体检并打印结论；全部网络可达返回 0，否则 1。"""
    parser = argparse.ArgumentParser(
        prog="iot-fetch doctor",
        description="IoT 接口连通性体检（不发任何凭据）",
    )
    parser.add_argument("--token-url", default=None, help="覆盖令牌端点（默认读 IOT_TOKEN_URL）")
    parser.add_argument("--api-url", default=None, help="覆盖取数端点（默认读 IOT_API_URL）")
    parser.add_argument("--timeout", type=float, default=8.0, help="单步超时秒数（默认 8）")
    parser.add_argument("--json", dest="as_json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    config = IotConfig.from_env()
    if args.token_url:
        config = config.replace(token_url=args.token_url)
    if args.api_url:
        config = config.replace(api_url=args.api_url)

    checks = [
        check_endpoint(
            "token",
            config.token_url,
            body=TOKEN_PROBE_BODY,
            timeout=args.timeout,
            trust_env=config.use_env_proxy,
            as_form=True,  # Keycloak 只吃表单编码，JSON 会 415
            token_endpoint=True,  # 401 + JSON error=invalid_client 也算可达（无 Bearer 头）
        ),
        check_endpoint(
            "api",
            config.api_url,
            body=API_PROBE_BODY,
            timeout=args.timeout,
            trust_env=config.use_env_proxy,
        ),
    ]
    ok = all(check.level != "fail" for check in checks)
    if args.as_json:
        print(
            json.dumps(
                {"ok": ok, "checks": [check.as_dict() for check in checks]},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print("IoT 连通性体检（不发任何凭据）")
        print(render_table(checks))
        print()
        for check in checks:
            if check.note:
                print("  · {0}: {1}".format(check.name, check.note))
        reachable = sum(1 for check in checks if check.level != "fail")
        if ok:
            print()
            print(
                "结论：{0}/{1} 端点可达，网络路径与证书链正常。".format(
                    reachable, len(checks)
                )
            )
        else:
            print()
            print(
                "结论：{0}/{1} 端点可达，存在不可达端点，先修网络再取数。".format(
                    reachable, len(checks)
                )
            )
    return 0 if ok else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    return run_doctor(argv)
