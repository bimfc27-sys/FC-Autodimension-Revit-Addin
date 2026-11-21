"""How the cloud reaches a camera or recorder: directly, or through its gateway tunnel.

Direct ("cloud") cameras must be reachable from the cloud itself - a public
address, port-forward, or a VPN route (WireGuard/Tailscale) - and are checked by
the cloud's SSRF guard. Gateway cameras are reached only via their gateway's
tunnel; the gateway enforces its own subnet/port allow-list.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from urllib.parse import quote, urlsplit, urlunsplit

from ..config import get_settings
from .gateway_hub import hub
from .netguard import DEFAULT_PORTS


@dataclass
class Route:
    gateway_id: uuid.UUID | None

    @property
    def via_gateway(self) -> bool:
        return self.gateway_id is not None

    @property
    def trusted(self) -> bool:
        return self.via_gateway

    async def map_url(self, url: str, for_mediamtx: bool = False) -> str:
        """Rewrite ``url`` so the cloud (or the cloud MediaMTX) can open it."""
        if not self.gateway_id:
            return url
        p = urlsplit(url)
        port = p.port or DEFAULT_PORTS.get(p.scheme, 80)
        host = p.hostname or ""
        target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        fw = await hub.forwarder(self.gateway_id, target)
        local = get_settings().backend_internal_host if for_mediamtx else "127.0.0.1"
        userinfo = ""
        if p.username:
            userinfo = quote(p.username, safe="%") + (":" + quote(p.password or "", safe="%") if p.password is not None else "") + "@"
        # http(s) through the tunnel: TLS (if any) is between the cloud and the camera; the
        # name in the certificate cannot match the forwarder address, see SECURITY.md.
        return urlunsplit((p.scheme, f"{userinfo}{local}:{fw.port}", p.path, p.query, p.fragment))

    async def connect_to(self, url: str) -> tuple[str, int] | None:
        """TCP destination for raw-socket clients (RTSP probe)."""
        if not self.gateway_id:
            return None
        mapped = urlsplit(await self.map_url(url))
        return mapped.hostname or "127.0.0.1", mapped.port or 0

    async def url_mapper(self, url: str) -> str:
        return await self.map_url(url)


async def relay_source(gateway_id: uuid.UUID, path_name: str) -> str:
    """RTSP URL the CLOUD MediaMTX pulls from: the gateway's local MediaMTX, through the tunnel."""
    conn = hub.get(gateway_id)
    relay = conn.hello.get("relay") or {}
    fw = await hub.forwarder(gateway_id, "@mediamtx-rtsp")
    user = quote(str(relay.get("user", "")), safe="")
    pw = quote(str(relay.get("pass", "")), safe="")
    return f"rtsp://{user}:{pw}@{get_settings().backend_internal_host}:{fw.port}/{path_name}"
