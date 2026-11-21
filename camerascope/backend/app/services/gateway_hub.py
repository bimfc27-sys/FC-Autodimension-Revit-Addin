"""Cloud side of the Local Camera Gateway tunnel.

* ``GatewayConnection`` wraps one authenticated agent WebSocket: it multiplexes
  TCP streams and RPC calls over it.
* ``Forwarder`` is a TCP listener inside the cloud (reachable only from the
  cloud MediaMTX and the backend itself) that pipes each accepted socket
  through the tunnel to a target on the gateway's network. Forwarders keep a
  stable port per (gateway, target), so MediaMTX source URLs stay valid while
  a gateway reconnects - MediaMTX simply retries until the tunnel is back.
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import itertools
import json
import logging
import uuid
from dataclasses import dataclass, field

from fastapi import WebSocket

from ..config import get_settings
from ..tunnel import protocol as P

log = logging.getLogger(__name__)


class GatewayOffline(Exception):
    pass


class TunnelOpenError(Exception):
    pass


@dataclass
class _Stream:
    sid: int
    opened: asyncio.Future
    writer: asyncio.StreamWriter | None = None
    closed: bool = False


@dataclass
class GatewayConnection:
    gateway_id: uuid.UUID
    ws: WebSocket
    hello: dict = field(default_factory=dict)
    _streams: dict[int, _Stream] = field(default_factory=dict)
    _rpcs: dict[int, asyncio.Future] = field(default_factory=dict)
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False

    async def _send_bytes(self, data: bytes) -> None:
        async with self._lock:
            await self.ws.send_bytes(data)

    async def _send_json(self, obj: dict) -> None:
        async with self._lock:
            await self.ws.send_text(json.dumps(obj))

    # ------------------------------------------------------------------ rpc
    async def call(self, op: str, args: dict | None = None, timeout: float = 15.0):
        if self.closed:
            raise GatewayOffline("gateway disconnected")
        rid = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._rpcs[rid] = fut
        try:
            await self._send_json({"t": "rpc", "id": rid, "op": op, "args": args or {}})
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError as exc:
            raise GatewayOffline(f"gateway did not answer {op!r} in time") from exc
        finally:
            self._rpcs.pop(rid, None)

    # -------------------------------------------------------------- streams
    async def pipe(self, target: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Connect a local socket to ``target`` on the gateway's network."""
        sid = next(self._ids)
        st = _Stream(sid, asyncio.get_running_loop().create_future(), writer)
        self._streams[sid] = st
        try:
            await self._send_bytes(P.encode(P.OPEN, sid, target.encode()))
            try:
                await asyncio.wait_for(st.opened, 15)
            except asyncio.TimeoutError as exc:
                raise TunnelOpenError("gateway did not open the connection in time") from exc
            while not self.closed:
                chunk = await reader.read(P.MAX_PAYLOAD)
                if not chunk:
                    break
                await self._send_bytes(P.encode(P.DATA, sid, chunk))
        except TunnelOpenError as exc:
            log.info("tunnel %s -> %s refused: %s", self.gateway_id, target, exc)
        except (ConnectionError, OSError, RuntimeError):
            pass
        finally:
            self._streams.pop(sid, None)
            if not self.closed:
                with contextlib.suppress(Exception):
                    await self._send_bytes(P.encode(P.CLOSE, sid))
            writer.close()

    async def handle_binary(self, frame: bytes) -> None:
        kind, sid, payload = P.decode(frame)
        st = self._streams.get(sid)
        if not st:
            return
        if kind == P.OPEN_OK and not st.opened.done():
            st.opened.set_result(True)
        elif kind == P.OPEN_FAIL and not st.opened.done():
            st.opened.set_exception(TunnelOpenError(payload.decode(errors="replace")[:200]))
        elif kind == P.DATA and st.writer:
            st.writer.write(payload)
            try:
                await asyncio.wait_for(st.writer.drain(), 10)
            except (asyncio.TimeoutError, ConnectionError, OSError):
                st.writer.close()
        elif kind == P.CLOSE and st.writer:
            st.writer.close()

    def handle_text(self, msg: dict) -> None:
        if msg.get("t") == "rpc_result":
            fut = self._rpcs.get(msg.get("id"))
            if fut and not fut.done():
                if msg.get("ok"):
                    fut.set_result(msg.get("result"))
                else:
                    fut.set_exception(RuntimeError(str(msg.get("error"))[:300]))

    def close(self) -> None:
        self.closed = True
        for st in list(self._streams.values()):
            if not st.opened.done():
                st.opened.set_exception(TunnelOpenError("gateway disconnected"))
            if st.writer:
                st.writer.close()
        for fut in self._rpcs.values():
            if not fut.done():
                fut.set_exception(GatewayOffline("gateway disconnected"))


class Forwarder:
    def __init__(self, hub: "GatewayHub", gateway_id: uuid.UUID, target: str):
        self.hub, self.gateway_id, self.target = hub, gateway_id, target
        self.server: asyncio.base_events.Server | None = None
        self.port = 0

    async def start(self) -> None:
        s = get_settings()
        self.server = await asyncio.start_server(self._accept, s.tunnel_bind_host, 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        if not self.hub.peer_allowed(peer[0] if peer else ""):
            log.warning("forwarder refused connection from %s", peer)
            writer.close()
            return
        conn = self.hub.connections.get(self.gateway_id)
        if not conn:
            writer.close()  # gateway offline: the client (MediaMTX) will retry
            return
        await conn.pipe(self.target, reader, writer)


class GatewayHub:
    def __init__(self) -> None:
        self.connections: dict[uuid.UUID, GatewayConnection] = {}
        self.forwarders: dict[tuple[uuid.UUID, str], Forwarder] = {}
        self._fw_lock = asyncio.Lock()
        self._allowed_peers: set[str] | None = None

    # ---------------------------------------------------------- connections
    def register(self, conn: GatewayConnection) -> None:
        old = self.connections.get(conn.gateway_id)
        if old:
            old.close()
        self.connections[conn.gateway_id] = conn

    def unregister(self, conn: GatewayConnection) -> None:
        conn.close()
        if self.connections.get(conn.gateway_id) is conn:
            del self.connections[conn.gateway_id]

    def is_online(self, gateway_id: uuid.UUID | None) -> bool:
        return gateway_id in self.connections

    def get(self, gateway_id: uuid.UUID) -> GatewayConnection:
        conn = self.connections.get(gateway_id)
        if not conn:
            raise GatewayOffline("The gateway for this camera is offline")
        return conn

    async def disconnect(self, gateway_id: uuid.UUID) -> None:
        conn = self.connections.get(gateway_id)
        if conn:
            self.unregister(conn)
            with contextlib.suppress(Exception):
                await conn.ws.close(code=4001)

    # ----------------------------------------------------------- forwarders
    def peer_allowed(self, ip: str) -> bool:
        if self._allowed_peers is None:
            import socket

            peers = {"127.0.0.1", "::1"}
            for h in get_settings().tunnel_allowed_peers.split(","):
                h = h.strip()
                if not h:
                    continue
                try:
                    ipaddress.ip_address(h)
                    peers.add(h)
                except ValueError:
                    with contextlib.suppress(OSError):
                        peers.update(i[4][0] for i in socket.getaddrinfo(h, None))
            self._allowed_peers = peers
        return ip.removeprefix("::ffff:") in self._allowed_peers

    async def forwarder(self, gateway_id: uuid.UUID, target: str) -> Forwarder:
        key = (gateway_id, target)
        async with self._fw_lock:
            fw = self.forwarders.get(key)
            if not fw:
                fw = Forwarder(self, gateway_id, target)
                await fw.start()
                self.forwarders[key] = fw
            return fw

    async def close_forwarders(self, gateway_id: uuid.UUID) -> None:
        async with self._fw_lock:
            for key in [k for k in self.forwarders if k[0] == gateway_id]:
                fw = self.forwarders.pop(key)
                if fw.server:
                    fw.server.close()


hub = GatewayHub()
