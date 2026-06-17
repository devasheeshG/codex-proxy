# Path: app/utils/security/keys.py
# Description: Proxy user API-key generation, hashing, and display prefix.

import hashlib
import secrets

# User key format: "usr_<random>". The random part is URL-safe and long enough to be unguessable.
_KEY_RANDOM_BYTES = 24


def generate_user_key() -> str:
    """Return a fresh plaintext user key. Shown to the admin exactly once."""
    return f"usr_{secrets.token_urlsafe(_KEY_RANDOM_BYTES)}"


def hash_key(plaintext_key: str) -> str:
    """Hash a user key for storage / lookup (SHA-256 hex)."""
    return hashlib.sha256(plaintext_key.encode()).hexdigest()


def key_prefix(plaintext_key: str) -> str:
    """A short, non-secret prefix kept for display in the dashboard."""
    return plaintext_key[:12]
