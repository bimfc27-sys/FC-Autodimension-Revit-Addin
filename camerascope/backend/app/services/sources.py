"""Turns a stored private camera into concrete source URLs.

Credentials are injected at the last moment and never persisted in URLs.
Vendor presets are the manufacturers' DOCUMENTED stream/snapshot endpoints;
they are used only with the credentials the owner supplied.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlsplit, urlunsplit

from ..models import PrivateCamera

# Official documented endpoints (Axis VAPIX, Hikvision ISAPI, Dahua/Amcrest HTTP API, Reolink API).
VENDOR_PRESETS: dict[str, dict] = {
    "axis": {
        "rtsp_main": "/axis-media/media.amp?resolution=1920x1080",
        "rtsp_sub": "/axis-media/media.amp?resolution=640x360",
        "snapshot": "/axis-cgi/jpg/image.cgi",
        "mjpeg": "/axis-cgi/mjpg/video.cgi",
        "docs": "https://developer.axis.com/vapix/",
    },
    "hikvision": {
        "rtsp_main": "/Streaming/Channels/{ch}01",
        "rtsp_sub": "/Streaming/Channels/{ch}02",
        "snapshot": "/ISAPI/Streaming/channels/{ch}01/picture",
        "docs": "ISAPI developer guide",
    },
    "dahua": {
        "rtsp_main": "/cam/realmonitor?channel={ch}&subtype=0",
        "rtsp_sub": "/cam/realmonitor?channel={ch}&subtype=1",
        "snapshot": "/cgi-bin/snapshot.cgi?channel={ch}",
        "mjpeg": "/cgi-bin/mjpg/video.cgi?channel={ch}&subtype=1",
        "docs": "Dahua HTTP API",
    },
    "amcrest": {
        "rtsp_main": "/cam/realmonitor?channel={ch}&subtype=0",
        "rtsp_sub": "/cam/realmonitor?channel={ch}&subtype=1",
        "snapshot": "/cgi-bin/snapshot.cgi?channel={ch}",
        "mjpeg": "/cgi-bin/mjpg/video.cgi?channel={ch}&subtype=1",
        "docs": "Amcrest HTTP API",
    },
    "reolink": {
        "rtsp_main": "/h264Preview_{ch2}_main",
        "rtsp_sub": "/h264Preview_{ch2}_sub",
        "snapshot": "/cgi-bin/api.cgi?cmd=Snap&channel={ch0}",
        "docs": "Reolink camera HTTP API",
    },
}


@dataclass
class ResolvedSource:
    url: str  # WITHOUT credentials
    kind: str  # rtsp | hls | whep | mjpeg | http-image


def _fmt(path: str, ch: int) -> str:
    return path.format(ch=ch, ch0=ch - 1, ch2=f"{ch:02d}")


def _base(cam: PrivateCamera, scheme: str, default_port: int) -> str:
    host = f"[{cam.host}]" if ":" in cam.host else cam.host
    port = cam.port or default_port
    return f"{scheme}://{host}:{port}"


def stream_sources(cam: PrivateCamera) -> dict[str, ResolvedSource]:
    """Return {'high': src, 'low': src?} for gateway-relayed protocols."""
    cfg = cam.config or {}
    ch = int(cfg.get("channel") or 1)
    out: dict[str, ResolvedSource] = {}
    if cam.protocol in ("rtsp", "vendor_api", "onvif"):
        if cam.protocol == "onvif":
            main, sub = cfg.get("resolved_main_uri"), cfg.get("resolved_sub_uri")
            if main:
                out["high"] = ResolvedSource(pin_host(main, cam.host), "rtsp")
            if sub:
                out["low"] = ResolvedSource(pin_host(sub, cam.host), "rtsp")
            return out
        scheme = "rtsps" if cam.use_tls else "rtsp"
        base = _base(cam, scheme, 322 if cam.use_tls else 554)
        preset = VENDOR_PRESETS.get(cam.vendor, {})
        main = cfg.get("main_path") or (preset.get("rtsp_main") and _fmt(preset["rtsp_main"], ch))
        sub = cfg.get("sub_path") or (preset.get("rtsp_sub") and _fmt(preset["rtsp_sub"], ch))
        if main:
            out["high"] = ResolvedSource(base + main, "rtsp")
        if sub:
            out["low"] = ResolvedSource(base + sub, "rtsp")
    elif cam.protocol == "hls":
        base = _base(cam, "https" if cam.use_tls else "http", 443 if cam.use_tls else 80)
        if cfg.get("main_path"):
            out["high"] = ResolvedSource(base + cfg["main_path"], "hls")
        if cfg.get("sub_path"):
            out["low"] = ResolvedSource(base + cfg["sub_path"], "hls")
    elif cam.protocol == "webrtc":
        base = _base(cam, "wheps" if cam.use_tls else "whep", 443 if cam.use_tls else 80)
        if cfg.get("main_path"):
            out["high"] = ResolvedSource(base + cfg["main_path"], "whep")
        if cfg.get("sub_path"):
            out["low"] = ResolvedSource(base + cfg["sub_path"], "whep")
    return out


def mjpeg_source(cam: PrivateCamera) -> str | None:
    cfg = cam.config or {}
    base = _base(cam, "https" if cam.use_tls else "http", 443 if cam.use_tls else 80)
    if cam.protocol == "mjpeg" and cfg.get("main_path"):
        return base + cfg["main_path"]
    return None


def snapshot_source(cam: PrivateCamera) -> str | None:
    cfg = cam.config or {}
    ch = int(cfg.get("channel") or 1)
    if cfg.get("resolved_snapshot_uri"):
        return pin_host(cfg["resolved_snapshot_uri"], cam.host)
    http_port = 443 if cam.use_tls else 80
    # For RTSP/vendor cameras the HTTP API usually lives on the default web port.
    host = f"[{cam.host}]" if ":" in cam.host else cam.host
    scheme = "https" if cam.use_tls else "http"
    web_base = f"{scheme}://{host}" + ("" if cam.protocol in ("rtsp", "vendor_api") else f":{cam.port or http_port}")
    if cfg.get("snapshot_path"):
        return web_base + cfg["snapshot_path"]
    preset = VENDOR_PRESETS.get(cam.vendor, {})
    if preset.get("snapshot"):
        return web_base + _fmt(preset["snapshot"], ch)
    return None


def strip_userinfo(url: str) -> str:
    p = urlsplit(url)
    netloc = p.hostname or ""
    if ":" in netloc:
        netloc = f"[{netloc}]"
    if p.port:
        netloc += f":{p.port}"
    return urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))


def pin_host(url: str, host: str) -> str:
    """Replace the host of a device-reported URI with the host the owner configured.

    ONVIF devices behind NAT often report their LAN address, and a malicious
    device could report an arbitrary host. The owner-configured host is
    authoritative, so the device can never redirect CameraScope elsewhere.
    """
    p = urlsplit(url)
    h = f"[{host}]" if ":" in host else host
    netloc = h + (f":{p.port}" if p.port else "")
    return urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))


def with_credentials(url: str, username: str | None, password: str | None) -> str:
    if not username:
        return url
    p = urlsplit(url)
    netloc = f"{quote(username, safe='')}:{quote(password or '', safe='')}@{p.netloc}"
    return urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))


def reolink_snapshot(url: str, username: str, password: str) -> str:
    # Reolink's documented API takes credentials as query parameters.
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}rs=camerascope&user={quote(username)}&password={quote(password)}"
