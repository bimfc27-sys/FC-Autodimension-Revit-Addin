import base64
import os

os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 40)
os.environ.setdefault("CREDENTIAL_KEYS", "k1:" + base64.b64encode(b"\x01" * 32).decode() + ",k0:" + base64.b64encode(b"\x02" * 32).decode())
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://camerascope:camerascope@localhost:5432/camerascope_test")
os.environ["ENVIRONMENT"] = "test"
os.environ["COOKIE_SECURE"] = "false"
os.environ["PROVIDERS_ENABLED"] = "seed"
os.environ["MEDIAMTX_API_URL"] = "http://127.0.0.1:1"  # unreachable on purpose

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.ratelimit import limiter  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
async def _schema():
    from app import models  # noqa: F401

    from sqlalchemy import text

    from app.migrate import upgrade

    async with engine().begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await upgrade()  # the real Alembic migrations
    yield
    await engine().dispose()


@pytest.fixture(autouse=True)
def _reset_limits():
    limiter.reset()


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


_counter = iter(range(10_000))


async def make_user(client, prefix="user"):
    email = f"{prefix}{next(_counter)}@example.com"
    r = await client.post("/api/auth/register", json={"email": email, "password": "Sup3r-Secret-pass", "display_name": prefix})
    assert r.status_code == 201, r.text
    return email


def pytest_collection_modifyitems(items):
    # Run every async test on the session loop shared with the DB engine.
    from pytest_asyncio import is_async_test

    marker = pytest.mark.asyncio(loop_scope="session")
    for item in items:
        if is_async_test(item):
            item.add_marker(marker, append=False)
