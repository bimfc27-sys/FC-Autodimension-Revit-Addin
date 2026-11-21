import asyncio
import hashlib
import re
import uuid

import pytest
from sqlalchemy import select

from app.db import sessionmaker
from app.models import PrivateCamera
from app.services import netguard
from app.services.providers.base import upsert
from app.services.providers.sources import SeedProvider

from .conftest import make_user

CAM = {
    "name": "Garage",
    "protocol": "rtsp",
    "vendor": "generic",
    "host": "8.8.8.8",
    "port": 554,
    "config": {"main_path": "/stream1", "sub_path": "/stream2"},
    "credentials": {"username": "viewer", "password": "Cam-Secret-123"},
    "authorization_basis": "owner",
    "authorization_attested": True,
}


async def test_register_login_logout(client):
    email = await make_user(client)
    me = await client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["email"] == email
    await client.post("/api/auth/logout")
    client.cookies.clear()
    assert (await client.get("/api/auth/me")).status_code == 401
    r = await client.post("/api/auth/login", json={"email": email, "password": "Sup3r-Secret-pass"})
    assert r.status_code == 200
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()


async def test_weak_password_rejected(client):
    r = await client.post("/api/auth/register", json={"email": "weak@example.com", "password": "password", "display_name": "w"})
    assert r.status_code == 422


async def test_account_lockout(client):
    email = await make_user(client)
    client.cookies.clear()
    for _ in range(5):
        r = await client.post("/api/auth/login", json={"email": email, "password": "Wrong-pass-123"})
        assert r.status_code == 401
    # Even the right password is refused while locked, with the same generic message.
    r = await client.post("/api/auth/login", json={"email": email, "password": "Sup3r-Secret-pass"})
    assert r.status_code == 401 and r.json()["detail"] == "Invalid e-mail or password"


async def test_cross_site_post_refused(client):
    r = await client.post("/api/auth/logout", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


async def test_public_nearby_and_saved(client):
    async with sessionmaker()() as db:
        await upsert(db, "seed", await SeedProvider().fetch_all())
        await db.commit()
    r = await client.get("/api/public-cameras/nearby", params={"lat": 40.76, "lon": -73.98, "radius_km": 5})
    assert r.status_code == 200
    cams = r.json()
    assert any(c["name"] == "Times Square" for c in cams)
    assert all(c["kind"] == "public" for c in cams)
    far = await client.get("/api/public-cameras/nearby", params={"lat": 0.5, "lon": 0.5, "radius_km": 5})
    assert far.json() == []
    await make_user(client)
    cid = cams[0]["id"]
    assert (await client.put(f"/api/saved/{cid}")).status_code == 204
    assert [c["id"] for c in (await client.get("/api/saved")).json()] == [cid]
    pb = (await client.get(f"/api/public-cameras/{cid}/playback")).json()
    assert pb["mode"] == "webpage" and pb["page_url"].startswith("https://")
    assert (await client.post("/api/history", json={"camera_kind": "public", "camera_id": cid})).status_code == 204
    hist = (await client.get("/api/history")).json()
    assert hist[0]["camera"]["id"] == cid


async def test_private_camera_requires_attestation(client):
    await make_user(client)
    bad = dict(CAM, authorization_attested=False)
    assert (await client.post("/api/cameras", json=bad)).status_code == 422


async def test_private_camera_isolation_and_secrecy(client):
    await make_user(client, "owner")
    r = await client.post("/api/cameras", json=CAM)
    assert r.status_code == 201, r.text
    cam = r.json()
    assert cam["kind"] == "private" and cam["has_credentials"] and cam["username"] == "viewer"
    assert "Cam-Secret-123" not in r.text
    async with sessionmaker()() as db:
        row = await db.get(PrivateCamera, uuid.UUID(cam["id"]))
        assert b"Cam-Secret-123" not in row.credentials_ciphertext
    # another user cannot see or touch it
    client.cookies.clear()
    await make_user(client, "intruder")
    assert (await client.get(f"/api/cameras/{cam['id']}")).status_code == 404
    assert (await client.delete(f"/api/cameras/{cam['id']}")).status_code == 404
    assert (await client.get(f"/api/cameras/{cam['id']}/playback")).status_code == 404
    assert (await client.get("/api/cameras")).json() == []
    path = f"prv-{uuid.UUID(cam['id']).hex}-high"
    assert (await client.get(f"/api/media/{path}/hls/index.m3u8")).status_code == 404
    assert (await client.post("/api/history", json={"camera_kind": "private", "camera_id": cam["id"]})).status_code == 404


async def test_private_camera_blocked_hosts(client):
    await make_user(client)
    for host in ("169.254.169.254", "127.0.0.1", "localhost", "postgres"):
        r = await client.post("/api/cameras", json=dict(CAM, host=host))
        assert r.status_code == 400, host
    r = await client.post("/api/cameras", json=dict(CAM, host="http://evil/x"))
    assert r.status_code == 422


async def test_gateway_unavailable_is_reported(client):
    await make_user(client)
    cam = (await client.post("/api/cameras", json=CAM)).json()
    r = await client.get(f"/api/cameras/{cam['id']}/playback?quality=low")
    assert r.status_code == 502 and "gateway" in r.json()["detail"].lower()


async def test_unauthenticated_private_access(client):
    client.cookies.clear()
    assert (await client.get("/api/cameras")).status_code == 401
    assert (await client.post("/api/cameras", json=CAM)).status_code == 401


# ------------------------------------------------------------ RTSP probe


class FakeRtsp:
    """Tiny RTSP server requiring digest auth, counting auth attempts."""

    def __init__(self, user, pw):
        self.user, self.pw, self.attempts = user, pw, 0

    async def handle(self, reader, writer):
        try:
            while True:
                head = (await reader.readuntil(b"\r\n\r\n")).decode()
                method, uri, _ = head.split("\r\n")[0].split(" ")
                cseq = re.search(r"CSeq: (\d+)", head).group(1)
                if method == "OPTIONS":
                    writer.write(f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nPublic: DESCRIBE\r\n\r\n".encode())
                elif "Authorization" not in head:
                    writer.write(
                        f'RTSP/1.0 401 Unauthorized\r\nCSeq: {cseq}\r\nWWW-Authenticate: Digest realm="cam", nonce="abc"\r\n\r\n'.encode()
                    )
                else:
                    self.attempts += 1
                    resp = re.search(r'response="(\w+)"', head).group(1)
                    ha1 = hashlib.md5(f"{self.user}:cam:{self.pw}".encode()).hexdigest()
                    ha2 = hashlib.md5(f"DESCRIBE:{uri}".encode()).hexdigest()
                    if resp == hashlib.md5(f"{ha1}:abc:{ha2}".encode()).hexdigest():
                        sdp = "v=0\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
                        writer.write(f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nContent-Length: {len(sdp)}\r\n\r\n{sdp}".encode())
                    else:
                        writer.write(f"RTSP/1.0 401 Unauthorized\r\nCSeq: {cseq}\r\n\r\n".encode())
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass


@pytest.fixture
def allow_loopback(monkeypatch):
    nets = [n for n in netguard._ALWAYS_BLOCKED_NETS if str(n) != "127.0.0.0/8"]
    monkeypatch.setattr(netguard, "_ALWAYS_BLOCKED_NETS", nets)
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "netguard_deny_hosts", "postgres")
    monkeypatch.setattr(netguard, "_deny_ip_cache", set())


async def test_rtsp_probe_and_lockout(client, allow_loopback):
    fake = FakeRtsp("viewer", "Cam-Secret-123")
    server = await asyncio.start_server(fake.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        await make_user(client)
        good = (await client.post("/api/cameras", json=dict(CAM, host="127.0.0.1", port=port))).json()
        r = await client.post("/api/diagnostics", json={"camera_kind": "private", "camera_id": good["id"]})
        steps = {s["name"]: s for s in r.json()["steps"]}
        assert steps["RTSP handshake"]["ok"], steps
        assert "H264" in steps["RTSP handshake"]["detail"]
        assert (await client.get(f"/api/cameras/{good['id']}")).json()["status"] in ("online", "offline")

        bad = dict(CAM, host="127.0.0.1", port=port, credentials={"username": "viewer", "password": "wrong"})
        badcam = (await client.post("/api/cameras", json=bad)).json()
        attempts_before = fake.attempts
        for i in range(3):
            r = await client.post("/api/diagnostics", json={"camera_kind": "private", "camera_id": badcam["id"]})
            assert r.status_code == 200
            assert not r.json()["ok"]
        # exactly one auth attempt per diagnostic run - never retries / guesses
        assert fake.attempts - attempts_before == 3
        assert (await client.get(f"/api/cameras/{badcam['id']}")).json()["status"] == "auth_failed"
        r = await client.post("/api/diagnostics", json={"camera_kind": "private", "camera_id": badcam["id"]})
        assert r.status_code == 423
        assert (await client.get(f"/api/cameras/{badcam['id']}/playback")).status_code == 423
        # owner supplies new credentials -> unlocked
        r = await client.patch(f"/api/cameras/{badcam['id']}", json={"credentials": {"username": "viewer", "password": "Cam-Secret-123"}})
        assert r.status_code == 200 and r.json()["status"] == "unknown"
    finally:
        server.close()


def test_onvif_uri_host_is_pinned():
    from app.services.sources import stream_sources

    cam = PrivateCamera(
        id=uuid.uuid4(), owner_id=uuid.uuid4(), protocol="onvif", vendor="generic", host="cam.example.org", port=8080,
        use_tls=False,
        config={"resolved_main_uri": "rtsp://admin:x@192.168.1.10:554/main", "resolved_sub_uri": "rtsp://10.0.0.1/sub"},
    )
    s = stream_sources(cam)
    assert s["high"].url == "rtsp://cam.example.org:554/main"
    assert s["low"].url == "rtsp://cam.example.org/sub"


def test_vendor_presets():
    from app.services.sources import snapshot_source, stream_sources

    cam = PrivateCamera(id=uuid.uuid4(), owner_id=uuid.uuid4(), protocol="vendor_api", vendor="hikvision", host="1.2.3.4", port=None, use_tls=False, config={"channel": 2})
    s = stream_sources(cam)
    assert s["high"].url == "rtsp://1.2.3.4:554/Streaming/Channels/201"
    assert s["low"].url == "rtsp://1.2.3.4:554/Streaming/Channels/202"
    assert snapshot_source(cam) == "http://1.2.3.4/ISAPI/Streaming/channels/201/picture"
