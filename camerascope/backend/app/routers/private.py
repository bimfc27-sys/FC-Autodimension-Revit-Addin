"""Private cameras: devices the user owns, is explicitly authorised to access,
or that were explicitly shared with them through a site (building)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import audit, current_user
from ..models import Device, Gateway, PrivateCamera, Site, SiteMember, User
from ..schemas import (
    GatewayRef,
    OnvifProbeIn,
    Playback,
    PrivateCameraIn,
    PrivateCameraOut,
    PrivateCameraUpdate,
    QualityOption,
    Ref,
)
from ..services import cameras as svc
from ..services import netguard
from ..services.gateway_hub import GatewayOffline, hub
from ..services.onvif import OnvifError
from ..services.ratelimit import limiter
from ..services.routing import Route
from ..services.sources import VENDOR_PRESETS, mjpeg_source, reolink_snapshot, snapshot_source, stream_sources
from .public import MAX_IMAGE_BYTES, proxy_mjpeg

router = APIRouter(prefix="/api/cameras", tags=["private cameras"])

# Never sent to the browser: device-reported stream/snapshot URIs.
_PRIVATE_CONFIG_KEYS = {"resolved_main_uri", "resolved_sub_uri", "resolved_snapshot_uri"}


async def camera_out(db: AsyncSession, cam: PrivateCamera, user: User) -> PrivateCameraOut:
    is_owner = cam.owner_id == user.id
    username = None
    if is_owner and cam.credentials_ciphertext:
        try:
            username = svc.get_credentials(cam)[0] or None
        except Exception:
            username = None
    gw = await db.get(Gateway, cam.gateway_id) if cam.gateway_id else None
    dev = await db.get(Device, cam.device_id) if cam.device_id else None
    site = await db.get(Site, cam.site_id) if cam.site_id else None
    cfg = {k: v for k, v in (cam.config or {}).items() if k not in _PRIVATE_CONFIG_KEYS}
    return PrivateCameraOut(
        id=cam.id,
        name=cam.name,
        description=cam.description,
        protocol=cam.protocol,
        vendor=cam.vendor,
        is_owner=is_owner,
        access="owner" if is_owner else "shared",
        host=cam.host if is_owner else None,
        port=cam.port if is_owner else None,
        use_tls=cam.use_tls,
        config=cfg if is_owner else {"snapshot_allowed": cfg.get("snapshot_allowed", True)},
        latitude=cam.latitude,
        longitude=cam.longitude,
        has_credentials=bool(cam.credentials_ciphertext or (dev and dev.credentials_ciphertext)),
        username=username,
        authorization_basis=cam.authorization_basis,
        authorization_attested_at=cam.authorization_attested_at,
        status=svc.camera_status(cam),
        connection="gateway" if cam.gateway_id else "cloud",
        gateway=GatewayRef(id=gw.id, name=gw.name, online=hub.is_online(gw.id)) if gw else None,
        device=Ref(id=dev.id, name=dev.name) if dev else None,
        site=Ref(id=site.id, name=site.name) if site else None,
        channel=(cam.config or {}).get("channel") if cam.device_id else None,
        reachable_checked_at=cam.reachable_checked_at,
        last_error=cam.last_error if is_owner else None,
        last_checked_at=cam.last_checked_at,
        created_at=cam.created_at,
    )


async def _check_gateway(db: AsyncSession, user: User, gateway_id: uuid.UUID | None) -> None:
    if gateway_id is None:
        return
    g = await db.get(Gateway, gateway_id)
    if not g or g.owner_id != user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown gateway")


async def check_site_assignable(db: AsyncSession, user: User, site_id: uuid.UUID | None) -> None:
    if site_id is None:
        return
    m = await db.get(SiteMember, (site_id, user.id))
    if not m or m.role != "manager":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only site managers can add cameras to a site")


async def _direct_policy(host: str, port: int | None, gateway_id) -> None:
    """Cloud-reached cameras go through the SSRF guard; gateway cameras are validated by the gateway."""
    if gateway_id is not None:
        return
    try:
        await netguard.check_host(host, port or 80)
    except netguard.TargetNotAllowed as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Blocked by network policy: {exc}") from exc


async def resolve_onvif(cam: PrivateCamera, user_: str, pw: str) -> None:
    """Ask the device for the stream URIs of the selected profiles (stored server-side only)."""
    client = svc.onvif_client(cam.host, cam.port, cam.use_tls, user_, pw, svc.route_for(cam))
    dev = await client.discover()
    cfg = dict(cam.config or {})
    by_token = {p.token: p for p in dev.profiles}
    main = by_token.get(cfg.get("onvif_main_profile") or "") or (dev.profiles[0] if dev.profiles else None)
    sub = by_token.get(cfg.get("onvif_sub_profile") or "")
    if not main or not main.stream_uri:
        raise OnvifError("Device returned no usable stream profile")
    cfg["onvif_main_profile"], cfg["resolved_main_uri"] = main.token, main.stream_uri
    cfg["resolved_snapshot_uri"] = main.snapshot_uri
    if sub and sub.token != main.token and sub.stream_uri:
        cfg["onvif_sub_profile"], cfg["resolved_sub_uri"] = sub.token, sub.stream_uri
    else:
        cfg["onvif_sub_profile"] = cfg["resolved_sub_uri"] = None
    cam.config = cfg


@router.get("/vendors")
async def vendors():
    """Supported manufacturer presets (documented official endpoints)."""
    return {k: {kk: vv for kk, vv in v.items()} for k, v in VENDOR_PRESETS.items()}


@router.get("", response_model=list[PrivateCameraOut])
async def list_cameras(
    scope: str = Query("all", pattern="^(all|mine|shared)$"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Every camera the user may view: their own plus those shared through sites (buildings)."""
    site_ids = await svc.viewer_site_ids(db, user)
    q = select(PrivateCamera)
    if scope == "mine":
        q = q.where(PrivateCamera.owner_id == user.id)
    elif scope == "shared":
        q = q.where(PrivateCamera.site_id.in_(site_ids or {uuid.uuid4()}), PrivateCamera.owner_id != user.id)
    else:
        q = q.where(or_(PrivateCamera.owner_id == user.id, PrivateCamera.site_id.in_(site_ids or {uuid.uuid4()})))
    rows = (await db.scalars(q.order_by(PrivateCamera.name))).all()
    return [await camera_out(db, c, user) for c in rows]


@router.post("", response_model=PrivateCameraOut, status_code=201)
async def create_camera(body: PrivateCameraIn, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    limiter.hit(f"cam-create:{user.id}", 60, 3600)
    await _check_gateway(db, user, body.gateway_id)
    await check_site_assignable(db, user, body.site_id)
    await _direct_policy(body.host, body.port, body.gateway_id)
    cam = PrivateCamera(
        id=uuid.uuid4(),
        owner_id=user.id,
        gateway_id=body.gateway_id,
        site_id=body.site_id,
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
        status="unknown",
        auth_failures=0,
    )
    # Device-reported URIs are never accepted from the client.
    for k in _PRIVATE_CONFIG_KEYS:
        cam.config[k] = None
    if body.credentials:
        svc.set_credentials(cam, body.credentials.username, body.credentials.password)
    if cam.protocol == "onvif":
        u, p = (body.credentials.username, body.credentials.password) if body.credentials else ("", "")
        try:
            await resolve_onvif(cam, u, p)
        except GatewayOffline as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
        except OnvifError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED if exc.auth_failed else status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        except (netguard.TargetNotAllowed, httpx.HTTPError) as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Device unreachable: {exc.__class__.__name__}") from exc
    db.add(cam)
    await audit(db, request, user.id, "camera.create", str(cam.id), protocol=cam.protocol, basis=cam.authorization_basis,
                gateway=str(cam.gateway_id) if cam.gateway_id else None)
    await db.commit()
    return await camera_out(db, cam, user)


@router.post("/onvif/probe")
async def onvif_probe(body: OnvifProbeIn, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    """List an ONVIF device's media profiles (names/resolutions only - no stream URLs)."""
    limiter.hit(f"onvif-probe:{user.id}", 10, 600)
    limiter.hit(f"onvif-probe-host:{user.id}:{body.host}", 3, 600)  # no rapid-fire credential attempts
    await _check_gateway(db, user, body.gateway_id)
    await _direct_policy(body.host, body.port, body.gateway_id)
    client = svc.onvif_client(body.host, body.port, body.use_tls, body.credentials.username, body.credentials.password, Route(body.gateway_id))
    await audit(db, request, user.id, "camera.onvif_probe", body.host, gateway=str(body.gateway_id) if body.gateway_id else None)
    await db.commit()
    try:
        dev = await client.discover()
    except GatewayOffline as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
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
            {"token": p.token, "name": p.name, "source_token": p.source_token, "encoding": p.encoding,
             "width": p.width, "height": p.height, "has_stream": bool(p.stream_uri)}
            for p in dev.profiles
        ],
    }


@router.get("/{camera_id}", response_model=PrivateCameraOut)
async def get_camera(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    return await camera_out(db, await svc.get_viewable_camera(db, user, camera_id), user)


@router.patch("/{camera_id}", response_model=PrivateCameraOut)
async def update_camera(
    camera_id: uuid.UUID, body: PrivateCameraUpdate, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)
):
    cam = await svc.get_owned_camera(db, user, camera_id)
    data = body.model_dump(exclude_unset=True)
    if "gateway_id" in data:
        await _check_gateway(db, user, data["gateway_id"])
    if "site_id" in data:
        await check_site_assignable(db, user, data["site_id"])
    gateway_id = data.get("gateway_id", cam.gateway_id)
    if data.get("host") or "gateway_id" in data:
        await _direct_policy(data.get("host") or cam.host, data.get("port") or cam.port, gateway_id)
    await svc.remove_streams(cam)
    for f in ("name", "description", "host", "port", "use_tls", "latitude", "longitude", "gateway_id", "site_id"):
        if f in data:
            setattr(cam, f, data[f])
    if body.config is not None:
        keep = {k: (cam.config or {}).get(k) for k in _PRIVATE_CONFIG_KEYS}
        cam.config = {**body.config.model_dump(), **keep}
    if body.credentials is not None:
        svc.set_credentials(cam, body.credentials.username, body.credentials.password)
        cam.auth_failures = 0  # new credentials from the owner unlock the camera
        cam.status = "unknown"
    await audit(db, request, user.id, "camera.update", str(cam.id), fields=sorted(data.keys()))
    await db.commit()
    return await camera_out(db, cam, user)


@router.delete("/{camera_id}", status_code=204)
async def delete_camera(camera_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    cam = await svc.get_owned_camera(db, user, camera_id)
    await svc.remove_streams(cam)
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
    u, p = await svc.camera_credentials(db, cam)
    try:
        await resolve_onvif(cam, u, p)
    except GatewayOffline as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except OnvifError as exc:
        svc.record_result(cam, False, str(exc), exc.auth_failed)
        await db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    except (netguard.TargetNotAllowed, httpx.HTTPError) as exc:
        svc.record_result(cam, False, str(exc))
        await db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Device unreachable: {exc}") from exc
    svc.record_result(cam, True)
    await svc.remove_streams(cam)
    await audit(db, request, user.id, "camera.onvif_refresh", str(cam.id))
    await db.commit()
    return await camera_out(db, cam, user)


@router.get("/{camera_id}/playback", response_model=Playback)
async def playback(
    camera_id: uuid.UUID,
    request: Request,
    quality: str = Query("high", pattern="^(high|low)$"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    cam = await svc.get_viewable_camera(db, user, camera_id)
    svc.ensure_not_locked(cam)
    limiter.hit(f"playback:{user.id}", 120, 60)
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
        if cam.gateway_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "MJPEG cameras behind a gateway: use RTSP or the vendor preset instead")
        pb.mode, pb.image_url = "mjpeg", f"/api/cameras/{cam.id}/mjpeg"
        pb.qualities = [QualityOption(id="high", label="Source")]
    else:
        sources = stream_sources(cam)
        pb.qualities = [QualityOption(id=k, label={"high": "High (main stream)", "low": "Low (sub stream)"}[k]) for k in sources]
        name = await svc.gateway_path_for_private(db, cam, quality)
        pb.quality = name.rsplit("-", 1)[1]
        pb.whep_url = f"/api/media/{name}/whep"
        pb.hls_url = f"/api/media/{name}/hls/index.m3u8"
    await audit(db, request, user.id, "camera.view", str(cam.id), quality=pb.quality, shared=cam.owner_id != user.id)
    await db.commit()
    return pb


@router.get("/{camera_id}/mjpeg")
async def mjpeg(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    cam = await svc.get_viewable_camera(db, user, camera_id)
    svc.ensure_not_locked(cam)
    limiter.hit(f"mjpeg:{user.id}", 30, 60)
    url = mjpeg_source(cam)
    if not url or cam.gateway_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not an MJPEG camera")
    from ..services.http import build_auth

    u, p = await svc.camera_credentials(db, cam)
    return await proxy_mjpeg(url, build_auth(u, p))


@router.get("/{camera_id}/snapshot")
async def snapshot(camera_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    cam = await svc.get_viewable_camera(db, user, camera_id)
    svc.ensure_not_locked(cam)
    if not (cam.config or {}).get("snapshot_allowed", True):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Snapshots are disabled for this camera")
    url = snapshot_source(cam)
    if not url:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No snapshot endpoint; use the in-player snapshot")
    limiter.hit(f"snap:{user.id}", 30, 60)
    u, p = await svc.camera_credentials(db, cam)
    route = svc.route_for(cam)
    if cam.vendor == "reolink" and not (cam.config or {}).get("snapshot_path"):
        url, u, p = reolink_snapshot(url, u, p), "", ""
    try:
        r = await svc.http_get(route, url, u, p)
    except GatewayOffline as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
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
        headers={"Content-Disposition": f'attachment; filename="snapshot-{cam.id.hex[:8]}.jpg"', "Cache-Control": "no-store"},
    )
