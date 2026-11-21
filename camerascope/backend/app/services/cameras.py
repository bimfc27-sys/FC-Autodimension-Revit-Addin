"""Business logic shared by the private camera, media and diagnostics routers."""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import PrivateCamera, PublicCamera, User
from ..schemas import DiagnosticStep
from ..security import EncryptedBlob, decrypt_credentials, encrypt_credentials
from . import mediamtx, netguard, rtsp
from .http import build_auth, guarded_request, make_client
from .onvif import OnvifClient, OnvifError
from .sources import (
    mjpeg_source,
    snapshot_source,
    stream_sources,
    with_credentials,
)

MAX_AUTH_FAILURES = 3


async def get_owned_camera(db: AsyncSession, user: User, camera_id: uuid.UUID) -> PrivateCamera:
    cam = await db.get(PrivateCamera, camera_id)
    # 404 (not 403) for cameras of other users: do not leak existence.
    if not cam or cam.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    return cam


def set_credentials(cam: PrivateCamera, username: str, password: str) -> None:
    if not username and not password:
        cam.credentials_ciphertext = cam.credentials_nonce = cam.credentials_key_id = None
        return
    blob = encrypt_credentials({"username": username, "password": password}, cam.id, cam.owner_id)
    cam.credentials_ciphertext, cam.credentials_nonce, cam.credentials_key_id = blob.ciphertext, blob.nonce, blob.key_id


def get_credentials(cam: PrivateCamera) -> tuple[str, str]:
    if not cam.credentials_ciphertext:
        return "", ""
    data = decrypt_credentials(
        EncryptedBlob(cam.credentials_ciphertext, cam.credentials_nonce, cam.credentials_key_id), cam.id, cam.owner_id
    )
    return data.get("username", ""), data.get("password", "")


def ensure_not_locked(cam: PrivateCamera) -> None:
    if cam.auth_failures >= MAX_AUTH_FAILURES:
        raise HTTPException(
            status.HTTP_423_LOCKED,
            "This camera rejected the stored credentials several times. Update the credentials "
            "to unlock it - CameraScope never retries or guesses passwords.",
        )


def record_result(cam: PrivateCamera, ok: bool, error: str | None = None, auth_failed: bool = False) -> None:
    cam.last_checked_at = datetime.now(timezone.utc)
    if ok:
        cam.status, cam.last_error, cam.auth_failures = "online", None, 0
    elif auth_failed:
        cam.status, cam.last_error = "auth_failed", error
        cam.auth_failures += 1
    else:
        cam.status, cam.last_error = "offline", error


async def gateway_path_for_private(cam: PrivateCamera, quality: str) -> str:
    sources = stream_sources(cam)
    if not sources:
        raise HTTPException(status.HTTP_409_CONFLICT, "Camera has no stream configured")
    src = sources.get(quality) or sources["high"]
    q = quality if quality in sources else "high"
    try:
        await netguard.check_url(src.url)
    except netguard.TargetNotAllowed as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Blocked by network policy: {exc}") from exc
    user, pw = get_credentials(cam)
    name = mediamtx.path_name("private", cam.id, q)
    try:
        await mediamtx.ensure_path(name, with_credentials(src.url, user, pw), cam.config.get("rtsp_transport", "tcp"))
    except mediamtx.GatewayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return name


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


def onvif_client(cam_host: str, port: int | None, use_tls: bool, user: str, pw: str) -> OnvifClient:
    return OnvifClient(cam_host, port or (443 if use_tls else 80), user, pw, use_tls)


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
        except Exception as exc:  # diagnostics must never crash
            ok, detail, value = False, f"{exc.__class__.__name__}: {str(exc)[:200]}", None
        self.steps.append(DiagnosticStep(name=name, ok=ok, detail=detail, duration_ms=int((time.perf_counter() - t) * 1000)))
        return ok, value

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps)


async def _policy(url: str):
    t = await netguard.check_url(url)
    scope = "private network" if t.is_private else "public internet"
    return True, f"{t.host} -> {', '.join(t.addresses)} ({scope})", t


async def _tcp(host: str, port: int):
    t = await netguard.check_host(host, port)
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(t.addresses[0], port), 6)
        w.close()
        return True, f"TCP {host}:{port} reachable", None
    except (OSError, asyncio.TimeoutError) as exc:
        return False, f"TCP {host}:{port} unreachable ({exc.__class__.__name__})", None


async def _http_probe(url: str, user: str = "", pw: str = "", expect: str = "any"):
    async with make_client() as client:
        r = await guarded_request(client, "GET", url, auth=build_auth(user, pw), stream=True)
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


async def _gateway(name: str | None):
    healthy = await mediamtx.gateway_healthy()
    if not healthy:
        return False, "Streaming gateway (MediaMTX) API is not reachable", None
    if not name:
        return True, "Streaming gateway healthy", None
    state = await mediamtx.path_state(name) or {}
    return True, f"Gateway path registered (ready={state.get('ready', False)}, readers={len(state.get('readers', []))})", None


async def diagnose_private(cam: PrivateCamera) -> tuple[bool, list[DiagnosticStep], bool]:
    s = _Steps()
    user, pw = get_credentials(cam)
    auth_failed = False
    sources = stream_sources(cam)
    primary = sources.get("high").url if sources.get("high") else mjpeg_source(cam)
    if cam.protocol == "onvif":
        device_url = onvif_client(cam.host, cam.port, cam.use_tls, user, pw).device_url
        primary_check = device_url
    else:
        primary_check = primary
    if not primary_check:
        s.steps.append(DiagnosticStep(name="Configuration", ok=False, detail="No stream path configured", duration_ms=0))
        return False, s.steps, False
    s.steps.append(
        DiagnosticStep(
            name="Configuration",
            ok=True,
            detail=f"{cam.protocol.upper()} via {cam.vendor}; credentials {'stored (encrypted)' if user else 'not set'}",
            duration_ms=0,
        )
    )
    ok, target = await s.run("DNS & network policy", _policy(primary_check))
    if not ok:
        return False, s.steps, False
    port = target.port  # type: ignore[union-attr]
    ok, _ = await s.run("TCP connectivity", _tcp(cam.host, port))
    if not ok:
        return False, s.steps, False

    if cam.protocol == "onvif":
        async def onvif_step():
            client = onvif_client(cam.host, cam.port, cam.use_tls, user, pw)
            try:
                info = await client.get_device_information()
            except OnvifError as exc:
                return False, str(exc), "auth" if exc.auth_failed else None
            return True, f"ONVIF device: {info.get('Manufacturer', '?')} {info.get('Model', '')}".strip(), None

        ok, flag = await s.run("ONVIF handshake", onvif_step())
        auth_failed = flag == "auth"
        if ok and primary:
            async def rtsp_step():
                r = await rtsp.probe(primary, user, pw)
                return r.ok, r.detail + (f" ({', '.join(r.codecs)})" if r.codecs else ""), "auth" if r.auth_failed else None

            ok, flag = await s.run("RTSP stream handshake", rtsp_step())
            auth_failed = auth_failed or flag == "auth"
    elif primary and primary.startswith(("rtsp://", "rtsps://")):
        async def rtsp_step2():
            r = await rtsp.probe(primary, user, pw)
            return r.ok, r.detail + (f" ({', '.join(r.codecs)})" if r.codecs else ""), "auth" if r.auth_failed else None

        ok, flag = await s.run("RTSP handshake", rtsp_step2())
        auth_failed = flag == "auth"
    elif cam.protocol == "hls":
        ok, flag = await s.run("HLS playlist", _http_probe(primary, user, pw, "hls"))
        auth_failed = flag == "auth"
    elif cam.protocol == "mjpeg":
        ok, flag = await s.run("MJPEG stream", _http_probe(primary, user, pw, "image"))
        auth_failed = flag == "auth"
    elif cam.protocol == "webrtc":
        s.steps.append(DiagnosticStep(name="WHEP endpoint", ok=True, detail="Endpoint reachable; negotiated by gateway on play", duration_ms=0))

    if cam.protocol != "mjpeg":
        await s.run("Streaming gateway", _gateway(mediamtx.path_name("private", cam.id, "high")))
    snap = snapshot_source(cam)
    if snap and cam.protocol in ("vendor_api",) and not auth_failed:
        await s.run("Snapshot endpoint", _http_probe(snap, user, pw, "image"))
    return s.ok, s.steps, auth_failed


async def diagnose_public(cam: PublicCamera) -> tuple[bool, list[DiagnosticStep]]:
    s = _Steps()
    url = cam.stream_url or cam.snapshot_url or cam.page_url
    s.steps.append(
        DiagnosticStep(name="Source", ok=True, detail=f"{cam.source_name} - {cam.stream_type}", duration_ms=0)
    )
    if not url:
        return False, s.steps
    ok, _ = await s.run("DNS & network policy", _policy(url))
    if not ok:
        return False, s.steps
    if cam.stream_type == "hls":
        await s.run("HLS playlist", _http_probe(url, expect="hls"))
    elif cam.stream_type in ("image", "mjpeg"):
        await s.run("Image endpoint", _http_probe(url, expect="image"))
    elif cam.stream_type == "gateway":
        await s.run("Streaming gateway", _gateway(mediamtx.path_name("public", cam.id, "high")))
    else:
        await s.run("Web page", _http_probe(url))
    return s.ok, s.steps
