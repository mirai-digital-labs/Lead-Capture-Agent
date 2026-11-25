"""Authentication & password hashing helpers using Python standard library (scrypt + secrets)."""

from __future__ import annotations

import hashlib
import hmac
import secrets


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Hash a password with scrypt and return (hex_hash, hex_salt)."""
    salt_bytes = bytes.fromhex(salt) if salt else secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt_bytes,
        n=16384,
        r=8,
        p=1,
    )
    return derived.hex(), salt_bytes.hex()


def verify_password(password: str, expected_hash: str, salt: str) -> bool:
    """Constant-time verification of a password against its stored scrypt hash."""
    computed, _ = hash_password(password, salt=salt)
    return hmac.compare_digest(computed, expected_hash)


def generate_session_token() -> str:
    """Return a cryptographically secure random bearer token."""
    return secrets.token_urlsafe(32)
