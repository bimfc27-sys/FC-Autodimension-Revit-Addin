"""Authenticated reverse proxy in front of the MediaMTX gateway.

The gateway's HLS and WHEP HTTP ports are never published; every request is
authorised here against the requesting user's session:
  * private paths  -> the camera must belong to the user
  * public paths   -> the camera must exist and be a relayed public feed
"""
from __future__ import annotations

import re

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..deps import current_user
from ..models import PrivateCamera, PublicCamera, User
from ..services import mediamtx

router = APIRouter(prefix="/api/media", tags=["media gateway"])
_FILE_RE = re.compile(r"^[A-Za-z0-9_\-./]{1,200}$")
_HOP = {"connection", "keep-alive", "transfer-encoding", "te", "upgrade", "proxy-authenticate", "proxy-authorization", "trailer"}


async def authorize_path(db: AsyncSession, user: User, path: str) -> None:
    parsed = mediamtx.parse_path_name(path)
    if not parsed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown stream")
    kind, cam_id, _ = parsed
    if kind == "private":
        cam = await db.get(PrivateCamera, cam_id)
        if not cam or cam.owner_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown stream")
    else:
        cam = await db.get(PublicCamera, cam_id)
        if not cam or cam.stream_type != "gateway":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown stream")


_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0), trust_env=False)
    return _client


def _copy_headers(resp: httpx.Response) -> dict:
    return {k: v for k, v in resp.headers.items() if k.lower() not in _HOP and k.lower() not in ("content-length", "content-encoding", "access-control-allow-origin", "access-control-allow-credentials", "server")}


@router.get("/{path}/hls/{file:path}")
async def hls(path: str, file: str, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    await authorize_path(db, user, path)
    if not _FILE_RE.match(file) or ".." in file:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bad file")
    url = f"{get_settings().mediamtx_hls_url}/{path}/{file}"
    try:
        req = client().build_request("GET", url, params=dict(request.query_params))
        resp = await client().send(req, stream=True)
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Gateway unavailable: {exc.__class__.__name__}") from exc

    async def body():
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()

    headers = _copy_headers(resp)
    headers["Cache-Control"] = "no-store"
    return StreamingResponse(body(), status_code=resp.status_code, headers=headers)


@router.post("/{path}/whep")
async def whep_offer(path: str, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    await authorize_path(db, user, path)
    if request.headers.get("content-type", "").split(";")[0] != "application/sdp":
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Expected application/sdp")
    offer = await request.body()
    if len(offer) > 64 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "SDP too large")
    try:
        resp = await client().post(
            f"{get_settings().mediamtx_webrtc_url}/{path}/whep",
            content=offer,
            headers={"Content-Type": "application/sdp"},
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Gateway unavailable: {exc.__class__.__name__}") from exc
    headers = {}
    loc = resp.headers.get("location")
    if loc:
        # MediaMTX returns /<path>/whep/<session-uuid>; rewrite to our proxy.
        session = loc.rstrip("/").rsplit("/", 1)[-1]
        headers["Location"] = f"/api/media/{path}/whep/{session}"
    for h in ("etag", "accept-patch"):
        if h in resp.headers:
            headers[h] = resp.headers[h]
    return Response(resp.content, status_code=resp.status_code, media_type=resp.headers.get("content-type"), headers=headers)


@router.api_route("/{path}/whep/{session}", methods=["PATCH", "DELETE"])
async def whep_session(path: str, session: str, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    await authorize_path(db, user, path)
    if not re.fullmatch(r"[A-Za-z0-9\-]{8,64}", session):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bad session id")
    body = await request.body()
    if len(body) > 16 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Body too large")
    try:
        resp = await client().request(
            request.method,
            f"{get_settings().mediamtx_webrtc_url}/{path}/whep/{session}",
            content=body,
            headers={k: v for k, v in request.headers.items() if k.lower() in ("content-type", "if-match")},
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Gateway unavailable: {exc.__class__.__name__}") from exc
    return Response(resp.content, status_code=resp.status_code, media_type=resp.headers.get("content-type"))
