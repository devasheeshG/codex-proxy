"""Persistent, user-safe health tracking for pooled upstream accounts."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Tuple

import httpx
from cryptography.fernet import InvalidToken
from sqlalchemy.orm import Session

from app import config
from app.logger import get_logger
from app.utils.models.api import ProviderHealth

logger = get_logger()


class ProviderReauthenticationRequired(RuntimeError):
    """The account cannot be used again until its OAuth credentials are replaced."""


def mark_success(account) -> None:
    now = datetime.now(timezone.utc)
    account.provider_health = ProviderHealth.HEALTHY
    account.provider_health_code = None
    account.provider_health_message = None
    account.provider_health_checked_at = now
    account.provider_health_last_success_at = now
    account.provider_health_failure_count = 0


def mark_response(account, response: httpx.Response) -> None:
    """Treat authenticated responses as healthy while surfacing access/server failures."""
    if response.status_code in {401, 403} or response.status_code >= 500:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            mark_failure(account, exc)
            return
    mark_success(account)


def classify_failure(exc: Exception, *, context: str = "provider_probe") -> Tuple[ProviderHealth, str, str]:
    if isinstance(exc, ProviderReauthenticationRequired):
        return ProviderHealth.REAUTH_REQUIRED, "upstream_unauthorized", str(exc)
    if isinstance(exc, InvalidToken) or context == "credential_decrypt":
        return (
            ProviderHealth.REAUTH_REQUIRED,
            "credential_unreadable",
            "Stored authentication credentials cannot be read. Re-authenticate this account.",
        )
    if context == "account_identity_missing":
        return (
            ProviderHealth.REAUTH_REQUIRED,
            "account_identity_missing",
            "The account identity is incomplete. Re-authenticate this account.",
        )
    if context == "upstream_empty_404":
        return (
            ProviderHealth.DEGRADED,
            "upstream_empty_404",
            "The provider temporarily rejected this account with an empty 404 response.",
        )
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        url = str(exc.request.url)
        # A 401 from any authenticated ChatGPT endpoint means the saved OAuth
        # access token was revoked/invalidated. The quota endpoint returns this
        # directly (rather than through OAUTH_TOKEN_URL), so classifying it as
        # merely degraded leaves dead accounts in the pool and misleads admins.
        if status == 401 or (url == config.OAUTH_TOKEN_URL and status == 400):
            return (
                ProviderHealth.REAUTH_REQUIRED,
                "oauth_refresh_rejected",
                "Authentication is no longer valid. Re-authenticate this account to restore it.",
            )
        if status in {401, 403}:
            return ProviderHealth.DEGRADED, "provider_access_rejected", "The provider rejected this account request."
        if status == 429:
            return ProviderHealth.DEGRADED, "provider_rate_limited", "The provider is temporarily rate-limiting checks."
        if status >= 500:
            return ProviderHealth.DEGRADED, "provider_server_error", "The provider is temporarily unavailable."
        return ProviderHealth.DEGRADED, "provider_probe_failed", "The provider health check failed."
    if isinstance(exc, httpx.TimeoutException):
        return ProviderHealth.DEGRADED, "provider_timeout", "The provider health check timed out."
    if isinstance(exc, httpx.RequestError):
        return ProviderHealth.DEGRADED, "provider_unreachable", "The provider could not be reached."
    if isinstance(exc, (KeyError, TypeError, ValueError)) and context == "oauth_response":
        return (
            ProviderHealth.REAUTH_REQUIRED,
            "oauth_response_invalid",
            "Authentication returned invalid credentials. Re-authenticate this account.",
        )
    return ProviderHealth.DEGRADED, "provider_probe_failed", "The provider health check failed."


def mark_failure(account, exc: Exception, *, context: str = "provider_probe") -> ProviderHealth:
    health, code, message = classify_failure(exc, context=context)
    account.provider_health = health
    account.provider_health_code = code
    account.provider_health_message = message
    account.provider_health_checked_at = datetime.now(timezone.utc)
    account.provider_health_failure_count = (account.provider_health_failure_count or 0) + 1
    return health


def persist_failure(db: Session, account_id: uuid.UUID, exc: Exception, *, context: str = "provider_probe") -> ProviderHealth:
    """Persist health in a clean transaction after an upstream operation failed."""
    from app.utils.postgres import AccountDb

    db.rollback()
    account = db.query(AccountDb).filter(AccountDb.id == account_id).with_for_update().one_or_none()
    if account is None:
        return ProviderHealth.UNKNOWN
    if isinstance(exc, ProviderReauthenticationRequired) and account.provider_health == ProviderHealth.REAUTH_REQUIRED:
        db.rollback()
        return ProviderHealth.REAUTH_REQUIRED
    was_reauthentication_required = account.provider_health == ProviderHealth.REAUTH_REQUIRED
    health = mark_failure(account, exc, context=context)
    if health == ProviderHealth.REAUTH_REQUIRED and not was_reauthentication_required:
        try:
            with db.begin_nested():
                from app.utils import notifications

                notifications.enqueue_account_authentication_expired(
                    db,
                    account,
                    config.get_settings().FRONTEND_ORIGIN,
                )
        except Exception:  # noqa: BLE001
            logger.exception("Could not queue authentication-expired notification for account %s", account.id)
    db.commit()
    return health


def reauthentication_error() -> ProviderReauthenticationRequired:
    return ProviderReauthenticationRequired("Authentication is no longer valid. Re-authenticate this account to restore it.")
