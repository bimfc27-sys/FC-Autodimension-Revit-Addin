import json, subprocess, sys, time
import httpx

S = sys.argv[1]
L = __import__('os').path.join(__import__('os').path.dirname(__file__), 'lab.sh')
CA = f"{S}/lab/caddy-data/pki/authorities/local/root.crt"
B = "https://localhost:8443"
c = httpx.Client(base_url=B, verify=CA, timeout=60)

def ok(r, code=200):
    assert r.status_code == code, (r.request.url, r.status_code, r.text[:400])
    return r.json() if r.content else None

ok(c.post("/api/auth/register", json={"email": "owner@example.com", "password": "Lab-Owner-Pass9", "display_name": "Lab owner"}), 201)
gwA = ok(c.post("/api/gateways", json={"name": "Site 2 Wi-Fi (office)"}), 201)
gwB = ok(c.post("/api/gateways", json={"name": "Site 3 NVR network"}), 201)
py = f"{S}/venv/bin/python"
subprocess.run(["bash", L, "agent", "site2", f"{S}/lab", py, gwA["enrollment_code"], "https://192.168.50.1:8443", CA, "192.168.50.0/24"], check=True)
subprocess.run(["bash", L, "agent", "site3", f"{S}/lab", py, gwB["enrollment_code"], "https://192.168.60.1:8443", CA, "192.168.60.0/24"], check=True)
for _ in range(30):
    gws = ok(c.get("/api/gateways"))
    if all(g["online"] for g in gws):
        break
    time.sleep(1)
print("gateways:", [(g["name"], g["status"], g["allowed_subnets"], g["remote_address"]) for g in gws])
assert all(g["online"] for g in gws)
A, Bg = gwA["gateway"]["id"], gwB["gateway"]["id"]

base = {"vendor": "generic", "authorization_basis": "owner", "authorization_attested": True, "protocol": "rtsp"}
cam1 = ok(c.post("/api/cameras", json={**base, "name": "Lobby (same LAN / VPN route)", "host": "10.10.1.2", "port": 8554,
       "config": {"main_path": "/main", "sub_path": "/sub"}, "credentials": {"username": "cam", "password": "Lan1-Cam-Pass9"}}), 201)
cam2 = ok(c.post("/api/cameras", json={**base, "name": "Office entrance (other Wi-Fi)", "host": "192.168.50.2", "port": 8554, "gateway_id": A,
       "config": {"main_path": "/stream1", "sub_path": "/stream2", "rtsp_transport": "udp"},
       "credentials": {"username": "admin", "password": "Site2-Cam-Pass9"}}), 201)
dev = ok(c.post("/api/devices", json={"name": "Warehouse NVR", "protocol": "onvif", "host": "192.168.60.2", "port": 8080, "rtsp_port": 8554,
      "gateway_id": Bg, "credentials": {"username": "admin", "password": "Nvr-Lab-Pass9"}, "authorization_basis": "owner",
      "authorization_attested": True}), 201)
disc = ok(c.post(f"/api/devices/{dev['id']}/discover"))
print("NVR model:", disc["device"]["model"], "channels:", json.dumps(disc["channels"]))
assert "uri" not in json.dumps(disc).lower(), "stream URIs leaked to client"
imported = ok(c.post(f"/api/devices/{dev['id']}/import", json={"channels": [{"index": ch["index"], "name": f"NVR CH{ch['index']}"} for ch in disc["channels"]]}))
print("imported:", [(x["name"], x["connection"], x["gateway"]["name"], x["device"]["name"]) for x in imported])

# security probe: a camera outside gateway A's allowed subnet must be refused BY THE GATEWAY
evil = ok(c.post("/api/cameras", json={**base, "name": "not allowed", "host": "192.168.60.2", "port": 8554, "gateway_id": A,
       "config": {"main_path": "/ch1_main"}, "credentials": {"username": "admin", "password": "x"}}), 201)
r = c.get(f"/api/cameras/{evil['id']}/playback")
print("outside-allow-list playback ->", r.status_code, r.json()["detail"][:120])
assert r.status_code == 502 and "outside ALLOWED_SUBNETS" in r.text
d = ok(c.post("/api/diagnostics", json={"camera_kind": "private", "camera_id": evil["id"]}))
print("outside-allow-list diagnostics:", [(s["name"], s["ok"], s["detail"][:80]) for s in d["steps"]])
ok(c.delete(f"/api/cameras/{evil['id']}"), 204)

cams = [cam1, cam2] + imported
json.dump({"cams": cams, "cookie": c.cookies.get("cs_session")}, open(f"{S}/lab/state.json", "w"))
for cam in cams:
    for q in ("high", "low"):
        pb = ok(c.get(f"/api/cameras/{cam['id']}/playback", params={"quality": q}))
        blob = json.dumps(pb)
        assert "Pass9" not in blob and "rtsp://" not in blob and "192.168" not in blob and "10.10" not in blob, blob
        t0 = time.time()
        out = subprocess.run(["ffprobe", "-v", "error", "-headers", f"Cookie: cs_session={c.cookies.get('cs_session')}\r\n",
                              "-ca_file", CA, "-show_entries", "stream=codec_name,width,height", "-of", "csv=p=0",
                              "-read_intervals", "%+3", "-count_frames", "-show_entries", "stream=nb_read_frames",
                              B + pb["hls_url"]], capture_output=True, text=True, timeout=90)
        print(f"{cam['name']:<34} {q:<4} HLS via cloud -> {out.stdout.strip() or out.stderr.strip()[:200]}  ({time.time()-t0:.1f}s)")
d = ok(c.post("/api/diagnostics", json={"camera_kind": "private", "camera_id": cam2["id"]}))
print("diagnostics cam2:", [(s["name"], s["ok"]) for s in d["steps"]])
print("statuses:", [(x["name"], x["status"], x["connection"]) for x in ok(c.get("/api/cameras"))])
