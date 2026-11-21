"""CameraScope Local Camera Gateway agent.

Runs inside a network whose cameras the owner is authorised to use, next to a
local MediaMTX instance. It keeps ONE outbound, TLS-encrypted, token-
authenticated WebSocket to the CameraScope cloud and reconnects automatically.

Security properties (enforced HERE, on the camera network, independent of the cloud):
  * No inbound ports. The agent only dials out.
  * Every TCP connection the cloud asks for is checked against ALLOWED_SUBNETS
    and ALLOWED_PORTS (configured locally by the network owner). Anything else
    is refused and logged.
  * There is no scan / sweep / discovery-by-range operation. The cloud can only
    reach hosts that were explicitly registered and are inside the allow-list.
  * Stream sources pushed to the local MediaMTX are validated the same way.
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import re
import secrets
import signal
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import websockets

import tunnel_protocol as P

VERSION = "2.0.0"
log = logging.getLogger("camerascope-gateway")

DEFAULT_PORTS = "80,443,554,322,8000,8080,8081,8443,8554,8899,8888,8889,9000,34567,37777"


def env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        sys.exit(f"missing required environment variable {name}")
    return v


class Config:
    def __init__(self) -> None:
        self.cloud = env("CLOUD_URL").rstrip("/")
        if not self.cloud.startswith("https://") and os.environ.get("ALLOW_INSECURE_CLOUD") != "1":
            sys.exit("CLOUD_URL must be https:// (set ALLOW_INSECURE_CLOUD=1 only for local testing)")
        self.state_dir = Path(env("STATE_DIR", "/data"))
        self.subnets = [ipaddress.ip_network(s.strip(), strict=False) for s in env("ALLOWED_SUBNETS").split(",") if s.strip()]
        if not self.subnets:
            sys.exit("ALLOWED_SUBNETS must list the camera networks this gateway may reach, e.g. 192.168.1.0/24")
        ports = env("ALLOWED_PORTS", DEFAULT_PORTS).strip()
        self.any_port = ports == "*"
        self.ports = set() if self.any_port else {int(p) for p in ports.split(",") if p.strip()}
        self.mtx_api = env("MEDIAMTX_API", "http://127.0.0.1:9997").rstrip("/")
        self.mtx_rtsp = env("MEDIAMTX_RTSP", "127.0.0.1:8554")
        self.relay_user = env("RELAY_USER", "relay")
        self.relay_pass = env("RELAY_PASS")
        self.ca_file = os.environ.get("CLOUD_CA_FILE") or None
        self.check_concurrency = 16

    def ssl_context(self) -> ssl.SSLContext | None:
        if not self.cloud.startswith("https://"):
            return None
        return ssl.create_default_context(cafile=self.ca_file)


# ----------------------------------------------------------------- policy


class Refused(Exception):
    pass


async def resolve_allowed(cfg: Config, host: str, port: int) -> str:
    """Resolve host and return an IP inside the allow-list, or raise Refused."""
    if not (cfg.any_port or port in cfg.ports):
        raise Refused(f"port {port} not in ALLOWED_PORTS")
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise Refused(f"cannot resolve {host}") from exc
    addrs = sorted({i[4][0] for i in infos})
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if not any(ip in n for n in cfg.subnets):
            raise Refused(f"{host} ({a}) is outside ALLOWED_SUBNETS")
    return addrs[0]


def split_target(target: str) -> tuple[str, int]:
    if target.startswith("["):
        host, _, port = target[1:].partition("]:")
    else:
        host, _, port = target.rpartition(":")
    return host, int(port)


# ----------------------------------------------------------- MediaMTX API


_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _mtx(cfg: Config, method: str, path: str, body: dict | None = None) -> tuple[int, str]:
    req = urllib.request.Request(cfg.mtx_api + path, method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with _NO_PROXY.open(req, data=data, timeout=5) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


NAME_RE = re.compile(r"^[a-z]{3}-[0-9a-f]{32}-(high|low)$")
SOURCE_SCHEMES = {"rtsp", "rtsps", "http", "https", "whep", "wheps"}
DEFAULT_SCHEME_PORT = {"rtsp": 554, "rtsps": 322, "http": 80, "https": 443, "whep": 80, "wheps": 443}


async def stream_ensure(cfg: Config, args: dict) -> dict:
    name, source = args.get("name", ""), args.get("source", "")
    if not NAME_RE.match(name):
        raise Refused("invalid path name")
    p = urlsplit(source)
    if p.scheme not in SOURCE_SCHEMES or not p.hostname:
        raise Refused("unsupported source")
    await resolve_allowed(cfg, p.hostname, p.port or DEFAULT_SCHEME_PORT[p.scheme])
    conf = {
        "source": source,
        "sourceOnDemand": True,
        "sourceOnDemandStartTimeout": "15s",
        "sourceOnDemandCloseAfter": "20s",
    }
    if p.scheme in ("rtsp", "rtsps"):
        conf["rtspTransport"] = args.get("rtsp_transport") if args.get("rtsp_transport") in ("tcp", "udp", "automatic") else "automatic"
    code, text = await asyncio.to_thread(_mtx, cfg, "PATCH", f"/v3/config/paths/patch/{name}", conf)
    if code == 404:
        code, text = await asyncio.to_thread(_mtx, cfg, "POST", f"/v3/config/paths/add/{name}", conf)
    if code >= 400:
        await asyncio.to_thread(_mtx, cfg, "DELETE", f"/v3/config/paths/delete/{name}")
        code, text = await asyncio.to_thread(_mtx, cfg, "POST", f"/v3/config/paths/add/{name}", conf)
    if code >= 400:
        raise RuntimeError(f"local MediaMTX rejected path ({code}): {text[:200]}")
    return {"path": name}


async def stream_remove(cfg: Config, args: dict) -> dict:
    prefix = args.get("prefix", "")
    if not re.fullmatch(r"[a-z]{3}-[0-9a-f]{32}-?", prefix):
        raise Refused("invalid prefix")
    code, text = await asyncio.to_thread(_mtx, cfg, "GET", "/v3/config/paths/list?itemsPerPage=1000")
    removed = 0
    if code == 200:
        for item in json.loads(text).get("items", []):
            if item.get("name", "").startswith(prefix):
                await asyncio.to_thread(_mtx, cfg, "DELETE", f"/v3/config/paths/delete/{item['name']}")
                removed += 1
    return {"removed": removed}


async def stream_state(cfg: Config, args: dict) -> dict:
    name = args.get("name", "")
    if not NAME_RE.match(name):
        raise Refused("invalid path name")
    code, text = await asyncio.to_thread(_mtx, cfg, "GET", f"/v3/paths/get/{name}")
    if code != 200:
        return {"exists": False}
    d = json.loads(text)
    return {"exists": True, "ready": d.get("ready", False), "readers": len(d.get("readers", [])),
            "tracks": d.get("tracks", [])}


async def tcp_check(cfg: Config, args: dict) -> dict:
    """Unauthenticated TCP reachability of registered targets (no credentials, no payload)."""
    sem = asyncio.Semaphore(cfg.check_concurrency)
    targets = args.get("targets", [])[:500]

    async def one(t: dict):
        async with sem:
            try:
                ip = await resolve_allowed(cfg, str(t["host"]), int(t["port"]))
                _, w = await asyncio.wait_for(asyncio.open_connection(ip, int(t["port"])), 4)
                w.close()
                return t["id"], {"reachable": True}
            except Refused as exc:
                return t["id"], {"reachable": False, "refused": str(exc)}
            except (OSError, asyncio.TimeoutError):
                return t["id"], {"reachable": False}

    return dict(await asyncio.gather(*(one(t) for t in targets)))


async def gateway_info(cfg: Config, args: dict) -> dict:
    code, _ = await asyncio.to_thread(_mtx, cfg, "GET", "/v3/config/global/get")
    return {"version": VERSION, "mediamtx_ok": code == 200, "allowed_subnets": [str(n) for n in cfg.subnets]}


RPCS = {
    "stream.ensure": stream_ensure,
    "stream.remove": stream_remove,
    "stream.state": stream_state,
    "check": tcp_check,
    "info": gateway_info,
}


# --------------------------------------------------------------- session


class Session:
    def __init__(self, cfg: Config, ws) -> None:
        self.cfg, self.ws = cfg, ws
        self.streams: dict[int, asyncio.StreamWriter] = {}
        self.tasks: set[asyncio.Task] = set()
        self.lock = asyncio.Lock()

    async def send(self, data) -> None:
        async with self.lock:
            await self.ws.send(data)

    def spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    async def open_stream(self, sid: int, target: str) -> None:
        try:
            if target == "@mediamtx-rtsp":
                host, port = split_target(self.cfg.mtx_rtsp)
                ip = host  # local MediaMTX: always allowed, it is part of the gateway
            elif target.startswith("@"):
                raise Refused("unknown alias")
            else:
                host, port = split_target(target)
                ip = await resolve_allowed(self.cfg, host, port)
            reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), 8)
        except Refused as exc:
            log.warning("REFUSED tunnel connection to %s: %s", target, exc)
            await self.send(P.encode(P.OPEN_FAIL, sid, f"refused by gateway policy: {exc}".encode()))
            return
        except (OSError, asyncio.TimeoutError, ValueError) as exc:
            await self.send(P.encode(P.OPEN_FAIL, sid, f"connect failed: {exc.__class__.__name__}".encode()))
            return
        self.streams[sid] = writer
        await self.send(P.encode(P.OPEN_OK, sid))
        try:
            while True:
                chunk = await reader.read(P.MAX_PAYLOAD)
                if not chunk:
                    break
                await self.send(P.encode(P.DATA, sid, chunk))
        except (ConnectionError, OSError, websockets.ConnectionClosed):
            pass
        finally:
            self.streams.pop(sid, None)
            writer.close()
            with contextlib.suppress(Exception):
                await self.send(P.encode(P.CLOSE, sid))

    async def on_binary(self, frame: bytes) -> None:
        kind, sid, payload = P.decode(frame)
        if kind == P.OPEN:
            self.spawn(self.open_stream(sid, payload.decode(errors="replace")))
        elif kind == P.DATA:
            w = self.streams.get(sid)
            if w:
                w.write(payload)
                try:
                    await asyncio.wait_for(w.drain(), 10)
                except (asyncio.TimeoutError, ConnectionError, OSError):
                    w.close()
        elif kind == P.CLOSE:
            w = self.streams.pop(sid, None)
            if w:
                w.close()

    async def on_rpc(self, msg: dict) -> None:
        fn = RPCS.get(msg.get("op", ""))
        try:
            if not fn:
                raise Refused(f"unknown operation {msg.get('op')!r}")
            result = await fn(self.cfg, msg.get("args") or {})
            await self.send(json.dumps({"t": "rpc_result", "id": msg.get("id"), "ok": True, "result": result}))
        except Exception as exc:  # report every failure to the cloud
            if isinstance(exc, Refused):
                log.warning("REFUSED rpc %s: %s", msg.get("op"), exc)
            await self.send(json.dumps({"t": "rpc_result", "id": msg.get("id"), "ok": False, "error": str(exc)[:300]}))

    async def run(self) -> None:
        await self.send(json.dumps({
            "t": "hello",
            "protocol": P.PROTOCOL_VERSION,
            "version": VERSION,
            "hostname": socket.gethostname(),
            "allowed_subnets": [str(n) for n in self.cfg.subnets],
            "allowed_ports": "*" if self.cfg.any_port else sorted(self.cfg.ports),
            "relay": {"user": self.cfg.relay_user, "pass": self.cfg.relay_pass},
        }))
        try:
            async for msg in self.ws:
                if isinstance(msg, bytes):
                    await self.on_binary(msg)
                else:
                    data = json.loads(msg)
                    if data.get("t") == "rpc":
                        self.spawn(self.on_rpc(data))
        finally:
            for w in self.streams.values():
                w.close()
            for t in list(self.tasks):
                t.cancel()


# ------------------------------------------------------------ enrollment


def load_credentials(cfg: Config) -> dict | None:
    f = cfg.state_dir / "gateway.json"
    if f.exists():
        return json.loads(f.read_text())
    return None


def enroll(cfg: Config) -> dict:
    code = os.environ.get("ENROLLMENT_CODE", "").strip()
    if not code:
        sys.exit("Not enrolled yet: set ENROLLMENT_CODE (shown once in CameraScope > Gateways > Add gateway)")
    req = urllib.request.Request(
        cfg.cloud + "/api/gateways/enroll",
        data=json.dumps({"code": code, "agent_version": VERSION}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        # Direct connection, like the tunnel itself (environment proxies are not used).
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=cfg.ssl_context())
        )
        with opener.open(req, timeout=20) as r:
            creds = json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"enrollment failed: HTTP {e.code} {e.read().decode(errors='replace')[:200]}")
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    f = cfg.state_dir / "gateway.json"
    f.write_text(json.dumps({"gateway_id": creds["gateway_id"], "secret": creds["secret"]}))
    os.chmod(f, 0o600)
    log.info("enrolled as gateway %s", creds["gateway_id"])
    return creds


# ------------------------------------------------- local MediaMTX auth hook


async def auth_server(cfg: Config) -> asyncio.base_events.Server:
    """MediaMTX (authMethod: http) asks this endpoint before serving a stream.

    Only the relay account (used by the cloud through the tunnel) may READ.
    Nobody may publish. Other LAN devices therefore cannot pull camera feeds
    from the gateway.
    """
    import hmac

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        code = 401
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            length = 0
            for line in head.decode(errors="replace").split("\r\n"):
                if line.lower().startswith("content-length:"):
                    length = min(int(line.split(":", 1)[1]), 16384)
            body = json.loads(await reader.readexactly(length)) if length else {}
            if (
                body.get("action") in ("read", "playback")
                and hmac.compare_digest(str(body.get("user", "")), cfg.relay_user)
                and hmac.compare_digest(str(body.get("password", "")), cfg.relay_pass)
            ):
                code = 200
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError, OSError):
            code = 400
        writer.write(f"HTTP/1.1 {code} X\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
        with contextlib.suppress(OSError):
            await writer.drain()
        writer.close()

    host, port = split_target(env("AUTH_LISTEN", "0.0.0.0:9123"))
    return await asyncio.start_server(handle, host, port)


# ------------------------------------------------------------------ main


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config()
    if len(cfg.relay_pass) < 16:
        sys.exit("RELAY_PASS must be at least 16 random characters")
    auth = await auth_server(cfg)
    creds = load_credentials(cfg) or enroll(cfg)
    token = f"{creds['gateway_id']}.{creds['secret']}"
    url = "ws" + cfg.cloud[len("http"):] + "/api/gateways/tunnel"
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    backoff = 1.0
    while not stop.is_set():
        started = time.monotonic()
        try:
            async with websockets.connect(
                url,
                additional_headers={"Authorization": f"Bearer {token}"},
                ssl=cfg.ssl_context(),
                ping_interval=20,
                ping_timeout=20,
                max_size=2 * P.MAX_PAYLOAD,
                open_timeout=15,
            ) as ws:
                log.info("connected to %s (allowed subnets: %s)", cfg.cloud, ", ".join(map(str, cfg.subnets)))
                session = Session(cfg, ws)
                runner = asyncio.create_task(session.run())
                stopper = asyncio.create_task(stop.wait())
                await asyncio.wait({runner, stopper}, return_when=asyncio.FIRST_COMPLETED)
                runner.cancel()
                stopper.cancel()
        except websockets.InvalidStatus as exc:
            status = exc.response.status_code
            if status in (401, 403):
                log.error("cloud rejected the gateway credentials (HTTP %s). Re-enroll: delete %s/gateway.json "
                          "and set a new ENROLLMENT_CODE.", status, cfg.state_dir)
                backoff = 300.0
            else:
                log.warning("tunnel handshake failed: HTTP %s", status)
        except (OSError, asyncio.TimeoutError, websockets.WebSocketException) as exc:
            log.warning("tunnel disconnected: %s", exc.__class__.__name__)
        if stop.is_set():
            break
        if time.monotonic() - started > 60:
            backoff = 1.0  # connection was healthy; reconnect quickly
        delay = min(backoff, 60.0) * (0.8 + 0.4 * secrets.randbelow(1000) / 1000)
        log.info("reconnecting in %.1fs", delay)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), delay)
        backoff = min(backoff * 2, 60.0) if backoff < 300 else backoff
    auth.close()


if __name__ == "__main__":
    asyncio.run(main())
