"""Concrete public-camera providers."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ...config import get_settings
from ..http import guarded_request, make_client
from .base import CameraRecord, Provider, guess_stream_type

log = logging.getLogger(__name__)
SEED_FILE = Path(__file__).resolve().parents[2] / "seed" / "public_cameras.json"


async def _get_json(url: str, headers: dict | None = None, timeout: float = 30.0, method: str = "GET", data=None):
    async with make_client(timeout) as client:
        r = await guarded_request(client, method, url, headers=headers or {}, data=data)
        r.raise_for_status()
        return r.json()


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class SeedProvider(Provider):
    name = "seed"
    title = "Curated public webcams"
    description = "Hand-curated list of publicly published webcams shipped with CameraScope."

    async def fetch_all(self) -> list[CameraRecord]:
        data = json.loads(SEED_FILE.read_text())
        return [CameraRecord(**item) for item in data]


class CaltransProvider(Provider):
    """California DOT CCTV - official public data feed (cwwp2.dot.ca.gov)."""

    name = "caltrans"
    title = "Caltrans traffic cameras"
    description = "California Department of Transportation public CCTV feeds (HLS video + still images)."

    async def fetch_all(self) -> list[CameraRecord]:
        out: list[CameraRecord] = []
        errors: list[str] = []
        for d in get_settings().caltrans_districts.split(","):
            d = d.strip()
            if not d.isdigit():
                continue
            url = f"https://cwwp2.dot.ca.gov/data/d{int(d)}/cctv/cctvStatusD{int(d):02d}.json"
            try:
                payload = await _get_json(url)
            except Exception as exc:  # one district failing must not stop the others
                log.warning("caltrans district %s failed: %s", d, exc)
                errors.append(f"D{d}: {exc}")
                continue
            for item in payload.get("data", []):
                c = item.get("cctv", {})
                loc = c.get("location", {})
                img = c.get("imageData", {})
                lat, lon = _f(loc.get("latitude")), _f(loc.get("longitude"))
                if lat is None or lon is None:
                    continue
                stream = img.get("streamingVideoURL") or ""
                still = (img.get("static") or {}).get("currentImageURL") or ""
                stream = stream if stream.startswith("https://") else None
                still = still if still.startswith("https://") else None
                if not (stream or still):
                    continue
                name = loc.get("locationName") or loc.get("nearbyPlace") or f"Caltrans D{d} #{c.get('index')}"
                out.append(
                    CameraRecord(
                        external_id=f"d{d}-{c.get('index')}",
                        name=name,
                        description=" ".join(x for x in [loc.get("route"), loc.get("direction"), loc.get("county")] if x) or None,
                        latitude=lat,
                        longitude=lon,
                        camera_type="traffic",
                        stream_type="hls" if stream else "image",
                        stream_url=stream,
                        snapshot_url=still,
                        source_name="Caltrans CWWP2",
                        source_url="https://cwwp2.dot.ca.gov/",
                        license="California public data - see source terms",
                        snapshot_allowed=True,
                        status="online" if str(c.get("inService", "true")).lower() == "true" else "offline",
                    )
                )
        if errors and not out:
            raise RuntimeError("all districts failed - " + "; ".join(errors)[:300])
        return out


class NycDotProvider(Provider):
    """New York City DOT traffic management center webcams (public still images)."""

    name = "nyc_dot"
    title = "NYC DOT traffic cameras"
    description = "New York City Department of Transportation public traffic camera images."

    async def fetch_all(self) -> list[CameraRecord]:
        data = await _get_json("https://webcams.nyctmc.org/api/cameras")
        out = []
        for c in data if isinstance(data, list) else []:
            lat, lon = _f(c.get("latitude")), _f(c.get("longitude"))
            img = c.get("imageUrl") or ""
            if lat is None or lon is None or not img.startswith("https://"):
                continue
            out.append(
                CameraRecord(
                    external_id=str(c.get("id")),
                    name=c.get("name") or "NYC DOT camera",
                    description=c.get("area"),
                    latitude=lat,
                    longitude=lon,
                    camera_type="traffic",
                    stream_type="image",
                    snapshot_url=img,
                    page_url="https://webcams.nyctmc.org/",
                    source_name="NYC DOT",
                    source_url="https://webcams.nyctmc.org/",
                    license="NYC DOT public camera imagery - see source terms",
                    snapshot_allowed=True,
                    status="online" if str(c.get("isOnline", "true")).lower() == "true" else "offline",
                )
            )
        return out


class OsmWebcamProvider(Provider):
    """OpenStreetMap features tagged with a public webcam URL (contact:webcam / webcam)."""

    name = "osm"
    title = "OpenStreetMap webcams"
    description = "Community-mapped public webcams from OpenStreetMap (© OpenStreetMap contributors, ODbL)."
    mode = "area"
    endpoint = "https://overpass-api.de/api/interpreter"

    async def fetch_area(self, lat: float, lon: float, radius_km: float) -> list[CameraRecord]:
        r = int(min(radius_km, 50) * 1000)
        q = (
            "[out:json][timeout:25];("
            f'nwr["contact:webcam"](around:{r},{lat},{lon});'
            f'nwr["webcam"](around:{r},{lat},{lon});'
            f'nwr["surveillance:type"="webcam"]["url"](around:{r},{lat},{lon});'
            ");out center 300;"
        )
        data = await _get_json(self.endpoint, method="POST", data={"data": q}, timeout=40)
        out = []
        for el in data.get("elements", []):
            tags = el.get("tags", {})
            url = tags.get("contact:webcam") or tags.get("webcam") or tags.get("url")
            la = el.get("lat") or (el.get("center") or {}).get("lat")
            lo = el.get("lon") or (el.get("center") or {}).get("lon")
            if not url or la is None or not url.startswith(("http://", "https://")):
                continue
            url = url.split(";")[0].strip()
            st = guess_stream_type(url)
            out.append(
                CameraRecord(
                    external_id=f"{el.get('type')}/{el.get('id')}",
                    name=tags.get("name") or tags.get("description") or "Public webcam",
                    latitude=float(la),
                    longitude=float(lo),
                    camera_type="webcam",
                    stream_type=st,
                    stream_url=url if st in ("hls", "mjpeg") else None,
                    snapshot_url=url if st == "image" else None,
                    page_url=url if st == "webpage" else (tags.get("website") or None),
                    source_name="OpenStreetMap",
                    source_url=f"https://www.openstreetmap.org/{el.get('type')}/{el.get('id')}",
                    license="Location data © OpenStreetMap contributors (ODbL); feed terms set by its operator",
                    snapshot_allowed=False,
                )
            )
        return out


class WindyProvider(Provider):
    """Windy Webcams API v3 (requires a free API key: WINDY_API_KEY)."""

    name = "windy"
    title = "Windy webcams"
    description = "Public webcams from the Windy.com Webcams API."
    mode = "area"

    async def fetch_area(self, lat: float, lon: float, radius_km: float) -> list[CameraRecord]:
        key = get_settings().windy_api_key
        if not key:
            return []
        url = (
            "https://api.windy.com/webcams/api/v3/webcams"
            f"?nearby={lat:.5f},{lon:.5f},{int(min(radius_km, 250))}&limit=50&include=location,images,urls"
        )
        data = await _get_json(url, headers={"x-windy-api-key": key})
        out = []
        for w in data.get("webcams", []):
            loc = w.get("location") or {}
            img = ((w.get("images") or {}).get("current") or {}).get("preview")
            page = (w.get("urls") or {}).get("detail")
            if loc.get("latitude") is None:
                continue
            out.append(
                CameraRecord(
                    external_id=str(w.get("webcamId")),
                    name=w.get("title") or "Windy webcam",
                    description=loc.get("city"),
                    latitude=float(loc["latitude"]),
                    longitude=float(loc["longitude"]),
                    camera_type="webcam",
                    stream_type="image" if img else "webpage",
                    snapshot_url=img,
                    page_url=page,
                    source_name="Windy.com",
                    source_url=page or "https://www.windy.com/webcams",
                    license="Webcams provided by windy.com - attribution required",
                    snapshot_allowed=False,
                    status="online" if w.get("status") == "active" else "offline",
                )
            )
        return out


class DemoProvider(Provider):
    """Local test-pattern camera started by docker compose --profile demo."""

    name = "demo"
    title = "Demo test pattern"
    description = "Synthetic public stream relayed through the gateway, for verifying the pipeline."

    async def fetch_all(self) -> list[CameraRecord]:
        return [
            CameraRecord(
                external_id="test-pattern",
                name="CameraScope demo test pattern",
                description="Synthetic stream generated by the demo-camera container",
                latitude=37.7793,
                longitude=-122.4193,
                camera_type="public_ip",
                stream_type="gateway",
                stream_url="rtsp://demo-camera:8554/public",
                source_name="CameraScope demo",
                license="Synthetic",
                snapshot_allowed=True,
                status="online",
            )
        ]


ALL_PROVIDERS: dict[str, Provider] = {
    p.name: p
    for p in (SeedProvider(), CaltransProvider(), NycDotProvider(), OsmWebcamProvider(), WindyProvider(), DemoProvider())
}
