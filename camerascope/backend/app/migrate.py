"""Run database migrations at startup (and via `python -m app.migrate`).

Databases created by CameraScope 1.0 (tables created with create_all, no
alembic_version table) are stamped at the 0001 baseline before upgrading.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from .config import get_settings

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]


def _config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


async def _needs_stamp() -> bool:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as conn:
            tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
    finally:
        await engine.dispose()
    return "users" in tables and "alembic_version" not in tables


def upgrade_sync() -> None:
    cfg = _config()
    if asyncio.run(_needs_stamp()):
        log.info("existing 1.0 schema found, stamping baseline 0001")
        command.stamp(cfg, "0001")
    command.upgrade(cfg, "head")


async def upgrade() -> None:
    # Alembic's env.py uses asyncio.run, so run it in a worker thread.
    await asyncio.to_thread(upgrade_sync)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    upgrade_sync()
