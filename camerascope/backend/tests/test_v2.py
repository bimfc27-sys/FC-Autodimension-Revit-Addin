"""Gateways, sites (sharing/roles), groups, favorites, NVR channel grouping, agent policy."""
import importlib.util
import ipaddress
import sys
import uuid
from pathlib import Path

import pytest

from app.services.onvif import OnvifProfile, group_channels

from .conftest import make_user
from .test_api import CAM

AGENT = Path(__file__).resolve().parents[2] / "gateway" / "agent"


async def _login(client, email):
    client.cookies.clear()
    r = await client.post("/api/auth/login", json={"email": email, "password": "Sup3r-Secret-pass"})
    assert r.status_code == 200


async def test_site_sharing_roles(client):
    owner = await make_user(client, "bowner")
    cam = (await client.post("/api/cameras", json=CAM)).json()
    site = (await client.post("/api/sites", json={"name": "HQ building"})).json()
    assert site["role"] == "manager"
    # owner assigns camera to the building
    r = await client.patch(f"/api/cameras/{cam['id']}", json={"site_id": site["id"]})
    assert r.status_code == 200 and r.json()["site"]["name"] == "HQ building"

    viewer = await make_user(client, "bviewer")
    stranger = await make_user(client, "bstranger")
    await _login(client, owner)
    assert (await client.post(f"/api/sites/{site['id']}/members", json={"email": viewer, "role": "viewer"})).status_code == 201
    assert (await client.post(f"/api/sites/{site['id']}/members", json={"email": "nobody@example.com"})).status_code == 404

    await _login(client, viewer)
    shared = (await client.get("/api/cameras?scope=shared")).json()
    assert [c["id"] for c in shared] == [cam["id"]]
    c = shared[0]
    assert c["access"] == "shared" and c["host"] is None and c["username"] is None
    assert "main_path" not in c["config"]
    # viewer can view but not modify / delete / diagnose / add members
    assert (await client.get(f"/api/cameras/{cam['id']}")).status_code == 200
    assert (await client.patch(f"/api/cameras/{cam['id']}", json={"name": "x"})).status_code == 404
    assert (await client.delete(f"/api/cameras/{cam['id']}")).status_code == 404
    assert (await client.post("/api/diagnostics", json={"camera_kind": "private", "camera_id": cam["id"]})).status_code == 404
    assert (await client.post(f"/api/sites/{site['id']}/members", json={"email": stranger})).status_code == 403
    # gateway unavailable in tests -> playback reaches the gateway step (authorised), not 404
    assert (await client.get(f"/api/cameras/{cam['id']}/playback")).status_code == 502
    assert (await client.put(f"/api/favorites/private/{cam['id']}")).status_code == 204
    assert [f["id"] for f in (await client.get("/api/favorites")).json()] == [cam["id"]]

    await _login(client, stranger)
    assert (await client.get(f"/api/cameras/{cam['id']}")).status_code == 404
    assert (await client.get("/api/cameras")).json() == []
    assert (await client.put(f"/api/favorites/private/{cam['id']}")).status_code == 404
    path = f"prv-{uuid.UUID(cam['id']).hex}-high"
    assert (await client.get(f"/api/media/{path}/hls/index.m3u8")).status_code == 404

    # removing the viewer revokes access immediately
    await _login(client, owner)
    members = (await client.get(f"/api/sites/{site['id']}/members")).json()
    vid = next(m["user_id"] for m in members if m["email"] == viewer)
    assert (await client.delete(f"/api/sites/{site['id']}/members/{vid}")).status_code == 204
    await _login(client, viewer)
    assert (await client.get(f"/api/cameras/{cam['id']}")).status_code == 404
    assert (await client.get("/api/favorites")).json() == []


async def test_viewer_cannot_assign_cameras_to_site(client):
    owner = await make_user(client, "s2owner")
    site = (await client.post("/api/sites", json={"name": "Shop"})).json()
    viewer = await make_user(client, "s2viewer")
    await _login(client, owner)
    await client.post(f"/api/sites/{site['id']}/members", json={"email": viewer, "role": "viewer"})
    await _login(client, viewer)
    r = await client.post("/api/cameras", json=dict(CAM, site_id=site["id"]))
    assert r.status_code == 403


async def test_groups(client):
    await make_user(client, "grp")
    cam = (await client.post("/api/cameras", json=CAM)).json()
    g = (await client.post("/api/groups", json={"name": "Entrances"})).json()
    r = await client.put(f"/api/groups/{g['id']}/items", json={"items": [{"camera_kind": "private", "camera_id": cam["id"]}]})
    assert r.status_code == 200 and [c["id"] for c in r.json()["cameras"]] == [cam["id"]]
    bogus = await client.put(f"/api/groups/{g['id']}/items", json={"items": [{"camera_kind": "private", "camera_id": str(uuid.uuid4())}]})
    assert bogus.status_code == 404
    assert len((await client.get("/api/groups")).json()) == 1


async def test_gateway_enrollment_single_use(client):
    await make_user(client, "gw")
    r = await client.post("/api/gateways", json={"name": "Home"})
    assert r.status_code == 201
    code = r.json()["enrollment_code"]
    gid = r.json()["gateway"]["id"]
    assert r.json()["gateway"]["status"] == "pending"
    client.cookies.clear()
    assert (await client.post("/api/gateways/enroll", json={"code": code[:-2] + "xx"})).status_code == 401
    ok = await client.post("/api/gateways/enroll", json={"code": code})
    assert ok.status_code == 200 and ok.json()["gateway_id"] == gid and len(ok.json()["secret"]) >= 40
    assert (await client.post("/api/gateways/enroll", json={"code": code})).status_code == 401  # single use


async def test_gateway_camera_requires_own_gateway(client):
    await make_user(client, "gwa")
    gw = (await client.post("/api/gateways", json={"name": "A"})).json()["gateway"]
    # LAN address behind a gateway is accepted (validated by the gateway, not the cloud)
    r = await client.post("/api/cameras", json=dict(CAM, host="192.168.50.20", gateway_id=gw["id"]))
    assert r.status_code == 201 and r.json()["connection"] == "gateway" and r.json()["status"] == "gateway_offline"
    assert r.json()["gateway"]["name"] == "A"
    # gateway offline -> clear 503, not a hang
    assert (await client.get(f"/api/cameras/{r.json()['id']}/playback")).status_code == 503
    # cannot delete a gateway that cameras use
    assert (await client.delete(f"/api/gateways/{gw['id']}")).status_code == 409
    await make_user(client, "gwb")
    r = await client.post("/api/cameras", json=dict(CAM, gateway_id=gw["id"]))
    assert r.status_code == 400  # someone else's gateway


def test_onvif_channel_grouping():
    ps = [
        OnvifProfile("p1", "ch1 main", "VS1", "H264", 1920, 1080),
        OnvifProfile("p2", "ch1 sub", "VS1", "H264", 640, 360),
        OnvifProfile("p3", "ch2 main", "VS2", "H264", 2560, 1440),
        OnvifProfile("p4", "ch2 sub", "VS2", "H264", 704, 576),
        OnvifProfile("p5", "ch3", "VS3", "H264", 1280, 720),
    ]
    chans = group_channels(ps)
    assert [(c.index, c.main.token, c.sub.token if c.sub else None) for c in chans] == [
        (1, "p1", "p2"), (2, "p3", "p4"), (3, "p5", None)
    ]


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setenv("CLOUD_URL", "https://cloud.example.com")
    monkeypatch.setenv("ALLOWED_SUBNETS", "192.168.50.0/24")
    monkeypatch.setenv("RELAY_PASS", "x" * 20)
    sys.path.insert(0, str(AGENT))
    spec = importlib.util.spec_from_file_location("camerascope_gateway", AGENT / "camerascope_gateway.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    sys.path.remove(str(AGENT))


async def test_agent_policy(agent):
    cfg = agent.Config()
    assert cfg.subnets == [ipaddress.ip_network("192.168.50.0/24")]
    assert await agent.resolve_allowed(cfg, "192.168.50.20", 554) == "192.168.50.20"
    for host, port in (("192.168.51.20", 554), ("10.0.0.1", 554), ("169.254.169.254", 80), ("127.0.0.1", 554), ("192.168.50.20", 22)):
        with pytest.raises(agent.Refused):
            await agent.resolve_allowed(cfg, host, port)
    with pytest.raises(agent.Refused):
        await agent.stream_ensure(cfg, {"name": "prv-" + "a" * 32 + "-high", "source": "rtsp://10.1.1.1/x"})
    with pytest.raises(agent.Refused):
        await agent.stream_ensure(cfg, {"name": "../etc", "source": "rtsp://192.168.50.2/x"})
    with pytest.raises(agent.Refused):
        await agent.stream_ensure(cfg, {"name": "prv-" + "a" * 32 + "-high", "source": "file:///etc/passwd"})
    res = await agent.tcp_check(cfg, {"targets": [{"id": "a", "host": "10.9.9.9", "port": 554}]})
    assert "refused" in res["a"]
