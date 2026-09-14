"""Dashboard permission catalog and request-to-permission mapping."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Final

PERMISSIONS: Final[frozenset[str]] = frozenset(
    {
        "analytics:read",
        "events:archive:read",
        "accounts:read",
        "accounts:write",
        "accounts:delete",
        "proxy_users:read",
        "proxy_users:write",
        "proxy_users:delete",
        "api_keys:read",
        "api_keys:write",
        "api_keys:delete",
        "fallbacks:read",
        "fallbacks:write",
        "fallbacks:delete",
        "notifications:read",
        "notifications:write",
        "team:members:read",
        "team:members:write",
        "team:members:delete",
    }
)


def has_permission(permissions: set[str] | frozenset[str], required: str | None) -> bool:
    """Check an exact permission or the root wildcard."""
    return required is None or "*" in permissions or required in permissions


def required_permission(path: str, method: str) -> str | None:
    """Map an admin request path to its minimum permission.

    The mapping is centralized so existing routes cannot accidentally omit an
    authorization check. Unclassified admin routes fail closed for members;
    the environment-configured root owner retains wildcard access.
    """
    normalized = path.removeprefix("/api").removeprefix("/v1") or "/"
    method = method.upper()
    if normalized.startswith("/auth/"):
        return None
    if normalized.startswith("/team/members"):
        if method == "GET":
            return "team:members:read"
        if method == "DELETE":
            return "team:members:delete"
        return "team:members:write"
    if normalized.startswith("/team/permissions"):
        return "team:members:read"
    if normalized.startswith("/lookups"):
        return "analytics:read"
    if normalized.startswith("/stats/") or normalized == "/events":
        return "analytics:read"
    if normalized.startswith("/requests") or normalized.startswith("/archive"):
        return "events:archive:read"
    if normalized.startswith("/accounts"):
        if method == "GET":
            return "accounts:read"
        if method == "DELETE":
            return "accounts:delete"
        return "accounts:write"
    if normalized.startswith("/users"):
        if "/keys/" in normalized:
            if method == "GET":
                return "api_keys:read"
            if method == "DELETE":
                return "api_keys:delete"
            return "api_keys:write"
        if normalized.endswith("/keys"):
            return "api_keys:read" if method == "GET" else "api_keys:write"
        if method == "GET":
            return "proxy_users:read"
        if method == "DELETE":
            return "proxy_users:delete"
        return "proxy_users:write"
    if normalized.startswith("/fallbacks"):
        if method == "GET":
            return "fallbacks:read"
        if method == "DELETE":
            return "fallbacks:delete"
        return "fallbacks:write"
    if normalized.startswith("/notifications"):
        return "notifications:read" if method == "GET" else "notifications:write"
    return "dashboard:unmapped"


def hash_password(password: str) -> str:
    """Hash a dashboard password with a salted scrypt digest."""
    if len(password) < 12:
        raise ValueError("Dashboard passwords must be at least 12 characters")
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)

    def encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    return f"scrypt${encode(salt)}${encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    """Verify a password hash without revealing whether the user exists."""
    try:
        algorithm, salt_text, digest_text = encoded.split("$", 2)
        if algorithm != "scrypt":
            return False

        def pad(value: str) -> str:
            return value + "=" * (-len(value) % 4)

        salt = base64.urlsafe_b64decode(pad(salt_text))
        expected = base64.urlsafe_b64decode(pad(digest_text))
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False
