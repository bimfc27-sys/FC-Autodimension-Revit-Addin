"""DVR/NVR channel discovery through AUTHORISED interfaces only.

* ONVIF: profiles are grouped by video source -> one channel per source.
* Hikvision ISAPI: /ISAPI/ContentMgmt/InputProxy/channels (NVR IP channels),
  falling back to /ISAPI/System/Video/inputs/channels (DVR analogue inputs).
* Dahua / Amcrest: configManager ChannelTitle.

Every request uses the owner's credentials exactly once per call; nothing is
guessed and no address ranges are probed. Unsupported recorders can still be
registered manually channel by channel.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from ..models import Device
from .cameras import get_device_credentials, http_get, onvif_client, route_for
from .onvif import OnvifError, group_channels


class DiscoveryError(Exception):
    def __init__(self, message: str, auth_failed: bool = False):
        super().__init__(message)
        self.auth_failed = auth_failed


@dataclass
class Channel:
    index: int
    name: str
    source_token: str | None = None
    main_profile: str | None = None
    sub_profile: str | None = None
    main_uri: str | None = None
    sub_uri: str | None = None
    snapshot_uri: str | None = None
    resolution: str | None = None

    def public(self) -> dict:
        """What the browser may see: no stream URIs."""
        return {
            "index": self.index,
            "name": self.name,
            "main_profile": self.main_profile,
            "sub_profile": self.sub_profile,
            "resolution": self.resolution,
            "has_sub_stream": bool(self.sub_profile or self.sub_uri),
        }


def _web_base(dev: Device) -> str:
    host = f"[{dev.host}]" if ":" in dev.host else dev.host
    scheme = "https" if dev.use_tls else "http"
    return f"{scheme}://{host}:{dev.port or (443 if dev.use_tls else 80)}"


async def discover(dev: Device) -> tuple[list[Channel], str | None]:
    user, pw = get_device_credentials(dev)
    if dev.protocol == "onvif":
        client = onvif_client(dev.host, dev.port, dev.use_tls, user, pw, route_for(dev))
        try:
            info = await client.discover()
        except OnvifError as exc:
            raise DiscoveryError(str(exc), exc.auth_failed) from exc
        chans = []
        for c in group_channels(info.profiles):
            res = f"{c.main.width}×{c.main.height}" if c.main.width else None
            chans.append(
                Channel(
                    index=c.index,
                    name=c.name,
                    source_token=c.source_token,
                    main_profile=c.main.token,
                    sub_profile=c.sub.token if c.sub else None,
                    main_uri=c.main.stream_uri,
                    sub_uri=c.sub.stream_uri if c.sub else None,
                    snapshot_uri=c.main.snapshot_uri,
                    resolution=res,
                )
            )
        model = " ".join(x for x in (info.manufacturer, info.model) if x) or None
        return chans, model
    if dev.vendor == "hikvision":
        return await _hikvision(dev, user, pw), "Hikvision"
    if dev.vendor in ("dahua", "amcrest"):
        return await _dahua(dev, user, pw), dev.vendor.title()
    raise DiscoveryError("Automatic channel discovery is not available for this recorder; add channels manually.")


def _strip_ns(root: ET.Element) -> None:
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


async def _hikvision(dev: Device, user: str, pw: str) -> list[Channel]:
    route = route_for(dev)
    for path, item in (
        ("/ISAPI/ContentMgmt/InputProxy/channels", "InputProxyChannel"),
        ("/ISAPI/System/Video/inputs/channels", "VideoInputChannel"),
    ):
        r = await http_get(route, _web_base(dev) + path, user, pw)
        if r.status_code in (401, 403):
            raise DiscoveryError("Recorder rejected the credentials", auth_failed=True)
        if r.status_code != 200:
            continue
        try:
            root = ET.fromstring(r.content[:1_000_000])
        except ET.ParseError:
            continue
        _strip_ns(root)
        chans = []
        for el in root.iter(item):
            try:
                idx = int(el.findtext("id") or "0")
            except ValueError:
                continue
            if idx > 0:
                chans.append(Channel(index=idx, name=(el.findtext("name") or f"Channel {idx}").strip()))
        if chans:
            return sorted(chans, key=lambda c: c.index)
    raise DiscoveryError("Recorder did not report any channels through ISAPI")


async def _dahua(dev: Device, user: str, pw: str) -> list[Channel]:
    r = await http_get(route_for(dev), _web_base(dev) + "/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle", user, pw)
    if r.status_code in (401, 403):
        raise DiscoveryError("Recorder rejected the credentials", auth_failed=True)
    if r.status_code != 200:
        raise DiscoveryError(f"Recorder returned HTTP {r.status_code}")
    chans = []
    for m in re.finditer(r"table\.ChannelTitle\[(\d+)\]\.Name=(.*)", r.text[:200_000]):
        chans.append(Channel(index=int(m.group(1)) + 1, name=m.group(2).strip() or f"Channel {int(m.group(1)) + 1}"))
    if not chans:
        raise DiscoveryError("Recorder did not report any channels")
    return chans
