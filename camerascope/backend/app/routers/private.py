"""Private cameras: devices the user owns or is explicitly authorised to access."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import audit, current_user
from ..models import PrivateCamera, User
from ..schemas import (
    OnvifProbeIn,
    Playback,
    PrivateCameraIn,
    PrivateCameraOut,
    PrivateCameraUpdate,
    QualityOption,
)
from ..services import cameras as svc
from ..services import mediamtx, netguard
from ..services.http import build_auth, guarded_request, make_client
from ..services.onvif import OnvifClient, OnvifError
from ..services.ratelimit import limiter
from ..services.sources import VENDOR_PRESETS, mjpeg_source, reolink_snapshot, snapshot_source, stream_sources, strip_userinfo
from .public import MAX_IMAGE_BYTES, proxy_mjpeg

router = APIRouter(prefix="/api/cameras", tags=["private cameras"])


def _out(cam: PrivateCamera) -> PrivateCameraOut:
    username = None
    if cam.credentials_ciphertext:
        try:
            username = svc.get_credentials(cam)[0] or None
        except Exception:
            username = None
    return PrivateCameraOut(
        id=cam.id,
        name=cam.name,
        description=cam.description,
        protocol=cam.protocol,
        vendor=cam.vendor,
        host=cam.host,
        port=cam.port,
        use_tls=cam.use_tls,
        config=cam.config or {},
        latitude=cam.latitude,
        longitude=cam.longitude,
        has_credentials=bool(cam.credentials_ciphertext),
        username=username,
        authorization_basis=cam.authorization_basis,
        authorization_attested_at=cam.authorization_attested_at,
        status=cam.status,
        last_error=cam.last_error,
        last_checked_at=cam.last_checked_at,
        created_at=cam.created_at,
    )


@router.get("/vendors")
async def vendors():
    """Supported manufacturer presets (documented official endpoints)."""
    return {k: {kk: vv for kk, vv in v.items()} for k, v in VENDOR_PRESETS.items()}


@router.get("", response_model=list[PrivateCameraOut])
async def list_cameras(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    rows = (await db.scalars(select(PrivateCamera).where(PrivateCamera.owner_id == user.id).order_by(PrivateCamera.name))).all()
    return [_out(c) for c in rows]


@router.post("", response_model=PrivateCameraOut, status_code=201)
async def create_camera(body: PrivateCameraIn, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    limiter.hit(f"cam-create:{user.id}", 30, 3600)
    try:
        await netguard.check_host(body.host, body.port or 80)
    except netguard.TargetNotAllowed as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Blocked by network policy: {exc}") from exc
    cam = PrivateCamera(
        id=uuid.uuid4(),
        owner_id=user.id,
        name=body.name,
        description=body.description,
        protocol=body.protocol,
        vendor=body.vendor,
        host=body.host,
        port=body.port,
        use_tls=body.use_tls,
        config=body.config.model_dump(),
        latitude=body.latitude,
        longitude=body.longitude,
        authorization_basis=body.authorization_basis,
        authorization_attested_at=datetime.now(timezone.utc),
    )
    if body.credentials:
        svc.set_credentials(cam, body.credentials.username, body.credentials.password)
    db.add(cam)
    await audit(db, request, user.id, "camera.create", str(cam.id), protocol=cam.protocol, basis=cam.authorization_basis)
    await db.commit()
    return _out(cam)


@router.post("/onvif/probe")
async def onvif_probe(body: OnvifProbeIn, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    """Query an ONVIF device with the owner's credentials to list its media profiles."""
    limiter.hit(f"onvif-probe:{user.id}", 10, 600)
    limiter.hit(f"onvif-probe-host:{user.id}:{body.host}", 3, 600)  # no rapid-fire credential attempts
    client = OnvifClient(body.host, body.port, body.credentials.username, body.credentials.password, body.use_tls)
    await audit(db, request, user.id, "camera.onvif_probe", body.host)
    await db.commit()
    try:
        dev = await client.discover()
    except netguard.TargetNotAllowed as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Blocked by network policy: {exc}") from exc
    except OnvifError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED if exc.auth_failed else status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, f"Device unreachable: {exc.__class__.__name__}") from exc
    return {
        "manufacturer": dev.manufacturer,
        "model": dev.model,
        "firmware": dev.firmware,
        "profiles": [
            {
                "token": p.token,
                "name": p.name,
                "encoding": p.encoding,
                "width": p.width,
                "height": p.height,
                "stream_uri": strip_userinfo(p.stream_uri) if p.stream_uri else None,
                "snapshot_uri": strip_userinfo(p.snapshot_uri) if p.snapshot_uri else None,
            }
            for p in dev.profiles
        ],
    }


@router.get("/{camera_id}", response_model=PrivateCameraOut)
async def get_camera(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    return _out(await svc.get_owned_camera(db, user, camera_id))


@router.patch("/{camera_id}", response_model=PrivateCameraOut)
async def update_camera(
    camera_id: uuid.UUID, body: PrivateCameraUpdate, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)
):
    cam = await svc.get_owned_camera(db, user, camera_id)
    data = body.model_dump(exclude_unset=True)
    if "host" in data and data["host"]:
        try:
            await netguard.check_host(data["host"], data.get("port") or cam.port or 80)
        except netguard.TargetNotAllowed as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Blocked by network policy: {exc}") from exc
    for f in ("name", "description", "host", "port", "use_tls", "latitude", "longitude"):
        if f in data:
            setattr(cam, f, data[f])
    if body.config is not None:
        cam.config = body.config.model_dump()
    if body.credentials is not None:
        svc.set_credentials(cam, body.credentials.username, body.credentials.password)
        cam.auth_failures = 0  # new credentials from the owner unlock the camera
        cam.status = "unknown"
    await mediamtx.remove_paths(mediamtx.path_name("private", cam.id, ""))
    await audit(db, request, user.id, "camera.update", str(cam.id), fields=sorted(data.keys()))
    await db.commit()
    return _out(cam)


@router.delete("/{camera_id}", status_code=204)
async def delete_camera(camera_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    cam = await svc.get_owned_camera(db, user, camera_id)
    await mediamtx.remove_paths(mediamtx.path_name("private", cam.id, ""))
    await db.delete(cam)
    await audit(db, request, user.id, "camera.delete", str(camera_id))
    await db.commit()


@router.post("/{camera_id}/onvif/refresh", response_model=PrivateCameraOut)
async def onvif_refresh(camera_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    """Re-resolve stream URIs from the device (after firmware/profile changes)."""
    cam = await svc.get_owned_camera(db, user, camera_id)
    if cam.protocol != "onvif":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not an ONVIF camera")
    svc.ensure_not_locked(cam)
    limiter.hit(f"onvif-refresh:{cam.id}", 5, 600)
    u, p = svc.get_credentials(cam)
    client = svc.onvif_client(cam.host, cam.port, cam.use_tls, u, p)
    try:
        dev = await client.discover()
    except OnvifError as exc:
        svc.record_result(cam, False, str(exc), exc.auth_failed)
        await db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    except (netguard.TargetNotAllowed, httpx.HTTPError) as exc:
        svc.record_result(cam, False, str(exc))
        await db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Device unreachable: {exc}") from exc
    cfg = dict(cam.config or {})
    by_token = {pr.token: pr for pr in dev.profiles}
    main = by_token.get(cfg.get("onvif_main_profile") or "") or (dev.profiles[0] if dev.profiles else None)
    sub = by_token.get(cfg.get("onvif_sub_profile") or "") or (dev.profiles[1] if len(dev.profiles) > 1 else None)
    if main:
        cfg["onvif_main_profile"], cfg["resolved_main_uri"] = main.token, main.stream_uri and strip_userinfo(main.stream_uri)
        cfg["resolved_snapshot_uri"] = main.snapshot_uri and strip_userinfo(main.snapshot_uri)
    if sub:
        cfg["onvif_sub_profile"], cfg["resolved_sub_uri"] = sub.token, sub.stream_uri and strip_userinfo(sub.stream_uri)
    cam.config = cfg
    svc.record_result(cam, True)
    await audit(db, request, user.id, "camera.onvif_refresh", str(cam.id))
    await db.commit()
    return _out(cam)


@router.get("/{camera_id}/playback", response_model=Playback)
async def playback(
    camera_id: uuid.UUID,
    request: Request,
    quality: str = Query("high", pattern="^(high|low)$"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    cam = await svc.get_owned_camera(db, user, camera_id)
    svc.ensure_not_locked(cam)
    limiter.hit(f"playback:{user.id}", 60, 60)
    snap_ok = bool((cam.config or {}).get("snapshot_allowed", True))
    pb = Playback(
        kind="private",
        camera_id=cam.id,
        mode="gateway",
        snapshot_allowed=snap_ok,
        snapshot_url=f"/api/cameras/{cam.id}/snapshot" if snap_ok and snapshot_source(cam) else None,
        quality=quality,
    )
    if cam.protocol == "mjpeg":
        if not mjpeg_source(cam):
            raise HTTPException(status.HTTP_409_CONFLICT, "Camera has no MJPEG path configured")
        pb.mode, pb.image_url = "mjpeg", f"/api/cameras/{cam.id}/mjpeg"
        pb.qualities = [QualityOption(id="high", label="Source")]
    else:
        sources = stream_sources(cam)
        pb.qualities = [QualityOption(id=k, label={"high": "High (main stream)", "low": "Low (sub stream)"}[k]) for k in sources]
        name = await svc.gateway_path_for_private(cam, quality)
        pb.quality = name.rsplit("-", 1)[1]
        pb.whep_url = f"/api/media/{name}/whep"
        pb.hls_url = f"/api/media/{name}/hls/index.m3u8"
    await audit(db, request, user.id, "camera.view", str(cam.id), quality=pb.quality)
    await db.commit()
    return pb


@router.get("/{camera_id}/mjpeg")
async def mjpeg(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    cam = await svc.get_owned_camera(db, user, camera_id)
    svc.ensure_not_locked(cam)
    limiter.hit(f"mjpeg:{user.id}", 30, 60)
    url = mjpeg_source(cam)
    if not url:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not an MJPEG camera")
    u, p = svc.get_credentials(cam)
    return await proxy_mjpeg(url, build_auth(u, p))


@router.get("/{camera_id}/snapshot")
async def snapshot(camera_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    cam = await svc.get_owned_camera(db, user, camera_id)
    svc.ensure_not_locked(cam)
    if not (cam.config or {}).get("snapshot_allowed", True):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Snapshots are disabled for this camera")
    url = snapshot_source(cam)
    if not url:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No snapshot endpoint; use the in-player snapshot")
    limiter.hit(f"snap:{user.id}", 30, 60)
    u, p = svc.get_credentials(cam)
    auth = build_auth(u, p)
    if cam.vendor == "reolink" and not (cam.config or {}).get("snapshot_path"):
        url, auth = reolink_snapshot(url, u, p), None
    try:
        async with make_client(12) as client:
            r = await guarded_request(client, "GET", url, auth=auth)
    except netguard.TargetNotAllowed as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Blocked by network policy: {exc}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, f"Camera unreachable: {exc.__class__.__name__}") from exc
    if r.status_code in (401, 403):
        svc.record_result(cam, False, "Snapshot credentials rejected", auth_failed=True)
        await db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Camera rejected the stored credentials")
    ctype = r.headers.get("content-type", "").split(";")[0]
    if r.status_code != 200 or not ctype.startswith("image/") or len(r.content) > MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Camera returned {r.status_code} {ctype}")
    await audit(db, request, user.id, "camera.snapshot", str(cam.id))
    await db.commit()
    return Response(
        r.content,
        media_type=ctype,
        headers={
            "Content-Disposition": f'attachment; filename="snapshot-{cam.id.hex[:8]}.jpg"',
            "Cache-Control": "no-store",
        },
    )
