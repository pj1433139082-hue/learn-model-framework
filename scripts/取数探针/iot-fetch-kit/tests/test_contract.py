"""契约测试：请求形状、令牌、重试、missing、probe_cap、窗口换算、落盘整理。

全部走 tests/fake_server.py 的离线假服务端，不碰真网络、不发真凭据。
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

import requests  # noqa: E402

import fake_server  # noqa: E402
import support  # noqa: E402

from iot_fetch import (  # noqa: E402
    IotClient,
    IotConfig,
    TokenProvider,
    fetch_range,
    make_nodes,
    month_slices,
    parse_signal_window,
    slice_windows,
)
from iot_fetch.config import prepare_network_env  # noqa: E402
from iot_fetch.writers import MonthWriter, normalize_frame, resolve_format  # noqa: E402

try:
    import pandas as pd
except ImportError:  # pragma: no cover - 依赖环境
    pd = None

try:
    import pyarrow  # noqa: F401

    HAS_PYARROW = True
except ImportError:  # pragma: no cover - 依赖环境
    HAS_PYARROW = False

HAS_PARQUET = pd is not None and HAS_PYARROW

NODES = [
    {"namespace": "PS1-BE_GFJF_0001-C_003_0001", "id": "V_IF"},
    {"namespace": "PS1-BE_GFJF_0002-C_003_0002", "id": "V_OP"},
]
NAMES = [node["namespace"] + "-" + node["id"] for node in NODES]
START = support.bj(2026, 3, 1)


def ms_of(moment) -> int:
    return int(moment.timestamp() * 1000)


class FakeServerCase(unittest.TestCase):
    server_kwargs = {}

    def setUp(self) -> None:
        self.server = fake_server.FakeIotServer(**self.server_kwargs)
        self.addCleanup(self.server.stop)
        self.config = support.make_config(self.server)

    def client(self, **kwargs):
        client = IotClient(self.config, **kwargs)
        self.addCleanup(client.close)
        return client


class TokenContractTest(FakeServerCase):
    def test_token_request_is_client_credentials_form(self) -> None:
        client = self.client()
        self.assertEqual(client.tokens.get(), "tok-1")
        requests_seen = self.server.token_requests()
        self.assertEqual(len(requests_seen), 1)
        form = requests_seen[0]["form"]
        self.assertEqual(form.get("grant_type"), "client_credentials")
        self.assertEqual(form.get("client_id"), "ai-console")
        self.assertEqual(form.get("client_secret"), support.TEST_VALUE)
        self.assertIn(
            "application/x-www-form-urlencoded",
            requests_seen[0]["headers"].get("content-type", ""),
        )

    def test_token_cache_and_invalidate(self) -> None:
        provider = TokenProvider(self.config)
        self.addCleanup(provider.close)
        self.assertEqual(provider.get(), "tok-1")
        self.assertEqual(provider.get(), "tok-1")
        self.assertEqual(self.server.issued, 1)
        self.assertTrue(provider.cached)
        self.assertGreaterEqual(provider.age, 0.0)
        provider.invalidate()
        self.assertFalse(provider.cached)
        self.assertEqual(provider.get(), "tok-2")
        self.assertEqual(self.server.issued, 2)

    def test_token_refreshed_after_401(self) -> None:
        client = self.client(retries=2)
        _, first = client.fetch_window(NODES, START, 1)
        self.assertIsNotNone(first)
        self.assertEqual(self.server.issued, 1)
        self.server.expire_tokens()
        _, second = client.fetch_window(NODES, START, 1)
        self.assertIsNotNone(second)
        self.assertEqual(self.server.issued, 2)
        self.assertEqual(client.stats.ok, 2)
        self.assertEqual(client.stats.retried, 1)
        self.assertEqual([item["status"] for item in self.server.api_requests()], [200, 401, 200])


class FetchContractTest(FakeServerCase):
    def test_fetch_window_request_shape_and_headers(self) -> None:
        client = self.client()
        _, cols = client.fetch_window(NODES, START, 1)
        self.assertIsNotNone(cols)
        request = self.server.api_requests()[-1]
        body = request["body"]
        self.assertEqual(body["detail"]["returnBounds"], False)
        self.assertEqual(body["detail"]["maxSizePerNode"], 100000)
        self.assertEqual(body["detail"]["startTime"], str(ms_of(START)))
        self.assertEqual(
            body["detail"]["endTime"], str(ms_of(START + timedelta(hours=1)))
        )
        self.assertEqual(body["nodes"], NODES)
        headers = request["headers"]
        self.assertEqual(headers.get("x-tenant-id"), "bewg")
        self.assertIn("application/json", headers.get("content-type", ""))
        self.assertTrue(headers.get("authorization", "").startswith("Bearer "))
        self.assertNotIn(support.TEST_VALUE, json.dumps(request["body"]))

    def test_fetch_window_values_and_stats(self) -> None:
        client = self.client()
        _, cols = client.fetch_window(NODES, START, 1)
        self.assertEqual([name for name, _ in cols], NAMES)
        for name, series in cols:
            self.assertEqual(len(series), 240)
            self.assertEqual(min(series), ms_of(START + timedelta(seconds=15)))
            self.assertEqual(max(series), ms_of(START + timedelta(hours=1)))
            for stamp, value in list(series.items())[:5]:
                self.assertEqual(value, self.server.value_for(name, stamp))
        self.assertEqual(client.stats.ok, 1)
        self.assertEqual(client.stats.retried, 0)
        self.assertEqual(client.missing, {})

    def test_session_does_not_trust_env(self) -> None:
        client = self.client()
        self.assertFalse(client.session().trust_env)

    def test_missing_tenant_header_returns_401(self) -> None:
        client = IotClient(self.config.replace(tenant="not-bewg"), retries=1)
        self.addCleanup(client.close)
        _, cols = client.fetch_window(NODES, START, 1)
        self.assertIsNone(cols)
        self.assertEqual(client.stats.fail, 1)
        request = self.server.api_requests()[-1]
        self.assertEqual(request["status"], 401)
        self.assertEqual(request["tenant"], "not-bewg")

        token = client.tokens.get()
        response = requests.post(
            self.server.api_url,
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
            json={"detail": {"returnBounds": False, "startTime": "0", "endTime": "0"}},
            timeout=5,
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("bearer", response.headers.get("WWW-Authenticate", "").lower())

    def test_waf_502_is_retried(self) -> None:
        self.server.waf_fail_first = 2
        client = self.client(retries=4)
        _, cols = client.fetch_window(NODES, START, 1)
        self.assertIsNotNone(cols)
        self.assertEqual(self.server.api_calls, 3)
        self.assertEqual(client.stats.retried, 1)
        self.assertEqual(client.stats.fail, 0)

    def test_code_not_zero_is_failure(self) -> None:
        self.server.code_error = True
        client = self.client(retries=1)
        _, cols = client.fetch_window(NODES, START, 1)
        self.assertIsNone(cols)
        self.assertEqual(client.stats.fail, 1)
        with self.assertRaises(RuntimeError):
            self.client(retries=1).fetch(NODES, START, hours=1)
        self.assertEqual(self.server.api_requests()[-1]["status"], 200)

    def test_empty_history_counts_as_missing(self) -> None:
        self.server.empty_history = True
        client = self.client()
        _, cols = client.fetch_window(NODES, START, 1)
        self.assertEqual(cols, [])
        self.assertEqual(dict(client.missing), {NAMES[0]: 1, NAMES[1]: 1})
        self.assertEqual(client.fetch(NODES, START, hours=1), {})
        self.assertEqual(client.stats.ok, 2)
        self.assertEqual(client.stats.fail, 0)

    def test_null_result_counts_as_missing(self) -> None:
        self.server.null_nodes = True
        client = self.client()
        _, cols = client.fetch_window(NODES, START, 1)
        self.assertEqual(cols, [])
        self.assertEqual(client.missing.get(NAMES[0]), 1)
        self.assertEqual(client.missing.get(NAMES[1]), 1)

    def test_max_size_clamped_per_node(self) -> None:
        client = self.client()
        _, cols = client.fetch_window(NODES, START, 1, max_size=100)
        for name, series in cols:
            self.assertEqual(len(series), 100)
        self.assertEqual(
            self.server.api_requests()[-1]["body"]["detail"]["maxSizePerNode"], 100
        )

    def test_probe_cap_reveals_limit_and_window(self) -> None:
        client = self.client()
        recent = client.probe_cap(NODES, support.bj(2026, 3, 10))
        old = client.probe_cap(NODES, support.bj(2025, 6, 1))
        self.assertEqual(recent, 1920)
        self.assertEqual(old, 1500)
        self.assertEqual(parse_signal_window(recent), 8.0)
        self.assertEqual(parse_signal_window(old), 6.0)
        self.assertEqual(parse_signal_window(old, 60.0), 25.0)
        self.assertEqual(parse_signal_window(0), 6.0)
        body = self.server.api_requests()[-1]["body"]
        self.assertEqual(body["detail"]["maxSizePerNode"], 100000)
        span = int(body["detail"]["endTime"]) - int(body["detail"]["startTime"])
        self.assertEqual(span, 8 * 3600 * 1000)

    def test_fetch_range_merges_windows(self) -> None:
        data = fetch_range(
            self.config,
            NODES,
            START,
            START + timedelta(hours=2),
            step_seconds=15.0,
            window_hours=1.0,
            workers=2,
            probe=False,
        )
        self.assertEqual(sorted(data), sorted(NAMES))
        self.assertEqual(len(data[NAMES[0]]), 480)
        self.assertEqual(min(data[NAMES[0]]), ms_of(START + timedelta(seconds=15)))


class TimeContractTest(unittest.TestCase):
    def test_month_slices_cover_range(self) -> None:
        slices = month_slices(support.bj(2026, 1, 15), support.bj(2026, 3, 10))
        self.assertEqual(
            [(year, month) for year, month, _, _ in slices],
            [(2026, 1), (2026, 2), (2026, 3)],
        )
        self.assertEqual(slices[0][2], support.bj(2026, 1, 15))
        self.assertEqual(slices[0][3], support.bj(2026, 2, 1))
        self.assertEqual(slices[2][2], support.bj(2026, 3, 1))
        self.assertEqual(slices[2][3], support.bj(2026, 3, 10))
        self.assertEqual(
            len(month_slices(support.bj(2026, 1, 1), support.bj(2026, 2, 1))), 1
        )

    def test_slice_windows(self) -> None:
        wins = slice_windows(support.bj(2026, 3, 1), support.bj(2026, 3, 1, 2), 1.0)
        self.assertEqual(wins, [support.bj(2026, 3, 1), support.bj(2026, 3, 1, 1)])

    def test_make_nodes(self) -> None:
        nodes = make_nodes("PS1", "-X_", ["0001", "0002"], ["V_IF", "V_OP"])
        self.assertEqual(len(nodes), 4)
        self.assertEqual(nodes[0], {"namespace": "PS1-X_0001", "id": "V_IF"})
        self.assertEqual(nodes[-1], {"namespace": "PS1-X_0002", "id": "V_OP"})


class ConfigContractTest(unittest.TestCase):
    def test_config_from_env_and_missing_secret(self) -> None:
        with support.env(
            IOT_TOKEN_URL="http://127.0.0.1:9/token",
            IOT_API_URL="http://127.0.0.1:9/api",
            IOT_TENANT="tenant-x",
            IOT_CLIENT_ID="cid-x",
            IOT_CLIENT_SECRET=None,
        ):
            config = IotConfig.from_env()
            self.assertEqual(config.token_url, "http://127.0.0.1:9/token")
            self.assertEqual(config.api_url, "http://127.0.0.1:9/api")
            self.assertEqual(config.tenant, "tenant-x")
            self.assertEqual(config.client_id, "cid-x")
            self.assertFalse(config.has_secret)
            with self.assertRaises(ValueError):
                config.require_secret()
        with support.env(IOT_CLIENT_SECRET="from-env"):
            self.assertTrue(IotConfig.from_env().has_secret)
            self.assertEqual(IotConfig.from_env().client_secret, "from-env")
        self.assertEqual(IotConfig.from_env(client_secret="explicit").client_secret, "explicit")

    def test_no_proxy_gets_company_domains(self) -> None:
        with support.env(NO_PROXY="localhost,127.0.0.1"):
            prepare_network_env(False)
            entries = [item.strip() for item in os.environ["NO_PROXY"].split(",")]
            self.assertIn("bewg.net.cn", entries)
            prepare_network_env(False)
            again = [item.strip() for item in os.environ["NO_PROXY"].split(",")]
            self.assertEqual(again.count("bewg.net.cn"), 1)
        with support.env(NO_PROXY="localhost"):
            prepare_network_env(True)
            self.assertEqual(os.environ["NO_PROXY"], "localhost")


@unittest.skipIf(pd is None, "需要 pandas")
class FrameContractTest(unittest.TestCase):
    def test_normalize_frame_contract(self) -> None:
        frame = pd.DataFrame({"A": [3.0, 1.0, 1.5, 2.0]}, index=[3000, 1000, 1000, 2000])
        out = normalize_frame(frame)
        self.assertEqual(list(out.index), [1000, 2000, 3000])
        self.assertEqual(str(out.index.dtype), "int64")
        self.assertEqual(list(out["A"]), [1.0, 2.0, 3.0])
        self.assertIn("data_time_utc", out.columns)
        self.assertIn("data_time_bj", out.columns)
        self.assertEqual(str(out["data_time_utc"].dt.tz), "UTC")
        self.assertEqual(out["data_time_bj"].iloc[0].utcoffset(), timedelta(hours=8))


class WriterContractTest(unittest.TestCase):
    def test_resolve_format(self) -> None:
        self.assertEqual(resolve_format("jsonl")[0], "jsonl")
        self.assertIn(resolve_format("auto")[0], ("parquet", "jsonl"))
        with support.env(IOT_FETCH_FORCE_JSONL="1"):
            fmt, reason = resolve_format("auto")
            self.assertEqual(fmt, "jsonl")
            self.assertIn("IOT_FETCH_FORCE_JSONL", reason)

    def test_jsonl_writer(self) -> None:
        stamp = ms_of(support.bj(2026, 3, 1))
        with support.temp_dir() as tmp:
            writer = MonthWriter(tmp, "FAC", "jsonl")
            path = writer.write(
                {stamp: {NAMES[0]: 1.5, NAMES[1]: float("nan")}}, 2026, 3
            )
            self.assertEqual(path.name, "FAC-202603.jsonl")
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertEqual(record["t"], stamp)
            self.assertTrue(record["data_time_utc"].endswith("+00:00"))
            self.assertEqual(record["signals"][NAMES[0]], 1.5)
            self.assertIsNone(record["signals"][NAMES[1]])

    @unittest.skipIf(not HAS_PARQUET, "需要 pandas + pyarrow")
    def test_parquet_writer_is_atomic_and_zstd(self) -> None:
        stamp = ms_of(support.bj(2026, 3, 1))
        table = {
            stamp: {NAMES[0]: 1.5, NAMES[1]: 2.5},
            stamp + 15000: {NAMES[0]: 3.0},
        }
        with support.temp_dir() as tmp:
            writer = MonthWriter(tmp, "FAC", "parquet")
            path = writer.write(table, 2026, 3)
            self.assertEqual(path.name, "FAC-202603.parquet")
            frame = pd.read_parquet(path)
            self.assertEqual(list(frame.index), [stamp, stamp + 15000])
            self.assertEqual(str(frame.index.dtype), "int64")
            self.assertIn(NAMES[0], frame.columns)
            self.assertEqual(frame[NAMES[0]].iloc[0], 1.5)
            leftovers = [item.name for item in tmp.iterdir() if item.suffix == ".tmp"]
            self.assertEqual(leftovers, [])
            with open(path, "rb") as handle:
                head = handle.read(4)
            self.assertEqual(head, b"PAR1")
