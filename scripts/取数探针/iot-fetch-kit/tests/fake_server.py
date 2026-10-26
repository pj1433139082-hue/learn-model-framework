"""离线假服务端：模拟令牌端点与 history/rawvalue 取数端点（只用标准库）。

它不连任何网络，绑在 127.0.0.1:0 上，测完即关。行为对齐真接口的契约：

* 令牌端点只接受 grant_type=client_credentials + 正确的 client_id/secret，
  成功返回 access_token；缺凭据返回 401 + WWW-Authenticate: Bearer（Keycloak 行为）；
* 取数端点先查 Authorization: Bearer，再查 X-Tenant-Id，任一不对都 401；
* 每个测点在窗口内按 step 生成点，点数被 min(maxSizePerNode, clamp) 截断
  （clamp 按数据日期切换：2026-01 起 2000，之前 1500），这就是 probe 能探到上限的原因；
* 可切换的故障开关：WAF 502、业务 code != 0、results 里出现 null、historyData 为空。

测试只需要 with FakeIotServer() as server: 然后用 server.token_url / server.api_url。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

STEP_SECONDS = 15
STEP_MS = STEP_SECONDS * 1000

# 与真接口一致：2026-01 起单点上限 2000，2025-12 及以前 1500。
RECENT_SINCE_MS = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
TOKEN_PATH = "/realms/test/protocol/openid-connect/token"
API_PATH = "/ts/v3/datapoint/history/rawvalue"


class FakeIotServer:
    """可配置的假 IoT 服务端。"""

    def __init__(
        self,
        *,
        tenant: str = "bewg",
        client_id: str = "ai-console",
        secret: str = "test-only-secret",
        clamp_recent: int = 2000,
        clamp_old: int = 1500,
        require_tenant: bool = True,
        waf_fail_first: int = 0,
        token_fail_first: int = 0,
        code_error: bool = False,
        null_nodes: bool = False,
        empty_history: bool = False,
        step_ms: int = STEP_MS,
        token_form_only: bool = False,
        token_401_error: str = "",
        token_401_non_json: bool = False,
    ) -> None:
        self.tenant = tenant
        self.client_id = client_id
        self.secret = secret
        self.clamp_recent = int(clamp_recent)
        self.clamp_old = int(clamp_old)
        self.require_tenant = require_tenant
        self.waf_fail_first = int(waf_fail_first)
        self.token_fail_first = int(token_fail_first)
        self.code_error = code_error
        self.null_nodes = null_nodes
        self.empty_history = empty_history
        self.step_ms = int(step_ms)
        self.token_form_only = token_form_only
        self.token_401_error = token_401_error
        self.token_401_non_json = token_401_non_json

        self.requests: List[Dict[str, Any]] = []
        self.lock = threading.Lock()
        self.tokens: set = set()
        self.issued = 0
        self.api_calls = 0

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.app = self
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="fake-iot-server", daemon=True
        )
        self._thread.start()

    # -- 地址 --------------------------------------------------------------
    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return "http://127.0.0.1:{0}".format(self.port)

    @property
    def token_url(self) -> str:
        return self.base_url + TOKEN_PATH

    @property
    def api_url(self) -> str:
        return self.base_url + API_PATH

    # -- 生命周期 ----------------------------------------------------------
    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> "FakeIotServer":
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        self.stop()
        return False

    # -- 测试控制 ----------------------------------------------------------
    def expire_tokens(self) -> None:
        """作废全部已发令牌（下一次请求会 401，用来验证刷新逻辑）。"""
        with self.lock:
            self.tokens.clear()

    def record(self, item: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            self.requests.append(item)
        return item

    def requests_to(self, path: str) -> List[Dict[str, Any]]:
        with self.lock:
            return [item for item in self.requests if item.get("path") == path]

    def token_requests(self) -> List[Dict[str, Any]]:
        return self.requests_to(TOKEN_PATH)

    def api_requests(self) -> List[Dict[str, Any]]:
        return self.requests_to(API_PATH)

    # -- 数据生成 ----------------------------------------------------------
    def clamp_for(self, start_ms: int) -> int:
        return self.clamp_recent if int(start_ms) >= RECENT_SINCE_MS else self.clamp_old

    def value_for(self, name: str, stamp: int) -> float:
        """确定性取值：同一个测点 + 同一毫秒永远同一个数。"""
        base = sum(ord(ch) for ch in name) % 997
        return round(((base * 31 + (int(stamp) // self.step_ms)) % 10000) / 10.0, 3)

    def points_for(self, start_ms: int, end_ms: int, limit: Optional[int]) -> List[int]:
        span = int(end_ms) - int(start_ms)
        if span <= 0:
            return []
        count = span // self.step_ms
        if limit is not None:
            count = min(count, int(limit))
        return [int(start_ms) + (i + 1) * self.step_ms for i in range(int(count))]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FakeIot/1.0"

    def log_message(self, *args: Any) -> None:
        return None

    @property
    def app(self) -> FakeIotServer:
        return self.server.app  # type: ignore[attr-defined]

    def _reply(
        self,
        status: int,
        payload: Any,
        *,
        content_type: str = "application/json",
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        if isinstance(payload, bytes):
            body = payload
        else:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._reply(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        path = urlsplit(self.path).path
        headers = {key.lower(): value for key, value in self.headers.items()}
        if path == TOKEN_PATH:
            return self._token(raw, headers)
        if path == API_PATH:
            return self._api(raw, headers)
        self._reply(404, {"error": "not_found", "path": path})

    # -- 令牌端点 ----------------------------------------------------------
    def _token(self, raw: bytes, headers: Dict[str, str]) -> None:
        app = self.app
        text = raw.decode("utf-8", "replace").strip()
        form: Dict[str, str] = {}
        if text.startswith("{"):
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = {}
            if isinstance(data, dict):
                form = {str(k): "" if v is None else str(v) for k, v in data.items()}
        elif text:
            form = {key: values[0] for key, values in parse_qs(text).items()}
        item = app.record({"path": TOKEN_PATH, "headers": headers, "form": form, "status": 0})
        if app.token_form_only and "x-www-form-urlencoded" not in headers.get(
            "content-type", ""
        ):
            item["status"] = 415
            self._reply(415, {"error": "unsupported_media_type"})
            return
        if app.token_fail_first > 0:
            app.token_fail_first -= 1
            item["status"] = 503
            self._reply(503, {"error": "temporarily_unavailable"})
            return
        if form.get("grant_type") != "client_credentials":
            item["status"] = 400
            self._reply(400, {"error": "unsupported_grant_type"})
            return
        if form.get("client_id") != app.client_id or form.get("client_secret") != app.secret:
            item["status"] = 401
            if app.token_401_non_json:
                # 401 但响应体不是 JSON：doctor 不应据此判可达
                self._reply(401, b"Unauthorized", content_type="text/plain")
            elif app.token_401_error:
                # 复现真 Keycloak:401 + JSON error，不带 WWW-Authenticate
                self._reply(
                    401,
                    {
                        "error": app.token_401_error,
                        "error_description": "Invalid client or Invalid client credentials",
                    },
                )
            else:
                self._reply(
                    401,
                    {"error": "invalid_client"},
                    headers={"WWW-Authenticate": 'Bearer realm="fake"'},
                )
            return
        with app.lock:
            app.issued += 1
            token = "tok-{0}".format(app.issued)
            app.tokens.add(token)
        item["status"] = 200
        self._reply(
            200,
            {"access_token": token, "expires_in": 1800, "token_type": "Bearer"},
        )

    # -- 取数端点 ----------------------------------------------------------
    def _api(self, raw: bytes, headers: Dict[str, str]) -> None:
        app = self.app
        try:
            body = json.loads(raw.decode("utf-8", "replace") or "{}")
        except json.JSONDecodeError:
            body = {}
        item = app.record({"path": API_PATH, "headers": headers, "body": body, "status": 0})
        with app.lock:
            app.api_calls += 1

        auth = headers.get("authorization", "")
        token = auth.split()[-1] if auth.lower().startswith("bearer ") else ""
        with app.lock:
            valid = bool(token) and token in app.tokens
        if not valid:
            item["status"] = 401
            self._reply(
                401,
                {"code": 401, "message": "invalid token"},
                headers={"WWW-Authenticate": "Bearer"},
            )
            return

        tenant = headers.get("x-tenant-id", "")
        item["tenant"] = tenant
        if app.require_tenant and tenant != app.tenant:
            item["status"] = 401
            self._reply(
                401,
                {"code": 401, "message": "missing or wrong tenant"},
                headers={"WWW-Authenticate": "Bearer"},
            )
            return

        if app.waf_fail_first > 0:
            app.waf_fail_first -= 1
            item["status"] = 502
            self._reply(502, b"<html>502 Bad Gateway</html>", content_type="text/html")
            return

        if app.code_error:
            item["status"] = 200
            self._reply(200, {"code": 500, "message": "simulated business error"})
            return

        detail = body.get("detail") or {}
        nodes = body.get("nodes") or []
        start_ms = int(detail.get("startTime") or 0)
        end_ms = int(detail.get("endTime") or 0)
        limit = detail.get("maxSizePerNode")
        if limit:
            limit = min(int(limit), app.clamp_for(start_ms))
        else:
            limit = app.clamp_for(start_ms)
        points = app.points_for(start_ms, end_ms, limit)

        results: List[Any] = []
        for node in nodes:
            name = "{0}-{1}".format(node.get("namespace", ""), node.get("id", ""))
            if app.null_nodes:
                results.append(None)
            elif app.empty_history:
                results.append({"historyData": []})
            else:
                results.append(
                    {
                        "historyData": [
                            {"t": stamp, "v": app.value_for(name, stamp)} for stamp in points
                        ]
                    }
                )

        item["status"] = 200
        item["points"] = len(points)
        self._reply(200, {"code": 0, "message": "success", "results": results})
