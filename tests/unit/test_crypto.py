"""
Tests for app/core/crypto.py

Pure functions — no external dependencies required.
"""
import pytest
from cryptography.fernet import InvalidToken

from app.core.crypto import encrypt_secret, decrypt_secret

MASTER = "test-master-key-for-unit-tests"


def test_roundtrip():
    plain = "my-secret-password"
    assert decrypt_secret(MASTER, encrypt_secret(MASTER, plain)) == plain


def test_wrong_key_raises():
    token = encrypt_secret(MASTER, "secret")
    with pytest.raises(InvalidToken):
        decrypt_secret("completely-different-key", token)


def test_tampered_token_raises():
    token = encrypt_secret(MASTER, "secret")
    tampered = token[:-5] + "XXXXX"
    with pytest.raises(Exception):
        decrypt_secret(MASTER, tampered)


def test_fernet_uses_random_iv():
    """Same plaintext must produce different ciphertext each call."""
    t1 = encrypt_secret(MASTER, "same-input")
    t2 = encrypt_secret(MASTER, "same-input")
    assert t1 != t2


def test_empty_string():
    assert decrypt_secret(MASTER, encrypt_secret(MASTER, "")) == ""


def test_unicode():
    plain = "pássword€日本語"
    assert decrypt_secret(MASTER, encrypt_secret(MASTER, plain)) == plain


def test_long_value():
    plain = "x" * 10_000
    assert decrypt_secret(MASTER, encrypt_secret(MASTER, plain)) == plain
