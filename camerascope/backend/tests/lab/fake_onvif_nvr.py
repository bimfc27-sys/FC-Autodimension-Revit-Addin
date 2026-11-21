"""Minimal ONVIF NVR simulator for the end-to-end lab (NOT used in production).

Serves Device + Media services for a 2-channel recorder, verifies WS-Security
UsernameToken digests, and reports RTSP URIs served by a MediaMTX instance on the
same host. Usage: python fake_onvif_nvr.py <listen_ip> <http_port> <rtsp_port> <user> <password>
"""
import base64
import hashlib
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

IP, PORT, RTSP_PORT, USER, PASSWORD = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5]
# Report an address the CLOUD cannot use directly, like a real NVR behind NAT would.
REPORTED = f"{IP}"
CHANNELS = [("VS1", "Front door"), ("VS2", "Loading bay")]

ENV = ('<?xml version="1.0" encoding="UTF-8"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
       'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
       'xmlns:tt="http://www.onvif.org/ver10/schema"><s:Body>{}</s:Body></s:Envelope>')


def authorised(body: str) -> bool:
    m = {k: re.search(rf"<(?:\w+:)?{k}[^>]*>([^<]*)</", body) for k in ("Username", "Password", "Nonce", "Created")}
    if not all(m.values()):
        return False
    user, digest, nonce, created = (m[k].group(1) for k in ("Username", "Password", "Nonce", "Created"))
    expect = base64.b64encode(hashlib.sha1(base64.b64decode(nonce) + created.encode() + PASSWORD.encode()).digest()).decode()
    return user == USER and digest == expect


def profiles() -> str:
    out = []
    for i, (src, _name) in enumerate(CHANNELS, start=1):
        for kind, (w, h) in (("main", (1280, 720)), ("sub", (640, 360))):
            out.append(
                f'<trt:Profiles token="ch{i}_{kind}"><tt:Name>CH{i} {kind}</tt:Name>'
                f'<tt:VideoSourceConfiguration token="vsc{i}"><tt:SourceToken>{src}</tt:SourceToken></tt:VideoSourceConfiguration>'
                f'<tt:VideoEncoderConfiguration token="vec{i}{kind}"><tt:Encoding>H264</tt:Encoding>'
                f"<tt:Resolution><tt:Width>{w}</tt:Width><tt:Height>{h}</tt:Height></tt:Resolution>"
                "</tt:VideoEncoderConfiguration></trt:Profiles>"
            )
    return "".join(out)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode(errors="replace")
        if not authorised(body):
            fault = ('<s:Fault><s:Code><s:Value>s:Sender</s:Value><s:Subcode><s:Value>ter:NotAuthorized</s:Value>'
                     '</s:Subcode></s:Code><s:Reason><s:Text>Sender not Authorized</s:Text></s:Reason></s:Fault>')
            return self._send(400, ENV.format(fault))
        if "GetDeviceInformation" in body:
            r = ("<tds:GetDeviceInformationResponse><tds:Manufacturer>LabVision</tds:Manufacturer>"
                 "<tds:Model>NVR-2CH</tds:Model><tds:FirmwareVersion>1.0</tds:FirmwareVersion>"
                 "</tds:GetDeviceInformationResponse>")
        elif "GetCapabilities" in body:
            r = (f"<tds:GetCapabilitiesResponse><tds:Capabilities><tt:Media><tt:XAddr>http://{REPORTED}:{PORT}/onvif/media_service"
                 "</tt:XAddr></tt:Media></tds:Capabilities></tds:GetCapabilitiesResponse>")
        elif "GetProfiles" in body:
            r = f"<trt:GetProfilesResponse>{profiles()}</trt:GetProfilesResponse>"
        elif "GetStreamUri" in body:
            tok = re.search(r"ProfileToken>([^<]+)<", body).group(1)
            r = (f"<trt:GetStreamUriResponse><trt:MediaUri><tt:Uri>rtsp://{REPORTED}:{RTSP_PORT}/{tok}</tt:Uri>"
                 "</trt:MediaUri></trt:GetStreamUriResponse>")
        elif "GetSnapshotUri" in body:
            r = "<trt:GetSnapshotUriResponse><trt:MediaUri><tt:Uri></tt:Uri></trt:MediaUri></trt:GetSnapshotUriResponse>"
        else:
            r = "<s:Fault><s:Reason><s:Text>Unsupported</s:Text></s:Reason></s:Fault>"
        self._send(200, ENV.format(r))

    def _send(self, code, text):
        data = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/soap+xml; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


ThreadingHTTPServer((IP, PORT), H).serve_forever()
