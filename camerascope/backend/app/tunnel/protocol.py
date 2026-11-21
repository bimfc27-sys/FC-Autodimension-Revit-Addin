"""CameraScope gateway tunnel wire protocol (shared by cloud and gateway agent).

One WebSocket (TLS, authenticated with the gateway token) carries:

* Text frames: JSON control messages
    {"t": "hello", ...}                         agent -> cloud, once after connect
    {"t": "rpc", "id": n, "op": str, "args": {}} cloud -> agent
    {"t": "rpc_result", "id": n, "ok": bool, "result"|"error": ...}  agent -> cloud
* Binary frames: multiplexed TCP streams
    [type:1][stream_id:4 big-endian][payload]

Only the CLOUD opens streams, and only to targets the agent validates against
its locally configured allow-list. The agent never accepts inbound connections.
"""
from __future__ import annotations

import struct

OPEN = 1  # payload: utf-8 target "host:port" or "@alias"
OPEN_OK = 2
OPEN_FAIL = 3  # payload: utf-8 reason
DATA = 4
CLOSE = 5

MAX_PAYLOAD = 64 * 1024
PROTOCOL_VERSION = 1
_HDR = struct.Struct(">BI")


def encode(kind: int, sid: int, payload: bytes = b"") -> bytes:
    return _HDR.pack(kind, sid) + payload


def decode(frame: bytes) -> tuple[int, int, bytes]:
    if len(frame) < _HDR.size:
        raise ValueError("short frame")
    kind, sid = _HDR.unpack_from(frame)
    return kind, sid, frame[_HDR.size :]
