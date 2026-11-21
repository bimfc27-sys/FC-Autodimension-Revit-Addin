"""Guarded outbound HTTP client. Redirects are followed manually so each hop is re-validated."""
from __future__ import annotations

from contextlib import asynccontextmanager
from urllib.parse import urljoin

import httpx

from ..config import get_settings
from .netguard import check_url

USER_AGENT = "CameraScope/1.0 (+https://github.com/; public-camera viewer)"
MAX_REDIRECTS = 3


def make_client(timeout: float | None = None, verify: bool = True) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout or get_settings().outbound_timeout_seconds,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
        trust_env=False,  # never let env proxies redirect user-controlled traffic
        verify=verify,
    )


def build_auth(username: str | None, password: str | None, scheme: str = "digest") -> httpx.Auth | None:
    """Digest auth (falls back to basic only if the device asks for it via 'basic')."""
    if not username:
        return None
    if scheme == "basic":
        return httpx.BasicAuth(username, password or "")
    return httpx.DigestAuth(username, password or "")


async def guarded_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    auth: httpx.Auth | None = None,
    stream: bool = False,
    trusted: bool = False,
    **kwargs,
) -> httpx.Response:
    """``trusted`` is set only for URLs that point at a gateway tunnel forwarder:
    the destination was already validated against the gateway's local allow-list."""
    for _ in range(MAX_REDIRECTS + 1):
        if not trusted:
            await check_url(url)
        elif _ > 0:
            raise httpx.TooManyRedirects("redirects are not followed through a gateway tunnel", request=req)
        req = client.build_request(method, url, **kwargs)
        resp = await client.send(req, auth=auth, stream=stream)
        if resp.is_redirect and "location" in resp.headers:
            nxt = urljoin(url, resp.headers["location"])
            await resp.aclose()
            # Never forward credentials to a different host after a redirect.
            if httpx.URL(nxt).host != httpx.URL(url).host:
                auth = None
            url = nxt
            continue
        return resp
    raise httpx.TooManyRedirects("too many redirects", request=req)


@asynccontextmanager
async def guarded_stream(
    method: str, url: str, *, auth: httpx.Auth | None = None, timeout: float | None = None, trusted: bool = False
):
    client = make_client(timeout, verify=not trusted)
    try:
        resp = await guarded_request(client, method, url, auth=auth, stream=True, trusted=trusted)
        try:
            yield resp
        finally:
            await resp.aclose()
    finally:
        await client.aclose()
