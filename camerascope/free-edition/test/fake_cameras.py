"""Fake cameras for local testing of the Free Edition (NOT used in production).

Implements the documented endpoints the Free Edition uses, with real HTTP Digest
authentication (RFC 7616, qop=auth) and real JPEG images:

  Hikvision NVR  :8000  /ISAPI/Streaming/channels/<ch>01/picture, /ISAPI/ContentMgmt/InputProxy/channels (3 channels)
  Dahua XVR      :8081  /cgi-bin/snapshot.cgi?channel=N, /cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle (2 channels)
  Reolink camera :8082  /cgi-bin/api.cgi?cmd=Snap&channel=0&user=..&password=..   (query-string credentials)

Each counts authentication attempts so tests can assert "exactly one attempt".
Usage: python fake_cameras.py <bind-ip>
"""
import hashlib
import io
import json
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from PIL import Image, ImageDraw

IP = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
ATTEMPTS = {"hikvision": 0, "dahua": 0, "reolink": 0}
COLORS = [(200, 40, 40), (40, 160, 60), (40, 80, 200), (200, 160, 30)]


def jpeg(label: str, ch: int) -> bytes:
    img = Image.new("RGB", (640, 360), COLORS[(ch - 1) % len(COLORS)])
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, 620, 110], fill=(0, 0, 0))
    d.text((30, 30), f"{label}  CH{ch}", fill=(255, 255, 255))
    d.text((30, 70), time.strftime("%H:%M:%S") + f".{int(time.time() * 10) % 10}", fill=(255, 255, 0))
    b = io.BytesIO()
    img.save(b, "JPEG", quality=80)
    return b.getvalue()


def digest_ok(header: str, method: str, user: str, pw: str, realm: str, nonces: set) -> bool:
    p = dict(re.findall(r'(\w+)="?([^",]+)"?', header))
    if p.get("username") != user or p.get("realm") != realm or p.get("nonce") not in nonces:
        return False
    ha1 = hashlib.md5(f"{user}:{realm}:{pw}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{p.get('uri')}".encode()).hexdigest()
    if p.get("qop") == "auth":
        exp = hashlib.md5(f"{ha1}:{p['nonce']}:{p['nc']}:{p['cnonce']}:auth:{ha2}".encode()).hexdigest()
    else:
        exp = hashlib.md5(f"{ha1}:{p['nonce']}:{ha2}".encode()).hexdigest()
    return secrets.compare_digest(exp, p.get("response", ""))


def make_handler(brand: str, user: str, pw: str, realm: str):
    nonces: set = set()

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def send(self, code, body=b"", ctype="text/plain", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def challenge(self):
            n = secrets.token_hex(16)
            nonces.add(n)
            self.send(401, b"Unauthorized", extra={"WWW-Authenticate": f'Digest realm="{realm}", qop="auth", nonce="{n}", algorithm=MD5'})

        def do_GET(self):
            u = urlsplit(self.path)
            q = parse_qs(u.query)
            if u.path == "/":
                return self.send(200, b"camera web ui", "text/html")
            if brand == "reolink":
                if u.path == "/cgi-bin/api.cgi" and q.get("cmd") == ["Snap"]:
                    ATTEMPTS["reolink"] += 1
                    if q.get("user") == [user] and q.get("password") == [pw]:
                        return self.send(200, jpeg("REOLINK", int(q.get("channel", ["0"])[0]) + 1), "image/jpeg")
                    return self.send(200, json.dumps([{"cmd": "Snap", "code": 1, "error": {"detail": "login failed", "rspCode": -6}}]).encode(), "application/json")
                return self.send(404, b"not found")
            auth = self.headers.get("Authorization")
            if not auth:
                return self.challenge()
            ATTEMPTS[brand] += 1
            if not digest_ok(auth, "GET", user, pw, realm, nonces):
                return self.challenge()
            if brand == "hikvision":
                m = re.fullmatch(r"/ISAPI/Streaming/channels/(\d+)01/picture", u.path)
                if m and 1 <= int(m.group(1)) <= 3:
                    return self.send(200, jpeg("HIKVISION NVR", int(m.group(1))), "image/jpeg")
                if u.path == "/ISAPI/ContentMgmt/InputProxy/channels":
                    xml = '<?xml version="1.0"?><InputProxyChannelList xmlns="http://www.hikvision.com/ver20/XMLSchema">' + "".join(
                        f"<InputProxyChannel><id>{i}</id><name>Camera {n}</name></InputProxyChannel>" for i, n in ((1, "Gate"), (2, "Yard"), (3, "Office")))
                    return self.send(200, (xml + "</InputProxyChannelList>").encode(), "application/xml")
            if brand == "dahua":
                if u.path == "/cgi-bin/snapshot.cgi":
                    ch = int(q.get("channel", ["1"])[0])
                    if 1 <= ch <= 2:
                        return self.send(200, jpeg("DAHUA XVR", ch), "image/jpeg")
                if u.path == "/cgi-bin/configManager.cgi":
                    return self.send(200, b"table.ChannelTitle[0].Name=Entrance\r\ntable.ChannelTitle[1].Name=Parking\r\n", "text/plain")
            return self.send(404, b"not found")

    return H


class Stats(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        b = json.dumps(ATTEMPTS).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


servers = [
    (8000, make_handler("hikvision", "admin", "Hik-Nvr-Pass9", "IP Camera(Hik)")),
    (8081, make_handler("dahua", "admin", "Dahua-Xvr-Pass9", "Login to DAHUA")),
    (8082, make_handler("reolink", "admin", "Reolink-Pass9", "")),
    (8099, Stats),
]
for port, h in servers:
    threading.Thread(target=ThreadingHTTPServer((IP, port), h).serve_forever, daemon=True).start()
print("fake cameras up", flush=True)
threading.Event().wait()
