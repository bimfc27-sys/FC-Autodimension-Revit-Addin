from __future__ import annotations

import ipaddress
import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

# ------------------------------------------------------------------ auth


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("password")
    @classmethod
    def strong(cls, v: str) -> str:
        classes = sum(bool(re.search(p, v)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
        if classes < 3:
            raise ValueError("password must mix at least 3 of: lowercase, uppercase, digits, symbols")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    display_name: str
    is_admin: bool


# --------------------------------------------------------------- cameras

CameraKind = Literal["public", "private"]


class PublicCameraOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    kind: Literal["public"] = "public"
    id: uuid.UUID
    provider: str
    name: str
    description: str | None
    camera_type: str
    latitude: float
    longitude: float
    stream_type: str
    page_url: str | None
    source_name: str
    source_url: str | None
    license: str | None
    snapshot_allowed: bool
    status: str
    last_checked_at: datetime | None
    distance_km: float | None = None
    saved: bool = False


Protocol = Literal["rtsp", "onvif", "webrtc", "hls", "mjpeg", "vendor_api"]
Vendor = Literal["generic", "axis", "hikvision", "dahua", "amcrest", "reolink"]

_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$")
_PATH_RE = re.compile(r"^/[\x21-\x7e]*$")  # printable, no spaces / control chars


def _valid_host(v: str) -> str:
    v = v.strip()
    try:
        ipaddress.ip_address(v.strip("[]"))
        return v.strip("[]")
    except ValueError:
        pass
    if not _HOST_RE.match(v):
        raise ValueError("host must be a hostname or IP address (no scheme, path or credentials)")
    return v.lower()


class CameraConfig(BaseModel):
    """Non-secret connection settings."""

    main_path: str | None = Field(None, max_length=512, description="Stream path for high quality, e.g. /stream1")
    sub_path: str | None = Field(None, max_length=512, description="Optional low quality stream path")
    snapshot_path: str | None = Field(None, max_length=512)
    rtsp_transport: Literal["automatic", "tcp", "udp"] = "tcp"
    channel: int = Field(1, ge=1, le=64)
    onvif_main_profile: str | None = Field(None, max_length=128)
    onvif_sub_profile: str | None = Field(None, max_length=128)
    # Filled by ONVIF discovery, stored without credentials:
    resolved_main_uri: str | None = Field(None, max_length=1024)
    resolved_sub_uri: str | None = Field(None, max_length=1024)
    resolved_snapshot_uri: str | None = Field(None, max_length=1024)
    snapshot_allowed: bool = True

    @field_validator("main_path", "sub_path", "snapshot_path")
    @classmethod
    def _path(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        if not v.startswith("/"):
            v = "/" + v
        if not _PATH_RE.match(v) or "@" in v.split("?")[0]:
            raise ValueError("invalid path")
        return v


class Credentials(BaseModel):
    username: str = Field("", max_length=128)
    password: str = Field("", max_length=256)

    @field_validator("username", "password")
    @classmethod
    def no_ctrl(cls, v: str) -> str:
        if any(ord(c) < 32 for c in v):
            raise ValueError("control characters are not allowed")
        return v


class PrivateCameraIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    protocol: Protocol
    vendor: Vendor = "generic"
    host: str
    port: int | None = Field(None, ge=1, le=65535)
    use_tls: bool = False
    config: CameraConfig = Field(default_factory=CameraConfig)
    credentials: Credentials | None = None
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    # Which Local Camera Gateway reaches this camera (None = directly from the cloud / VPN).
    gateway_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None
    authorization_basis: Literal["owner", "authorized_user"]
    # Explicit attestation is mandatory; the API refuses to store a camera without it.
    authorization_attested: bool

    @field_validator("host")
    @classmethod
    def _check_host(cls, v: str) -> str:
        return _valid_host(v)

    @model_validator(mode="after")
    def _attest(self):
        if not self.authorization_attested:
            raise ValueError("You must confirm you own or are authorised to access this camera")
        if self.protocol == "vendor_api" and self.vendor == "generic":
            raise ValueError("vendor_api protocol requires a specific vendor")
        return self


class PrivateCameraUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    host: str | None = None
    port: int | None = Field(None, ge=1, le=65535)
    use_tls: bool | None = None
    config: CameraConfig | None = None
    credentials: Credentials | None = None
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    gateway_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None

    @field_validator("host")
    @classmethod
    def _h(cls, v: str | None) -> str | None:
        return None if v is None else _valid_host(v)


class GatewayRef(BaseModel):
    id: uuid.UUID
    name: str
    online: bool


class Ref(BaseModel):
    id: uuid.UUID
    name: str


class PrivateCameraOut(BaseModel):
    """Never contains passwords or full stream URLs. Connection details (host, paths)
    are returned only to the camera's owner; shared viewers see name/status only."""

    kind: Literal["private"] = "private"
    id: uuid.UUID
    name: str
    description: str | None
    protocol: str
    vendor: str
    is_owner: bool = True
    access: Literal["owner", "shared"] = "owner"
    host: str | None = None
    port: int | None = None
    use_tls: bool = False
    config: dict = {}
    latitude: float | None
    longitude: float | None
    has_credentials: bool
    username: str | None = None  # shown so the owner knows which account is used; password never returned
    authorization_basis: str
    authorization_attested_at: datetime
    # online | offline | auth_failed | gateway_offline | unknown
    status: str
    connection: Literal["gateway", "cloud"] = "cloud"
    gateway: GatewayRef | None = None
    device: Ref | None = None
    site: Ref | None = None
    channel: int | None = None
    reachable_checked_at: datetime | None = None
    last_error: str | None
    last_checked_at: datetime | None
    created_at: datetime


class OnvifProbeIn(BaseModel):
    host: str
    port: int = Field(80, ge=1, le=65535)
    use_tls: bool = False
    credentials: Credentials
    gateway_id: uuid.UUID | None = None
    authorization_attested: bool

    @field_validator("host")
    @classmethod
    def _check_host(cls, v: str) -> str:
        return _valid_host(v)

    @model_validator(mode="after")
    def _attest(self):
        if not self.authorization_attested:
            raise ValueError("You must confirm you own or are authorised to access this camera")
        return self


# --------------------------------------------------------------- playback


class QualityOption(BaseModel):
    id: str
    label: str


class Playback(BaseModel):
    kind: CameraKind
    camera_id: uuid.UUID
    # webrtc+hls: gateway stream (WHEP first, HLS fallback); hls: direct/proxied HLS
    # mjpeg: multipart image stream; image: periodically refreshed still; webpage: external page only
    mode: Literal["gateway", "hls", "mjpeg", "image", "webpage"]
    hls_url: str | None = None
    whep_url: str | None = None
    image_url: str | None = None
    page_url: str | None = None
    refresh_seconds: int | None = None
    quality: str = "auto"
    qualities: list[QualityOption] = []
    snapshot_allowed: bool = False
    snapshot_url: str | None = None
    attribution: str | None = None


class HistoryIn(BaseModel):
    camera_kind: CameraKind
    camera_id: uuid.UUID


class DiagnosticsIn(BaseModel):
    camera_kind: CameraKind
    camera_id: uuid.UUID


class DiagnosticStep(BaseModel):
    name: str
    ok: bool
    detail: str
    duration_ms: int


class DiagnosticsOut(BaseModel):
    id: int | None = None
    camera_kind: CameraKind
    camera_id: uuid.UUID
    ok: bool
    steps: list[DiagnosticStep]
    created_at: datetime | None = None
