"""Minimal RTSP handshake prober (OPTIONS + DESCRIBE) used by diagnostics/tests.

It authenticates ONCE with the credentials the owner provided. It never retries
with other credentials and never attempts any non-standard request.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit

from .netguard import DEFAULT_PORTS, check_host


@dataclass
class RtspResult:
    ok: bool
    status: int | None
    detail: str
    auth_failed: bool = False
    codecs: list[str] | None = None


def _digest(user: str, pw: str, method: str, uri: str, chal: dict) -> str:
    ha1 = hashlib.md5(f"{user}:{chal['realm']}:{pw}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    resp = hashlib.md5(f"{ha1}:{chal['nonce']}:{ha2}".encode()).hexdigest()
    return (
        f'Digest username="{user}", realm="{chal["realm"]}", nonce="{chal["nonce"]}", '
        f'uri="{uri}", response="{resp}"'
    )


async def _read_response(reader: asyncio.StreamReader) -> tuple[int, dict, str]:
    head = await reader.readuntil(b"\r\n\r\n")
    lines = head.decode(errors="replace").split("\r\n")
    m = re.match(r"RTSP/1\.\d (\d{3})", lines[0])
    if not m:
        raise ValueError("not an RTSP server")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers.setdefault(k.strip().lower(), v.strip())
    body = ""
    length = int(headers.get("content-length", "0") or 0)
    if 0 < length <= 64 * 1024:
        body = (await reader.readexactly(length)).decode(errors="replace")
    return int(m.group(1)), headers, body


async def probe(
    url: str, username: str = "", password: str = "", timeout: float = 8.0, connect_to: tuple[str, int] | None = None
) -> RtspResult:
    """Probe ``url``. ``connect_to`` (a gateway tunnel forwarder) overrides the TCP
    destination; the camera-facing URL in requests stays the LAN URL."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("rtsp", "rtsps"):
        return RtspResult(False, None, "not an rtsp:// URL")
    port = parts.port or DEFAULT_PORTS[scheme]
    if connect_to:
        ip, port_c = connect_to
    else:
        target = await check_host(parts.hostname or "", port)
        # Connect to the validated IP, not the name, to avoid DNS-rebinding between check and use.
        ip, port_c = target.addresses[0], port
    clean_uri = f"{scheme}://{parts.hostname}{':' + str(parts.port) if parts.port else ''}{parts.path or '/'}"
    if parts.query:
        clean_uri += "?" + parts.query
    ctx = None
    if scheme == "rtsps":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # cameras overwhelmingly use self-signed certs
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port_c, ssl=ctx, server_hostname=parts.hostname if ctx else None),
            timeout,
        )
    except (OSError, asyncio.TimeoutError) as exc:
        return RtspResult(False, None, f"TCP connect to {parts.hostname}:{port} failed: {exc.__class__.__name__}")
    cseq = 0

    async def send(method: str, auth: str | None = None) -> tuple[int, dict, str]:
        nonlocal cseq
        cseq += 1
        req = f"{method} {clean_uri} RTSP/1.0\r\nCSeq: {cseq}\r\nUser-Agent: CameraScope\r\n"
        if method == "DESCRIBE":
            req += "Accept: application/sdp\r\n"
        if auth:
            req += f"Authorization: {auth}\r\n"
        writer.write((req + "\r\n").encode())
        await writer.drain()
        return await asyncio.wait_for(_read_response(reader), timeout)

    try:
        status, _, _ = await send("OPTIONS")
        status, headers, body = await send("DESCRIBE")
        if status == 401:
            if not username:
                return RtspResult(False, 401, "Camera requires credentials", auth_failed=True)
            chal_hdr = headers.get("www-authenticate", "")
            if chal_hdr.lower().startswith("digest"):
                chal = dict(re.findall(r'(\w+)="([^"]*)"', chal_hdr))
                auth = _digest(username, password, "DESCRIBE", clean_uri, chal)
            else:
                auth = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
            status, headers, body = await send("DESCRIBE", auth)
            if status == 401:
                return RtspResult(False, 401, "Authentication rejected by camera", auth_failed=True)
        if status != 200:
            return RtspResult(False, status, f"DESCRIBE returned RTSP {status}")
        codecs = re.findall(r"a=rtpmap:\d+ ([A-Za-z0-9\-]+)/", body)
        return RtspResult(True, 200, "Stream description received", codecs=codecs or None)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError, OSError) as exc:
        return RtspResult(False, None, f"RTSP handshake failed: {exc.__class__.__name__}")
    finally:
        writer.close()
