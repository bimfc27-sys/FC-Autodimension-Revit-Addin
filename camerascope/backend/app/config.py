"""Application configuration, loaded from environment variables (12-factor)."""
from __future__ import annotations

import base64
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    app_name: str = "CameraScope"
    environment: str = Field("production", description="production | development | test")

    database_url: str = "postgresql+asyncpg://camerascope:camerascope@localhost:5432/camerascope"

    # --- Auth -------------------------------------------------------------
    jwt_secret: str = Field(..., min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 12
    session_cookie_name: str = "cs_session"
    # Cookies are Secure by default; set to false ONLY for plain-http local dev.
    cookie_secure: bool = True
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # --- Credential encryption (AES-256-GCM) ------------------------------
    # Comma separated list of "<key_id>:<base64 32-byte key>". The FIRST key
    # encrypts new secrets, every key can decrypt (supports rotation).
    credential_keys: str = Field(..., description="kid:base64key[,kid:base64key...]")

    # --- Outbound network guard (SSRF protection) -------------------------
    # Self-hosted installs usually want to reach cameras on the LAN. A hosted
    # multi-tenant deployment MUST set this to false.
    allow_private_network_targets: bool = True
    # Hostnames of internal infrastructure that user supplied targets may never reach.
    netguard_deny_hosts: str = "postgres,db,mediamtx,backend,frontend,caddy,localhost"
    outbound_timeout_seconds: float = 8.0

    # --- MediaMTX (RTSP/ONVIF/WebRTC -> browser gateway) ------------------
    mediamtx_api_url: str = "http://localhost:9997"
    mediamtx_hls_url: str = "http://localhost:8888"
    mediamtx_webrtc_url: str = "http://localhost:8889"

    # --- Public camera providers -----------------------------------------
    providers_enabled: str = "seed,caltrans,nyc_dot,osm"
    caltrans_districts: str = "3,4,7,11,12"
    windy_api_key: str = ""
    sync_providers_on_startup: bool = True
    provider_sync_interval_hours: int = 12

    cors_origins: str = ""

    @field_validator("credential_keys")
    @classmethod
    def _validate_keys(cls, v: str) -> str:
        for item in v.split(","):
            kid, _, b64 = item.strip().partition(":")
            if not kid or not b64:
                raise ValueError("credential_keys entries must look like kid:base64key")
            if len(base64.b64decode(b64)) != 32:
                raise ValueError(f"credential key {kid!r} must decode to 32 bytes")
        return v

    @property
    def deny_hosts(self) -> list[str]:
        return [h.strip().lower() for h in self.netguard_deny_hosts.split(",") if h.strip()]

    @property
    def enabled_providers(self) -> list[str]:
        return [p.strip() for p in self.providers_enabled.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
