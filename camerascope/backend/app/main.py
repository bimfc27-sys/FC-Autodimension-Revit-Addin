from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .db import init_db
from .routers import auth, devices, gateways, library, media, organize, private, public
from .services.cameras import status_monitor
from .services.providers.runner import periodic_sync

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("camerascope")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    stop = asyncio.Event()
    tasks = []
    if get_settings().environment != "test":
        tasks = [asyncio.create_task(periodic_sync(stop)), asyncio.create_task(status_monitor(stop))]
    yield
    stop.set()
    for t in tasks:
        t.cancel()


settings = get_settings()
app = FastAPI(
    title="CameraScope API",
    version="1.0.0",
    description="Discover public cameras and view authorised private cameras. "
    "Public feeds and private (owned/authorised) cameras are strictly separated.",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Content-Type", "If-Match"],
    )

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # CSRF defence in depth (cookies are already SameSite=Strict): refuse
    # state-changing requests a browser marks as cross-site.
    if request.method in UNSAFE and request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if request.url.path.startswith("/api/") and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    return response


for r in (auth.router, public.router, private.router, devices.router, gateways.router, organize.router, media.router, library.router):
    app.include_router(r)
