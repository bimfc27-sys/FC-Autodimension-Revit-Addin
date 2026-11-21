import pytest

from app.config import get_settings
from app.services.netguard import TargetNotAllowed, check_url


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8080/",
        "http://localhost/",
        "rtsp://[::1]/stream",
        "file:///etc/passwd",
        "gopher://8.8.8.8/",
        "http://postgres:5432/",
        "http://metadata.google.internal/",
        "http://0.0.0.0/",
    ],
)
async def test_blocked_targets(url):
    with pytest.raises(TargetNotAllowed):
        await check_url(url)


async def test_public_ip_allowed():
    t = await check_url("rtsp://8.8.8.8:554/live")
    assert not t.is_private


async def test_private_ranges_toggle(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "allow_private_network_targets", True)
    assert (await check_url("rtsp://192.168.1.20/stream1")).is_private
    monkeypatch.setattr(s, "allow_private_network_targets", False)
    with pytest.raises(TargetNotAllowed):
        await check_url("rtsp://192.168.1.20/stream1")
    with pytest.raises(TargetNotAllowed):
        await check_url("http://10.0.0.5/")
