from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..deps import audit, client_ip, current_user
from ..models import User
from ..schemas import LoginIn, RegisterIn, UserOut
from ..security import create_session_token, hash_password, needs_rehash, verify_password
from ..services.ratelimit import limiter

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_cookie(response: Response, token: str, expires: datetime) -> None:
    s = get_settings()
    response.set_cookie(
        s.session_cookie_name,
        token,
        expires=expires,
        httponly=True,
        secure=s.cookie_secure,
        samesite="strict",
        path="/",
    )


@router.post("/register", response_model=UserOut, status_code=201)
async def register(body: RegisterIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    limiter.hit(f"register:{client_ip(request)}", 5, 3600)
    email = body.email.lower()
    if await db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this e-mail already exists")
    first_user = (await db.scalar(select(func.count()).select_from(User))) == 0
    user = User(
        email=email,
        password_hash=hash_password(body.password),
        display_name=body.display_name.strip(),
        is_admin=first_user,  # the very first account administers the instance
    )
    db.add(user)
    await db.flush()
    await audit(db, request, user.id, "auth.register", email)
    await db.commit()
    token, exp = create_session_token(user.id, user.token_version)
    _set_cookie(response, token, exp)
    return user


@router.post("/login", response_model=UserOut)
async def login(body: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    s = get_settings()
    ip = client_ip(request)
    limiter.hit(f"login-ip:{ip}", 20, 300)
    email = body.email.lower()
    limiter.hit(f"login-email:{email}", 10, 300)
    user = await db.scalar(select(User).where(User.email == email))
    now = datetime.now(timezone.utc)
    if user and user.locked_until and user.locked_until > now:
        # Constant generic message: do not reveal whether the account exists.
        verify_password(body.password, None)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid e-mail or password")
    if not verify_password(body.password, user.password_hash if user else None):
        if user:
            user.failed_logins += 1
            if user.failed_logins >= s.max_failed_logins:
                user.locked_until = now + timedelta(minutes=s.lockout_minutes)
                user.failed_logins = 0
                await audit(db, request, user.id, "auth.lockout", email)
            await audit(db, request, user.id, "auth.login_failed", email)
            await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid e-mail or password")
    user.failed_logins = 0
    user.locked_until = None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    await audit(db, request, user.id, "auth.login", email)
    await db.commit()
    token, exp = create_session_token(user.id, user.token_version)
    _set_cookie(response, token, exp)
    return user


@router.post("/logout", status_code=204)
async def logout(response: Response):
    response.delete_cookie(get_settings().session_cookie_name, path="/")


@router.post("/logout-all", status_code=204)
async def logout_all(
    request: Request, response: Response, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
):
    """Revoke every session of the current user (bumps the token version)."""
    user.token_version += 1
    await audit(db, request, user.id, "auth.logout_all")
    await db.commit()
    response.delete_cookie(get_settings().session_cookie_name, path="/")


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)):
    return user
