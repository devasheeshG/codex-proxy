# Path: app/utils/rotation.py
# Description: Account rotation engine -- token freshness, quota tracking, quota-aware selection, and 429 cooldowns.

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Mapping, Optional, Set

from sqlalchemy.orm import Session

from app import config
from app.utils import crypto, oauth, provider_health
from app.utils.models.api import AccountStatus, ProviderHealth
from app.utils.postgres import AccountDb, UserDb

# Values currently emitted by the Codex backend when a limit cannot serve more
# traffic. HTTP 429 is still the primary failover signal.
HARD_LIMIT_REACHED_TYPES = {
    "rate_limit_reached",
    "workspace_owner_credits_depleted",
    "workspace_member_credits_depleted",
    "workspace_owner_usage_limit_reached",
    "workspace_member_usage_limit_reached",
}


@dataclass(frozen=True)
class QuotaWindow:
    """One provider quota window attached to a pooled account."""

    key: str
    label: str
    used_pct: Optional[float]
    reset_at: Optional[datetime]
    threshold: float


def quota_windows(account: AccountDb) -> tuple[QuotaWindow, ...]:
    return (
        QuotaWindow(
            "five_hour",
            "5-hour",
            account.five_hour_used_pct,
            account.five_hour_reset_at,
            account.five_hour_rotation_threshold,
        ),
        QuotaWindow(
            "weekly",
            "Weekly",
            account.weekly_used_pct,
            account.weekly_reset_at,
            account.weekly_rotation_threshold,
        ),
        QuotaWindow(
            "monthly",
            "Monthly",
            account.monthly_used_pct,
            account.monthly_reset_at,
            1.0,
        ),
    )


def account_load(account: AccountDb) -> float:
    """Highest known provider-window utilization (0.0 when both are unknown)."""
    known = [window.used_pct for window in quota_windows(account) if window.used_pct is not None]
    return max(known, default=0.0)


def most_used_quota_window(account: AccountDb) -> Optional[QuotaWindow]:
    """Return the most utilized known window for generic status/template aliases."""
    known = [window for window in quota_windows(account) if window.used_pct is not None]
    return max(known, key=lambda window: window.used_pct or 0.0, default=None)


def blocking_quota_window(account: AccountDb) -> Optional[QuotaWindow]:
    """Return the window that keeps the account unavailable for the longest.

    When both windows cross the configured threshold, the later reset is the
    useful deduplication boundary: a five-hour reset cannot restore an account
    while its weekly window remains exhausted.
    """
    blocked = [window for window in quota_windows(account) if window.used_pct is not None and window.used_pct >= window.threshold]
    if not blocked:
        return None

    def reset_timestamp(window: QuotaWindow) -> float:
        if window.reset_at is None:
            return 0.0
        value = window.reset_at
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()

    return max(blocked, key=lambda window: (reset_timestamp(window), window.used_pct or 0.0))


def is_usable(account: AccountDb, now: Optional[datetime] = None) -> bool:
    """True when an account is healthy and contributing capacity to the usable pool."""
    now = now or datetime.now(timezone.utc)

    if account.status == AccountStatus.DISABLED:
        return False
    if account.provider_health != ProviderHealth.HEALTHY:
        return False
    if account.cooldown_until is not None and account.cooldown_until > now:
        return False
    return all(window.used_pct is None or window.used_pct < window.threshold for window in quota_windows(account))


def normalize_expired_cooldown(account: AccountDb, now: Optional[datetime] = None) -> bool:
    """Clear a persisted cooldown once its provider retry window has elapsed."""
    if account.status != AccountStatus.COOLDOWN or account.cooldown_until is None:
        return False
    current = now or datetime.now(timezone.utc)
    cooldown_until = account.cooldown_until
    if cooldown_until.tzinfo is None:
        cooldown_until = cooldown_until.replace(tzinfo=timezone.utc)
    if cooldown_until > current:
        return False
    account.status = AccountStatus.ACTIVE
    account.cooldown_until = None
    account.updated_at = current
    return True


def is_available(account: AccountDb, now: Optional[datetime] = None) -> bool:
    """True if the account may serve traffic right now."""
    now = now or datetime.now(timezone.utc)

    if account.status == AccountStatus.DISABLED:
        return False
    if account.provider_health == ProviderHealth.REAUTH_REQUIRED:
        return False
    if account.cooldown_until is not None and account.cooldown_until > now:
        return False
    if any(window.used_pct is not None and window.used_pct >= window.threshold for window in quota_windows(account)):
        return False

    return True


def account_selection_key(account: AccountDb) -> tuple:
    """Order eligible accounts by priority, then weekly and five-hour resets.

    Reset metadata is only a tie-breaker within a priority level. Unknown reset
    times sort after known values, while creation time and ID keep ties stable.
    """

    def reset_key(reset_at: Optional[datetime]) -> tuple[int, float]:
        if reset_at is None:
            return (1, float("inf"))
        value = reset_at
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return (0, value.timestamp())

    weekly = reset_key(account.weekly_reset_at)
    five_hour = reset_key(account.five_hour_reset_at)
    monthly = reset_key(account.monthly_reset_at)
    return (account.priority, *weekly, *five_hour, *monthly, account.created_at, str(account.id))


def catalog_model_ids(account: AccountDb) -> Optional[Set[str]]:
    """Return the account's last known model ids, or None before its first successful catalog probe."""
    if not account.model_catalog_json:
        return None
    try:
        models = json.loads(account.model_catalog_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(models, list):
        return None
    return {str(model.get("slug") or model.get("id")) for model in models if isinstance(model, dict) and (model.get("slug") or model.get("id"))}


def supports_model(account: AccountDb, model: str) -> Optional[bool]:
    """Return known model support; None means the account has not been catalogued yet."""
    model_ids = catalog_model_ids(account)
    return None if model_ids is None else model in model_ids


def preview_account(
    db: Session,
    user: Optional[UserDb] = None,
    exclude_ids: Optional[Set[uuid.UUID]] = None,
    model: Optional[str] = None,
) -> Optional[AccountDb]:
    """Return the highest-priority available account, optionally constrained by model capability."""
    del user
    exclude_ids = exclude_ids or set()
    candidates = [a for a in db.query(AccountDb).all() if a.id not in exclude_ids and is_available(a)]
    if not candidates:
        return None

    if model:
        # A newly added account has no cached model catalog until it is probed.
        # Keep that account in normal priority order and let the request loop
        # fail over if the upstream rejects the model. Only accounts known not
        # to support the model should be filtered out here.
        candidates = [account for account in candidates if supports_model(account, model) is not False]
        if not candidates:
            return None

    return min(candidates, key=account_selection_key)


def select_account(
    db: Session,
    user: Optional[UserDb] = None,
    exclude_ids: Optional[Set[uuid.UUID]] = None,
    model: Optional[str] = None,
) -> Optional[AccountDb]:
    """Choose the highest-priority eligible account and update its activity timestamp."""
    chosen = preview_account(db, user, exclude_ids, model)
    if chosen is None:
        return None

    if chosen.status == AccountStatus.COOLDOWN:
        chosen.status = AccountStatus.ACTIVE
        chosen.cooldown_until = None

    chosen.last_used_at = datetime.now(timezone.utc)
    return chosen


def ensure_fresh_token(db: Session, account: AccountDb, *, force_refresh: bool = False) -> str:
    """Return a usable access token, serializing refresh-token rotation in the database.

    ``force_refresh`` is used after an upstream 401.  The provider can revoke an
    access token before its JWT expiry, so callers must be able to refresh it
    without writing a synthetic expiry into the account row.  Keeping that
    decision inside the row lock prevents a stale request from clobbering a
    concurrently rotated token/expiry.
    """
    if account.provider_health == ProviderHealth.REAUTH_REQUIRED:
        raise provider_health.reauthentication_error()
    now = datetime.now(timezone.utc)
    leeway = timedelta(seconds=config.TOKEN_REFRESH_LEEWAY_SECONDS)

    expires_at = account.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if not force_refresh and expires_at - leeway > now:
        try:
            return crypto.decrypt(account.access_token_enc)
        except Exception as exc:
            provider_health.persist_failure(db, account.id, exc, context="credential_decrypt")
            raise provider_health.reauthentication_error() from exc

    # Refresh tokens rotate and cannot safely be reused. Lock the account row so
    # concurrent workers do not refresh the same token at the same time.
    locked = db.query(AccountDb).filter(AccountDb.id == account.id).with_for_update().one()
    locked_expiry = locked.expires_at
    if locked_expiry.tzinfo is None:
        locked_expiry = locked_expiry.replace(tzinfo=timezone.utc)
    if not force_refresh and locked_expiry - leeway > now:
        try:
            return crypto.decrypt(locked.access_token_enc)
        except Exception as exc:
            provider_health.persist_failure(db, account.id, exc, context="credential_decrypt")
            raise provider_health.reauthentication_error() from exc

    try:
        refresh_plain = crypto.decrypt(locked.refresh_token_enc)
        tokens = oauth.refresh_access_token(refresh_plain)
    except Exception as exc:
        health = provider_health.persist_failure(
            db,
            account.id,
            exc,
            context="token_refresh",
        )
        if health == ProviderHealth.REAUTH_REQUIRED:
            raise provider_health.reauthentication_error() from exc
        raise

    locked.access_token_enc = crypto.encrypt(tokens["access_token"])
    locked.refresh_token_enc = crypto.encrypt(tokens["refresh_token"])
    locked.expires_at = tokens["expires_at"]
    if tokens.get("account_id"):
        locked.chatgpt_account_id = tokens["account_id"]
    if tokens.get("account_user_id"):
        locked.chatgpt_account_user_id = tokens["account_user_id"]
    if tokens.get("user_id"):
        locked.chatgpt_user_id = tokens["user_id"]
    if tokens.get("email"):
        locked.account_email = tokens["email"]
    if tokens.get("tier"):
        locked.tier = tokens["tier"]
    if tokens.get("workspace_name"):
        locked.workspace_name = tokens["workspace_name"]
    provider_health.mark_success(locked)
    locked.updated_at = now
    db.commit()

    return tokens["access_token"]


def _lower(headers: Mapping[str, str]) -> Dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


def _parse_reset(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        ts = float(value)
    except ValueError:
        return None
    if ts > 1e12:  # milliseconds -> seconds
        ts = ts / 1000.0
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def update_quota_from_headers(account: AccountDb, headers: Mapping[str, str]) -> Optional[str]:
    """Update Codex quota fields from inference response headers."""
    h = _lower(headers)

    # Free ChatGPT accounts expose their monthly allowance through the
    # primary headers; paid/team accounts expose the 5-hour window there.
    primary_used_attr = "monthly_used_pct" if (account.tier or "").strip().lower() == "free" else "five_hour_used_pct"
    primary_reset_attr = "monthly_reset_at" if primary_used_attr == "monthly_used_pct" else "five_hour_reset_at"
    five_hour_util = h.get("x-codex-primary-used-percent")
    if five_hour_util is not None:
        try:
            setattr(account, primary_used_attr, max(0.0, min(1.0, float(five_hour_util) / 100.0)))
        except ValueError:
            pass
    five_hour_reset = _parse_reset(h.get("x-codex-primary-reset-at"))
    if five_hour_reset is not None:
        setattr(account, primary_reset_attr, five_hour_reset)

    weekly_util = h.get("x-codex-secondary-used-percent")
    if weekly_util is not None:
        try:
            account.weekly_used_pct = max(0.0, min(1.0, float(weekly_util) / 100.0))
        except ValueError:
            pass
    weekly_reset = _parse_reset(h.get("x-codex-secondary-reset-at"))
    if weekly_reset is not None:
        account.weekly_reset_at = weekly_reset

    reset_credits = h.get("x-codex-rate-limit-reset-credits-available")
    if reset_credits is not None:
        try:
            account.reset_credits_available = max(0, int(reset_credits))
        except ValueError:
            pass

    account.quota_refreshed_at = datetime.now(timezone.utc)
    return h.get("x-codex-rate-limit-reached-type")


def apply_usage_probe(account: AccountDb, usage: Mapping[str, object]) -> bool:
    """Update quota fields from a zero-spend usage probe (see oauth.fetch_usage)."""
    for key, used_attr, reset_attr in (
        ("five_hour", "five_hour_used_pct", "five_hour_reset_at"),
        ("weekly", "weekly_used_pct", "weekly_reset_at"),
        ("monthly", "monthly_used_pct", "monthly_reset_at"),
    ):
        window = usage.get(key)
        if key in usage and window is None:
            setattr(account, used_attr, None)
            setattr(account, reset_attr, None)
            continue
        if not isinstance(window, Mapping):
            continue
        if window.get("utilization") is not None:
            setattr(account, used_attr, window["utilization"])
        if key == "five_hour" and window.get("is_cold") is True:
            # The usage endpoint emits a rolling now+5h placeholder before
            # the first request starts the provider window. Do not persist it
            # as a real reset: the warmer relies on a missing reset to find
            # cold accounts, and the dashboard must not show a fake timer.
            setattr(account, reset_attr, None)
        elif window.get("reset_at") is not None:
            setattr(account, reset_attr, window["reset_at"])

    if usage.get("tier"):
        account.tier = str(usage["tier"])
    if usage.get("reset_credits_available") is not None:
        account.reset_credits_available = int(usage["reset_credits_available"])
    account.quota_refreshed_at = datetime.now(timezone.utc)
    return bool(usage.get("limit_reached", False))


def _reset_credit_expiry(raw: object) -> Optional[datetime]:
    if isinstance(raw, datetime):
        parsed = raw
    elif isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def auto_redeem_weekly_reset(db: Session, account: AccountDb, access_token: str) -> bool:
    """Redeem the earliest-expiring available credit once a weekly window is full."""
    if account.weekly_used_pct is None or account.weekly_used_pct < 1.0 or not account.chatgpt_account_id:
        return False

    # Serialize redemptions per account. The deterministic request id also makes
    # a retry safe if the provider completed a request before the connection died.
    # Persist quota changes learned from the response/probe before refreshing
    # this identity-map entry under a row lock.
    db.flush()

    # ``account`` is commonly already present in this session's identity map.
    # Refresh it while acquiring the lock so a worker that waited for another
    # redemption observes the newly reset weekly window instead of spending a
    # second credit from stale ORM state.
    locked = db.query(AccountDb).filter(AccountDb.id == account.id).populate_existing().with_for_update().one()
    if locked.weekly_used_pct is None or locked.weekly_used_pct < 1.0 or not locked.chatgpt_account_id:
        return False

    # Another worker may have redeemed this window after our upstream request
    # was sent but before we acquired the lock. Verify the provider's current
    # window while holding the lock so a stale 100% response cannot spend the
    # next credit too.
    apply_usage_probe(locked, oauth.fetch_usage(access_token, locked.chatgpt_account_id))
    if locked.weekly_used_pct is None or locked.weekly_used_pct < 1.0:
        db.commit()
        return False

    now = datetime.now(timezone.utc)
    reset_data = oauth.list_reset_credits(access_token, locked.chatgpt_account_id)
    locked.reset_credits_available = int(reset_data.get("available_count") or 0)
    candidates = []
    for credit in reset_data.get("credits") or []:
        if not isinstance(credit, Mapping) or credit.get("status") != "available":
            continue
        if credit.get("is_supported_by_plan") is False or not isinstance(credit.get("id"), str):
            continue
        expires_at = _reset_credit_expiry(credit.get("expires_at"))
        if expires_at is not None and expires_at <= now:
            continue
        candidates.append((expires_at is None, expires_at or datetime.max.replace(tzinfo=timezone.utc), credit["id"]))
    if not candidates:
        db.commit()
        return False

    _, _, credit_id = min(candidates)
    redeem_request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"codex-proxy:auto-weekly-reset:{locked.id}:{credit_id}"))
    result = oauth.consume_reset_credit(
        access_token,
        locked.chatgpt_account_id,
        credit_id=credit_id,
        idempotency_key=redeem_request_id,
    )
    refreshed = oauth.fetch_usage(access_token, locked.chatgpt_account_id)
    apply_usage_probe(locked, refreshed)
    locked.updated_at = datetime.now(timezone.utc)
    db.commit()
    return result["code"] in {"reset", "already_redeemed"}


def weekly_reset_recovery_candidates(db: Session) -> list[AccountDb]:
    """Return exhausted accounts whose cached credit count permits recovery.

    This is intentionally a cheap database-only preflight. The quota refresher
    performs authoritative provider checks for exhausted accounts even when the
    cached count is zero; request routing only contacts the provider when its
    latest known state says a credit is available.
    """
    candidates = [
        account
        for account in db.query(AccountDb).all()
        if account.status != AccountStatus.DISABLED
        and account.provider_health != ProviderHealth.REAUTH_REQUIRED
        and account.chatgpt_account_id
        and account.weekly_used_pct is not None
        and account.weekly_used_pct >= 1.0
        and (account.reset_credits_available or 0) > 0
    ]
    return sorted(candidates, key=account_selection_key)


def parse_retry_after(headers: Mapping[str, str], default_seconds: int) -> int:
    """Seconds to rest an account after a 429, clamped to a sane range.

    `default_seconds` is the account's own `cooldown_seconds`, used when the upstream sends no usable retry-after.
    """
    fallback = default_seconds
    h = _lower(headers)
    raw = h.get("retry-after")

    seconds = fallback
    if raw:
        try:
            seconds = int(float(raw))
        except ValueError:
            seconds = fallback

    return max(1, min(seconds, 300))


def mark_cooldown(db: Session, account: AccountDb, retry_after_seconds: int) -> None:
    """Park an account on cooldown for `retry_after_seconds`."""
    now = datetime.now(timezone.utc)
    account.status = AccountStatus.COOLDOWN
    account.cooldown_until = now + timedelta(seconds=retry_after_seconds)
    account.updated_at = now
    db.commit()
