"""Database models.

Public and private cameras live in SEPARATE tables on purpose: a public feed can
never accidentally inherit an owner's credentials, and every private-camera
query is forced to filter on ``owner_id``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_version: Mapped[int] = mapped_column(Integer, default=0)  # bump to revoke all sessions
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PublicCamera(Base):
    """A camera whose operator intentionally publishes the feed to the public."""

    __tablename__ = "public_cameras"
    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_public_camera_provider_ext"),
        Index("ix_public_cameras_lat_lon", "latitude", "longitude"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    # traffic | webcam | public_ip
    camera_type: Mapped[str] = mapped_column(String(20))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    # hls | mjpeg | image | webpage
    stream_type: Mapped[str] = mapped_column(String(20))
    stream_url: Mapped[str | None] = mapped_column(Text)
    snapshot_url: Mapped[str | None] = mapped_column(Text)
    page_url: Mapped[str | None] = mapped_column(Text)
    source_name: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(String(200))
    snapshot_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    # online | offline | unknown
    status: Mapped[str] = mapped_column(String(20), default="unknown")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PrivateCamera(Base):
    """A camera the owning user has attested they own or are authorised to view."""

    __tablename__ = "private_cameras"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    # rtsp | onvif | webrtc | hls | mjpeg | vendor_api
    protocol: Mapped[str] = mapped_column(String(20))
    # generic | axis | hikvision | dahua | reolink | amcrest
    vendor: Mapped[str] = mapped_column(String(40), default="generic")
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int | None] = mapped_column(Integer)
    use_tls: Mapped[bool] = mapped_column(Boolean, default=False)
    # Non secret connection settings: stream paths, rtsp transport, onvif profile tokens...
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)

    # Encrypted {"username":..., "password":...}. AES-256-GCM, AAD bound to row+owner.
    credentials_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    credentials_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    credentials_key_id: Mapped[str | None] = mapped_column(String(40))

    authorization_attested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    authorization_basis: Mapped[str] = mapped_column(String(40))  # owner | authorized_user

    status: Mapped[str] = mapped_column(String(20), default="unknown")  # online|offline|auth_failed|unknown
    last_error: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Consecutive authentication failures. The camera is locked after a few so the
    # platform can never be used as a password oracle against a device.
    auth_failures: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SavedCamera(Base):
    __tablename__ = "saved_cameras"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    public_camera_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("public_cameras.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ViewHistory(Base):
    __tablename__ = "view_history"
    __table_args__ = (Index("ix_view_history_user_time", "user_id", "viewed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    camera_kind: Mapped[str] = mapped_column(String(10))  # public | private
    camera_id: Mapped[uuid.UUID] = mapped_column()
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DiagnosticRun(Base):
    __tablename__ = "diagnostic_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    camera_kind: Mapped[str] = mapped_column(String(10))
    camera_id: Mapped[uuid.UUID] = mapped_column()
    ok: Mapped[bool] = mapped_column(Boolean)
    steps: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(60))
    target: Mapped[str | None] = mapped_column(String(200))
    ip: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProviderSync(Base):
    __tablename__ = "provider_syncs"

    provider: Mapped[str] = mapped_column(String(40), primary_key=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    imported: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text)
