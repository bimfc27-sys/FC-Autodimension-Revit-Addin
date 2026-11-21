"""Public camera provider framework.

A provider returns camera records from an OFFICIAL public source (government
traffic agencies, OpenStreetMap community data, webcam directories with an
API). Providers never scan IP ranges or search engines for exposed devices.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import ProviderSync, PublicCamera

log = logging.getLogger(__name__)


@dataclass
class CameraRecord:
    external_id: str
    name: str
    latitude: float
    longitude: float
    camera_type: str  # traffic | webcam | public_ip
    stream_type: str  # hls | mjpeg | image | webpage | gateway
    source_name: str
    stream_url: str | None = None
    snapshot_url: str | None = None
    page_url: str | None = None
    source_url: str | None = None
    description: str | None = None
    license: str | None = None
    snapshot_allowed: bool = False
    status: str = "unknown"
    extra: dict = field(default_factory=dict)

    def valid(self) -> bool:
        return (
            -90 <= self.latitude <= 90
            and -180 <= self.longitude <= 180
            and not (self.latitude == 0 and self.longitude == 0)
            and bool(self.stream_url or self.snapshot_url or self.page_url)
        )


class Provider:
    name: str = "base"
    title: str = ""
    description: str = ""
    # area providers are queried on demand around a point; bulk providers sync everything
    mode: str = "bulk"

    async def fetch_all(self) -> list[CameraRecord]:  # bulk
        return []

    async def fetch_area(self, lat: float, lon: float, radius_km: float) -> list[CameraRecord]:  # area
        return []


async def upsert(db: AsyncSession, provider: str, records: list[CameraRecord]) -> int:
    now = datetime.now(timezone.utc)
    count = 0
    existing = {
        c.external_id: c
        for c in (
            await db.scalars(
                select(PublicCamera).where(
                    PublicCamera.provider == provider,
                    PublicCamera.external_id.in_([r.external_id for r in records]),
                )
            )
        ).all()
    } if records else {}
    for r in records:
        if not r.valid():
            continue
        cam = existing.get(r.external_id) or PublicCamera(provider=provider, external_id=r.external_id)
        cam.name = r.name[:300]
        cam.description = r.description
        cam.camera_type = r.camera_type
        cam.latitude, cam.longitude = r.latitude, r.longitude
        cam.stream_type = r.stream_type
        cam.stream_url, cam.snapshot_url, cam.page_url = r.stream_url, r.snapshot_url, r.page_url
        cam.source_name, cam.source_url, cam.license = r.source_name, r.source_url, r.license
        cam.snapshot_allowed = r.snapshot_allowed
        cam.status = r.status
        cam.last_checked_at = now
        if cam.id is None:
            db.add(cam)
        count += 1
    return count


async def record_sync(db: AsyncSession, provider: str, ok: bool, imported: int, message: str | None) -> None:
    row = await db.get(ProviderSync, provider) or ProviderSync(provider=provider)
    row.last_run_at = datetime.now(timezone.utc)
    row.last_ok, row.imported, row.message = ok, imported, (message or "")[:1000]
    db.add(row)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    dlat = radius_km / 111.0
    dlon = radius_km / max(1e-6, 111.0 * math.cos(math.radians(lat)))
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon


def guess_stream_type(url: str) -> str:
    u = url.lower().split("?")[0]
    if u.endswith(".m3u8"):
        return "hls"
    if u.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
        return "image"
    if u.endswith((".mjpg", ".mjpeg", ".cgi")) or "mjpg" in u:
        return "mjpeg"
    return "webpage"
