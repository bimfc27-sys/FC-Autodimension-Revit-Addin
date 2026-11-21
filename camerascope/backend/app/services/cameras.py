"""Business logic shared by the private camera, device, media and diagnostics routers."""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Device, Gateway, PrivateCamera, PublicCamera, SiteMember, User
from ..schemas import DiagnosticStep
from ..security import EncryptedBlob, decrypt_credentials, encrypt_credentials
from . import mediamtx, netguard, rtsp
from .gateway_hub import GatewayOffline, hub
from .http import build_auth, guarded_request, make_client
from .onvif import OnvifClient, OnvifError
from .routing import Route, relay_source
from .sources import mjpeg_source, snapshot_source, stream_sources, with_credentials

MAX_AUTH_FAILURES = 3


# ------------------------------------------------------------- access control


async def get_owned_camera(db: AsyncSession, user: User, camera_id: uuid.UUID) -> PrivateCamera:
    """Cameras the user OWNS (may edit, delete, see connection settings)."""
    cam = await db.get(PrivateCamera, camera_id)
    # 404 (not 403) for cameras of other users: do not leak existence.
    if not cam or cam.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    return cam


async def viewer_site_ids(db: AsyncSession, user: User) -> set[uuid.UUID]:
    return set((await db.scalars(select(SiteMember.site_id).where(SiteMember.user_id == user.id))).all())


async def get_viewable_camera(db: AsyncSession, user: User, camera_id: uuid.UUID) -> PrivateCamera:
    """Cameras the user owns, or that were explicitly shared with them through a site (building)."""
    cam = await db.get(PrivateCamera, camera_id)
    if cam and cam.owner_id == user.id:
        return cam
    if cam and cam.site_id and await db.get(SiteMember, (cam.site_id, user.id)):
        return cam
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")


async def can_view(db: AsyncSession, user: User, cam: PrivateCamera) -> bool:
    if cam.owner_id == user.id:
        return True
    return bool(cam.site_id and await db.get(SiteMember, (cam.site_id, user.id)))


# ------------------------------------------------------------------ secrets


def _set_creds(obj, username: str, password: str) -> None:
    if not username and not password:
        obj.credentials_ciphertext = obj.credentials_nonce = obj.credentials_key_id = None
        return
    blob = encrypt_credentials({"username": username, "password": password}, obj.id, obj.owner_id)
    obj.credentials_ciphertext, obj.credentials_nonce, obj.credentials_key_id = blob.ciphertext, blob.nonce, blob.key_id


def _get_creds(obj) -> tuple[str, str]:
    if not obj.credentials_ciphertext:
        return "", ""
    data = decrypt_credentials(
        EncryptedBlob(obj.credentials_ciphertext, obj.credentials_nonce, obj.credentials_key_id), obj.id, obj.owner_id
    )
    return data.get("username", ""), data.get("password", "")


set_credentials = _set_creds
set_device_credentials = _set_creds
get_device_credentials = _get_creds


def get_credentials(cam: PrivateCamera) -> tuple[str, str]:
    """Credentials stored on the camera itself (not inherited)."""
    return _get_creds(cam)


async def camera_credentials(db: AsyncSession, cam: PrivateCamera) -> tuple[str, str]:
    """Credentials to use for a camera; NVR channels inherit the recorder's credentials."""
    if cam.credentials_ciphertext or not cam.device_id:
        return _get_creds(cam)
    dev = await db.get(Device, cam.device_id)
    return _get_creds(dev) if dev else ("", "")


def ensure_not_locked(cam) -> None:
    if cam.auth_failures >= MAX_AUTH_FAILURES:
        raise HTTPException(
            status.HTTP_423_LOCKED,
            "This device rejected the stored credentials several times. Update the credentials "
            "to unlock it - CameraScope never retries or guesses passwords.",
        )


def record_result(cam, ok: bool, error: str | None = None, auth_failed: bool = False) -> None:
    cam.last_checked_at = datetime.now(timezone.utc)
    if ok:
        cam.status, cam.last_error, cam.auth_failures = "online", None, 0
    elif auth_failed:
        cam.status, cam.last_error = "auth_failed", error
        cam.auth_failures += 1
    else:
        cam.status, cam.last_error = "offline", error


def camera_status(cam: PrivateCamera) -> str:
    """One clear status per camera, measured - never inferred from location.

    online | offline | auth_failed | gateway_offline | unknown
    """
    if cam.gateway_id and not hub.is_online(cam.gateway_id):
        return "gateway_offline"
    if cam.auth_failures >= MAX_AUTH_FAILURES or cam.status == "auth_failed":
        return "auth_failed"
    if cam.reachable is False:
        return "offline"
    if cam.status == "offline" and cam.reachable is not True:
        return "offline"
    if cam.reachable or cam.status == "online":
        return "online"
    return "unknown"


def route_for(obj) -> Route:
    return Route(obj.gateway_id)


# ------------------------------------------------------------------ streaming


async def gateway_path_for_private(db: AsyncSession, cam: PrivateCamera, quality: str) -> str:
    sources = stream_sources(cam)
    if not sources:
        raise HTTPException(status.HTTP_409_CONFLICT, "Camera has no stream configured")
    q = quality if quality in sources else "high"
    src = sources[q]
    user, pw = await camera_credentials(db, cam)
    name = mediamtx.path_name("private", cam.id, q)
    source_with_creds = with_credentials(src.url, user, pw)
    try:
        if cam.gateway_id:
            # 1) the camera's own gateway pulls it on its LAN (validated against its allow-list)
            conn = hub.get(cam.gateway_id)
            await conn.call(
                "stream.ensure",
                {"name": name, "source": source_with_creds, "rtsp_transport": (cam.config or {}).get("rtsp_transport", "automatic")},
            )
            # 2) the cloud gateway pulls the gateway's relay through the encrypted tunnel
            await mediamtx.ensure_path(name, await relay_source(cam.gateway_id, name), "tcp")
        else:
            try:
                await netguard.check_url(src.url)
            except netguard.TargetNotAllowed as exc:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Blocked by network policy: {exc}") from exc
            await mediamtx.ensure_path(name, source_with_creds, (cam.config or {}).get("rtsp_transport", "tcp"))
    except GatewayOffline as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"{exc}. Viewing resumes automatically when it reconnects.") from exc
    except mediamtx.GatewayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    except RuntimeError as exc:  # refused / failed on the gateway
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Gateway: {exc}") from exc
    return name


async def remove_streams(cam: PrivateCamera) -> None:
    prefix = mediamtx.path_name("private", cam.id, "")
    await mediamtx.remove_paths(prefix)
    if cam.gateway_id and hub.is_online(cam.gateway_id):
        try:
            await hub.get(cam.gateway_id).call("stream.remove", {"prefix": prefix}, timeout=5)
        except Exception:
            pass


async def gateway_path_for_public(cam: PublicCamera) -> str:
    if not cam.stream_url:
        raise HTTPException(status.HTTP_409_CONFLICT, "No relayable stream")
    try:
        await netguard.check_url(cam.stream_url)
    except netguard.TargetNotAllowed as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Blocked by network policy: {exc}") from exc
    name = mediamtx.path_name("public", cam.id, "high")
    try:
        await mediamtx.ensure_path(name, cam.stream_url)
    except mediamtx.GatewayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return name


def onvif_client(host: str, port: int | None, use_tls: bool, user: str, pw: str, route: Route | None = None) -> OnvifClient:
    route = route or Route(None)
    return OnvifClient(
        host, port or (443 if use_tls else 80), user, pw, use_tls,
        url_map=route.url_mapper if route.via_gateway else None, trusted=route.trusted,
    )


async def http_get(route: Route, url: str, user: str, pw: str, timeout: float = 12):
    """GET through the right route. Returns the httpx response (body loaded)."""
    mapped = await route.map_url(url)
    async with make_client(timeout, verify=not route.trusted) as client:
        return await guarded_request(client, "GET", mapped, auth=build_auth(user, pw), trusted=route.trusted)


# ------------------------------------------------------------------ diagnostics


class _Steps:
    def __init__(self) -> None:
        self.steps: list[DiagnosticStep] = []

    async def run(self, name: str, coro) -> tuple[bool, object]:
        t = time.perf_counter()
        try:
            ok, detail, value = await coro
        except netguard.TargetNotAllowed as exc:
            ok, detail, value = False, f"Blocked by network policy: {exc}", None
        except GatewayOffline as exc:
            ok, detail, value = False, str(exc), None
        except Exception as exc:  # diagnostics must never crash
            ok, detail, value = False, f"{exc.__class__.__name__}: {str(exc)[:200]}", None
        self.steps.append(DiagnosticStep(name=name, ok=ok, detail=detail, duration_ms=int((time.perf_counter() - t) * 1000)))
        return ok, value

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.steps.append(DiagnosticStep(name=name, ok=ok, detail=detail, duration_ms=0))

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps)


async def _policy(url: str):
    t = await netguard.check_url(url)
    scope = "private network (VPN/LAN route)" if t.is_private else "public internet"
    return True, f"{t.host} -> {', '.join(t.addresses)} ({scope})", t


async def _tcp_direct(host: str, port: int):
    t = await netguard.check_host(host, port)
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(t.addresses[0], port), 6)
        w.close()
        return True, f"TCP {host}:{port} reachable from the cloud", None
    except (OSError, asyncio.TimeoutError) as exc:
        return False, f"TCP {host}:{port} unreachable from the cloud ({exc.__class__.__name__})", None


async def _tcp_via_gateway(gateway_id: uuid.UUID, host: str, port: int):
    res = await hub.get(gateway_id).call("check", {"targets": [{"id": "t", "host": host, "port": port}]}, timeout=10)
    r = (res or {}).get("t", {})
    if r.get("refused"):
        return False, f"Gateway refused: {r['refused']} (adjust ALLOWED_SUBNETS / ALLOWED_PORTS on the gateway)", None
    if r.get("reachable"):
        return True, f"TCP {host}:{port} reachable from the gateway", None
    return False, f"TCP {host}:{port} unreachable from the gateway", None


async def _http_probe(route: Route, url: str, user: str = "", pw: str = "", expect: str = "any"):
    mapped = await route.map_url(url)
    async with make_client(verify=not route.trusted) as client:
        r = await guarded_request(client, "GET", mapped, auth=build_auth(user, pw), stream=True, trusted=route.trusted)
        try:
            ctype = r.headers.get("content-type", "")
            head = b""
            async for chunk in r.aiter_bytes():
                head += chunk
                if len(head) > 2048:
                    break
        finally:
            await r.aclose()
    if r.status_code in (401, 403):
        return False, f"HTTP {r.status_code}: credentials rejected", "auth"
    if r.status_code >= 400:
        return False, f"HTTP {r.status_code}", None
    if expect == "hls" and b"#EXTM3U" not in head:
        return False, f"HTTP {r.status_code} but response is not an HLS playlist ({ctype})", None
    if expect == "image" and not (ctype.startswith("image/") or ctype.startswith("multipart/")):
        return False, f"HTTP {r.status_code} but content-type is {ctype or 'unknown'}", None
    return True, f"HTTP {r.status_code} {ctype}".strip(), None


async def _rtsp(route: Route, url: str, user: str, pw: str):
    r = await rtsp.probe(url, user, pw, connect_to=await route.connect_to(url))
    return r.ok, r.detail + (f" ({', '.join(r.codecs)})" if r.codecs else ""), "auth" if r.auth_failed else None


async def _gateway_path(cam: PrivateCamera):
    name = mediamtx.path_name("private", cam.id, "high")
    parts = []
    if cam.gateway_id:
        st = await hub.get(cam.gateway_id).call("stream.state", {"name": name}, timeout=8)
        parts.append(f"site gateway path: {'ready' if st.get('ready') else 'idle (starts on demand)' if st.get('exists') else 'not created yet'}")
    if not await mediamtx.gateway_healthy():
        return False, "Cloud streaming gateway (MediaMTX) API is not reachable", None
    state = await mediamtx.path_state(name) or {}
    parts.append(f"cloud path: {'ready' if state.get('ready') else 'idle'}; viewers: {len(state.get('readers', []))}")
    return True, "; ".join(parts), None


async def diagnose_private(db: AsyncSession, cam: PrivateCamera) -> tuple[bool, list[DiagnosticStep], bool]:
    s = _Steps()
    route = route_for(cam)
    user, pw = await camera_credentials(db, cam)
    auth_failed = False
    sources = stream_sources(cam)
    primary = sources["high"].url if sources.get("high") else mjpeg_source(cam)
    if cam.protocol == "onvif" and not cam.device_id:
        check_url = onvif_client(cam.host, cam.port, cam.use_tls, user, pw).device_url
    else:
        check_url = primary
    if not check_url:
        s.add("Configuration", False, "No stream path configured")
        return False, s.steps, False

    gw_name = None
    if cam.gateway_id:
        g = await db.get(Gateway, cam.gateway_id)
        gw_name = g.name if g else str(cam.gateway_id)
    via = f"via gateway “{gw_name}”" if gw_name else "direct from the cloud (public address or VPN route)"
    s.add(
        "Configuration",
        True,
        f"{cam.protocol.upper()} ({cam.vendor}) {via}; credentials "
        f"{'stored encrypted' + (' (inherited from recorder)' if cam.device_id and not cam.credentials_ciphertext else '') if user else 'not set'}",
    )
    from urllib.parse import urlsplit

    from .netguard import DEFAULT_PORTS

    p = urlsplit(check_url)
    port = p.port or DEFAULT_PORTS.get(p.scheme, 80)
    if cam.gateway_id:
        online = hub.is_online(cam.gateway_id)
        s.add("Gateway tunnel", online, f"“{gw_name}” is {'connected' if online else 'OFFLINE - check the gateway device and its internet connection'}")
        if not online:
            return False, s.steps, False
        ok, _ = await s.run("Reachability (gateway allow-list + TCP)", _tcp_via_gateway(cam.gateway_id, cam.host, port))
    else:
        ok, _ = await s.run("DNS & network policy", _policy(check_url))
        if not ok:
            return False, s.steps, False
        ok, _ = await s.run("TCP connectivity", _tcp_direct(cam.host, port))
    cam.reachable, cam.reachable_checked_at = ok, datetime.now(timezone.utc)
    if not ok:
        return False, s.steps, False

    if cam.protocol == "onvif" and not cam.device_id:
        async def onvif_step():
            client = onvif_client(cam.host, cam.port, cam.use_tls, user, pw, route)
            try:
                info = await client.get_device_information()
            except OnvifError as exc:
                return False, str(exc), "auth" if exc.auth_failed else None
            return True, f"ONVIF device: {info.get('Manufacturer', '?')} {info.get('Model', '')}".strip(), None

        ok, flag = await s.run("ONVIF handshake", onvif_step())
        auth_failed = flag == "auth"
    if not auth_failed and primary and primary.startswith(("rtsp://", "rtsps://")):
        ok, flag = await s.run("RTSP stream handshake", _rtsp(route, primary, user, pw))
        auth_failed = auth_failed or flag == "auth"
    elif not auth_failed and cam.protocol == "hls":
        ok, flag = await s.run("HLS playlist", _http_probe(route, primary, user, pw, "hls"))
        auth_failed = flag == "auth"
    elif cam.protocol == "mjpeg":
        ok, flag = await s.run("MJPEG stream", _http_probe(route, primary, user, pw, "image"))
        auth_failed = flag == "auth"
    elif cam.protocol == "webrtc":
        s.add("WHEP endpoint", True, "TCP reachable; WebRTC is negotiated by the gateway on play")

    if cam.protocol != "mjpeg" and not auth_failed:
        await s.run("Streaming gateways", _gateway_path(cam))
    return s.ok, s.steps, auth_failed


async def diagnose_public(cam: PublicCamera) -> tuple[bool, list[DiagnosticStep]]:
    s = _Steps()
    url = cam.stream_url or cam.snapshot_url or cam.page_url
    s.add("Source", True, f"{cam.source_name} - {cam.stream_type}")
    if not url:
        return False, s.steps
    ok, _ = await s.run("DNS & network policy", _policy(url))
    if not ok:
        return False, s.steps
    direct = Route(None)
    if cam.stream_type == "hls":
        await s.run("HLS playlist", _http_probe(direct, url, expect="hls"))
    elif cam.stream_type in ("image", "mjpeg"):
        await s.run("Image endpoint", _http_probe(direct, url, expect="image"))
    elif cam.stream_type == "gateway":
        async def gw():
            ok = await mediamtx.gateway_healthy()
            return ok, "Cloud streaming gateway healthy" if ok else "Cloud streaming gateway unreachable", None

        await s.run("Streaming gateway", gw())
    else:
        await s.run("Web page", _http_probe(direct, url))
    return s.ok, s.steps


# ------------------------------------------------------------ status monitor


async def _check_direct(host: str, port: int) -> bool | None:
    try:
        t = await netguard.check_host(host, port)
    except netguard.TargetNotAllowed:
        return None
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(t.addresses[0], port), 4)
        w.close()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


def _check_target(obj) -> tuple[str, int]:
    """Host/port whose TCP reachability represents the camera (no credentials used)."""
    from urllib.parse import urlsplit

    from .netguard import DEFAULT_PORTS

    if isinstance(obj, PrivateCamera):
        src = stream_sources(obj).get("high")
        url = src.url if src else mjpeg_source(obj)
        if obj.protocol == "onvif" and not url:
            return obj.host, obj.port or 80
        if url:
            p = urlsplit(url)
            return obj.host, p.port or DEFAULT_PORTS.get(p.scheme, 80)
    return obj.host, obj.port or 80


async def refresh_reachability(db: AsyncSession, camera_ids: set[uuid.UUID] | None = None) -> None:
    """Periodic, credential-free TCP checks from the place each camera is reached from."""
    now = datetime.now(timezone.utc)
    q = select(PrivateCamera)
    if camera_ids is not None:
        q = q.where(PrivateCamera.id.in_(camera_ids))
    cams = (await db.scalars(q)).all()
    devs = [] if camera_ids is not None else (await db.scalars(select(Device))).all()
    by_gw: dict[uuid.UUID | None, list] = {}
    for o in [*cams, *devs]:
        by_gw.setdefault(o.gateway_id, []).append(o)
    for gw_id, items in by_gw.items():
        if gw_id is None:
            results = await asyncio.gather(*(_check_direct(*_check_target(o)) for o in items))
            for o, r in zip(items, results):
                _apply(o, r, now)
            continue
        if not hub.is_online(gw_id):
            for o in items:
                _apply(o, None, now)
            continue
        targets = [{"id": str(o.id), "host": _check_target(o)[0], "port": _check_target(o)[1]} for o in items]
        try:
            res = await hub.get(gw_id).call("check", {"targets": targets}, timeout=30)
        except Exception:
            continue
        for o in items:
            r = (res or {}).get(str(o.id), {})
            _apply(o, bool(r.get("reachable")), now)
    await db.commit()


def _apply(obj, reachable: bool | None, now: datetime) -> None:
    if isinstance(obj, PrivateCamera):
        obj.reachable, obj.reachable_checked_at = reachable, now
    else:
        if reachable is None:
            obj.status = "gateway_offline" if obj.gateway_id else "unknown"
        elif obj.status != "auth_failed":
            obj.status = "online" if reachable else "offline"


def check_soon(camera_ids: set[uuid.UUID]) -> None:
    """Measure reachability of newly added cameras right away (in the background)."""
    from ..db import sessionmaker

    async def run():
        try:
            async with sessionmaker()() as db:
                await refresh_reachability(db, camera_ids)
        except Exception:
            pass

    task = asyncio.create_task(run())
    _background.add(task)
    task.add_done_callback(_background.discard)


_background: set[asyncio.Task] = set()


async def status_monitor(stop: asyncio.Event) -> None:
    from ..config import get_settings
    from ..db import sessionmaker

    import logging

    log = logging.getLogger(__name__)
    interval = get_settings().status_check_interval_seconds
    await asyncio.sleep(5)
    while not stop.is_set():
        try:
            async with sessionmaker()() as db:
                await refresh_reachability(db)
        except Exception as exc:  # keep monitoring
            log.warning("status monitor: %s", exc)
        try:
            await asyncio.wait_for(stop.wait(), interval)
        except asyncio.TimeoutError:
            pass
