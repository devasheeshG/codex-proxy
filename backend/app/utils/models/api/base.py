# Path: app/utils/models/api/base.py
# Description: Shared Pydantic enums used across the routers and the database schema.

from enum import Enum


class AccountStatus(str, Enum):
    """Lifecycle of a pooled subscription account."""

    ACTIVE = "ACTIVE"  # eligible for rotation
    DISABLED = "DISABLED"  # manually parked (e.g. banned / removed)
    COOLDOWN = "COOLDOWN"  # temporarily resting after a 429


class ProviderHealth(str, Enum):
    """Latest known health of an account's upstream provider credentials."""

    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
