import uuid

import pytest
from cryptography.exceptions import InvalidTag

from app.security import (
    EncryptedBlob,
    create_session_token,
    decode_session_token,
    decrypt_credentials,
    encrypt_credentials,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("Correct-Horse-9")
    assert h.startswith("$argon2id$")
    assert verify_password("Correct-Horse-9", h)
    assert not verify_password("wrong", h)
    assert not verify_password("anything", None)


def test_credentials_encrypted_and_bound_to_row():
    cam, owner = uuid.uuid4(), uuid.uuid4()
    blob = encrypt_credentials({"username": "admin", "password": "p@ss"}, cam, owner)
    assert b"p@ss" not in blob.ciphertext
    assert decrypt_credentials(blob, cam, owner) == {"username": "admin", "password": "p@ss"}
    # Copying the ciphertext onto another camera / owner must fail authentication.
    with pytest.raises(InvalidTag):
        decrypt_credentials(blob, uuid.uuid4(), owner)
    with pytest.raises(InvalidTag):
        decrypt_credentials(blob, cam, uuid.uuid4())


def test_old_key_still_decrypts(monkeypatch):
    cam, owner = uuid.uuid4(), uuid.uuid4()
    blob = encrypt_credentials({"username": "u", "password": "p"}, cam, owner)
    assert blob.key_id == "k1"
    from app.security import _keys
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import os, json
    k0 = dict(_keys())["k0"]
    nonce = os.urandom(12)
    ct = AESGCM(k0).encrypt(nonce, json.dumps({"username": "old"}).encode(), f"camerascope:v1:{cam}:{owner}".encode())
    assert decrypt_credentials(EncryptedBlob(ct, nonce, "k0"), cam, owner)["username"] == "old"


def test_session_token():
    uid = uuid.uuid4()
    tok, _ = create_session_token(uid, 3)
    data = decode_session_token(tok)
    assert data["sub"] == str(uid) and data["ver"] == 3
    assert decode_session_token(tok + "x") is None
