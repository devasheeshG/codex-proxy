"""Dashboard roles, permissions, and request-to-permission mapping.

The proxy API-key users are deliberately separate from dashboard members. This
module only governs the administrative console.
"""

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
        "team:roles:read",
        "team:roles:write",
    }
)

ROLE_PERMISSIONS: Final[dict[str, frozenset[str]]] = {
    "owner": frozenset({"*"}),
    "administrator": PERMISSIONS,
    "overview_viewer": frozenset({"analytics:read"}),
    "event_viewer": frozenset({"analytics:read", "events:archive:read"}),
    "operations_operator": frozenset({"analytics:read", "accounts:read", "accounts:write"}),
    "account_manager": frozenset({"accounts:read", "accounts:write", "accounts:delete"}),
    "user_manager": frozenset(
        {"analytics:read", "proxy_users:read", "proxy_users:write", "proxy_users:delete", "api_keys:read", "api_keys:write", "api_keys:delete"}
    ),
    "fallback_manager": frozenset({"fallbacks:read", "fallbacks:write", "fallbacks:delete"}),
    "notification_manager": frozenset({"notifications:read", "notifications:write"}),
    "team_manager": frozenset(
        {
            "team:members:read",
            "team:members:write",
            "team:members:delete",
            "team:roles:read",
            "team:roles:write",
        }
    ),
    "read_only": frozenset({"analytics:read", "accounts:read", "proxy_users:read", "api_keys:read", "fallbacks:read", "notifications:read"}),
}

ROLE_LABELS: Final[dict[str, str]] = {
    "owner": "Owner",
    "administrator": "Administrator",
    "overview_viewer": "Overview viewer",
    "event_viewer": "Event viewer",
    "operations_operator": "Operations operator",
    "account_manager": "Account manager",
    "user_manager": "User manager",
    "fallback_manager": "Fallback manager",
    "notification_manager": "Notification manager",
    "team_manager": "Team manager",
    "read_only": "Read-only viewer",
}


def permissions_for_role(role: str) -> frozenset[str]:
    """Return the immutable permission set for a supported role."""
    return ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(permissions: set[str] | frozenset[str], required: str | None) -> bool:
    """Check an exact permission or the root wildcard."""
    return required is None or "*" in permissions or required in permissions


def required_permission(path: str, method: str) -> str | None:
    """Map an admin request path to its minimum permission.

    Keeping this mapping centralized means existing routes cannot accidentally
    forget to add a permission dependency. The backend remains authoritative;
    frontend visibility is only a convenience.
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
    if normalized.startswith("/team/roles"):
        return "team:roles:read" if method == "GET" else "team:roles:write"
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
    # Every authenticated dashboard route must be explicitly classified. The
    # wildcard owner can still use newly added routes, while regular members
    # fail closed until a permission is deliberately assigned here.
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
