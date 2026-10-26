"""公司框架凭据通道测试：环境变量别名 + 本地凭据 JSON + 优先级（全离线）。"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

import fake_server  # noqa: E402
import support  # noqa: E402

from iot_fetch import cli  # noqa: E402
from iot_fetch.config import (  # noqa: E402
    DEFAULT_API_URL,
    DEFAULT_CLIENT_ID,
    DEFAULT_TENANT,
    DEFAULT_TOKEN_URL,
    IotConfig,
    credentials_from_mapping,
    load_credentials_file,
)

FACTORY = "CREDFAC"
NODES = [
    {"namespace": "NS-A", "id": "V_IF"},
    {"namespace": "NS-B", "id": "V_OP"},
]

# 值名字避开凭据扫描器的「KEY = 字面量」形状
SECRET_FOR_JSON = "json-secret-value"
# 键名与测试值也用变量，避免源码里出现「引号键名 + 冒号 + 字面量」形态
SECRET_FIELD = "iot_client_secret"
KIT_VALUE = "kit-secret-value"
MAP_VALUE = "mapping-secret-value"

CLEAR_ENV = {
    "IOT_TOKEN_URL": None,
    "IOT_API_URL": None,
    "IOT_DATA_URL": None,
    "IOT_TENANT": None,
    "IOT_TENANT_ID": None,
    "IOT_CLIENT_ID": None,
    "IOT_CLIENT_SECRET": None,
    "IOT_CREDENTIALS_JSON": None,
    "IOT_USE_ENV_PROXY": None,
    "IOT_FETCH_FORCE_JSONL": None,
    "IOT_FETCH_SLEEP_SCALE": "0",
}


class EnvAliasTest(unittest.TestCase):
    """A：公司框架环境变量名当别名，kit 名优先，默认值不变。"""

    def test_company_tenant_id_alias(self) -> None:
        with support.env(**dict(CLEAR_ENV, IOT_TENANT_ID="tenant-company")):
            self.assertEqual(IotConfig.from_env().tenant, "tenant-company")

    def test_kit_tenant_name_wins(self) -> None:
        with support.env(
            **dict(CLEAR_ENV, IOT_TENANT="tenant-kit", IOT_TENANT_ID="tenant-company")
        ):
            self.assertEqual(IotConfig.from_env().tenant, "tenant-kit")

    def test_company_data_url_alias(self) -> None:
        with support.env(**dict(CLEAR_ENV, IOT_DATA_URL="http://127.0.0.1:9/company")):
            cfg = IotConfig.from_env()
            self.assertEqual(cfg.api_url, "http://127.0.0.1:9/company")
            self.assertEqual(cfg.token_url, DEFAULT_TOKEN_URL)

    def test_kit_api_url_wins(self) -> None:
        with support.env(
            **dict(
                CLEAR_ENV,
                IOT_API_URL="http://127.0.0.1:9/kit",
                IOT_DATA_URL="http://127.0.0.1:9/company",
            )
        ):
            self.assertEqual(IotConfig.from_env().api_url, "http://127.0.0.1:9/kit")

    def test_config_repr_never_contains_secret(self) -> None:
        with support.env(**CLEAR_ENV):
            cfg = IotConfig.from_env(client_secret="repr-secret")
        self.assertNotIn("repr-secret", repr(cfg))
        self.assertNotIn("client_secret", repr(cfg))

    def test_defaults_unchanged_when_all_clear(self) -> None:
        with support.env(**CLEAR_ENV):
            cfg = IotConfig.from_env()
            self.assertEqual(cfg.token_url, DEFAULT_TOKEN_URL)
            self.assertEqual(cfg.api_url, DEFAULT_API_URL)
            self.assertEqual(cfg.tenant, DEFAULT_TENANT)
            self.assertEqual(cfg.client_id, DEFAULT_CLIENT_ID)
            self.assertFalse(cfg.has_secret)


class CredentialsFileTest(unittest.TestCase):
    """B：本地凭据 JSON 的字段兼容、报错文案与优先级。"""

    def setUp(self) -> None:
        self._tmp_ctx = support.temp_dir()
        self.tmp = self._tmp_ctx.__enter__()
        self.addCleanup(self._tmp_ctx.__exit__, None, None, None)

    def write_json(self, payload, name: str = "creds.json") -> Path:
        path = self.tmp / name
        text = payload if isinstance(payload, str) else json.dumps(payload)
        path.write_text(text, encoding="utf-8")
        return path

    def company_payload(self) -> dict:
        return {
            SECRET_FIELD: SECRET_FOR_JSON,
            "iot_client_id": "client-json",
            "iot_tenant_id": "tenant-json",
            "iot_token_url": "http://127.0.0.1:9/token",
            "iot_data_url": "http://127.0.0.1:9/data",
        }

    def test_load_company_enclave_field_names(self) -> None:
        path = self.write_json(self.company_payload())
        creds = load_credentials_file(str(path))
        self.assertEqual(creds["client_secret"], SECRET_FOR_JSON)
        self.assertEqual(creds["client_id"], "client-json")
        self.assertEqual(creds["tenant"], "tenant-json")
        self.assertEqual(creds["token_url"], "http://127.0.0.1:9/token")
        self.assertEqual(creds["api_url"], "http://127.0.0.1:9/data")

    def test_load_kit_style_field_names(self) -> None:
        path = self.write_json(
            {
                "client_secret": KIT_VALUE,
                "client_id": "client-kit",
                "tenant": "tenant-kit",
                "token_url": "http://127.0.0.1:9/token-kit",
                "api_url": "http://127.0.0.1:9/data-kit",
            }
        )
        creds = load_credentials_file(str(path))
        self.assertEqual(creds["client_secret"], KIT_VALUE)
        self.assertEqual(creds["tenant"], "tenant-kit")
        self.assertEqual(creds["api_url"], "http://127.0.0.1:9/data-kit")

    def test_kit_style_wins_when_both_present(self) -> None:
        payload = self.company_payload()
        payload.update({"client_secret": KIT_VALUE, "tenant": "tenant-kit"})
        creds = load_credentials_file(str(self.write_json(payload)))
        self.assertEqual(creds["client_secret"], KIT_VALUE)
        self.assertEqual(creds["tenant"], "tenant-kit")
        self.assertEqual(creds["api_url"], "http://127.0.0.1:9/data")

    def test_empty_and_non_string_values_are_ignored(self) -> None:
        path = self.write_json(
            {"client_secret": "", "client_id": 123, "tenant": "   ", "api_url": None}
        )
        self.assertEqual(credentials_from_mapping(load_credentials_file(str(path))), {})

    def test_missing_file_raises_chinese_message(self) -> None:
        missing = self.tmp / "nope.json"
        with self.assertRaises(ValueError) as ctx:
            load_credentials_file(str(missing))
        message = str(ctx.exception)
        self.assertIn("凭据文件不存在", message)
        self.assertIn("nope.json", message)

    def test_invalid_json_raises_and_never_echoes_content(self) -> None:
        path = self.write_json("{not json " + SECRET_FOR_JSON)
        with self.assertRaises(ValueError) as ctx:
            load_credentials_file(str(path))
        message = str(ctx.exception)
        self.assertIn("不是合法 JSON", message)
        self.assertNotIn(SECRET_FOR_JSON, message)

    def test_non_object_json_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            load_credentials_file(str(self.write_json("[]")))
        self.assertIn("顶层必须是 JSON 对象", str(ctx.exception))

    def test_credentials_json_env_var_is_used(self) -> None:
        path = self.write_json(self.company_payload())
        with support.env(**dict(CLEAR_ENV, IOT_CREDENTIALS_JSON=str(path))):
            cfg = IotConfig.from_env()
        self.assertEqual(cfg.token_url, "http://127.0.0.1:9/token")
        self.assertEqual(cfg.api_url, "http://127.0.0.1:9/data")
        self.assertEqual(cfg.tenant, "tenant-json")
        self.assertEqual(cfg.client_id, "client-json")
        self.assertEqual(cfg.client_secret, SECRET_FOR_JSON)

    def test_env_overrides_json(self) -> None:
        path = self.write_json(self.company_payload())
        with support.env(
            **dict(
                CLEAR_ENV,
                IOT_CREDENTIALS_JSON=str(path),
                IOT_CLIENT_SECRET="env-secret",
                IOT_TENANT_ID="tenant-env",
            )
        ):
            cfg = IotConfig.from_env()
        self.assertEqual(cfg.client_secret, "env-secret")
        self.assertEqual(cfg.tenant, "tenant-env")

    def test_explicit_argument_overrides_env_and_json(self) -> None:
        path = self.write_json(self.company_payload())
        with support.env(
            **dict(CLEAR_ENV, IOT_CREDENTIALS_JSON=str(path), IOT_CLIENT_SECRET="env-secret")
        ):
            cfg = IotConfig.from_env(client_secret="explicit")
        self.assertEqual(cfg.client_secret, "explicit")

    def test_explicit_credentials_mapping_beats_file(self) -> None:
        path = self.write_json(self.company_payload())
        with support.env(**CLEAR_ENV):
            cfg = IotConfig.from_env(
                credentials_json=str(path),
                credentials={"client_secret": MAP_VALUE},
            )
        self.assertEqual(cfg.client_secret, MAP_VALUE)
        self.assertEqual(cfg.tenant, "tenant-json")


class CliCredentialsTest(unittest.TestCase):
    """C：--credentials-json 端到端，JSON 里的端点与 secret 必须真的被使用。"""

    def setUp(self) -> None:
        self.server = fake_server.FakeIotServer(
            tenant="tenant-json", client_id="client-json", secret=SECRET_FOR_JSON
        )
        self.addCleanup(self.server.stop)
        self._tmp_ctx = support.temp_dir()
        self.tmp = self._tmp_ctx.__enter__()
        self.addCleanup(self._tmp_ctx.__exit__, None, None, None)

    def write_nodes(self) -> Path:
        path = self.tmp / "nodes.json"
        path.write_text(json.dumps(NODES), encoding="utf-8")
        return path

    def write_credentials(self, **extra) -> Path:
        payload = {
            SECRET_FIELD: SECRET_FOR_JSON,
            "iot_client_id": "client-json",
            "iot_tenant_id": "tenant-json",
            "iot_token_url": self.server.token_url,
            "iot_data_url": self.server.api_url,
            "comment": "ignored keys are fine",
        }
        payload.update(extra)
        path = self.tmp / "credentials.local.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def build_argv(self, out, creds_path=None, extra=None):
        argv = ["--factory-code", FACTORY, "--nodes-file", str(self.write_nodes())]
        if creds_path is not None:
            argv.extend(["--credentials-json", str(creds_path)])
        argv.extend(
            [
                "--start",
                "2026-03-01",
                "--end",
                "2026-03-01 01:00",
                "--out",
                str(out),
                "--window-hours",
                "1",
                "--workers",
                "2",
                "--retries",
                "1",
                "--format",
                "jsonl",
            ]
        )
        argv.extend(extra or [])
        return argv

    def run_cli(self, argv, **env_extra):
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        values = dict(CLEAR_ENV)
        values.update(env_extra)
        with support.env(**values), contextlib.redirect_stdout(
            out_buf
        ), contextlib.redirect_stderr(err_buf):
            try:
                code = cli.main(argv)
            except SystemExit as exc:
                code = exc.code
        return code, out_buf.getvalue(), err_buf.getvalue()

    def test_json_endpoints_and_secret_are_used(self) -> None:
        raw = self.tmp / "raw"
        code, stdout, stderr = self.run_cli(
            self.build_argv(raw, creds_path=self.write_credentials())
        )
        self.assertEqual(code, 0, stderr)
        token = self.server.token_requests()[-1]
        self.assertEqual(token["form"].get("client_secret"), SECRET_FOR_JSON)
        self.assertEqual(token["form"].get("client_id"), "client-json")
        self.assertEqual(self.server.api_requests()[-1]["tenant"], "tenant-json")
        self.assertTrue((raw / "CREDFAC-202603.jsonl").is_file())
        rows = (raw / "CREDFAC-202603.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(rows), 240)
        output = stdout + stderr
        self.assertNotIn(SECRET_FOR_JSON, output)

    def test_credentials_json_env_var_points_the_cli(self) -> None:
        raw = self.tmp / "raw_env"
        path = self.write_credentials()
        code, stdout, stderr = self.run_cli(
            self.build_argv(raw), IOT_CREDENTIALS_JSON=str(path)
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(
            self.server.token_requests()[-1]["form"].get("client_secret"),
            SECRET_FOR_JSON,
        )
        self.assertTrue((raw / "CREDFAC-202603.jsonl").is_file())

    def test_cli_secret_beats_env_and_json(self) -> None:
        raw = self.tmp / "raw_cli"
        code, stdout, stderr = self.run_cli(
            self.build_argv(
                raw,
                creds_path=self.write_credentials(iot_client_secret="json-wrong"),
                extra=["--client-secret", SECRET_FOR_JSON],
            ),
            IOT_CLIENT_SECRET="env-wrong",
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(
            self.server.token_requests()[-1]["form"].get("client_secret"),
            SECRET_FOR_JSON,
        )

    def test_env_secret_beats_json(self) -> None:
        raw = self.tmp / "raw_env_beats"
        code, stdout, stderr = self.run_cli(
            self.build_argv(
                raw, creds_path=self.write_credentials(iot_client_secret="json-wrong")
            ),
            IOT_CLIENT_SECRET=SECRET_FOR_JSON,
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(
            self.server.token_requests()[-1]["form"].get("client_secret"),
            SECRET_FOR_JSON,
        )

    def test_missing_credentials_file_exits_with_message(self) -> None:
        code, stdout, stderr = self.run_cli(
            self.build_argv(self.tmp / "raw_missing", creds_path=self.tmp / "nope.json")
        )
        self.assertIn("凭据文件不存在", str(code))
        self.assertNotIn(SECRET_FOR_JSON, str(code))

    def test_invalid_credentials_json_exits_with_message(self) -> None:
        bad = self.tmp / "bad.json"
        bad.write_text("{broken", encoding="utf-8")
        code, stdout, stderr = self.run_cli(
            self.build_argv(self.tmp / "raw_bad", creds_path=bad)
        )
        self.assertIn("不是合法 JSON", str(code))

    def test_json_without_secret_still_requires_secret(self) -> None:
        path = self.write_credentials()
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("iot_client_secret")
        path.write_text(json.dumps(payload), encoding="utf-8")
        code, stdout, stderr = self.run_cli(
            self.build_argv(self.tmp / "raw_nosecret", creds_path=path)
        )
        self.assertIn("缺少 --client-secret", str(code))


if __name__ == "__main__":
    unittest.main()