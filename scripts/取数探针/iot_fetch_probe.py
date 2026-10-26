#!/usr/bin/env python3
"""Real IoT fetch probe with DNS / connect / TLS / HTTP auditing.

The company provider module is PyArmor-encrypted, so its endpoints cannot be read
from the source. This script therefore watches the network from the outside:

  * patches socket.getaddrinfo, socket.create_connection and SSLContext.wrap_socket
    before the provider is imported, so every DNS lookup, TCP connect, TLS
    handshake (with SNI) and HTTP request/response the provider performs is
    recorded;
  * then calls ``mlops_deploy_iot_armor2_311.fetch_iot_data(...)`` and reports
    either the returned rows or the exact failure chain;
  * classifies the outcome (intranet unreachable / public path / token rejected /
    fetch ok) so the machine's network path is visible without reading the module.

The script never reads, prints or writes a credential. Request headers are
recorded by NAME only.

Usage:
    python iot_fetch_probe.py --factory-code <CODE> --point <POINT_CODE>
    python iot_fetch_probe.py --framework ..\\framework --factory-code <CODE> \\
        --point <POINT_CODE> --start "2026-01-01 12:00:00"
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib
import json
import os
import platform
import re
import socket
import ssl
import sys
import time

PROVIDER_MODULE = "mlops_deploy_iot_armor2_311"
APPROVED_ARCHIVE_SHA256 = "06f08382f652c303d451fb1770114a546babba5388247185729e46fd2e793942"
APPROVED_MODULE_SHA256 = "86a2a28e9474684999e2d0f661284cb6e662c69e99b7c56a1cfc4e3c468c3332"
DEFAULT_OUT = "iot_fetch_probe_receipt.json"
PROXY_VARIABLES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
COMPANY_DOMAIN = "bewg.net.cn"


class Audit:
    """Collects everything the provider does on the wire."""

    def __init__(self) -> None:
        self.dns: list[dict] = []
        self.connects: list[dict] = []
        self.tls: list[dict] = []
        self.http: list[dict] = []

    def as_dict(self) -> dict:
        return {"dns": self.dns, "connects": self.connects, "tls": self.tls, "http": self.http}

    def contacted(self) -> list[str]:
        seen: list[str] = []
        for item in self.connects:
            address = str(item.get("address") or item.get("host") or "")
            if address and address not in seen:
                seen.append(address)
        return seen


def _clock(started: float) -> int:
    return round((time.time() - started) * 1000)


def _port_number(port: object) -> int:
    if isinstance(port, int):
        return port
    try:
        return socket.getservbyname(str(port), "tcp") if port else 443
    except OSError:
        try:
            return int(str(port))
        except ValueError:
            return 443


def install_audit(audit: Audit, host_map: dict[str, str]) -> None:
    real_getaddrinfo = socket.getaddrinfo
    real_create_connection = socket.create_connection
    real_wrap_socket = ssl.SSLContext.wrap_socket

    def patched_getaddrinfo(host, port, *args, **kwargs):  # noqa: ANN001
        started = time.time()
        try:
            infos = real_getaddrinfo(host, port, *args, **kwargs)
        except OSError as error:
            audit.dns.append(
                {
                    "host": str(host),
                    "port": _port_number(port),
                    "ok": False,
                    "error": f"{type(error).__name__}: {error}",
                    "ms": _clock(started),
                }
            )
            raise
        addresses = sorted({info[4][0] for info in infos})
        entry = {
            "host": str(host),
            "port": _port_number(port),
            "ok": True,
            "addresses": addresses,
            "ms": _clock(started),
        }
        forced = host_map.get(str(host))
        if forced:
            entry["forced_to"] = forced
            entry["original_addresses"] = addresses
            audit.dns.append(entry)
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (forced, _port_number(port)))
            ]
        audit.dns.append(entry)
        return infos

    def patched_create_connection(address, *args, **kwargs):  # noqa: ANN001
        host, port = address[0], address[1]
        started = time.time()
        try:
            connection = real_create_connection(address, *args, **kwargs)
        except OSError as error:
            audit.connects.append(
                {
                    "host": str(host),
                    "port": _port_number(port),
                    "ok": False,
                    "error": f"{type(error).__name__}: {error}",
                    "ms": _clock(started),
                }
            )
            raise
        peer = ""
        source = ""
        try:
            peer = str(connection.getpeername()[0])
        except OSError:
            peer = str(host)
        try:
            source = str(connection.getsockname()[0])
        except OSError:
            pass
        audit.connects.append(
            {
                "host": str(host),
                "port": _port_number(port),
                "address": peer,
                "source": source,
                "ok": True,
                "ms": _clock(started),
            }
        )
        return connection

    def patched_wrap_socket(self, *args, **kwargs):  # noqa: ANN001
        started = time.time()
        sni = kwargs.get("server_hostname")
        if sni is None and len(args) > 4:
            sni = args[4]
        peer = ""
        try:
            peer = str(args[0].getpeername()[0]) if args else ""
        except (OSError, IndexError):
            pass
        try:
            tls_socket = real_wrap_socket(self, *args, **kwargs)
        except Exception as error:  # noqa: BLE001 - recorded, then re-raised
            audit.tls.append(
                {
                    "address": peer,
                    "sni": sni,
                    "ok": False,
                    "error": f"{type(error).__name__}: {error}",
                    "ms": _clock(started),
                }
            )
            raise
        version = ""
        try:
            version = str(tls_socket.version())
        except (AttributeError, ValueError):
            pass
        audit.tls.append(
            {"address": peer, "sni": sni, "ok": True, "version": version, "ms": _clock(started)}
        )
        return tls_socket

    socket.getaddrinfo = patched_getaddrinfo
    socket.create_connection = patched_create_connection
    ssl.SSLContext.wrap_socket = patched_wrap_socket

    try:
        import requests
    except ImportError:
        requests = None
    if requests is not None:
        real_session_request = requests.sessions.Session.request

        def patched_session_request(self, method, url, *args, **kwargs):  # noqa: ANN001
            started = time.time()
            try:
                response = real_session_request(self, method, url, *args, **kwargs)
            except Exception as error:  # noqa: BLE001
                audit.http.append(
                    {
                        "layer": "requests.Session",
                        "method": str(method).upper(),
                        "url": str(url),
                        "ok": False,
                        "error": f"{type(error).__name__}: {error}",
                        "ms": _clock(started),
                    }
                )
                raise
            header_names: list[str] = []
            try:
                header_names = sorted(str(name) for name in response.request.headers)
            except (AttributeError, TypeError):
                pass
            audit.http.append(
                {
                    "layer": "requests.Session",
                    "method": str(method).upper(),
                    "url": str(url),
                    "status": int(response.status_code),
                    "request_header_names": header_names,
                    "ms": _clock(started),
                }
            )
            return response

        requests.sessions.Session.request = patched_session_request

    try:
        import http.client
    except ImportError:
        http = None  # type: ignore[assignment]
    if http is not None:
        real_http_request = http.client.HTTPConnection.request
        real_http_getresponse = http.client.HTTPConnection.getresponse

        def patched_http_request(self, method, url, *args, **kwargs):  # noqa: ANN001
            self._probe_started = time.time()
            self._probe_method = str(method).upper()
            self._probe_url = str(url)
            return real_http_request(self, method, url, *args, **kwargs)

        def patched_http_getresponse(self):  # noqa: ANN001
            response = real_http_getresponse(self)
            started = getattr(self, "_probe_started", time.time())
            audit.http.append(
                {
                    "layer": "http.client",
                    "method": getattr(self, "_probe_method", "?"),
                    "url": f"{getattr(self, 'host', '?')}:{getattr(self, 'port', '?')}{getattr(self, '_probe_url', '')}",
                    "status": int(getattr(response, "status", 0) or 0),
                    "ms": _clock(started),
                }
            )
            return response

        http.client.HTTPConnection.request = patched_http_request
        http.client.HTTPConnection.getresponse = patched_http_getresponse


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_network(argv: argparse.Namespace) -> dict[str, str]:
    """Make sure the company domains never go through an HTTP proxy."""

    report = {
        name: ("set" if os.environ.get(name) else "unset") for name in PROXY_VARIABLES + ("NO_PROXY", "no_proxy")
    }
    if argv.respect_proxy:
        report["action"] = "untouched (--respect-proxy)"
        return report
    if argv.bypass_proxy:
        for name in PROXY_VARIABLES:
            os.environ.pop(name, None)
        report["action"] = "proxy variables removed (--bypass-proxy)"
    else:
        report["action"] = f"{COMPANY_DOMAIN} appended to NO_PROXY"
    for name in ("NO_PROXY", "no_proxy"):
        current = os.environ.get(name, "")
        entries = [item.strip() for item in current.split(",") if item.strip()]
        for wanted in (COMPANY_DOMAIN, f".{COMPANY_DOMAIN}"):
            if wanted not in entries:
                entries.append(wanted)
        os.environ[name] = ",".join(entries)
    report["NO_PROXY_final"] = os.environ.get("NO_PROXY", "")
    return report


def load_provider(framework_dir: str):
    if framework_dir:
        resolved = os.path.abspath(framework_dir)
        if resolved not in sys.path:
            sys.path.insert(0, resolved)
    module = importlib.import_module(PROVIDER_MODULE)
    fetch = getattr(module, "fetch_iot_data", None)
    if fetch is None:
        raise RuntimeError(f"{PROVIDER_MODULE} does not expose fetch_iot_data")
    return fetch


def summarize(result: object) -> dict:
    info: dict[str, object] = {"type": type(result).__name__}
    rows = None
    columns: list[str] = []
    if hasattr(result, "columns"):
        try:
            columns = [str(name) for name in result.columns]  # type: ignore[union-attr]
            rows = int(len(result))  # type: ignore[arg-type]
        except (TypeError, AttributeError):
            columns = []
    elif isinstance(result, (list, tuple)):
        rows = len(result)
    elif isinstance(result, dict):
        rows = len(result)
    if rows is not None:
        info["rows"] = rows
        info["columns"] = columns
    try:
        import pandas as pd

        if isinstance(result, pd.DataFrame) and len(result):
            for column in ("ts_utc", "time_utc", "timestamp_utc", "ts", "time", "datetime", "point_time"):
                if column in result.columns:
                    info["first_time"] = str(result[column].iloc[0])
                    info["last_time"] = str(result[column].iloc[-1])
                    break
            numeric = result.select_dtypes("number")
            digest: dict[str, object] = {}
            for column in list(numeric.columns)[:8]:
                series = numeric[column]
                digest[str(column)] = {
                    "count": int(series.count()),
                    "min": float(series.min()),
                    "max": float(series.max()),
                    "last": float(series.iloc[-1]),
                }
            info["numeric"] = digest
            info["preview_head"] = result.head(3).to_dict(orient="records")
    except ImportError:
        info["pandas"] = "not installed (summary limited to type/rows/columns)"
    except (TypeError, ValueError) as error:
        info["summary_error"] = f"{type(error).__name__}: {error}"
    return info


TOKEN_PATTERN = re.compile(
    r"(401|403|unauthor|forbidden|invalid[_ ]token|token.*expir|access[_ ]denied|"
    r"getaddrinfo|name or service not known|nodename nor servname|timed out|timeout|"
    r"connectionrefused|connection refused|no route to host|ssl|certificate)",
    re.IGNORECASE,
)


def classify(audit: Audit, error_text: str) -> tuple[str, str]:
    if not error_text:
        return "FETCH_OK", "provider returned data; token exchange and fetch both worked"
    statuses = [int(item.get("status") or 0) for item in audit.http]
    if any(status in (401, 403) for status in statuses):
        return "TOKEN_REJECTED", "the endpoint answered 401/403 - credential or client identity problem"
    if any(re.search(r"getaddrinfo|name or service not known|nodename nor servname", str(item.get("error") or ""), re.I) for item in audit.dns):
        return "DNS_FAILED", "a company hostname did not resolve on this machine"
    failed = [item for item in audit.connects if not item.get("ok")]
    if failed:
        private_failures = [
            item for item in failed if str(item.get("address") or item.get("host", "")).startswith("10.")
        ]
        if private_failures and len(private_failures) == len(failed):
            return (
                "INTRANET_PATH_UNREACHABLE",
                "every failed connection was to a 10.x address - this machine has no intranet route",
            )
        return "CONNECT_FAILED", "TCP connect failed; see the audit lines above for the exact address"
    if complete_statuses := [status for status in statuses if status]:
        return "HTTP_ERROR", f"the endpoint answered HTTP {complete_statuses[-1]}"
    if re.search(r"ModuleNotFoundError|No module named|ImportError", error_text):
        return (
            "MODULE_NOT_FOUND",
            "the provider module could not be imported - check the --framework directory",
        )
    if TOKEN_PATTERN.search(error_text):
        return "UNCLASSIFIED_NETWORK_ERROR", "see the error chain below"
    return "PROVIDER_ERROR", "the provider raised before any network call - see the traceback"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--framework", default=os.environ.get("MODEL_LAB_FRAMEWORK", ""),
                        help="directory holding mlops_deploy_iot_armor2_311.py (and pyarmor_runtime_000000)")
    parser.add_argument("--factory-code", required=True, help="plant/factory code expected by the provider")
    parser.add_argument("--point", action="append", default=[],
                        help="point code; repeat for several. Same value is used as the output column name")
    parser.add_argument("--start", default="", help='local naive start time, "YYYY-MM-DD HH:MM:SS" (default: now - 10 min)')
    parser.add_argument("--rolling-window", default="60min", help='provider rolling window, e.g. "60min" (default)')
    parser.add_argument("--interval", default="15s", help='provider interval, e.g. "15s" (default)')
    parser.add_argument("--zip", default="", help="optional company IoT package zip, checked against the approved digest")
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"JSON receipt path (default: {DEFAULT_OUT})")
    parser.add_argument("--map", action="append", default=[], metavar="HOST=IP",
                        help="force a hostname to an IP (use to test the public path deliberately)")
    parser.add_argument("--respect-proxy", action="store_true", help="do not touch proxy environment variables")
    parser.add_argument("--bypass-proxy", action="store_true", help="also remove HTTP(S)_PROXY variables")
    arguments = parser.parse_args()

    if not arguments.point:
        parser.error("at least one --point is required (repeat the flag for several points)")

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    host_map: dict[str, str] = {}
    for item in arguments.map:
        if "=" not in item:
            parser.error(f"--map expects HOST=IP, got {item!r}")
        host, _, address = item.partition("=")
        host_map[host.strip()] = address.strip()

    started_local = arguments.start.strip() or (
        datetime.datetime.now() - datetime.timedelta(minutes=10)
    ).strftime("%Y-%m-%d %H:%M:%S")
    try:
        parsed_start = datetime.datetime.strptime(started_local, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        parser.error(f'--start must look like "YYYY-MM-DD HH:MM:SS", got {started_local!r}')
    age_minutes = (datetime.datetime.now() - parsed_start).total_seconds() / 60.0

    audit = Audit()
    report: dict[str, object] = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "machine": socket.gethostname(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "argv_summary": {
            "factory_code_length": len(arguments.factory_code),
            "point_count": len(arguments.point),
            "start": started_local,
            "rolling_window": arguments.rolling_window,
            "interval": arguments.interval,
            "host_map": host_map,
        },
        "network": prepare_network(arguments),
    }

    print("=== IOT FETCH PROBE ===")
    print(f"machine={report['machine']} platform={report['platform']} python={report['python']}")
    print(f"start(local naive)={started_local}  age={age_minutes:.1f} min  window={arguments.rolling_window} interval={arguments.interval}")
    if age_minutes > 120:
        print("WARNING: the provider has no end_time parameter and only returns data near the execution moment;")
        print("         a start older than ~2h is usually rejected or returns nothing. Consider a recent start.")
    for name, value in report["network"].items():  # type: ignore[union-attr]
        print(f"network.{name} = {value}")
    if host_map:
        print("WARNING: --map forces hostnames to fixed addresses; any credential the provider sends will go there.")

    framework_dir = arguments.framework
    if framework_dir:
        framework_dir = os.path.abspath(framework_dir)
        if not os.path.isdir(framework_dir):
            print(f"WARNING: --framework {arguments.framework} is not a directory (looked for {framework_dir})")
        elif not os.path.exists(os.path.join(framework_dir, f"{PROVIDER_MODULE}.py")):
            print(f"WARNING: {PROVIDER_MODULE}.py not found in {framework_dir}")
    module_path = os.path.join(framework_dir, f"{PROVIDER_MODULE}.py") if framework_dir else ""
    provenance: dict[str, object] = {"framework_dir": framework_dir or "<from sys.path>"}
    if module_path and os.path.exists(module_path):
        digest = sha256_of(module_path)
        provenance.update(
            {
                "module_path": module_path,
                "module_sha256": digest,
                "module_matches_approved": digest == APPROVED_MODULE_SHA256,
                "approved_module_sha256": APPROVED_MODULE_SHA256,
            }
        )
        print(f"provider module sha256 = {digest} (approved={APPROVED_MODULE_SHA256})")
        if digest != APPROVED_MODULE_SHA256:
            print("WARNING: module digest does not match the approved value.")
    if arguments.zip:
        if os.path.exists(arguments.zip):
            digest = sha256_of(arguments.zip)
            provenance.update(
                {
                    "zip_path": os.path.abspath(arguments.zip),
                    "zip_sha256": digest,
                    "zip_matches_approved": digest == APPROVED_ARCHIVE_SHA256,
                    "approved_zip_sha256": APPROVED_ARCHIVE_SHA256,
                }
            )
            print(f"iot package sha256     = {digest} (approved={APPROVED_ARCHIVE_SHA256})")
        else:
            provenance["zip_path"] = f"{arguments.zip} (not found)"
            print(f"WARNING: --zip {arguments.zip} does not exist")
    report["provenance"] = provenance

    install_audit(audit, host_map)

    print("\n--- provider call ---")
    result: object = None
    error_text = ""
    traceback_text = ""
    try:
        fetch = load_provider(framework_dir)
        result = fetch(
            factory_code=arguments.factory_code,
            points={code: code for code in arguments.point},
            start_time=started_local,
            rolling_window=arguments.rolling_window,
            interval=arguments.interval,
        )
    except BaseException as error:  # noqa: BLE001 - the failure chain is the evidence
        import traceback

        error_text = f"{type(error).__name__}: {error}"
        traceback_text = traceback.format_exc()
        print(f"provider raised: {error_text}")

    print("\n--- [1] DNS LOOKUPS ---")
    for item in audit.dns:
        if item.get("ok"):
            forced = f"  (FORCED to {item['forced_to']})" if item.get("forced_to") else ""
            print(f"{item['host']}:{item['port']} -> {', '.join(item.get('addresses') or [])} ({item['ms']}ms){forced}")
        else:
            print(f"{item['host']}:{item['port']} -> FAILED {item.get('error')} ({item['ms']}ms)")

    print("\n--- [2] TCP CONNECTS ---")
    for item in audit.connects:
        if item.get("ok"):
            print(
                f"{item['host']}:{item['port']} -> {item.get('address')} ok ({item['ms']}ms, source {item.get('source')})"
            )
        else:
            print(f"{item['host']}:{item['port']} -> FAILED {item.get('error')} ({item['ms']}ms)")

    print("\n--- [3] TLS HANDSHAKES ---")
    for item in audit.tls:
        state = "ok" if item.get("ok") else "FAILED"
        detail = item.get("version") or item.get("error")
        print(f"{item.get('address')} sni={item.get('sni')} {state} {detail} ({item['ms']}ms)")

    print("\n--- [4] HTTP ---")
    for item in audit.http:
        if item.get("status"):
            print(f"{item['method']} {item['url']} -> {item['status']} ({item['ms']}ms) via {item['layer']}")
        else:
            print(f"{item['method']} {item['url']} -> ERROR {item.get('error')} ({item['ms']}ms) via {item['layer']}")

    print("\n--- [5] RESULT ---")
    summary = summarize(result) if result is not None else {}
    if summary:
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    elif error_text:
        print("no data returned")

    verdict, explanation = classify(audit, error_text)
    print("\n--- [6] VERDICT ---")
    print(f"classification = {verdict}")
    print(f"explanation    = {explanation}")
    if traceback_text:
        print("\n--- error chain ---")
        print(traceback_text.rstrip())

    report.update(
        {
            "audit": audit.as_dict(),
            "contacted_addresses": audit.contacted(),
            "result": summary,
            "error": error_text,
            "traceback": traceback_text,
            "verdict": {"classification": verdict, "explanation": explanation},
        }
    )
    with open(arguments.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, default=str)
    print(f"\nreceipt written to {os.path.abspath(arguments.out)}")
    return 0 if result is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
