"""Saved cameras, recently viewed, diagnostics, providers and health."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import admin_user, audit, current_user
from ..models import DiagnosticRun, PrivateCamera, ProviderSync, PublicCamera, SavedCamera, User, ViewHistory
from ..schemas import DiagnosticsIn, DiagnosticsOut, HistoryIn, PublicCameraOut
from ..services import cameras as svc
from ..services import mediamtx
from ..services.providers.runner import sync_provider
from ..services.providers.sources import ALL_PROVIDERS
from ..services.ratelimit import limiter
from .private import _out as private_out

router = APIRouter(prefix="/api", tags=["library"])


# ----------------------------------------------------------------- saved


@router.get("/saved", response_model=list[PublicCameraOut])
async def list_saved(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    rows = (
        await db.scalars(
            select(PublicCamera)
            .join(SavedCamera, SavedCamera.public_camera_id == PublicCamera.id)
            .where(SavedCamera.user_id == user.id)
            .order_by(SavedCamera.created_at.desc())
        )
    ).all()
    out = []
    for c in rows:
        o = PublicCameraOut.model_validate(c)
        o.saved = True
        out.append(o)
    return out


@router.put("/saved/{camera_id}", status_code=204)
async def save(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    if not await db.get(PublicCamera, camera_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    if not await db.get(SavedCamera, (user.id, camera_id)):
        db.add(SavedCamera(user_id=user.id, public_camera_id=camera_id))
        await db.commit()


@router.delete("/saved/{camera_id}", status_code=204)
async def unsave(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    await db.execute(delete(SavedCamera).where(SavedCamera.user_id == user.id, SavedCamera.public_camera_id == camera_id))
    await db.commit()


# --------------------------------------------------------------- history


@router.post("/history", status_code=204)
async def add_history(body: HistoryIn, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    if body.camera_kind == "private":
        await svc.get_owned_camera(db, user, body.camera_id)
    elif not await db.get(PublicCamera, body.camera_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    db.add(ViewHistory(user_id=user.id, camera_kind=body.camera_kind, camera_id=body.camera_id))
    # keep history bounded
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    await db.execute(delete(ViewHistory).where(ViewHistory.user_id == user.id, ViewHistory.viewed_at < cutoff))
    await db.commit()


@router.get("/history")
async def history(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    rows = (
        await db.scalars(
            select(ViewHistory).where(ViewHistory.user_id == user.id).order_by(ViewHistory.viewed_at.desc()).limit(200)
        )
    ).all()
    seen: dict[tuple[str, uuid.UUID], datetime] = {}
    for r in rows:
        seen.setdefault((r.camera_kind, r.camera_id), r.viewed_at)
    items = list(seen.items())[:20]
    out = []
    for (kind, cid), at in items:
        if kind == "public":
            cam = await db.get(PublicCamera, cid)
            if cam:
                out.append({"viewed_at": at, "camera": PublicCameraOut.model_validate(cam).model_dump(mode="json")})
        else:
            cam = await db.get(PrivateCamera, cid)
            if cam and cam.owner_id == user.id:
                out.append({"viewed_at": at, "camera": private_out(cam).model_dump(mode="json")})
    return out


@router.delete("/history", status_code=204)
async def clear_history(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    await db.execute(delete(ViewHistory).where(ViewHistory.user_id == user.id))
    await db.commit()


# ----------------------------------------------------------- diagnostics


@router.post("/diagnostics", response_model=DiagnosticsOut)
async def run_diagnostics(body: DiagnosticsIn, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    limiter.hit(f"diag:{user.id}", 10, 60)
    if body.camera_kind == "private":
        cam = await svc.get_owned_camera(db, user, body.camera_id)
        svc.ensure_not_locked(cam)
        limiter.hit(f"diag-cam:{cam.id}", 4, 300)
        ok, steps, auth_failed = await svc.diagnose_private(cam)
        failed = next((s for s in steps if not s.ok), None)
        svc.record_result(cam, ok, failed.detail if failed else None, auth_failed)
    else:
        pcam = await db.get(PublicCamera, body.camera_id)
        if not pcam:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
        ok, steps = await svc.diagnose_public(pcam)
        pcam.status = "online" if ok else "offline"
        pcam.last_checked_at = datetime.now(timezone.utc)
    run = DiagnosticRun(
        user_id=user.id, camera_kind=body.camera_kind, camera_id=body.camera_id, ok=ok, steps=[s.model_dump() for s in steps]
    )
    db.add(run)
    await audit(db, request, user.id, "camera.diagnostics", str(body.camera_id), ok=ok)
    await db.commit()
    return DiagnosticsOut(id=run.id, camera_kind=body.camera_kind, camera_id=body.camera_id, ok=ok, steps=steps, created_at=run.created_at)


@router.get("/diagnostics", response_model=list[DiagnosticsOut])
async def list_diagnostics(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    rows = (
        await db.scalars(
            select(DiagnosticRun).where(DiagnosticRun.user_id == user.id).order_by(DiagnosticRun.created_at.desc()).limit(25)
        )
    ).all()
    return [
        DiagnosticsOut(id=r.id, camera_kind=r.camera_kind, camera_id=r.camera_id, ok=r.ok, steps=r.steps, created_at=r.created_at)
        for r in rows
    ]


# ------------------------------------------------------------- providers


@router.get("/providers")
async def providers(db: AsyncSession = Depends(get_db)):
    from ..config import get_settings

    enabled = set(get_settings().enabled_providers)
    syncs = {s.provider: s for s in (await db.scalars(select(ProviderSync))).all()}
    return [
        {
            "name": p.name,
            "title": p.title,
            "description": p.description,
            "mode": p.mode,
            "enabled": p.name in enabled,
            "last_run_at": syncs[p.name].last_run_at if p.name in syncs else None,
            "last_ok": syncs[p.name].last_ok if p.name in syncs else None,
            "imported": syncs[p.name].imported if p.name in syncs else None,
            "message": syncs[p.name].message if p.name in syncs else None,
        }
        for p in ALL_PROVIDERS.values()
    ]


@router.post("/admin/providers/{name}/sync")
async def admin_sync(name: str, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(admin_user)):
    p = ALL_PROVIDERS.get(name)
    if not p or p.mode != "bulk":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown bulk provider")
    ok, n, msg = await sync_provider(db, name)
    await audit(db, request, user.id, "admin.provider_sync", name, ok=ok, imported=n)
    await db.commit()
    return {"ok": ok, "imported": n, "message": msg}


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    await db.execute(select(1))
    return {"status": "ok", "gateway": await mediamtx.gateway_healthy()}
