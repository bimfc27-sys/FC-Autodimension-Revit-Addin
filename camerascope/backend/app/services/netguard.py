"""Outbound request guard (SSRF protection).

Every outbound connection the backend makes on behalf of a user - camera probes,
image proxying, ONVIF calls, provider downloads - is validated here first.

* Only expected schemes/ports are allowed.
* Hostnames are resolved and EVERY resolved address is checked, preventing
  DNS-based bypasses (a name that resolves to 169.254.169.254, etc.).
* Cloud metadata, loopback, link-local, multicast and reserved ranges are
  always refused.
* Internal infrastructure hostnames (db, mediamtx, ...) are always refused.
* RFC1918/ULA private ranges are refused unless the operator explicitly allows
  LAN cameras (self-hosted installs).
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..config import get_settings

ALLOWED_SCHEMES = {"http", "https", "rtsp", "rtsps", "whep", "wheps"}

_ALWAYS_BLOCKED_NETS = [
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",  # link-local incl. cloud metadata 169.254.169.254
        "100.100.100.200/32",  # alibaba metadata
        "224.0.0.0/4",
        "240.0.0.0/4",
        "255.255.255.255/32",
        "::/128",
        "::1/128",
        "fe80::/10",
        "ff00::/8",
        "fd00:ec2::254/128",  # AWS IPv6 metadata
    )
]

_PRIVATE_NETS = [
    ipaddress.ip_network(n)
    for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7")
]


class TargetNotAllowed(Exception):
    pass


@dataclass
class CheckedTarget:
    host: str
    port: int
    addresses: list[str]
    is_private: bool


async def _resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise TargetNotAllowed(f"DNS resolution failed for {host}: {exc.strerror}") from exc
    return sorted({info[4][0] for info in infos})


_deny_ip_cache: set[str] | None = None


async def _denied_infra_ips() -> set[str]:
    global _deny_ip_cache
    if _deny_ip_cache is None:
        ips: set[str] = set()
        for h in get_settings().deny_hosts:
            try:
                ips.update(await _resolve(h, 80))
            except TargetNotAllowed:
                pass
        _deny_ip_cache = ips
    return _deny_ip_cache


def _classify(addr: str) -> tuple[bool, bool]:
    """Return (always_blocked, is_private)."""
    ip = ipaddress.ip_address(addr)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if any(ip in n for n in _ALWAYS_BLOCKED_NETS):
        return True, False
    return False, any(ip in n for n in _PRIVATE_NETS)


async def check_host(host: str, port: int) -> CheckedTarget:
    s = get_settings()
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        raise TargetNotAllowed("empty host")
    if not (1 <= port <= 65535):
        raise TargetNotAllowed("invalid port")
    if host in s.deny_hosts or host.endswith(".internal") or host.endswith(".localhost"):
        raise TargetNotAllowed("target host is reserved for internal infrastructure")
    addrs = await _resolve(host, port)
    denied = await _denied_infra_ips()
    any_private = False
    for a in addrs:
        blocked, private = _classify(a)
        if blocked:
            raise TargetNotAllowed(f"{host} resolves to a blocked address range ({a})")
        if a in denied:
            raise TargetNotAllowed("target resolves to internal infrastructure")
        if private and not s.allow_private_network_targets:
            raise TargetNotAllowed(
                f"{host} resolves to a private network address ({a}); private targets are disabled"
            )
        any_private = any_private or private
    return CheckedTarget(host, port, addrs, any_private)


DEFAULT_PORTS = {"http": 80, "https": 443, "rtsp": 554, "rtsps": 322, "whep": 80, "wheps": 443}


async def check_url(url: str) -> CheckedTarget:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise TargetNotAllowed(f"scheme {scheme!r} is not allowed")
    if not parts.hostname:
        raise TargetNotAllowed("URL has no host")
    return await check_host(parts.hostname, parts.port or DEFAULT_PORTS[scheme])
