"""Public camera discovery and viewing (feeds their operators publish openly)."""
from __future__ import annotations

import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import current_user, optional_user
from ..models import PublicCamera, SavedCamera, User
from ..schemas import Playback, PublicCameraOut, QualityOption
from ..services import cameras as svc
from ..services import mediamtx, netguard
from ..services.http import guarded_request, make_client, guarded_stream
from ..services.providers.base import bbox, haversine_km
from ..services.providers.runner import discover_area
from ..services.ratelimit import limiter

router = APIRouter(prefix="/api/public-cameras", tags=["public cameras"])
MAX_IMAGE_BYTES = 6 * 1024 * 1024


async def _saved_ids(db: AsyncSession, user: User | None) -> set[uuid.UUID]:
    if not user:
        return set()
    return set((await db.scalars(select(SavedCamera.public_camera_id).where(SavedCamera.user_id == user.id))).all())


def _out(cam: PublicCamera, saved: set[uuid.UUID], dist: float | None = None) -> PublicCameraOut:
    o = PublicCameraOut.model_validate(cam)
    o.distance_km = round(dist, 2) if dist is not None else None
    o.saved = cam.id in saved
    return o


async def nearby_query(
    db: AsyncSession, lat: float, lon: float, radius_km: float, camera_type: str | None, limit: int
) -> list[tuple[PublicCamera, float]]:
    la0, la1, lo0, lo1 = bbox(lat, lon, radius_km)
    q = select(PublicCamera).where(
        PublicCamera.latitude.between(la0, la1), PublicCamera.longitude.between(lo0, lo1)
    )
    if camera_type:
        q = q.where(PublicCamera.camera_type == camera_type)
    rows = (await db.scalars(q.limit(5000))).all()
    with_d = [(c, haversine_km(lat, lon, c.latitude, c.longitude)) for c in rows]
    with_d = [x for x in with_d if x[1] <= radius_km]
    with_d.sort(key=lambda x: x[1])
    return with_d[:limit]


@router.get("/nearby", response_model=list[PublicCameraOut])
async def nearby(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(25, gt=0, le=500),
    camera_type: str | None = Query(None, pattern="^(traffic|webcam|public_ip)$"),
    limit: int = Query(300, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    saved = await _saved_ids(db, user)
    return [_out(c, saved, d) for c, d in await nearby_query(db, lat, lon, radius_km, camera_type, limit)]


class DiscoverIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    radius_km: float = Field(10, gt=0, le=50)


class DiscoverOut(BaseModel):
    imported: dict[str, int]
    errors: dict[str, str]
    cameras: list[PublicCameraOut]


@router.post("/discover", response_model=DiscoverOut)
async def discover(body: DiscoverIn, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    """Query on-demand providers (OpenStreetMap, Windy) around a point and import results."""
    limiter.hit(f"discover:{user.id}", 6, 300)
    imported, errors = await discover_area(db, body.lat, body.lon, body.radius_km)
    await db.commit()
    saved = await _saved_ids(db, user)
    rows = await nearby_query(db, body.lat, body.lon, body.radius_km, None, 500)
    return DiscoverOut(imported=imported, errors=errors, cameras=[_out(c, saved, d) for c, d in rows])


async def _get(db: AsyncSession, camera_id: uuid.UUID) -> PublicCamera:
    cam = await db.get(PublicCamera, camera_id)
    if not cam:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    return cam


@router.get("/{camera_id}", response_model=PublicCameraOut)
async def get_public(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User | None = Depends(optional_user)):
    return _out(await _get(db, camera_id), await _saved_ids(db, user))


@router.get("/{camera_id}/playback", response_model=Playback)
async def playback(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    cam = await _get(db, camera_id)
    base = f"/api/public-cameras/{cam.id}"
    pb = Playback(
        kind="public",
        camera_id=cam.id,
        mode="webpage",
        page_url=cam.page_url,
        snapshot_allowed=cam.snapshot_allowed,
        snapshot_url=f"{base}/snapshot" if cam.snapshot_allowed and cam.snapshot_url else None,
        attribution=f"{cam.source_name}" + (f" - {cam.license}" if cam.license else ""),
    )
    if cam.stream_type == "hls" and cam.stream_url:
        pb.mode = "hls"
        pb.hls_url = cam.stream_url  # played directly from the operator's CDN
        pb.qualities = [QualityOption(id="auto", label="Auto (adaptive)")]
        if cam.snapshot_url:
            pb.image_url = f"{base}/image"
    elif cam.stream_type == "gateway":
        name = await svc.gateway_path_for_public(cam)
        pb.mode = "gateway"
        pb.whep_url = f"/api/media/{name}/whep"
        pb.hls_url = f"/api/media/{name}/hls/index.m3u8"
        pb.qualities = [QualityOption(id="high", label="Source")]
    elif cam.stream_type == "mjpeg" and cam.stream_url:
        pb.mode = "mjpeg"
        pb.image_url = f"{base}/mjpeg"
    elif cam.stream_type == "image" and cam.snapshot_url:
        pb.mode = "image"
        pb.image_url = f"{base}/image"
        pb.refresh_seconds = 5
    return pb


async def _fetch_image(url: str) -> tuple[bytes, str]:
    try:
        async with make_client(12) as client:
            r = await guarded_request(client, "GET", url, stream=True)
            try:
                ctype = r.headers.get("content-type", "").split(";")[0].strip()
                if r.status_code != 200 or not ctype.startswith("image/"):
                    raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Upstream returned {r.status_code} {ctype}")
                data = bytearray()
                async for chunk in r.aiter_bytes():
                    data += chunk
                    if len(data) > MAX_IMAGE_BYTES:
                        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Upstream image too large")
            finally:
                await r.aclose()
    except netguard.TargetNotAllowed as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Blocked by network policy: {exc}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, f"Upstream unreachable: {exc.__class__.__name__}") from exc
    return bytes(data), ctype


@router.get("/{camera_id}/image")
async def image(camera_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    """Proxy the latest still image (avoids mixed content / CORS and hides viewer IPs from operators)."""
    limiter.hit(f"img:{user.id}", 240, 60)
    cam = await _get(db, camera_id)
    if not cam.snapshot_url:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera has no still image")
    data, ctype = await _fetch_image(cam.snapshot_url)
    return Response(data, media_type=ctype, headers={"Cache-Control": "private, max-age=2"})


@router.get("/{camera_id}/snapshot")
async def snapshot(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    cam = await _get(db, camera_id)
    if not cam.snapshot_allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The operator of this feed does not permit snapshots")
    if not cam.snapshot_url:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Use the in-player snapshot for video feeds")
    data, ctype = await _fetch_image(cam.snapshot_url)
    ext = ctype.split("/")[-1].replace("jpeg", "jpg")
    return Response(
        data,
        media_type=ctype,
        headers={"Content-Disposition": f'attachment; filename="camerascope-{cam.id.hex[:8]}.{ext}"'},
    )


@router.get("/{camera_id}/mjpeg")
async def mjpeg(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    limiter.hit(f"mjpeg:{user.id}", 30, 60)
    cam = await _get(db, camera_id)
    if cam.stream_type != "mjpeg" or not cam.stream_url:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not an MJPEG camera")
    return await proxy_mjpeg(cam.stream_url)


async def proxy_mjpeg(url: str, auth: httpx.Auth | None = None) -> StreamingResponse:
    cm = guarded_stream("GET", url, auth=auth, timeout=15)
    try:
        resp = await cm.__aenter__()
    except netguard.TargetNotAllowed as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Blocked by network policy: {exc}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, f"Upstream unreachable: {exc.__class__.__name__}") from exc
    ctype = resp.headers.get("content-type", "")
    if resp.status_code != 200 or not ctype.startswith(("multipart/", "image/")):
        await cm.__aexit__(None, None, None)
        code = resp.status_code
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Upstream returned {code} {ctype}")

    async def body():
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        except httpx.HTTPError:
            return
        finally:
            await cm.__aexit__(None, None, None)

    return StreamingResponse(body(), media_type=ctype, headers={"Cache-Control": "no-store"})
