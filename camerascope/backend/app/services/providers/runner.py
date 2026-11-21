from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db import sessionmaker
from .base import record_sync, upsert
from .sources import ALL_PROVIDERS

log = logging.getLogger(__name__)


def enabled() -> list:
    names = get_settings().enabled_providers
    return [ALL_PROVIDERS[n] for n in names if n in ALL_PROVIDERS]


async def sync_provider(db: AsyncSession, name: str) -> tuple[bool, int, str]:
    provider = ALL_PROVIDERS[name]
    try:
        records = await provider.fetch_all()
        n = await upsert(db, provider.name, records)
        await record_sync(db, provider.name, True, n, f"{n} cameras")
        return True, n, f"{n} cameras"
    except Exception as exc:
        msg = f"{exc.__class__.__name__}: {str(exc)[:300]}"
        log.warning("provider %s sync failed: %s", name, msg)
        await record_sync(db, provider.name, False, 0, msg)
        return False, 0, msg


async def sync_all_bulk() -> None:
    for p in enabled():
        if p.mode != "bulk":
            continue
        async with sessionmaker()() as db:
            await sync_provider(db, p.name)
            await db.commit()


async def periodic_sync(stop: asyncio.Event) -> None:
    s = get_settings()
    first = True
    while not stop.is_set():
        if not first or s.sync_providers_on_startup:
            await sync_all_bulk()
        first = False
        try:
            await asyncio.wait_for(stop.wait(), timeout=s.provider_sync_interval_hours * 3600)
        except asyncio.TimeoutError:
            pass


async def discover_area(db: AsyncSession, lat: float, lon: float, radius_km: float) -> tuple[dict, dict]:
    imported, errors = {}, {}
    for p in enabled():
        if p.mode != "area":
            continue
        try:
            records = await p.fetch_area(lat, lon, radius_km)
            imported[p.name] = await upsert(db, p.name, records)
        except Exception as exc:
            errors[p.name] = f"{exc.__class__.__name__}: {str(exc)[:200]}"
    return imported, errors
