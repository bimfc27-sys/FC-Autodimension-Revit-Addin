"""MediaMTX control-plane client.

MediaMTX pulls RTSP / ONVIF / WHEP / HLS sources from cameras and re-publishes
them to browsers as WebRTC (WHEP) and HLS. CameraScope registers one on-demand
path per (camera, quality). Path names are random-looking (derived from the
camera UUID) and the media ports are never exposed directly: browsers reach
them only through the authenticated /api/media proxy.
"""
from __future__ import annotations

import logging
import uuid

import httpx

from ..config import get_settings

log = logging.getLogger(__name__)


class GatewayError(Exception):
    pass


def path_name(kind: str, camera_id: uuid.UUID, quality: str) -> str:
    return f"{'pub' if kind == 'public' else 'prv'}-{camera_id.hex}-{quality}"


def parse_path_name(name: str) -> tuple[str, uuid.UUID, str] | None:
    try:
        prefix, hexid, quality = name.split("-", 2)
        kind = {"pub": "public", "prv": "private"}[prefix]
        return kind, uuid.UUID(hex=hexid), quality
    except (ValueError, KeyError):
        return None


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=get_settings().mediamtx_api_url, timeout=5.0, trust_env=False)


async def ensure_path(name: str, source_url: str, rtsp_transport: str = "tcp") -> None:
    """Create or replace an on-demand path. The source URL may embed credentials;
    MediaMTX keeps them in memory only (API changes are not written to disk)."""
    conf = {
        "source": source_url,
        "sourceOnDemand": True,
        "sourceOnDemandStartTimeout": "15s",
        "sourceOnDemandCloseAfter": "20s",
    }
    if source_url.startswith(("rtsp://", "rtsps://")):
        conf["rtspTransport"] = rtsp_transport
    try:
        async with _client() as c:
            r = await c.patch(f"/v3/config/paths/patch/{name}", json=conf)
            if r.status_code == 404:
                r = await c.post(f"/v3/config/paths/add/{name}", json=conf)
            if r.status_code >= 400:
                # A changed source cannot always be patched; recreate.
                await c.delete(f"/v3/config/paths/delete/{name}")
                r = await c.post(f"/v3/config/paths/add/{name}", json=conf)
            if r.status_code >= 400:
                raise GatewayError(f"MediaMTX rejected path: {r.status_code} {r.text[:200]}")
    except httpx.HTTPError as exc:
        raise GatewayError(f"Streaming gateway unavailable: {exc.__class__.__name__}") from exc


async def remove_paths(prefix: str) -> None:
    try:
        async with _client() as c:
            r = await c.get("/v3/config/paths/list", params={"itemsPerPage": 1000})
            for item in r.json().get("items", []):
                if item.get("name", "").startswith(prefix):
                    await c.delete(f"/v3/config/paths/delete/{item['name']}")
    except (httpx.HTTPError, ValueError) as exc:  # best effort
        log.warning("could not remove MediaMTX paths %s: %s", prefix, exc)


async def path_state(name: str) -> dict | None:
    try:
        async with _client() as c:
            r = await c.get(f"/v3/paths/get/{name}")
            if r.status_code == 200:
                return r.json()
    except httpx.HTTPError:
        pass
    return None


async def gateway_healthy() -> bool:
    try:
        async with _client() as c:
            r = await c.get("/v3/config/global/get")
            return r.status_code == 200
    except httpx.HTTPError:
        return False
