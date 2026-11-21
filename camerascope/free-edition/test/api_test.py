"""End-to-end API test of the Free Edition against `wrangler pages dev` (local workerd + D1)
and test/fake_cameras.py. Usage: python api_test.py http://localhost:8788 http://127.0.0.1:8099
"""
import json
import sys
import urllib.error
import urllib.request

BASE, STATS = sys.argv[1], sys.argv[2]
SECRETS = ["Hik-Nvr-Pass9", "Dahua-Xvr-Pass9", "Reolink-Pass9", "Wrong-Pass-1"]
seen_bodies: list[str] = []
results = []


class Client:
    def __init__(self):
        self.cookie = None

    def req(self, method, path, data=None, origin=None, raw=False):
        body = json.dumps(data).encode() if data is not None else None
        r = urllib.request.Request(BASE + path, data=body, method=method)
        if body is not None:
            r.add_header("Content-Type", "application/json")
        if method not in ("GET", "HEAD"):
            r.add_header("Origin", origin or BASE)
        if self.cookie:
            r.add_header("Cookie", self.cookie)
        try:
            resp = urllib.request.urlopen(r, timeout=60)
            code, headers, content = resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            code, headers, content = e.code, e.headers, e.read()
        for c in headers.get_all("Set-Cookie") or []:
            if c.startswith("__Host-cs_session="):
                v = c.split(";")[0]
                self.cookie = None if v.endswith("=") else v
        if raw:
            return code, headers, content
        text = content.decode(errors="replace")
        seen_bodies.append(text)
        return code, (json.loads(text) if text and text[0] in "[{" else text)


def check(name, cond, info=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if info and not cond else ""))


def stats():
    return json.loads(urllib.request.urlopen(STATS).read())


admin = Client()
code, reg = admin.req("GET", "/api/auth/registration")
check("registration open for the first user", code == 200 and reg["first_user"])
code, me = admin.req("POST", "/api/auth/register", {"email": "owner@example.com", "password": "Owner-Pass-123", "display_name": "Owner"})
check("first account registered and is admin", code == 201 and me["is_admin"] is True, me)
check("session cookie is __Host-, HttpOnly, Secure, SameSite=Strict", admin.cookie is not None)
code, _ = Client().req("POST", "/api/auth/register", {"email": "stranger@example.com", "password": "Stranger-Pass-1", "display_name": "x"})
check("second self-registration refused (403)", code == 403)
code, _ = admin.req("POST", "/api/groups", {"name": "x"}, origin="https://evil.example")
check("cross-site POST refused (CSRF, 403)", code == 403)
code, _ = Client().req("GET", "/api/cameras")
check("unauthenticated camera list refused (401)", code == 401)
code, _ = Client().req("POST", "/api/auth/login", {"email": "owner@example.com", "password": "nope"})
check("wrong password refused (401)", code == 401)

base = {"authorization_basis": "owner", "authorization_attested": True}
# --- Hikvision camera (ISAPI digest) on a custom port -> raw TCP path
code, hik = admin.req("POST", "/api/cameras", {**base, "name": "Hik gate", "brand": "hikvision", "host": "127.0.0.1", "port": 8000, "channel": 1,
                                               "credentials": {"username": "admin", "password": "Hik-Nvr-Pass9"}})
check("Hikvision camera added and VERIFIED (snapshot view enabled)", code == 201 and hik["video"]["available"], json.dumps(hik.get("verification", hik))[:400])
code, pb = admin.req("GET", f"/api/cameras/{hik['id']}/playback")
check("playback descriptor = verified snapshot view (no RTSP/stream URLs)", code == 200 and pb["mode"] == "image" and pb["image_url"].endswith("/snapshot-view"))
code, h, img = admin.req("GET", pb["image_url"], raw=True)
check("snapshot view returns a real JPEG through the API", code == 200 and img[:3] == b"\xff\xd8\xff" and h["Content-Type"] == "image/jpeg", (code, img[:40]))

# --- wrong password: exactly one auth attempt, then lock after 3 failures
before = stats()["hikvision"]
code, bad = admin.req("POST", "/api/cameras", {**base, "name": "Hik wrong", "brand": "hikvision", "host": "127.0.0.1", "port": 8000, "channel": 2,
                                               "credentials": {"username": "admin", "password": "Wrong-Pass-1"}})
check("wrong camera password -> no video, status auth_failed", code == 201 and not bad["video"]["available"] and bad["status"] == "auth_failed", bad.get("status"))
check("exactly ONE authentication attempt was made", stats()["hikvision"] - before == 1, stats()["hikvision"] - before)
for _ in range(2):
    admin.req("POST", "/api/diagnostics", {"camera_kind": "private", "camera_id": bad["id"]})
code, _ = admin.req("POST", "/api/diagnostics", {"camera_kind": "private", "camera_id": bad["id"]})
check("camera locked after 3 rejected logins (423), no further attempts", code == 423 and stats()["hikvision"] - before == 3, (code, stats()["hikvision"] - before))
code, _ = admin.req("GET", f"/api/cameras/{bad['id']}/playback")
check("locked camera has no playback (409)", code == 409)

# --- brands without a documented snapshot API
code, ez = admin.req("POST", "/api/cameras", {**base, "name": "EZVIZ porch", "brand": "ezviz", "host": "127.0.0.1", "port": 8000})
check("EZVIZ: saved, reachable, but NO video with an explicit reason", code == 201 and not ez["video"]["available"] and "developer account" in (ez["video"]["reason"] or ""), ez.get("video"))
code, _ = admin.req("GET", f"/api/cameras/{ez['id']}/playback")
check("EZVIZ playback refused (409) - never pretends to have video", code == 409)
code, tp = admin.req("POST", "/api/cameras", {**base, "name": "Tapo hall", "brand": "tapo", "host": "127.0.0.1", "port": 8000})
check("Tapo: no video, explicit reason", code == 201 and not tp["video"]["available"] and "proprietary" in tp["video"]["reason"])
code, un = admin.req("POST", "/api/cameras", {**base, "name": "Uniview no path", "brand": "uniview", "host": "127.0.0.1", "port": 8000})
check("Uniview without a documented path: no video until a path is entered", code == 201 and not un["video"]["available"])

# --- offline camera
code, off = admin.req("POST", "/api/cameras", {**base, "name": "Unplugged", "brand": "hikvision", "host": "127.0.0.1", "port": 8977, "credentials": {"username": "a", "password": "b"}})
check("unreachable camera -> offline, no video", code == 201 and off["status"] == "offline" and not off["video"]["available"], off.get("status"))

# --- Reolink (documented query-string API)
code, reo = admin.req("POST", "/api/cameras", {**base, "name": "Reolink drive", "brand": "reolink", "host": "127.0.0.1", "port": 8082, "use_tls": False,
                                               "credentials": {"username": "admin", "password": "Reolink-Pass9"}})
check("Reolink camera verified (snapshot view)", code == 201 and reo["video"]["available"], json.dumps(reo.get("verification", reo))[:300])
code, reo_bad = admin.req("POST", "/api/cameras", {**base, "name": "Reolink wrong", "brand": "reolink", "host": "127.0.0.1", "port": 8082,
                                                   "credentials": {"username": "admin", "password": "Wrong-Pass-1"}})
check("Reolink wrong password detected (auth_failed)", code == 201 and reo_bad["status"] == "auth_failed")

# --- Hikvision NVR: discovery + channel import
code, nvr = admin.req("POST", "/api/devices", {**base, "name": "Hik NVR", "brand": "hikvision", "host": "127.0.0.1", "port": 8000,
                                               "credentials": {"username": "admin", "password": "Hik-Nvr-Pass9"}})
code, disc = admin.req("POST", f"/api/devices/{nvr['id']}/discover")
names = [c["name"] for c in disc.get("channels", [])]
check("Hikvision NVR channels discovered via ISAPI", code == 200 and names == ["Camera Gate", "Camera Yard", "Camera Office"], names)
code, imp = admin.req("POST", f"/api/devices/{nvr['id']}/import", {"channels": [{"index": c["index"], "name": c["name"]} for c in disc["channels"]]})
check("3 NVR channels imported and each VERIFIED", code == 200 and len(imp) == 3 and all(c["video"]["available"] for c in imp), [c["video"] for c in imp] if isinstance(imp, list) else imp)
code, h, img = admin.req("GET", f"/api/cameras/{imp[2]['id']}/snapshot-view", raw=True)
check("NVR channel 3 snapshot is a JPEG", code == 200 and img[:2] == b"\xff\xd8")

# --- Dahua XVR
code, xvr = admin.req("POST", "/api/devices", {**base, "name": "Dahua XVR", "brand": "dahua", "host": "127.0.0.1", "port": 8081,
                                               "credentials": {"username": "admin", "password": "Dahua-Xvr-Pass9"}})
code, disc = admin.req("POST", f"/api/devices/{xvr['id']}/discover")
check("Dahua channels discovered via configManager", code == 200 and [c["name"] for c in disc["channels"]] == ["Entrance", "Parking"], disc)
code, imp2 = admin.req("POST", f"/api/devices/{xvr['id']}/import", {"channels": [{"index": 1, "name": "Entrance"}, {"index": 2, "name": "Parking"}]})
check("Dahua channels verified", code == 200 and all(c["video"]["available"] for c in imp2))

# --- manual channels on a recorder without discovery
code, gen = admin.req("POST", "/api/devices", {**base, "name": "Reolink NVR", "brand": "reolink", "host": "127.0.0.1", "port": 8082,
                                               "credentials": {"username": "admin", "password": "Reolink-Pass9"}})
code, _ = admin.req("POST", f"/api/devices/{gen['id']}/discover")
check("recorder without documented discovery -> 409, add manually", code == 409)
code, man = admin.req("POST", f"/api/devices/{gen['id']}/import", {"channels": [{"index": 1, "name": "Manual CH1"}]})
check("manual channel added and verified", code == 200 and man[0]["video"]["available"])

# --- buildings / roles / sharing
code, site = admin.req("POST", "/api/sites", {"name": "Main building"})
code, _ = admin.req("PATCH", f"/api/cameras/{hik['id']}", {"site_id": site["id"]})
code, _ = admin.req("POST", "/api/auth/users", {"email": "guard@example.com", "password": "Guard-Pass-123", "display_name": "Guard"})
check("admin creates an account", code == 201)
code, _ = admin.req("POST", f"/api/sites/{site['id']}/members", {"email": "guard@example.com", "role": "viewer"})
guard = Client()
guard.req("POST", "/api/auth/login", {"email": "guard@example.com", "password": "Guard-Pass-123"})
code, gl = guard.req("GET", "/api/cameras")
check("viewer sees only the shared camera, without host/username/config", code == 200 and [c["name"] for c in gl] == ["Hik gate"] and gl[0]["host"] is None and gl[0]["username"] is None and "snapshot_path" not in gl[0]["config"], gl)
code, h, img = guard.req("GET", f"/api/cameras/{hik['id']}/snapshot-view", raw=True)
check("viewer can watch the shared snapshot view", code == 200 and img[:2] == b"\xff\xd8")
code, _ = guard.req("POST", "/api/diagnostics", {"camera_kind": "private", "camera_id": hik["id"]})
check("viewer cannot run diagnostics on it (404)", code == 404)
code, _ = guard.req("GET", f"/api/cameras/{reo['id']}")
check("viewer cannot see unshared cameras (404)", code == 404)
code, _ = guard.req("DELETE", f"/api/cameras/{hik['id']}")
check("viewer cannot delete (404)", code == 404)

# --- favorites, groups, history
admin.req("PUT", f"/api/favorites/private/{hik['id']}")
code, favs = admin.req("GET", "/api/favorites")
check("favorites", code == 200 and favs[0]["id"] == hik["id"])
code, g = admin.req("POST", "/api/groups", {"name": "Entrances"})
code, g = admin.req("PUT", f"/api/groups/{g['id']}/items", {"items": [{"camera_kind": "private", "camera_id": hik["id"]}, {"camera_kind": "private", "camera_id": reo["id"]}]})
check("groups", code == 200 and len(g["cameras"]) == 2)
admin.req("POST", "/api/history", {"camera_kind": "private", "camera_id": reo["id"]})
code, hist = admin.req("GET", "/api/history")
check("recent history", code == 200 and hist[0]["camera"]["id"] == reo["id"])
code, near = admin.req("GET", "/api/public-cameras/nearby?lat=40.758&lon=-73.985&radius_km=5")
check("public discovery (seed data)", code == 200 and any(c["name"] == "Times Square" for c in near))
code, diag = admin.req("GET", "/api/diagnostics")
check("diagnostics history recorded", code == 200 and len(diag) >= 3)

# --- secrets never leave the server
leaks = [s for s in SECRETS if any(s in b for b in seen_bodies)]
check("no camera password appears in ANY API response", not leaks, leaks)

ok = sum(1 for _, r in results if r)
print(f"\n{ok}/{len(results)} checks passed")
sys.exit(0 if ok == len(results) else 1)
