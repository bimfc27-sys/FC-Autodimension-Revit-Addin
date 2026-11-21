from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_db
from .models import AuditLog, User
from .security import decode_session_token


def client_ip(request: Request) -> str:
    # The reverse proxy (Caddy) sets X-Forwarded-For; uvicorn is started with
    # --proxy-headers --forwarded-allow-ips limited to the proxy network.
    return request.client.host if request.client else "unknown"


async def _user_from_request(request: Request, db: AsyncSession) -> User | None:
    token = request.cookies.get(get_settings().session_cookie_name)
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not token:
        return None
    data = decode_session_token(token)
    if not data:
        return None
    try:
        user = await db.get(User, uuid.UUID(data["sub"]))
    except ValueError:
        return None
    if not user or user.token_version != data.get("ver"):
        return None
    return user


async def optional_user(request: Request, db: AsyncSession = Depends(get_db)) -> User | None:
    return await _user_from_request(request, db)


async def current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    user = await _user_from_request(request, db)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    return user


async def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator role required")
    return user


async def audit(
    db: AsyncSession,
    request: Request | None,
    user_id: uuid.UUID | None,
    action: str,
    target: str | None = None,
    **detail,
) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            target=target,
            ip=client_ip(request) if request else None,
            detail=detail,
        )
    )
