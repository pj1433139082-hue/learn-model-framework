"""doctor 子命令测试：不发任何凭据、离线假服务端 + 本机不可达端口。"""

from __future__ import annotations

import contextlib
import io
import json
import socket
import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

import fake_server  # noqa: E402
import requests  # noqa: E402
import support  # noqa: E402

from iot_fetch import doctor  # noqa: E402


class DoctorEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = fake_server.FakeIotServer()
        self.addCleanup(self.server.stop)

    def test_api_endpoint_is_reachable(self) -> None:
        check = doctor.check_endpoint(
            "api", self.server.api_url, body=doctor.API_PROBE_BODY, timeout=5.0
        )
        self.assertEqual(check.level, "ok")
        self.assertEqual(check.dns, "OK")
        self.assertEqual(check.tls, "跳过（http）")
        self.assertEqual(check.status, 401)
        self.assertEqual(check.http, "401 Bearer")
        self.assertIn("未认证被正确拒绝", check.note)
        record = self.server.api_requests()[-1]
        self.assertEqual(record["body"], doctor.API_PROBE_BODY)
        self.assertNotIn("authorization", record["headers"])
        self.assertNotIn("x-tenant-id", record["headers"])

    def test_token_endpoint_is_reachable_without_credentials(self) -> None:
        check = doctor.check_endpoint(
            "token",
            self.server.token_url,
            body=doctor.TOKEN_PROBE_BODY,
            timeout=5.0,
            as_form=True,
        )
        self.assertEqual(check.level, "ok")
        record = self.server.token_requests()[-1]
        # Keycloak 只吃表单编码：JSON 会得到 415，所以这里必须断言表单体
        self.assertIn("x-www-form-urlencoded", record["headers"]["content-type"])
        self.assertEqual(record["form"], {"grant_type": "client_credentials"})
        self.assertNotIn("client_secret", record["form"])
        self.assertNotIn("authorization", record["headers"])

    def test_token_json_body_gets_415_and_is_not_ok(self) -> None:
        self.server.token_form_only = True
        bad = doctor.check_endpoint(
            "token",
            self.server.token_url,
            body=doctor.TOKEN_PROBE_BODY,
            timeout=5.0,
            as_form=False,
        )
        self.assertEqual(bad.status, 415)
        self.assertEqual(bad.level, "warn")  # 415 不能算「可达」
        self.assertIn("415", bad.note)
        self.assertIn("x-www-form-urlencoded", bad.note)

    def test_token_form_body_is_ok_on_form_only_server(self) -> None:
        self.server.token_form_only = True
        check = doctor.check_endpoint(
            "token",
            self.server.token_url,
            body=doctor.TOKEN_PROBE_BODY,
            timeout=5.0,
            as_form=True,
        )
        self.assertEqual(check.level, "ok", check.note)
        self.assertEqual(check.status, 401)

    def test_token_401_json_error_is_ok_without_bearer(self) -> None:
        # 真 Keycloak 未认证时回 401 + {"error":"invalid_client"}，不带 WWW-Authenticate
        for error in ("invalid_client", "invalid_grant", "unauthorized_client"):
            with self.subTest(error=error):
                server = fake_server.FakeIotServer(token_401_error=error)
                self.addCleanup(server.stop)
                check = doctor.check_endpoint(
                    "token",
                    server.token_url,
                    body=doctor.TOKEN_PROBE_BODY,
                    timeout=5.0,
                    as_form=True,
                    token_endpoint=True,
                )
                self.assertEqual(check.status, 401)
                self.assertEqual(check.bearer, "")  # 确实没有 WWW-Authenticate
                self.assertEqual(check.level, "ok", check.note)
                self.assertEqual(
                    check.note, "401 {0}（表单探针，凭据是假的）".format(error)
                )

    def test_token_401_non_json_body_is_not_ok(self) -> None:
        server = fake_server.FakeIotServer(token_401_non_json=True)
        self.addCleanup(server.stop)
        check = doctor.check_endpoint(
            "token",
            server.token_url,
            body=doctor.TOKEN_PROBE_BODY,
            timeout=5.0,
            as_form=True,
            token_endpoint=True,
        )
        self.assertEqual(check.status, 401)
        self.assertEqual(check.level, "warn")  # 读不到 error 就不算可达
        self.assertIn("WWW-Authenticate", check.note)

    def test_401_without_token_flag_keeps_old_semantics(self) -> None:
        # 非令牌端点（token_endpoint=False）仍按 Bearer 头判定，不能因 error 字段放水
        server = fake_server.FakeIotServer(token_401_error="invalid_client")
        self.addCleanup(server.stop)
        check = doctor.check_endpoint(
            "api", server.token_url, body=doctor.TOKEN_PROBE_BODY, timeout=5.0, as_form=True
        )
        self.assertEqual(check.status, 401)
        self.assertEqual(check.level, "warn")

    def test_timeout_is_fail(self) -> None:
        with mock.patch.object(
            doctor.requests.Session, "post", side_effect=requests.exceptions.Timeout
        ):
            check = doctor.check_endpoint(
                "api", self.server.api_url, body=doctor.API_PROBE_BODY, timeout=0.5
            )
        self.assertEqual(check.level, "fail")
        self.assertEqual(check.http, "超时")
        self.assertIn("超时", check.note)

    def test_connection_refused_is_fail(self) -> None:
        check = doctor.check_endpoint(
            "api",
            "http://127.0.0.1:1/api",
            body=doctor.API_PROBE_BODY,
            timeout=1.0,
        )
        self.assertEqual(check.level, "fail")
        self.assertIn(check.http, ("连接失败", "超时"))

    def test_dns_failure_is_fail(self) -> None:
        failure = socket.gaierror(11001, "mock getaddrinfo failure")
        with mock.patch.object(doctor, "_resolve", side_effect=failure):
            check = doctor.check_endpoint(
                "api", "https://example.invalid/api", body=doctor.API_PROBE_BODY
            )
        self.assertEqual(check.level, "fail")
        self.assertEqual(check.dns, "失败")
        self.assertIn("DNS 解析失败", check.note)

    def test_url_without_host_is_fail(self) -> None:
        check = doctor.check_endpoint("api", "http:///api", body=doctor.API_PROBE_BODY)
        self.assertEqual(check.level, "fail")
        self.assertIn("URL", check.note)

    def test_render_table_marks(self) -> None:
        checks = [
            doctor.Check(
                name="token",
                url="https://example.invalid/token",
                host="example.invalid",
                port=443,
                dns="OK",
                tls="OK (TLSv1.3)",
                http="401 Bearer",
                status=401,
                bearer="Bearer",
                level="ok",
            ),
            doctor.Check(
                name="api",
                url="http://127.0.0.1:1/api",
                host="127.0.0.1",
                port=1,
                dns="OK",
                tls="跳过（http）",
                http="连接失败",
                level="fail",
            ),
        ]
        text = doctor.render_table(checks)
        self.assertIn("端点", text)
        self.assertIn("结论", text)
        self.assertIn("✅ 可达", text)
        self.assertIn("❌ 不可达", text)
        self.assertEqual(len(text.splitlines()), 4)  # 表头 + 分隔线 + 两行


class DoctorCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = fake_server.FakeIotServer()
        self.addCleanup(self.server.stop)

    def run_doctor(self, argv):
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with support.env(IOT_USE_ENV_PROXY=None), contextlib.redirect_stdout(
            out_buf
        ), contextlib.redirect_stderr(err_buf):
            code = doctor.run_doctor(argv)
        return code, out_buf.getvalue(), err_buf.getvalue()

    def argv_ok(self):
        return [
            "--token-url",
            self.server.token_url,
            "--api-url",
            self.server.api_url,
            "--timeout",
            "5",
        ]

    def test_ok_returns_zero(self) -> None:
        code, stdout, stderr = self.run_doctor(self.argv_ok())
        self.assertEqual(code, 0, stderr)
        self.assertIn("IoT 连通性体检", stdout)
        self.assertIn("✅ 可达", stdout)
        self.assertIn("结论：2/2 端点可达", stdout)
        self.assertIn("未认证被正确拒绝", stdout)
        token_record = self.server.token_requests()[-1]
        self.assertIn(
            "x-www-form-urlencoded", token_record["headers"]["content-type"]
        )
        api_record = self.server.api_requests()[-1]
        self.assertIn("application/json", api_record["headers"]["content-type"])

    def test_json_output_is_machine_readable(self) -> None:
        code, stdout, stderr = self.run_doctor(self.argv_ok() + ["--json"])
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual([item["name"] for item in payload["checks"]], ["token", "api"])
        for item in payload["checks"]:
            self.assertEqual(item["status"], 401)
            self.assertIn("bearer", item["bearer"].lower())
            self.assertEqual(item["level"], "ok")

    def test_unreachable_api_returns_one(self) -> None:
        code, stdout, stderr = self.run_doctor(
            [
                "--token-url",
                self.server.token_url,
                "--api-url",
                "http://127.0.0.1:1/api",
                "--timeout",
                "1",
            ]
        )
        self.assertEqual(code, 1, stderr)
        self.assertIn("存在不可达端点", stdout)
        self.assertIn("❌ 不可达", stdout)

    def test_token_invalid_client_still_returns_zero(self) -> None:
        server = fake_server.FakeIotServer(token_401_error="invalid_client")
        self.addCleanup(server.stop)
        with support.env(IOT_USE_ENV_PROXY=None), contextlib.redirect_stdout(
            io.StringIO()
        ) as out_buf:
            code = doctor.run_doctor(
                [
                    "--token-url",
                    server.token_url,
                    "--api-url",
                    server.api_url,
                    "--timeout",
                    "5",
                ]
            )
        stdout = out_buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("✅ 可达", stdout)
        self.assertIn("401 invalid_client（表单探针，凭据是假的）", stdout)
        self.assertIn("结论：2/2 端点可达", stdout)

    def test_never_sends_credentials(self) -> None:
        with support.env(IOT_CLIENT_SECRET="leak-me-not"):
            code, stdout, stderr = self.run_doctor(self.argv_ok())
        self.assertEqual(code, 0, stderr)
        dumped = json.dumps(
            [
                {
                    "headers": record.get("headers", {}),
                    "form": record.get("form", {}),
                    "body": record.get("body", {}),
                }
                for record in list(self.server.requests)
            ],
            ensure_ascii=False,
        )
        self.assertNotIn("leak-me-not", dumped)
        self.assertNotIn("authorization", dumped.lower())
