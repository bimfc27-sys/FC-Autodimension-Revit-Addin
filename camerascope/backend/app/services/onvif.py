"""Minimal ONVIF client (Device + Media services) using WS-Security UsernameToken.

Only the calls CameraScope needs are implemented. All requests go through the
network guard and use ONLY the credentials the camera owner provided.
"""
from __future__ import annotations

import base64
import hashlib
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .http import guarded_request, make_client

NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
}
MAX_RESPONSE = 512 * 1024


class OnvifError(Exception):
    def __init__(self, message: str, auth_failed: bool = False):
        super().__init__(message)
        self.auth_failed = auth_failed


@dataclass
class OnvifProfile:
    token: str
    name: str
    encoding: str | None = None
    width: int | None = None
    height: int | None = None
    stream_uri: str | None = None
    snapshot_uri: str | None = None


@dataclass
class OnvifDevice:
    manufacturer: str | None = None
    model: str | None = None
    firmware: str | None = None
    media_xaddr: str | None = None
    profiles: list[OnvifProfile] = field(default_factory=list)


def _security_header(username: str, password: str) -> str:
    if not username:
        return ""
    nonce = os.urandom(16)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + password.encode()).digest()).decode()

    def esc(v: str) -> str:
        return v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    return (
        '<s:Header><Security s:mustUnderstand="1" xmlns="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-wssecurity-secext-1.0.xsd"><UsernameToken>'
        f"<Username>{esc(username)}</Username>"
        '<Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
        f"{digest}</Password>"
        '<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
        f"{base64.b64encode(nonce).decode()}</Nonce>"
        '<Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
        f"{created}</Created></UsernameToken></Security></s:Header>"
    )


class OnvifClient:
    def __init__(self, host: str, port: int, username: str, password: str, use_tls: bool = False):
        scheme = "https" if use_tls else "http"
        h = f"[{host}]" if ":" in host else host
        self.device_url = f"{scheme}://{h}:{port}/onvif/device_service"
        self.username = username
        self.password = password

    async def _call(self, url: str, body: str) -> ET.Element:
        envelope = (
            f'<?xml version="1.0" encoding="UTF-8"?><s:Envelope xmlns:s="{NS["s"]}" '
            f'xmlns:tds="{NS["tds"]}" xmlns:trt="{NS["trt"]}" xmlns:tt="{NS["tt"]}">'
            f"{_security_header(self.username, self.password)}<s:Body>{body}</s:Body></s:Envelope>"
        )
        async with make_client() as client:
            resp = await guarded_request(
                client,
                "POST",
                url,
                content=envelope.encode(),
                headers={"Content-Type": "application/soap+xml; charset=utf-8"},
            )
        if len(resp.content) > MAX_RESPONSE:
            raise OnvifError("ONVIF response too large")
        text = resp.content
        if resp.status_code in (401, 403):
            raise OnvifError("ONVIF authentication failed (check username/password)", auth_failed=True)
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise OnvifError(f"Device did not return valid ONVIF XML (HTTP {resp.status_code})") from exc
        fault = root.find(".//s:Fault", NS)
        if fault is not None:
            reason = " ".join(t.strip() for t in fault.itertext() if t.strip())
            auth = any(k in reason for k in ("NotAuthorized", "FailedAuthentication", "Sender not Authorized"))
            raise OnvifError(f"ONVIF fault: {reason[:300]}", auth_failed=auth)
        return root

    async def get_device_information(self) -> dict:
        root = await self._call(self.device_url, "<tds:GetDeviceInformation/>")
        r = root.find(".//tds:GetDeviceInformationResponse", NS)
        if r is None:
            return {}
        return {child.tag.split("}")[1]: (child.text or "") for child in r}

    async def get_media_xaddr(self) -> str:
        root = await self._call(
            self.device_url, "<tds:GetCapabilities><tds:Category>Media</tds:Category></tds:GetCapabilities>"
        )
        x = root.find(".//tt:Media/tt:XAddr", NS)
        if x is None or not x.text:
            raise OnvifError("Device does not expose an ONVIF Media service")
        return x.text.strip()

    async def get_profiles(self, media_url: str) -> list[OnvifProfile]:
        root = await self._call(media_url, "<trt:GetProfiles/>")
        out = []
        for p in root.findall(".//trt:Profiles", NS):
            token = p.get("token", "")
            name = p.findtext("tt:Name", default=token, namespaces=NS)
            enc = p.findtext(".//tt:VideoEncoderConfiguration/tt:Encoding", namespaces=NS)
            w = p.findtext(".//tt:VideoEncoderConfiguration/tt:Resolution/tt:Width", namespaces=NS)
            h = p.findtext(".//tt:VideoEncoderConfiguration/tt:Resolution/tt:Height", namespaces=NS)
            out.append(OnvifProfile(token, name, enc, int(w) if w else None, int(h) if h else None))
        return out

    async def get_stream_uri(self, media_url: str, token: str) -> str:
        body = (
            "<trt:GetStreamUri><trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream>"
            "<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup>"
            f"<trt:ProfileToken>{_xml_text(token)}</trt:ProfileToken></trt:GetStreamUri>"
        )
        root = await self._call(media_url, body)
        uri = root.findtext(".//tt:Uri", namespaces=NS)
        if not uri:
            raise OnvifError("Device returned no stream URI")
        return uri.strip()

    async def get_snapshot_uri(self, media_url: str, token: str) -> str | None:
        try:
            root = await self._call(
                media_url, f"<trt:GetSnapshotUri><trt:ProfileToken>{_xml_text(token)}</trt:ProfileToken></trt:GetSnapshotUri>"
            )
        except OnvifError as exc:
            if exc.auth_failed:
                raise
            return None
        uri = root.findtext(".//tt:Uri", namespaces=NS)
        return uri.strip() if uri else None

    async def discover(self) -> OnvifDevice:
        dev = OnvifDevice()
        info = await self.get_device_information()
        dev.manufacturer, dev.model, dev.firmware = (
            info.get("Manufacturer"),
            info.get("Model"),
            info.get("FirmwareVersion"),
        )
        dev.media_xaddr = await self.get_media_xaddr()
        dev.profiles = await self.get_profiles(dev.media_xaddr)
        for p in dev.profiles[:6]:
            try:
                p.stream_uri = await self.get_stream_uri(dev.media_xaddr, p.token)
            except OnvifError as exc:
                if exc.auth_failed:
                    raise
            p.snapshot_uri = await self.get_snapshot_uri(dev.media_xaddr, p.token)
        return dev


def _xml_text(v: str) -> str:
    return v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
