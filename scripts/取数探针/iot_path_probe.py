#!/usr/bin/env python3
"""Network path probe for the company IoT endpoints.

Answers one question for the machine it runs on:

    Which path does the company IoT data fetch use here, and does it need a VPN?

What it does, in order:

  1. resolves the three company endpoints and classifies each address as
     intranet (10.x) or public;
  2. reads the local routing table and reports, for each resolved address,
     the longest-prefix owner, the adapter name and whether that adapter is a
     VPN/tunnel, Wi-Fi or wired interface;
  3. opens a TLS connection to the data endpoint with SNI and sends an
     unauthenticated GET, so the HTTP response proves the request reached the
     application (and not just a firewall);
  4. prints a verdict plus an egress source address.

Standard library only. It carries no company module and no credentials, so it is
safe to run on any machine, with or without VPN.

Usage:
    python iot_path_probe.py
    python iot_path_probe.py --json iot_path_probe.json
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import platform
import re
import socket
import ssl
import subprocess
import sys
import time

HOSTS = {
    "iam": "saas-iam.bewg.net.cn",
    "iot": "be-iot-saas.bewg.net.cn",
    "mlflow": "ailab-mlflow.bewg.net.cn",
}

# Last observed intranet addresses (documentation only, never used to connect).
INTERNAL_HINT = {
    "iam": "10.3.30.12",
    "iot": "10.3.33.209",
    "mlflow": "10.3.26.247",
}

PROBE_PATH = "/ts/v3/datapoint/history/rawvalue"
PUBLIC_IP_HINT = "59.110.245.236"

VPN_ADAPTER = re.compile(
    r"vpn|hillstone|virtualnet|letstap|openvpn|wireguard|tailscale|zerotier|softether|"
    r"sangfor|anyconnect|virtual network adapter|\btap\b",
    re.IGNORECASE,
)
WIFI_ADAPTER = re.compile(r"wi-?fi|wlan|wireless|rz6", re.IGNORECASE)
WIRED_ADAPTER = re.compile(r"ethernet|realtek pcie|intel\(r\) i2|以太", re.IGNORECASE)

# ipconfig prints an adapter header line for every interface; the DNS suffix line
# ("连接特定的 DNS 后缀 . . . . :") also ends with a colon and must NOT be read as a
# header, so the adapter keyword is required. The leading label is dropped so that
# "未知适配器 Hillstone Virtual Network Adapter Tunnel:" keeps the vendor name.
ADAPTER_LABEL = re.compile(r"适配器|adapter", re.IGNORECASE)
ADAPTER_LABEL_PREFIXES = (
    "无线局域网适配器",
    "以太网适配器",
    "隧道适配器",
    "未知适配器",
    "Wireless LAN adapter",
    "Ethernet adapter",
    "Tunnel adapter",
    "Unknown adapter",
)


def _run(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return f"<command failed: {type(error).__name__}>"
    return completed.stdout or ""


def classify_adapter(name: str) -> str:
    if not name:
        return "unknown"
    if VPN_ADAPTER.search(name):
        return "vpn/tunnel"
    if WIFI_ADAPTER.search(name):
        return "wifi"
    if WIRED_ADAPTER.search(name):
        return "wired"
    return "other"


def parse_address_owner(text: str) -> dict[str, str]:
    """Map adapter IPv4 address -> adapter name from ipconfig / ip addr output."""

    owners: dict[str, str] = {}
    current = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if (
            stripped.endswith(":")
            and ADAPTER_LABEL.search(stripped)
            and not re.search(r"\d+\.\d+\.\d+\.\d+", stripped)
        ):
            # "Wireless LAN adapter WLAN:" / "无线局域网适配器 WLAN:"
            # "Unknown adapter Hillstone Virtual Network Adapter Tunnel:"
            label = stripped[:-1].strip()
            for prefix in ADAPTER_LABEL_PREFIXES:
                if label.lower().startswith(prefix.lower()):
                    label = label[len(prefix) :].strip() or label
                    break
            current = label
            continue
        match = re.search(r"IPv4[^:]*:\s*(\d+\.\d+\.\d+\.\d+)", stripped, re.IGNORECASE)
        if match and current:
            owners[match.group(1)] = current
            continue
        match = re.search(r"^\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)", stripped)
        if match:  # Linux: "2: eth0    inet 10.0.0.5/24 ..."
            owners[match.group(2)] = match.group(1)
    return owners


def parse_routes(text: str) -> list[dict[str, object]]:
    """Parse `route print -4` (Windows) or `ip route` (Linux) into route rows."""

    routes: list[dict[str, object]] = []
    for line in text.splitlines():
        windows = re.match(
            r"^\s*(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\S+)\s+"
            r"(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s*$",
            line,
        )
        if windows:
            destination, netmask, gateway, interface, metric = windows.groups()
            routes.append(
                {
                    "destination": destination,
                    "netmask": netmask,
                    "gateway": gateway,
                    "interface": interface,
                    "metric": int(metric),
                }
            )
            continue
        linux = re.match(
            r"^(?:default|(\d+\.\d+\.\d+\.\d+)(?:/(\d+))?)\s+via\s+(\S+)\s+dev\s+(\S+)"
            r"(?:\s+proto\s+\S+)?(?:\s+src\s+(\d+\.\d+\.\d+\.\d+))?"
            r"(?:\s+metric\s+(\d+))?",
            line.strip(),
        )
        if linux:
            destination = linux.group(1) or "0.0.0.0"
            prefix = int(linux.group(2)) if linux.group(2) else 0
            netmask = str(ipaddress.IPv4Network(f"{destination}/{prefix}", strict=False).netmask)
            routes.append(
                {
                    "destination": destination,
                    "netmask": netmask,
                    "gateway": linux.group(3),
                    "interface": linux.group(5) or linux.group(4),
                    "metric": int(linux.group(6) or 0),
                    "device": linux.group(4),
                }
            )
    return routes


def routing_table() -> tuple[list[dict[str, object]], dict[str, str], str]:
    if sys.platform == "win32":
        routes = parse_routes(_run(["route", "print", "-4"]))
        owners = parse_address_owner(_run(["ipconfig"]))
        source = "route print -4 + ipconfig"
    else:
        routes = parse_routes(_run(["ip", "route"]))
        owners = parse_address_owner(_run(["ip", "-o", "-4", "addr", "show"]))
        source = "ip route + ip -o -4 addr show"
    return routes, owners, source


def address_owner(address: str, routes: list[dict[str, object]], owners: dict[str, str]) -> dict[str, object]:
    """Longest-prefix match: who carries traffic to this address?"""

    target = int(ipaddress.IPv4Address(address))
    best: dict[str, object] = {}
    best_prefix = -1
    for route in routes:
        try:
            destination = int(ipaddress.IPv4Address(str(route["destination"])))
            netmask = int(ipaddress.IPv4Address(str(route["netmask"])))
        except (ValueError, KeyError):
            continue
        if target & netmask != destination & netmask:
            continue
        prefix = bin(netmask).count("1")
        metric = int(route.get("metric") or 0)
        if prefix > best_prefix or (
            prefix == best_prefix and metric < int(best.get("metric") or 1 << 30)
        ):
            best_prefix = prefix
            best = dict(route)
            best["prefix_length"] = prefix
    if not best:
        return {"address": address, "owner": "no matching route"}
    interface = str(best.get("interface", ""))
    device = str(best.get("device") or owners.get(interface) or interface)
    name = device if best.get("device") else owners.get(interface, interface)
    best.update(
        {
            "address": address,
            "interface_address": interface,
            "adapter": name,
            "adapter_kind": classify_adapter(str(name)),
            "network": f"{best['destination']}/{best['prefix_length']}",
        }
    )
    return best


def resolve(host: str) -> tuple[list[str], str]:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError as error:
        return [], f"{type(error).__name__}: {error}"
    addresses = sorted({info[4][0] for info in infos})
    return addresses, ""


def address_class(address: str) -> str:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return "unparsed"
    if parsed.is_loopback:
        return "loopback"
    return "intranet" if parsed.is_private else "public"


def egress_address(address: str) -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((address, 443))
        return str(probe.getsockname()[0])
    except OSError:
        return ""
    finally:
        probe.close()


def tls_probe(address: str, host: str, path: str, timeout: float = 8.0) -> dict[str, object]:
    """TLS + one unauthenticated GET; identifier headers only, no credentials."""

    result: dict[str, object] = {"host": host, "address": address, "path": path}
    started = time.time()
    try:
        raw_socket = socket.create_connection((address, 443), timeout=timeout)
    except OSError as error:
        result.update(
            tcp="failed",
            error=f"{type(error).__name__}: {error}",
            elapsed_s=round(time.time() - started, 3),
        )
        return result
    result["tcp_ms"] = round((time.time() - started) * 1000)
    verified = True
    try:
        tls_socket = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=host)
    except ssl.SSLCertVerificationError as error:
        verified = False
        result["certificate"] = f"unverified: {type(error).__name__}"
        raw_socket.close()
        try:
            raw_socket = socket.create_connection((address, 443), timeout=timeout)
            tls_socket = ssl._create_unverified_context().wrap_socket(raw_socket, server_hostname=host)
        except (OSError, ssl.SSLError) as retry_error:
            result.update(tls="failed", error=f"{type(retry_error).__name__}: {retry_error}")
            return result
    except (OSError, ssl.SSLError) as error:
        raw_socket.close()
        result.update(
            tls="failed",
            error=f"{type(error).__name__}: {error}",
            elapsed_s=round(time.time() - started, 3),
        )
        return result
    result["tls_verified"] = verified
    result["tls_protocol"] = tls_socket.version()
    result["tls_ms"] = round((time.time() - started) * 1000)
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "User-Agent: model-lab-iot-path-probe\r\n"
        "Accept: application/json\r\n"
        "Connection: close\r\n\r\n"
    )
    try:
        tls_socket.sendall(request.encode("ascii"))
        payload = b""
        while b"\r\n\r\n" not in payload and len(payload) < 16384:
            chunk = tls_socket.recv(4096)
            if not chunk:
                break
            payload += chunk
    except (OSError, ssl.SSLError) as error:
        result.update(http="failed", error=f"{type(error).__name__}: {error}")
        return result
    finally:
        try:
            tls_socket.close()
        except OSError:
            pass
    head = payload.split(b"\r\n\r\n", 1)[0].decode("iso-8859-1")
    lines = head.split("\r\n")
    result["status_line"] = lines[0]
    parts = lines[0].split()
    result["status"] = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    result["headers"] = {
        key: headers[key]
        for key in ("server", "content-type", "www-authenticate", "content-length")
        if key in headers
    }
    result["elapsed_s"] = round(time.time() - started, 3)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", dest="json_path", help="write the machine-readable receipt here")
    parser.add_argument("--timeout", type=float, default=8.0, help="per-connection timeout in seconds")
    arguments = parser.parse_args()

    try:  # keep the log readable when redirected on a GBK console
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    routes, owners, route_source = routing_table()
    report: dict[str, object] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "machine": socket.gethostname(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "route_source": route_source,
        "routes": routes,
        "adapters": owners,
        "endpoints": {},
    }

    print("=== IOT PATH PROBE ===")
    print(f"machine={report['machine']} platform={report['platform']} python={report['python']}")
    print(f"route source: {route_source}")
    proxies = {
        name: ("set" if __import__("os").environ.get(name) else "unset")
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")
    }
    print(f"proxy env: {proxies}")

    print("\n--- [1] DNS ---")
    resolved: dict[str, list[str]] = {}
    for role, host in HOSTS.items():
        addresses, error = resolve(host)
        resolved[role] = addresses
        if addresses:
            rendered = ", ".join(f"{address} [{address_class(address)}]" for address in addresses)
            print(f"{role:<7} {host:<26} -> {rendered}")
        else:
            print(f"{role:<7} {host:<26} -> FAILED ({error})")
        report["endpoints"][role] = {"host": host, "addresses": addresses, "error": error}

    print("\n--- [2] ROUTE OWNER ---")
    for role, addresses in resolved.items():
        for address in addresses:
            owner = address_owner(address, routes, owners)
            report["endpoints"][role].setdefault("routes", []).append(owner)
            print(
                f"{address:<16} -> {owner.get('network', '?'):<18} via {str(owner.get('gateway', '?')):<15} "
                f"dev '{owner.get('adapter', '?')}' [{owner.get('adapter_kind', '?')}] metric {owner.get('metric', '?')}"
            )
    for role, hint in INTERNAL_HINT.items():
        owner = address_owner(hint, routes, owners)
        print(
            f"{hint:<16} -> {owner.get('network', '?'):<18} via {str(owner.get('gateway', '?')):<15} "
            f"dev '{owner.get('adapter', '?')}' [{owner.get('adapter_kind', '?')}]  (intranet hint for {role})"
        )
        report["endpoints"][role]["intranet_hint_route"] = owner

    print("\n--- [3] TLS + UNAUTHENTICATED HTTP ---")
    for role in ("iot", "iam"):
        host = HOSTS[role]
        addresses = resolved.get(role) or []
        if not addresses:
            print(f"{host}: skipped (no address)")
            continue
        probe = tls_probe(addresses[0], host, PROBE_PATH, arguments.timeout)
        probe["egress_source"] = egress_address(addresses[0])
        report["endpoints"][role]["probe"] = probe
        if probe.get("status_line"):
            print(
                f"GET https://{host}{PROBE_PATH} @{addresses[0]} -> {probe['status_line']} "
                f"(tcp={probe.get('tcp_ms')}ms tls={probe.get('tls_ms')}ms total={probe.get('elapsed_s')}s "
                f"src={probe.get('egress_source')})"
            )
            print(f"    headers: {probe.get('headers')}")
        else:
            print(f"GET https://{host}{PROBE_PATH} @{addresses[0]} -> FAILED: {probe.get('error')}")

    print("\n--- [4] VERDICT ---")
    iot_addresses = resolved.get("iot") or []
    mlflow_addresses = resolved.get("mlflow") or []
    iot_classes = {address_class(address) for address in iot_addresses}
    if not iot_addresses:
        path = "unresolved"
    elif iot_classes == {"intranet"}:
        path = "intranet"
    elif "public" in iot_classes:
        path = "public"
    else:
        path = "mixed"
    iot_route = (report["endpoints"]["iot"].get("routes") or [{}])[0] if iot_addresses else {}
    iot_probe = (report["endpoints"]["iot"].get("probe") or {}) if iot_addresses else {}
    iot_status = int(iot_probe.get("status") or 0)
    if path == "public" and iot_status:
        vpn_required = f"no (the public endpoint answered HTTP {iot_status} over the normal default route)"
    elif path == "public":
        vpn_required = (
            "likely no (DNS answers with a public address) but the endpoint did NOT answer - "
            "read section [3] before concluding"
        )
    elif path == "intranet":
        vpn_required = "already on the intranet path (VPN or an on-site network carries it)"
    elif path == "mixed":
        vpn_required = "undetermined (both public and intranet answers)"
    else:
        vpn_required = "unknown (DNS did not answer)"
    print(f"iot_endpoint_path        = {path}")
    print(f"vpn_required_for_fetch   = {vpn_required}")
    if iot_route.get("adapter"):
        print(f"iot_route_adapter        = {iot_route.get('adapter')} [{iot_route.get('adapter_kind')}]")
    print(
        "publish_reachable_here   = "
        + ("yes (MLflow resolved)" if mlflow_addresses else "no (only intranet DNS knows these hostnames)")
    )
    print("note                     = unauthenticated probe only; token exchange is NOT tested here")
    report["verdict"] = {
        "iot_endpoint_path": path,
        "vpn_required_for_fetch": vpn_required,
        "iot_endpoint_answered_http": iot_status or None,
        "iot_route_adapter": iot_route.get("adapter"),
        "iot_route_adapter_kind": iot_route.get("adapter_kind"),
        "publish_reachable_from_here": "yes" if mlflow_addresses else "no",
        "unauthenticated_only": True,
    }

    if arguments.json_path:
        try:
            with open(arguments.json_path, "w", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=2, default=str)
            print(f"\nreceipt written to {arguments.json_path}")
        except OSError as error:
            print(f"\nWARNING: could not write {arguments.json_path}: {type(error).__name__}: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
