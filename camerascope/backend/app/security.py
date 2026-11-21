"""Password hashing, session tokens and camera-credential encryption."""
from __future__ import annotations

import base64
import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_settings

_hasher = PasswordHasher()  # argon2id with library defaults (RFC 9106 low-memory profile)
# Pre-computed hash used to equalise timing when the e-mail does not exist.
_DUMMY_HASH = _hasher.hash("camerascope-timing-equaliser")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


# --------------------------------------------------------------------------- JWT


def create_session_token(user_id: uuid.UUID, token_version: int) -> tuple[str, datetime]:
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(minutes=s.access_token_ttl_minutes)
    payload = {
        "sub": str(user_id),
        "ver": token_version,
        "iat": int(time.time()),
        "exp": exp,
        "jti": uuid.uuid4().hex,
        "typ": "session",
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm), exp


def decode_session_token(token: str) -> dict | None:
    s = get_settings()
    try:
        data = jwt.decode(
            token,
            s.jwt_secret,
            algorithms=[s.jwt_algorithm],
            options={"require": ["sub", "exp", "iat", "typ"]},
        )
    except jwt.PyJWTError:
        return None
    if data.get("typ") != "session":
        return None
    return data


# ------------------------------------------------------- credential encryption


@dataclass(frozen=True)
class EncryptedBlob:
    ciphertext: bytes
    nonce: bytes
    key_id: str


def _keys() -> list[tuple[str, bytes]]:
    out = []
    for item in get_settings().credential_keys.split(","):
        kid, _, b64 = item.strip().partition(":")
        out.append((kid, base64.b64decode(b64)))
    return out


def _aad(camera_id: uuid.UUID, owner_id: uuid.UUID) -> bytes:
    # Binding ciphertext to the row prevents an attacker with DB write access
    # from copying another user's encrypted credentials onto their own camera.
    return f"camerascope:v1:{camera_id}:{owner_id}".encode()


def encrypt_credentials(creds: dict, camera_id: uuid.UUID, owner_id: uuid.UUID) -> EncryptedBlob:
    kid, key = _keys()[0]
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, json.dumps(creds).encode(), _aad(camera_id, owner_id))
    return EncryptedBlob(ct, nonce, kid)


def decrypt_credentials(blob: EncryptedBlob, camera_id: uuid.UUID, owner_id: uuid.UUID) -> dict:
    keys = dict(_keys())
    if blob.key_id not in keys:
        raise ValueError("credential key no longer configured")
    pt = AESGCM(keys[blob.key_id]).decrypt(blob.nonce, blob.ciphertext, _aad(camera_id, owner_id))
    return json.loads(pt)


def generate_key() -> str:
    """Helper for operators: python -c 'from app.security import generate_key; print(generate_key())'"""
    return base64.b64encode(os.urandom(32)).decode()
