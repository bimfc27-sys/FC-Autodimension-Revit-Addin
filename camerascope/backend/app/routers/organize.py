"""Sites (buildings) with role-based sharing, camera groups and favorites."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import audit, current_user
from ..models import CameraGroup, CameraGroupItem, Device, Favorite, PrivateCamera, PublicCamera, Site, SiteMember, User
from ..schemas import PublicCameraOut
from ..services import cameras as svc
from ..services.ratelimit import limiter
from .private import camera_out

router = APIRouter(prefix="/api", tags=["sites, groups & favorites"])

Kind = Literal["public", "private"]


# ======================================================================= sites


class SiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    address: str | None = Field(None, max_length=300)


class SiteOut(BaseModel):
    id: uuid.UUID
    name: str
    address: str | None
    role: str
    camera_count: int
    member_count: int


class MemberIn(BaseModel):
    email: EmailStr
    role: Literal["manager", "viewer"] = "viewer"


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: str
    added_at: datetime


async def _membership(db: AsyncSession, user: User, site_id: uuid.UUID, manager: bool = False) -> SiteMember:
    m = await db.get(SiteMember, (site_id, user.id))
    if not m:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found")
    if manager and m.role != "manager":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Site manager role required")
    return m


async def _site_out(db: AsyncSession, site: Site, role: str) -> SiteOut:
    cams = await db.scalar(select(func.count()).select_from(PrivateCamera).where(PrivateCamera.site_id == site.id))
    members = await db.scalar(select(func.count()).select_from(SiteMember).where(SiteMember.site_id == site.id))
    return SiteOut(id=site.id, name=site.name, address=site.address, role=role, camera_count=cams or 0, member_count=members or 0)


@router.get("/sites", response_model=list[SiteOut])
async def list_sites(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    rows = (await db.execute(select(Site, SiteMember.role).join(SiteMember, SiteMember.site_id == Site.id)
                             .where(SiteMember.user_id == user.id).order_by(Site.name))).all()
    return [await _site_out(db, s, r) for s, r in rows]


@router.post("/sites", response_model=SiteOut, status_code=201)
async def create_site(body: SiteIn, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    limiter.hit(f"site-create:{user.id}", 20, 3600)
    site = Site(id=uuid.uuid4(), name=body.name, address=body.address, created_by=user.id)
    db.add(site)
    await db.flush()
    db.add(SiteMember(site_id=site.id, user_id=user.id, role="manager", added_by=user.id))
    await audit(db, request, user.id, "site.create", str(site.id))
    await db.commit()
    return await _site_out(db, site, "manager")


@router.patch("/sites/{site_id}", response_model=SiteOut)
async def update_site(site_id: uuid.UUID, body: SiteIn, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    m = await _membership(db, user, site_id, manager=True)
    site = await db.get(Site, site_id)
    site.name, site.address = body.name, body.address
    await db.commit()
    return await _site_out(db, site, m.role)


@router.delete("/sites/{site_id}", status_code=204)
async def delete_site(site_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    await _membership(db, user, site_id, manager=True)
    await db.execute(update(PrivateCamera).where(PrivateCamera.site_id == site_id).values(site_id=None))
    await db.execute(update(Device).where(Device.site_id == site_id).values(site_id=None))
    await db.delete(await db.get(Site, site_id))
    await audit(db, request, user.id, "site.delete", str(site_id))
    await db.commit()


@router.get("/sites/{site_id}/members", response_model=list[MemberOut])
async def list_members(site_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    await _membership(db, user, site_id)
    rows = (await db.execute(select(SiteMember, User).join(User, User.id == SiteMember.user_id)
                             .where(SiteMember.site_id == site_id).order_by(User.display_name))).all()
    return [MemberOut(user_id=u.id, email=u.email, display_name=u.display_name, role=m.role, added_at=m.created_at) for m, u in rows]


@router.post("/sites/{site_id}/members", response_model=MemberOut, status_code=201)
async def add_member(site_id: uuid.UUID, body: MemberIn, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    await _membership(db, user, site_id, manager=True)
    limiter.hit(f"site-invite:{user.id}", 30, 3600)
    target = await db.scalar(select(User).where(User.email == body.email.lower()))
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No CameraScope account uses that e-mail. Ask them to register first.")
    m = await db.get(SiteMember, (site_id, target.id))
    if m:
        m.role = body.role
    else:
        m = SiteMember(site_id=site_id, user_id=target.id, role=body.role, added_by=user.id)
        db.add(m)
    await audit(db, request, user.id, "site.member_set", str(site_id), member=str(target.id), role=body.role)
    await db.commit()
    return MemberOut(user_id=target.id, email=target.email, display_name=target.display_name, role=m.role, added_at=m.created_at or datetime.now())


@router.delete("/sites/{site_id}/members/{member_id}", status_code=204)
async def remove_member(site_id: uuid.UUID, member_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    if member_id != user.id:
        await _membership(db, user, site_id, manager=True)
    m = await db.get(SiteMember, (site_id, member_id))
    if not m:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    if m.role == "manager":
        managers = await db.scalar(select(func.count()).select_from(SiteMember).where(SiteMember.site_id == site_id, SiteMember.role == "manager"))
        if managers <= 1:
            raise HTTPException(status.HTTP_409_CONFLICT, "A site needs at least one manager")
    # The removed member's own cameras leave the site, so nobody keeps access through it.
    await db.execute(update(PrivateCamera).where(PrivateCamera.site_id == site_id, PrivateCamera.owner_id == member_id).values(site_id=None))
    await db.delete(m)
    await audit(db, request, user.id, "site.member_remove", str(site_id), member=str(member_id))
    await db.commit()


# ==================================================================== favorites


async def _camera_dict(db: AsyncSession, user: User, kind: str, cid: uuid.UUID) -> dict | None:
    if kind == "public":
        c = await db.get(PublicCamera, cid)
        if not c:
            return None
        o = PublicCameraOut.model_validate(c)
        o.saved = True
        return o.model_dump(mode="json")
    c = await db.get(PrivateCamera, cid)
    if not c or not await svc.can_view(db, user, c):
        return None
    return (await camera_out(db, c, user)).model_dump(mode="json")


async def _ensure_visible(db: AsyncSession, user: User, kind: str, cid: uuid.UUID) -> None:
    if kind == "public":
        if not await db.get(PublicCamera, cid):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    else:
        await svc.get_viewable_camera(db, user, cid)


@router.get("/favorites")
async def list_favorites(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    rows = (await db.scalars(select(Favorite).where(Favorite.user_id == user.id).order_by(Favorite.created_at.desc()))).all()
    out = []
    for f in rows:
        d = await _camera_dict(db, user, f.camera_kind, f.camera_id)
        if d:
            out.append(d)
    return out


@router.put("/favorites/{kind}/{camera_id}", status_code=204)
async def add_favorite(kind: Kind, camera_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    await _ensure_visible(db, user, kind, camera_id)
    if not await db.get(Favorite, (user.id, kind, camera_id)):
        db.add(Favorite(user_id=user.id, camera_kind=kind, camera_id=camera_id))
        await db.commit()


@router.delete("/favorites/{kind}/{camera_id}", status_code=204)
async def remove_favorite(kind: Kind, camera_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    await db.execute(delete(Favorite).where(Favorite.user_id == user.id, Favorite.camera_kind == kind, Favorite.camera_id == camera_id))
    await db.commit()


# ======================================================================= groups


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class GroupItemIn(BaseModel):
    camera_kind: Kind
    camera_id: uuid.UUID


class GroupItemsIn(BaseModel):
    items: list[GroupItemIn] = Field(max_length=64)


async def _group(db: AsyncSession, user: User, group_id: uuid.UUID) -> CameraGroup:
    g = await db.get(CameraGroup, group_id)
    if not g or g.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")
    return g


async def _group_out(db: AsyncSession, user: User, g: CameraGroup) -> dict:
    items = (await db.scalars(select(CameraGroupItem).where(CameraGroupItem.group_id == g.id).order_by(CameraGroupItem.position))).all()
    cams = []
    for it in items:
        d = await _camera_dict(db, user, it.camera_kind, it.camera_id)
        if d:
            cams.append(d)
    return {"id": str(g.id), "name": g.name, "position": g.position, "cameras": cams}


@router.get("/groups")
async def list_groups(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    rows = (await db.scalars(select(CameraGroup).where(CameraGroup.owner_id == user.id).order_by(CameraGroup.position, CameraGroup.name))).all()
    return [await _group_out(db, user, g) for g in rows]


@router.post("/groups", status_code=201)
async def create_group(body: GroupIn, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    n = await db.scalar(select(func.count()).select_from(CameraGroup).where(CameraGroup.owner_id == user.id))
    if n >= 100:
        raise HTTPException(status.HTTP_409_CONFLICT, "Too many groups")
    g = CameraGroup(id=uuid.uuid4(), owner_id=user.id, name=body.name, position=n)
    db.add(g)
    await db.commit()
    return await _group_out(db, user, g)


@router.patch("/groups/{group_id}")
async def rename_group(group_id: uuid.UUID, body: GroupIn, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    g = await _group(db, user, group_id)
    g.name = body.name
    await db.commit()
    return await _group_out(db, user, g)


@router.put("/groups/{group_id}/items")
async def set_group_items(group_id: uuid.UUID, body: GroupItemsIn, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    g = await _group(db, user, group_id)
    seen = set()
    for it in body.items:
        await _ensure_visible(db, user, it.camera_kind, it.camera_id)
    await db.execute(delete(CameraGroupItem).where(CameraGroupItem.group_id == g.id))
    for pos, it in enumerate(body.items):
        key = (it.camera_kind, it.camera_id)
        if key in seen:
            continue
        seen.add(key)
        db.add(CameraGroupItem(group_id=g.id, camera_kind=it.camera_kind, camera_id=it.camera_id, position=pos))
    await db.commit()
    return await _group_out(db, user, g)


@router.delete("/groups/{group_id}", status_code=204)
async def delete_group(group_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    await db.delete(await _group(db, user, group_id))
    await db.commit()
