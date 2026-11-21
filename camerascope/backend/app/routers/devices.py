"""DVR / NVR recorders: register once, discover channels, import them as cameras."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import audit, current_user
from ..models import Device, Gateway, PrivateCamera, Site, User
from ..schemas import Credentials, GatewayRef, PrivateCameraOut, Ref, _valid_host
from ..services import cameras as svc
from ..services import netguard
from ..services.gateway_hub import GatewayOffline, hub
from ..services.nvr import DiscoveryError, discover
from ..services.ratelimit import limiter
from .private import _check_gateway, _direct_policy, camera_out, check_site_assignable

router = APIRouter(prefix="/api/devices", tags=["recorders (DVR/NVR)"])


class DeviceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["nvr", "dvr"] = "nvr"
    protocol: Literal["onvif", "vendor_api"]
    vendor: Literal["generic", "hikvision", "dahua", "amcrest", "axis", "reolink"] = "generic"
    host: str
    port: int | None = Field(None, ge=1, le=65535, description="HTTP / ONVIF port")
    rtsp_port: int | None = Field(None, ge=1, le=65535)
    use_tls: bool = False
    credentials: Credentials
    gateway_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None
    authorization_basis: Literal["owner", "authorized_user"]
    authorization_attested: bool

    @field_validator("host")
    @classmethod
    def _h(cls, v: str) -> str:
        return _valid_host(v)

    @model_validator(mode="after")
    def _attest(self):
        if not self.authorization_attested:
            raise ValueError("You must confirm you own or are authorised to access this recorder")
        if self.protocol == "vendor_api" and self.vendor not in ("hikvision", "dahua", "amcrest"):
            raise ValueError("Vendor channel discovery supports hikvision, dahua and amcrest; use ONVIF otherwise")
        return self


class DeviceUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    credentials: Credentials | None = None


class DeviceOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    protocol: str
    vendor: str
    host: str
    port: int | None
    rtsp_port: int | None
    use_tls: bool
    model: str | None
    status: str
    last_error: str | None
    last_discovery_at: datetime | None
    connection: Literal["gateway", "cloud"]
    gateway: GatewayRef | None
    site: Ref | None
    channel_count: int


class ImportChannel(BaseModel):
    index: int = Field(ge=1, le=512)
    name: str = Field(min_length=1, max_length=200)


class ImportIn(BaseModel):
    channels: list[ImportChannel] = Field(min_length=1, max_length=256)


async def device_out(db: AsyncSession, d: Device) -> DeviceOut:
    gw = await db.get(Gateway, d.gateway_id) if d.gateway_id else None
    site = await db.get(Site, d.site_id) if d.site_id else None
    n = len((await db.scalars(select(PrivateCamera.id).where(PrivateCamera.device_id == d.id))).all())
    status_ = "gateway_offline" if d.gateway_id and not hub.is_online(d.gateway_id) else d.status
    return DeviceOut(
        id=d.id, name=d.name, kind=d.kind, protocol=d.protocol, vendor=d.vendor, host=d.host, port=d.port,
        rtsp_port=d.rtsp_port, use_tls=d.use_tls, model=d.model, status=status_, last_error=d.last_error,
        last_discovery_at=d.last_discovery_at, connection="gateway" if d.gateway_id else "cloud",
        gateway=GatewayRef(id=gw.id, name=gw.name, online=hub.is_online(gw.id)) if gw else None,
        site=Ref(id=site.id, name=site.name) if site else None, channel_count=n,
    )


async def owned_device(db: AsyncSession, user: User, device_id: uuid.UUID) -> Device:
    d = await db.get(Device, device_id)
    if not d or d.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recorder not found")
    return d


@router.get("", response_model=list[DeviceOut])
async def list_devices(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    rows = (await db.scalars(select(Device).where(Device.owner_id == user.id).order_by(Device.name))).all()
    return [await device_out(db, d) for d in rows]


@router.post("", response_model=DeviceOut, status_code=201)
async def create_device(body: DeviceIn, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    limiter.hit(f"dev-create:{user.id}", 30, 3600)
    await _check_gateway(db, user, body.gateway_id)
    await check_site_assignable(db, user, body.site_id)
    await _direct_policy(body.host, body.port, body.gateway_id)
    d = Device(
        id=uuid.uuid4(), owner_id=user.id, gateway_id=body.gateway_id, site_id=body.site_id, name=body.name,
        kind=body.kind, protocol=body.protocol, vendor=body.vendor, host=body.host,
        port=body.port or (443 if body.use_tls else 80), rtsp_port=body.rtsp_port or 554, use_tls=body.use_tls,
        authorization_basis=body.authorization_basis, authorization_attested_at=datetime.now(timezone.utc),
        status="unknown", auth_failures=0,
    )
    svc.set_device_credentials(d, body.credentials.username, body.credentials.password)
    db.add(d)
    await audit(db, request, user.id, "device.create", str(d.id), protocol=d.protocol, vendor=d.vendor,
                gateway=str(d.gateway_id) if d.gateway_id else None, basis=d.authorization_basis)
    await db.commit()
    return await device_out(db, d)


@router.patch("/{device_id}", response_model=DeviceOut)
async def update_device(device_id: uuid.UUID, body: DeviceUpdate, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    d = await owned_device(db, user, device_id)
    if body.name:
        d.name = body.name
    if body.credentials:
        svc.set_device_credentials(d, body.credentials.username, body.credentials.password)
        d.auth_failures, d.status = 0, "unknown"
        for cam in (await db.scalars(select(PrivateCamera).where(PrivateCamera.device_id == d.id))).all():
            cam.auth_failures, cam.status = 0, "unknown"
            await svc.remove_streams(cam)
    await audit(db, request, user.id, "device.update", str(d.id))
    await db.commit()
    return await device_out(db, d)


@router.delete("/{device_id}", status_code=204)
async def delete_device(device_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    d = await owned_device(db, user, device_id)
    for cam in (await db.scalars(select(PrivateCamera).where(PrivateCamera.device_id == d.id))).all():
        await svc.remove_streams(cam)
    await db.delete(d)
    await audit(db, request, user.id, "device.delete", str(device_id))
    await db.commit()


async def _discover(db: AsyncSession, d: Device):
    svc.ensure_not_locked(d)
    try:
        chans, model = await discover(d)
    except GatewayOffline as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except DiscoveryError as exc:
        svc.record_result(d, False, str(exc), exc.auth_failed)
        await db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    except netguard.TargetNotAllowed as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Blocked by network policy: {exc}") from exc
    except httpx.HTTPError as exc:
        svc.record_result(d, False, f"unreachable: {exc.__class__.__name__}")
        await db.commit()
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, f"Recorder unreachable: {exc.__class__.__name__}") from exc
    svc.record_result(d, True)
    d.model = model or d.model
    d.last_discovery_at = datetime.now(timezone.utc)
    return chans


@router.post("/{device_id}/discover")
async def discover_channels(device_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    """List the recorder's channels (names/resolutions only)."""
    d = await owned_device(db, user, device_id)
    limiter.hit(f"dev-discover:{d.id}", 5, 300)
    chans = await _discover(db, d)
    existing = {
        (c.config or {}).get("channel"): c.id
        for c in (await db.scalars(select(PrivateCamera).where(PrivateCamera.device_id == d.id))).all()
    }
    await audit(db, request, user.id, "device.discover", str(d.id), channels=len(chans))
    await db.commit()
    return {
        "device": (await device_out(db, d)).model_dump(mode="json"),
        "channels": [{**c.public(), "imported_camera_id": str(existing[c.index]) if c.index in existing else None} for c in chans],
    }


@router.post("/{device_id}/import", response_model=list[PrivateCameraOut])
async def import_channels(device_id: uuid.UUID, body: ImportIn, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    """Create one camera per selected channel. Stream URIs are re-resolved server-side."""
    d = await owned_device(db, user, device_id)
    limiter.hit(f"dev-import:{d.id}", 10, 300)
    chans = {c.index: c for c in await _discover(db, d)}
    existing = {
        (c.config or {}).get("channel")
        for c in (await db.scalars(select(PrivateCamera).where(PrivateCamera.device_id == d.id))).all()
    }
    created = []
    for sel in body.channels:
        ch = chans.get(sel.index)
        if not ch or sel.index in existing:
            continue
        cfg = {"channel": sel.index, "http_port": d.port, "rtsp_transport": "tcp", "snapshot_allowed": True}
        if d.protocol == "onvif":
            if not ch.main_uri:
                continue
            cfg.update(onvif_main_profile=ch.main_profile, onvif_sub_profile=ch.sub_profile,
                       resolved_main_uri=ch.main_uri, resolved_sub_uri=ch.sub_uri, resolved_snapshot_uri=ch.snapshot_uri)
            protocol, vendor, port = "onvif", "generic", d.port
        else:
            protocol, vendor, port = "vendor_api", d.vendor, d.rtsp_port or 554
        cam = PrivateCamera(
            id=uuid.uuid4(), owner_id=user.id, gateway_id=d.gateway_id, device_id=d.id, site_id=d.site_id,
            name=sel.name, protocol=protocol, vendor=vendor, host=d.host, port=port, use_tls=False, config=cfg,
            authorization_basis=d.authorization_basis, authorization_attested_at=d.authorization_attested_at,
            status="unknown", auth_failures=0,
        )
        db.add(cam)
        created.append(cam)
    await audit(db, request, user.id, "device.import", str(d.id), channels=[c.config["channel"] for c in created])
    await db.commit()
    return [await camera_out(db, c, user) for c in created]
