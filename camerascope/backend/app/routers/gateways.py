"""Local Camera Gateways: registration, enrollment and the agent tunnel endpoint."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketState

from ..config import get_settings
from ..db import get_db, sessionmaker
from ..deps import audit, client_ip, current_user
from ..models import Device, Gateway, PrivateCamera, User
from ..services.gateway_hub import GatewayConnection, hub
from ..services.ratelimit import limiter

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/gateways", tags=["gateways"])


def _h(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


class GatewayIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(None, max_length=1000)


class GatewayOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    status: str  # pending | online | offline
    online: bool
    last_seen_at: datetime | None
    agent_version: str | None
    allowed_subnets: list
    remote_address: str | None
    camera_count: int = 0
    device_count: int = 0
    created_at: datetime


class EnrollmentOut(BaseModel):
    gateway: GatewayOut
    enrollment_code: str
    expires_at: datetime


def gateway_out(g: Gateway, cams: int = 0, devs: int = 0) -> GatewayOut:
    online = hub.is_online(g.id)
    return GatewayOut(
        id=g.id,
        name=g.name,
        description=g.description,
        status="online" if online else ("pending" if not g.token_hash else "offline"),
        online=online,
        last_seen_at=g.last_seen_at,
        agent_version=g.agent_version,
        allowed_subnets=g.allowed_subnets or [],
        remote_address=g.remote_address,
        camera_count=cams,
        device_count=devs,
        created_at=g.created_at,
    )


async def owned_gateway(db: AsyncSession, user: User, gateway_id: uuid.UUID) -> Gateway:
    g = await db.get(Gateway, gateway_id)
    if not g or g.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gateway not found")
    return g


def _new_enrollment(g: Gateway) -> tuple[str, datetime]:
    code = f"{g.id.hex}.{secrets.token_urlsafe(24)}"
    exp = datetime.now(timezone.utc) + timedelta(minutes=get_settings().enrollment_ttl_minutes)
    g.enrollment_code_hash, g.enrollment_expires_at = _h(code), exp
    return code, exp


@router.get("", response_model=list[GatewayOut])
async def list_gateways(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    gws = (await db.scalars(select(Gateway).where(Gateway.owner_id == user.id).order_by(Gateway.name))).all()
    cams = dict((await db.execute(
        select(PrivateCamera.gateway_id, func.count()).where(PrivateCamera.owner_id == user.id).group_by(PrivateCamera.gateway_id)
    )).all())
    devs = dict((await db.execute(
        select(Device.gateway_id, func.count()).where(Device.owner_id == user.id).group_by(Device.gateway_id)
    )).all())
    return [gateway_out(g, cams.get(g.id, 0), devs.get(g.id, 0)) for g in gws]


@router.post("", response_model=EnrollmentOut, status_code=201)
async def create_gateway(body: GatewayIn, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    limiter.hit(f"gw-create:{user.id}", 20, 3600)
    g = Gateway(id=uuid.uuid4(), owner_id=user.id, name=body.name, description=body.description, status="pending", allowed_subnets=[])
    code, exp = _new_enrollment(g)
    db.add(g)
    await audit(db, request, user.id, "gateway.create", str(g.id))
    await db.commit()
    return EnrollmentOut(gateway=gateway_out(g), enrollment_code=code, expires_at=exp)


@router.post("/{gateway_id}/reenroll", response_model=EnrollmentOut)
async def reenroll(gateway_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    """Revoke the current gateway secret and issue a new one-time enrollment code."""
    g = await owned_gateway(db, user, gateway_id)
    g.token_hash = None
    code, exp = _new_enrollment(g)
    await hub.disconnect(g.id)
    await audit(db, request, user.id, "gateway.reenroll", str(g.id))
    await db.commit()
    return EnrollmentOut(gateway=gateway_out(g), enrollment_code=code, expires_at=exp)


@router.patch("/{gateway_id}", response_model=GatewayOut)
async def update_gateway(gateway_id: uuid.UUID, body: GatewayIn, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    g = await owned_gateway(db, user, gateway_id)
    g.name, g.description = body.name, body.description
    await db.commit()
    return gateway_out(g)


@router.delete("/{gateway_id}", status_code=204)
async def delete_gateway(gateway_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    g = await owned_gateway(db, user, gateway_id)
    in_use = await db.scalar(select(func.count()).select_from(PrivateCamera).where(PrivateCamera.gateway_id == g.id))
    in_use += await db.scalar(select(func.count()).select_from(Device).where(Device.gateway_id == g.id))
    if in_use:
        raise HTTPException(status.HTTP_409_CONFLICT, "Move or delete the cameras and recorders using this gateway first")
    await hub.disconnect(g.id)
    await hub.close_forwarders(g.id)
    await db.delete(g)
    await audit(db, request, user.id, "gateway.delete", str(gateway_id))
    await db.commit()


@router.get("/{gateway_id}/info")
async def gateway_info(gateway_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    g = await owned_gateway(db, user, gateway_id)
    if not hub.is_online(g.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Gateway is offline")
    try:
        return await hub.get(g.id).call("info", timeout=8)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


class EnrollIn(BaseModel):
    code: str = Field(max_length=200)
    agent_version: str | None = Field(None, max_length=40)


@router.post("/enroll")
async def enroll(body: EnrollIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Exchange a one-time enrollment code for the gateway's long-lived secret (agent only)."""
    limiter.hit(f"enroll:{client_ip(request)}", 10, 600)
    gid_hex, _, _ = body.code.partition(".")
    try:
        g = await db.get(Gateway, uuid.UUID(hex=gid_hex))
    except ValueError:
        g = None
    now = datetime.now(timezone.utc)
    if (
        not g
        or not g.enrollment_code_hash
        or not hmac.compare_digest(g.enrollment_code_hash, _h(body.code))
        or not g.enrollment_expires_at
        or g.enrollment_expires_at < now
    ):
        await audit(db, request, None, "gateway.enroll_failed", gid_hex[:40])
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired enrollment code")
    secret = secrets.token_urlsafe(40)
    g.token_hash = _h(secret)
    g.enrollment_code_hash = g.enrollment_expires_at = None  # single use
    g.agent_version = body.agent_version
    g.status = "offline"
    await audit(db, request, g.owner_id, "gateway.enrolled", str(g.id))
    await db.commit()
    return {"gateway_id": str(g.id), "secret": secret}


async def _authenticate(ws: WebSocket) -> Gateway | None:
    auth = ws.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    gid, _, secret = auth[7:].strip().partition(".")
    try:
        gateway_id = uuid.UUID(gid)
    except ValueError:
        return None
    async with sessionmaker()() as db:
        g = await db.get(Gateway, gateway_id)
        if not g or not g.token_hash or not hmac.compare_digest(g.token_hash, _h(secret)):
            return None
        return g


@router.websocket("/tunnel")
async def tunnel(ws: WebSocket):
    """Outbound tunnel opened BY the gateway agent (wss, bearer token)."""
    ip = ws.client.host if ws.client else "unknown"
    try:
        limiter.hit(f"tunnel:{ip}", 30, 60)
    except HTTPException:
        await ws.close(code=4429)
        return
    g = await _authenticate(ws)
    if not g:
        async with sessionmaker()() as db:
            from ..deps import audit as _audit

            await _audit(db, None, None, "gateway.auth_failed", ip)
            await db.commit()
        await ws.close(code=4401)
        return
    await ws.accept()
    conn = GatewayConnection(g.id, ws)
    try:
        hello = json.loads(await ws.receive_text())
        if hello.get("t") != "hello":
            await ws.close(code=4400)
            return
    except (WebSocketDisconnect, ValueError, KeyError):
        return
    conn.hello = hello
    hub.register(conn)
    async with sessionmaker()() as db:
        row = await db.get(Gateway, g.id)
        row.status, row.last_seen_at, row.remote_address = "online", datetime.now(timezone.utc), ip
        row.agent_version = str(hello.get("version", ""))[:40]
        row.allowed_subnets = [str(s)[:64] for s in hello.get("allowed_subnets", [])][:64]
        await audit(db, None, row.owner_id, "gateway.connected", str(g.id), ip=ip, version=row.agent_version)
        await db.commit()
    log.info("gateway %s connected from %s", g.id, ip)
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                await conn.handle_binary(msg["bytes"])
            elif msg.get("text") is not None:
                conn.handle_text(json.loads(msg["text"]))
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        hub.unregister(conn)
        if ws.client_state != WebSocketState.DISCONNECTED:
            try:
                await ws.close()
            except RuntimeError:
                pass
        async with sessionmaker()() as db:
            row = await db.get(Gateway, g.id)
            if row and not hub.is_online(g.id):
                row.status, row.last_seen_at = "offline", datetime.now(timezone.utc)
                await audit(db, None, row.owner_id, "gateway.disconnected", str(g.id))
                await db.commit()
        log.info("gateway %s disconnected", g.id)
