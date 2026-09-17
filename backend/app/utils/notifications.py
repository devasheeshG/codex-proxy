"""Channel-neutral notification rules, templating, deduplication, and Telegram delivery."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping, MutableMapping, Optional

import httpx
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.logger import get_logger
from app.utils import crypto, rotation
from app.utils.models.api import AccountStatus, ProviderHealth
from app.utils.postgres import (
    AccountDb,
    NotificationChannelDb,
    NotificationDeliveryDb,
    NotificationRuleDb,
    ProxyEventDb,
    UsageRecordDb,
    UserDb,
)

logger = get_logger()

TELEGRAM_CHANNEL_TYPE = "telegram"
TELEGRAM_API_BASE = "https://api.telegram.org"
_VARIABLE = re.compile(r"{{\s*([a-z][a-z0-9_]*)\s*}}")
_MAX_ATTEMPTS = 5
DEFAULT_NOTIFICATION_TIMEZONE = "Asia/Kolkata"
TELEGRAM_STATUS_COMMAND = "codex"
_STATUS_COMMAND = re.compile(
    r"^\s*/codex(?:@[a-z0-9_]+)?\s+status(?:\s+(all|usable|available))?\s*$",
    re.IGNORECASE,
)
_TELEGRAM_MESSAGE_LIMIT = 4096
ELEVATED_503_WINDOW_SECONDS = 60


@dataclass(frozen=True)
class EventDefinition:
    title: str
    description: str
    default_template: str
    default_enabled: bool
    cooldown_seconds: int
    variables: tuple[str, ...]


COMMON_VARIABLES = ("event_time", "dashboard_url")
ACCOUNT_VARIABLES = COMMON_VARIABLES + (
    "account_label",
    "account_email",
    "account_tier",
    "usage_percent",
    "limiting_window",
    "five_hour_usage_percent",
    "five_hour_reset_at",
    "five_hour_rotation_threshold",
    "weekly_usage_percent",
    "weekly_reset_at",
    "weekly_rotation_threshold",
    "monthly_usage_percent",
    "monthly_reset_at",
    "rotation_threshold",
    "reset_at",
    "usable_accounts",
    "total_accounts",
)
AUTHENTICATION_VARIABLES = ACCOUNT_VARIABLES + (
    "authentication_code",
    "authentication_reason",
)
REPORT_VARIABLES = COMMON_VARIABLES + (
    "period_start",
    "period_end",
    "generated_at",
    "total_requests",
    "total_tokens",
    "user_count",
    "user_distribution",
)

_ACCOUNT_ADDED_TEMPLATE_V1 = (
    "✅ Codex account added\n\n"
    "Account: {{ account_label }}\n"
    "Plan: {{ account_tier }}\n"
    "Current usage: {{ usage_percent }}%\n"
    "Pool: {{ usable_accounts }} of {{ total_accounts }} accounts usable\n"
    "Added: {{ event_time }}"
)
_ACCOUNT_ADDED_TEMPLATE_V2 = (
    "✅ Codex account added\n\n"
    "Account: {{ account_label }}\n"
    "Email: {{ account_email }}\n"
    "Plan: {{ account_tier }}\n"
    "Current usage: {{ usage_percent }}%\n"
    "Pool: {{ usable_accounts }} of {{ total_accounts }} accounts usable\n"
    "Added: {{ event_time }}"
)
_ACCOUNT_ADDED_TEMPLATE = (
    "✅ Codex account added\n\n"
    "Account: {{ account_label }}\n"
    "Email: {{ account_email }}\n"
    "Plan: {{ account_tier }}\n"
    "5-hour usage: {{ five_hour_usage_percent }}\n"
    "Weekly usage: {{ weekly_usage_percent }}\n"
    "Pool: {{ usable_accounts }} of {{ total_accounts }} accounts usable\n"
    "Added: {{ event_time }}"
)
_ACCOUNT_QUOTA_THRESHOLD_TEMPLATE_V1 = (
    "⚠️ Codex account reached its usage threshold\n\n"
    "Account: {{ account_label }}\n"
    "Account email: {{ account_email }}\n"
    "Usage: {{ usage_percent }}%\n"
    "Threshold: {{ rotation_threshold }}%\n"
    "Resets: {{ reset_at }}\n"
    "Pool capacity: {{ usable_accounts }} accounts"
)
_ACCOUNT_QUOTA_THRESHOLD_TEMPLATE_V2 = (
    "⚠️ Codex account reached its usage threshold\n\n"
    "Account: {{ account_label }}\n"
    "Email: {{ account_email }}\n"
    "Limiting window: {{ limiting_window }}\n"
    "5-hour usage: {{ five_hour_usage_percent }} (resets {{ five_hour_reset_at }})\n"
    "Weekly usage: {{ weekly_usage_percent }} (resets {{ weekly_reset_at }})\n"
    "Threshold: {{ rotation_threshold }}%\n"
    "Pool capacity: {{ usable_accounts }} accounts"
)
_ACCOUNT_QUOTA_THRESHOLD_TEMPLATE = (
    "⚠️ Codex account reached its usage threshold\n\n"
    "Account: {{ account_label }}\n"
    "Email: {{ account_email }}\n"
    "Limiting window: {{ limiting_window }}\n"
    "5-hour usage: {{ five_hour_usage_percent }} (resets {{ five_hour_reset_at }})\n"
    "Weekly usage: {{ weekly_usage_percent }} (resets {{ weekly_reset_at }})\n"
    "5-hour threshold: {{ five_hour_rotation_threshold }}%\n"
    "Weekly threshold: {{ weekly_rotation_threshold }}%\n"
    "Pool capacity: {{ usable_accounts }} accounts"
)
_ACCOUNT_HARD_LIMIT_TEMPLATE_V1 = (
    "🚨 Codex account limit exhausted\n\n"
    "Account: {{ account_label }}\n"
    "Usage: {{ usage_percent }}%\n"
    "Resets: {{ reset_at }}\n"
    "Traffic is moving to the next available account."
)
_ACCOUNT_HARD_LIMIT_TEMPLATE_V2 = (
    "🚨 Codex account limit exhausted\n\n"
    "Account: {{ account_label }}\n"
    "Email: {{ account_email }}\n"
    "Limiting window: {{ limiting_window }}\n"
    "5-hour usage: {{ five_hour_usage_percent }} (resets {{ five_hour_reset_at }})\n"
    "Weekly usage: {{ weekly_usage_percent }} (resets {{ weekly_reset_at }})\n"
    "Traffic is moving to the next available account."
)
_ACCOUNT_HARD_LIMIT_TEMPLATE = (
    "🚨 Codex account limit exhausted\n\n"
    "Account: {{ account_label }}\n"
    "Email: {{ account_email }}\n"
    "Limiting window: {{ limiting_window }}\n"
    "5-hour usage: {{ five_hour_usage_percent }} (resets {{ five_hour_reset_at }})\n"
    "Weekly usage: {{ weekly_usage_percent }} (resets {{ weekly_reset_at }})\n"
    "5-hour threshold: {{ five_hour_rotation_threshold }}%\n"
    "Weekly threshold: {{ weekly_rotation_threshold }}%\n"
    "Traffic is moving to the next available account."
)
_ELEVATED_503_TEMPLATE_V1 = (
    "🚨 Elevated 503 errors detected\n\n"
    "Multiple users are experiencing unavailable proxy capacity.\n"
    "503 responses: {{ error_count }}\n"
    "Affected users: {{ affected_user_count }}\n"
    "Window: {{ window_seconds }} seconds\n"
    "Users: {{ affected_users }}\n"
    "Detected: {{ event_time }}"
)
LEGACY_DEFAULT_TEMPLATES: dict[str, tuple[str, ...]] = {
    "account_added": (_ACCOUNT_ADDED_TEMPLATE_V1, _ACCOUNT_ADDED_TEMPLATE_V2),
    "account_quota_threshold": (_ACCOUNT_QUOTA_THRESHOLD_TEMPLATE_V1, _ACCOUNT_QUOTA_THRESHOLD_TEMPLATE_V2),
    "account_hard_limit": (_ACCOUNT_HARD_LIMIT_TEMPLATE_V1, _ACCOUNT_HARD_LIMIT_TEMPLATE_V2),
    "elevated_503": (_ELEVATED_503_TEMPLATE_V1,),
}

EVENTS: dict[str, EventDefinition] = {
    "notifier_installed": EventDefinition(
        title="Telegram notifier connected",
        description=(
            "Sent once when Telegram has a saved bot token and destination and notifications are enabled for the first time. "
            "Later settings edits or disable/re-enable cycles do not resend it."
        ),
        default_template=("✅ Codex Proxy notifier installed\n\nDestination: {{ destination }}\nTopic: {{ thread_id }}\nInstalled: {{ event_time }}"),
        default_enabled=True,
        cooldown_seconds=0,
        variables=COMMON_VARIABLES + ("destination", "thread_id"),
    ),
    "account_added": EventDefinition(
        title="Pooled Codex account added",
        description=(
            "Sent after a new Codex subscription account is persisted and its initial quota probe finishes. "
            "Reauthentication, renaming, and policy edits do not count as new accounts."
        ),
        default_template=_ACCOUNT_ADDED_TEMPLATE,
        default_enabled=True,
        cooldown_seconds=0,
        variables=ACCOUNT_VARIABLES,
    ),
    "account_authentication_expired": EventDefinition(
        title="Account authentication expired",
        description=(
            "Sent once when a Codex account's stored OAuth credentials are rejected or become unreadable and the account "
            "enters reauthentication-required state. Normal access-token refreshes do not trigger it. A successful "
            "reauthentication starts a new notification cycle."
        ),
        default_template=(
            "🔐 Codex account authentication expired\n\n"
            "Account: {{ account_label }}\n"
            "Email: {{ account_email }}\n"
            "Plan: {{ account_tier }}\n"
            "Reason: {{ authentication_reason }}\n"
            "Detected: {{ event_time }}\n\n"
            "Re-authenticate this account in the dashboard: {{ dashboard_url }}"
        ),
        default_enabled=True,
        cooldown_seconds=0,
        variables=AUTHENTICATION_VARIABLES,
    ),
    "account_quota_threshold": EventDefinition(
        title="Account rotation threshold reached",
        description=(
            "Sent once per limiting five-hour or weekly quota window when that window reaches its own configured "
            "rotation threshold. The proxy stops selecting that account even when failover succeeds."
        ),
        default_template=_ACCOUNT_QUOTA_THRESHOLD_TEMPLATE,
        default_enabled=True,
        cooldown_seconds=0,
        variables=ACCOUNT_VARIABLES,
    ),
    "account_hard_limit": EventDefinition(
        title="Provider hard usage limit reached",
        description=(
            "Sent once per limiting five-hour or weekly quota window when Codex explicitly reports exhausted usage, "
            "credits, or a workspace limit. The account is cooled down and traffic fails over."
        ),
        default_template=_ACCOUNT_HARD_LIMIT_TEMPLATE,
        default_enabled=True,
        cooldown_seconds=0,
        variables=ACCOUNT_VARIABLES,
    ),
    "pool_unavailable": EventDefinition(
        title="Entire account pool unavailable",
        description=(
            "Sent when an incoming request exhausts or skips every account and the proxy is about to return 503. "
            "It repeats only after the configured cooldown and only when another request arrives."
        ),
        default_template=(
            "🛑 Codex account pool unavailable\n\n"
            "No pooled account can currently serve requests.\n"
            "Total accounts: {{ total_accounts }}\n"
            "Usable accounts: {{ usable_accounts }}\n"
            "Next known reset: {{ next_reset_at }}\n"
            "Detected: {{ event_time }}"
        ),
        default_enabled=True,
        cooldown_seconds=900,
        variables=COMMON_VARIABLES + ("usable_accounts", "total_accounts", "next_reset_at"),
    ),
    "elevated_503": EventDefinition(
        title="503 capacity exhaustion",
        description=(
            "Sent whenever an exhausted request returns HTTP 503, including a single affected user. "
            "The configurable cooldown prevents duplicate Telegram alerts while failures continue."
        ),
        default_template=(
            "🚨 Proxy request exhausted (HTTP 503)\n\n"
            "No subscription account or fallback could serve a request.\n"
            "503 responses in the last {{ window_seconds }} seconds: {{ error_count }}\n"
            "Affected users: {{ affected_user_count }}\n"
            "Window: {{ window_seconds }} seconds\n"
            "Users: {{ affected_users }}\n"
            "Detected: {{ event_time }}"
        ),
        default_enabled=False,
        cooldown_seconds=900,
        variables=COMMON_VARIABLES + ("error_count", "affected_user_count", "window_seconds", "affected_users"),
    ),
    "user_rate_limit": EventDefinition(
        title="User request rate limit exceeded",
        description=(
            "Sent when a user's combined traffic across all API keys reaches their requests-per-minute limit and the "
            "current request is rejected with 429. Repeats are controlled by the event cooldown."
        ),
        default_template=("⚠️ Proxy user rate limit reached\n\nUser: {{ user_name }}\nLimit: {{ limit }} requests/minute\nDetected: {{ event_time }}"),
        default_enabled=False,
        cooldown_seconds=900,
        variables=COMMON_VARIABLES + ("user_name", "limit"),
    ),
    "user_monthly_budget": EventDefinition(
        title="User monthly token budget exhausted",
        description=(
            "Sent when a user's combined month-to-date token usage reaches their configured budget and the next request "
            "is rejected with 403. Repeats are controlled by the event cooldown."
        ),
        default_template=("🚫 Proxy user monthly budget exhausted\n\nUser: {{ user_name }}\nBudget: {{ limit }} tokens\nDetected: {{ event_time }}"),
        default_enabled=False,
        cooldown_seconds=3600,
        variables=COMMON_VARIABLES + ("user_name", "limit"),
    ),
    "api_key_rate_limit": EventDefinition(
        title="API key request rate limit exceeded",
        description=(
            "Sent when one API key reaches its own requests-per-minute limit and the current request is rejected with 429. "
            "The owning user's other keys are unaffected."
        ),
        default_template=(
            "⚠️ API key rate limit reached\n\n"
            "User: {{ user_name }}\nKey: {{ key_label }}\n"
            "Limit: {{ limit }} requests/minute\nDetected: {{ event_time }}"
        ),
        default_enabled=False,
        cooldown_seconds=900,
        variables=COMMON_VARIABLES + ("user_name", "key_label", "key_prefix", "limit"),
    ),
    "api_key_monthly_budget": EventDefinition(
        title="API key monthly token budget exhausted",
        description=(
            "Sent when one API key reaches its month-to-date token budget and the next request is rejected with 403. "
            "The owning user's other keys remain available."
        ),
        default_template=(
            "🚫 API key monthly budget exhausted\n\n"
            "User: {{ user_name }}\nKey: {{ key_label }}\n"
            "Budget: {{ limit }} tokens\nDetected: {{ event_time }}"
        ),
        default_enabled=False,
        cooldown_seconds=3600,
        variables=COMMON_VARIABLES + ("user_name", "key_label", "key_prefix", "limit"),
    ),
    "daily_usage_report": EventDefinition(
        title="Daily usage report",
        description=(
            "Sent once after each completed local calendar day. It summarizes total requests and tokens and lists every "
            "active user who made a request during that day, using the destination timezone."
        ),
        default_template=(
            "📊 Daily usage report\n\nPeriod: {{ period_start }} → {{ period_end }}\n"
            "Generated: {{ generated_at }}\n\nTotal requests: {{ total_requests }}\n"
            "Total tokens: {{ total_tokens }}\nActive users: {{ user_count }}\n\n"
            "User distribution:\n{{ user_distribution }}"
        ),
        default_enabled=True,
        cooldown_seconds=0,
        variables=REPORT_VARIABLES,
    ),
    "weekly_usage_report": EventDefinition(
        title="Weekly usage report",
        description=(
            "Sent once after each completed Monday–Sunday reporting week. It summarizes total requests and tokens and "
            "lists every active user who made a request during that week, using the destination timezone."
        ),
        default_template=(
            "📊 Weekly usage report\n\nPeriod: {{ period_start }} → {{ period_end }}\n"
            "Generated: {{ generated_at }}\n\nTotal requests: {{ total_requests }}\n"
            "Total tokens: {{ total_tokens }}\nActive users: {{ user_count }}\n\n"
            "User distribution:\n{{ user_distribution }}"
        ),
        default_enabled=True,
        cooldown_seconds=0,
        variables=REPORT_VARIABLES,
    ),
    "monthly_usage_report": EventDefinition(
        title="Monthly usage report",
        description=(
            "Sent once after each completed calendar month. It summarizes total requests and tokens and lists every active "
            "user who made a request during that month, using the destination timezone."
        ),
        default_template=(
            "📊 Monthly usage report\n\nPeriod: {{ period_start }} → {{ period_end }}\n"
            "Generated: {{ generated_at }}\n\nTotal requests: {{ total_requests }}\n"
            "Total tokens: {{ total_tokens }}\nActive users: {{ user_count }}\n\n"
            "User distribution:\n{{ user_distribution }}"
        ),
        default_enabled=True,
        cooldown_seconds=0,
        variables=REPORT_VARIABLES,
    ),
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def validate_timezone(value: str) -> str:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Timezone must be a valid IANA timezone, for example Asia/Kolkata") from exc
    return value


def validate_template(event_type: str, template: str) -> None:
    definition = EVENTS.get(event_type)
    if definition is None:
        raise ValueError("Unknown notification event")
    unknown = sorted(set(_VARIABLE.findall(template)) - set(definition.variables))
    if unknown:
        raise ValueError(f"Unknown template variable(s): {', '.join(unknown)}")


def render_template(event_type: str, template: str, context: Mapping[str, object]) -> str:
    validate_template(event_type, template)

    def replace(match: re.Match[str]) -> str:
        value = context.get(match.group(1))
        return "—" if value is None or value == "" else str(value)

    return _VARIABLE.sub(replace, template)


def ensure_default_rules(db: Session, channel: NotificationChannelDb) -> list[NotificationRuleDb]:
    existing = {rule.event_type: rule for rule in db.query(NotificationRuleDb).filter(NotificationRuleDb.channel_id == channel.id).all()}
    now = utcnow()
    for event_type, definition in EVENTS.items():
        if event_type not in existing:
            rule = NotificationRuleDb(
                id=uuid.uuid4(),
                channel_id=channel.id,
                event_type=event_type,
                enabled=definition.default_enabled,
                template=definition.default_template,
                cooldown_seconds=definition.cooldown_seconds,
                created_at=now,
                updated_at=now,
            )
            db.add(rule)
            existing[event_type] = rule
        elif existing[event_type].template in LEGACY_DEFAULT_TEMPLATES.get(event_type, ()):
            existing[event_type].template = definition.default_template
            existing[event_type].updated_at = now
    db.flush()
    return [existing[event_type] for event_type in EVENTS]


def _reset_key(account: AccountDb) -> str:
    """Return a stable limiting-window key despite small provider timestamp drift."""
    window = rotation.blocking_quota_window(account) or rotation.most_used_quota_window(account)
    if window is not None and window.reset_at is not None:
        reset = window.reset_at
        if reset.tzinfo is None:
            reset = reset.replace(tzinfo=timezone.utc)
        # Five-minute buckets absorb the few-second reset timestamp drift seen
        # between probes while keeping distinct five-hour windows separate.
        reset_bucket = round(reset.astimezone(timezone.utc).timestamp() / 300)
        return f"{window.key}:{reset_bucket}"
    fallback_key = window.key if window is not None else "unknown"
    return f"{fallback_key}:{int(utcnow().timestamp() // 18_000)}"


def _format_time(value: Optional[datetime]) -> str:
    if value is None:
        return "Unknown"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _format_usage_percent(value: Optional[float]) -> str:
    return "Unknown" if value is None else f"{max(0.0, min(1.0, value)) * 100:.1f}%"


def _pool_counts(db: Session) -> tuple[int, int]:
    accounts = db.query(AccountDb).all()
    return len(accounts), sum(rotation.is_usable(account) for account in accounts)


def account_context(db: Session, account: AccountDb, dashboard_url: str = "") -> dict[str, object]:
    total, usable = _pool_counts(db)
    effective_window = rotation.blocking_quota_window(account) or rotation.most_used_quota_window(account)
    return {
        "event_time": _format_time(utcnow()),
        "dashboard_url": dashboard_url,
        "account_label": account.label,
        "account_email": account.account_email,
        "account_tier": account.tier,
        "authentication_code": account.provider_health_code,
        "authentication_reason": account.provider_health_message,
        # usage_percent/reset_at remain as aliases so customized templates from
        # earlier releases continue to render. New templates should use the
        # explicit five-hour and weekly variables below.
        "usage_percent": round(((effective_window.used_pct if effective_window else None) or 0) * 100, 1),
        "limiting_window": effective_window.label if effective_window else "Unknown",
        "five_hour_usage_percent": _format_usage_percent(account.five_hour_used_pct),
        "five_hour_reset_at": _format_time(account.five_hour_reset_at),
        "weekly_usage_percent": _format_usage_percent(account.weekly_used_pct),
        "weekly_reset_at": _format_time(account.weekly_reset_at),
        "monthly_usage_percent": _format_usage_percent(account.monthly_used_pct),
        "monthly_reset_at": _format_time(account.monthly_reset_at),
        "five_hour_rotation_threshold": round(account.five_hour_rotation_threshold * 100, 1),
        "weekly_rotation_threshold": round(account.weekly_rotation_threshold * 100, 1),
        "rotation_threshold": round(effective_window.threshold * 100, 1) if effective_window else round(account.rotation_threshold * 100, 1),
        "reset_at": _format_time(effective_window.reset_at if effective_window else None),
        "usable_accounts": usable,
        "total_accounts": total,
    }


def enqueue_event(
    db: Session,
    event_type: str,
    context: Mapping[str, object],
    dedupe_key: str,
    channel_id=None,
) -> int:
    """Render and persist one delivery per enabled channel/rule without making network calls."""
    if event_type not in EVENTS:
        raise ValueError("Unknown notification event")
    for channel in db.query(NotificationChannelDb).all():
        ensure_default_rules(db, channel)
    now = utcnow()
    filters = [
        NotificationRuleDb.event_type == event_type,
        NotificationRuleDb.enabled.is_(True),
        NotificationChannelDb.enabled.is_(True),
    ]
    if channel_id is not None:
        filters.append(NotificationChannelDb.id == channel_id)
    rules = (
        db.query(NotificationRuleDb, NotificationChannelDb)
        .join(NotificationChannelDb, NotificationChannelDb.id == NotificationRuleDb.channel_id)
        .filter(*filters)
        .all()
    )
    queued = 0
    for rule, channel in rules:
        event_key = dedupe_key
        if rule.cooldown_seconds > 0:
            recent = (
                db.query(NotificationDeliveryDb)
                .filter(
                    NotificationDeliveryDb.channel_id == channel.id,
                    NotificationDeliveryDb.event_type == event_type,
                    NotificationDeliveryDb.event_key.like(f"{dedupe_key}:%"),
                    NotificationDeliveryDb.created_at >= now - timedelta(seconds=rule.cooldown_seconds),
                )
                .first()
            )
            if recent is not None:
                continue
            bucket = int(now.timestamp() // rule.cooldown_seconds)
            event_key = f"{dedupe_key}:{bucket}"
        try:
            with db.begin_nested():
                delivery = NotificationDeliveryDb(
                    id=uuid.uuid4(),
                    channel_id=channel.id,
                    event_type=event_type,
                    event_key=event_key,
                    message=render_template(event_type, rule.template, context),
                    status="pending",
                    attempts=0,
                    available_at=now,
                    created_at=now,
                )
                db.add(delivery)
                db.flush()
            queued += 1
        except IntegrityError:
            logger.debug("Notification event already queued: %s/%s", event_type, event_key)
    return queued


def enqueue_account_added(db: Session, account: AccountDb, dashboard_url: str = "") -> int:
    return enqueue_event(db, "account_added", account_context(db, account, dashboard_url), str(account.id))


def enqueue_account_authentication_expired(db: Session, account: AccountDb, dashboard_url: str = "") -> int:
    """Queue one alert for the current authentication cycle.

    Existing channels may predate this event, so materialize their new default
    rule before querying enabled rules. The last successful provider check is a
    stable cycle boundary and changes only after the account works again.
    """
    cycle_started_at = account.provider_health_last_success_at or account.created_at
    if cycle_started_at.tzinfo is None:
        cycle_started_at = cycle_started_at.replace(tzinfo=timezone.utc)
    cycle_key = cycle_started_at.astimezone(timezone.utc).isoformat()
    return enqueue_event(
        db,
        "account_authentication_expired",
        account_context(db, account, dashboard_url),
        f"{account.id}:{cycle_key}",
    )


def enqueue_notifier_installed(db: Session, channel: NotificationChannelDb, dashboard_url: str = "") -> int:
    return enqueue_event(
        db,
        "notifier_installed",
        {
            "event_time": _format_time(utcnow()),
            "dashboard_url": dashboard_url,
            "channel_name": channel.name,
            "destination": channel.destination,
            "thread_id": channel.thread_id or "None",
        },
        str(channel.id),
    )


def enqueue_account_threshold(db: Session, account: AccountDb, dashboard_url: str = "") -> int:
    if rotation.blocking_quota_window(account) is None:
        return 0
    key = f"{account.id}:{_reset_key(account)}"
    return enqueue_event(db, "account_quota_threshold", account_context(db, account, dashboard_url), key)


def enqueue_account_hard_limit(db: Session, account: AccountDb, dashboard_url: str = "") -> int:
    key = f"{account.id}:{_reset_key(account)}"
    return enqueue_event(db, "account_hard_limit", account_context(db, account, dashboard_url), key)


def enqueue_pool_unavailable(db: Session, dashboard_url: str = "") -> int:
    accounts = db.query(AccountDb).all()
    resets = [window.reset_at for account in accounts for window in rotation.quota_windows(account) if window.reset_at is not None]
    total, usable = _pool_counts(db)
    context = {
        "event_time": _format_time(utcnow()),
        "dashboard_url": dashboard_url,
        "usable_accounts": usable,
        "total_accounts": total,
        "next_reset_at": _format_time(min(resets) if resets else None),
    }
    return enqueue_event(db, "pool_unavailable", context, "pool")


def enqueue_elevated_503(db: Session, dashboard_url: str = "") -> int:
    """Queue an alert for every exhausted 503, subject to each rule's cooldown."""
    now = utcnow()
    recent = (
        db.query(ProxyEventDb)
        .filter(
            ProxyEventDb.event_type == "request.exhausted",
            ProxyEventDb.status_code == 503,
            ProxyEventDb.created_at >= now - timedelta(seconds=ELEVATED_503_WINDOW_SECONDS),
        )
        .all()
    )
    if not recent:
        return 0
    user_ids = {event.user_id for event in recent if event.user_id is not None}
    names = {user.name for user in db.query(UserDb).filter(UserDb.id.in_(user_ids)).all() if user.name}
    context = {
        "event_time": _format_time(now),
        "dashboard_url": dashboard_url,
        "error_count": len(recent),
        "affected_user_count": len(user_ids),
        "window_seconds": ELEVATED_503_WINDOW_SECONDS,
        "affected_users": ", ".join(sorted(names)) or "Unknown users",
    }
    # Keep the dedupe key stable so the configured cooldown spans rolling
    # minute boundaries instead of resetting at every new minute bucket.
    return enqueue_event(db, "elevated_503", context, "503")


def enqueue_client_limit(
    db: Session,
    event_type: str,
    *,
    user_name: str,
    limit: int,
    dedupe_key: str,
    key_label: Optional[str] = None,
    key_prefix: Optional[str] = None,
    dashboard_url: str = "",
) -> int:
    context = {
        "event_time": _format_time(utcnow()),
        "dashboard_url": dashboard_url,
        "user_name": user_name,
        "key_label": key_label or "Unlabelled key",
        "key_prefix": key_prefix,
        "limit": limit,
    }
    return enqueue_event(db, event_type, context, dedupe_key)


def _report_period(event_type: str, local_now: datetime) -> tuple[datetime, datetime, str]:
    """Return the most recently completed local calendar period and its stable key."""
    today = local_now.date()
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    if event_type == "daily_usage_report":
        start = midnight - timedelta(days=1)
        return start, midnight, f"daily:{start.date().isoformat()}"
    if event_type == "weekly_usage_report":
        current_week_start = midnight - timedelta(days=today.weekday())
        start = current_week_start - timedelta(days=7)
        end = current_week_start
        return start, end, f"weekly:{start.date().isoformat()}"
    if event_type == "monthly_usage_report":
        current_month_start = midnight.replace(day=1)
        start = (current_month_start - timedelta(days=1)).replace(day=1)
        return start, current_month_start, f"monthly:{start.strftime('%Y-%m')}"
    raise ValueError(f"Unsupported report event: {event_type}")


def _report_context(db: Session, start: datetime, end: datetime, local_now: datetime) -> dict[str, object]:
    rows = (
        db.query(
            UserDb.name.label("user_name"),
            func.count(UsageRecordDb.id).label("requests"),
            func.coalesce(func.sum(UsageRecordDb.input_tokens + UsageRecordDb.output_tokens), 0).label("tokens"),
        )
        .join(UserDb, UsageRecordDb.user_id == UserDb.id)
        .filter(UsageRecordDb.created_at >= start.astimezone(timezone.utc), UsageRecordDb.created_at < end.astimezone(timezone.utc))
        .group_by(UserDb.id, UserDb.name)
        .order_by(func.sum(UsageRecordDb.input_tokens + UsageRecordDb.output_tokens).desc(), UserDb.name.asc())
        .all()
    )
    total_requests = sum(int(row.requests or 0) for row in rows)
    total_tokens = sum(int(row.tokens or 0) for row in rows)
    lines: list[str] = []
    overflow_requests = 0
    overflow_tokens = 0
    for row in rows:
        name = (row.user_name or "Unnamed user").strip() or "Unnamed user"
        requests = int(row.requests or 0)
        tokens = int(row.tokens or 0)
        request_share = (requests / total_requests * 100) if total_requests else 0
        token_share = (tokens / total_tokens * 100) if total_tokens else 0
        line = f"- {name}: {requests:,} requests ({request_share:.1f}%), {tokens:,} tokens ({token_share:.1f}%)"
        if sum(len(item) + 1 for item in lines) + len(line) > 3400:
            overflow_requests += requests
            overflow_tokens += tokens
        else:
            lines.append(line)
    if overflow_requests or overflow_tokens:
        request_share = (overflow_requests / total_requests * 100) if total_requests else 0
        token_share = (overflow_tokens / total_tokens * 100) if total_tokens else 0
        lines.append(f"- Other active users: {overflow_requests:,} requests ({request_share:.1f}%), {overflow_tokens:,} tokens ({token_share:.1f}%)")
    return {
        "period_start": start.strftime("%Y-%m-%d %H:%M %Z"),
        "period_end": end.strftime("%Y-%m-%d %H:%M %Z"),
        "generated_at": local_now.strftime("%Y-%m-%d %H:%M %Z"),
        "total_requests": total_requests,
        "total_tokens": total_tokens,
        "user_count": len(rows),
        "user_distribution": "\n".join(lines) if lines else "No active users in this period.",
        "event_time": _format_time(utcnow()),
        "dashboard_url": "",
    }


def enqueue_scheduled_reports(db: Session, now: Optional[datetime] = None) -> int:
    """Queue each completed report once per enabled Telegram destination."""
    from zoneinfo import ZoneInfo

    current = now or utcnow()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    queued = 0
    channels = (
        db.query(NotificationChannelDb)
        .filter(NotificationChannelDb.channel_type == TELEGRAM_CHANNEL_TYPE, NotificationChannelDb.enabled.is_(True))
        .all()
    )
    for channel in channels:
        try:
            zone = ZoneInfo(validate_timezone(channel.timezone or DEFAULT_NOTIFICATION_TIMEZONE))
        except ValueError:
            logger.warning("Skipping scheduled reports for channel with invalid timezone")
            continue
        local_now = current.astimezone(zone)
        for event_type in ("daily_usage_report", "weekly_usage_report", "monthly_usage_report"):
            start, end, event_key = _report_period(event_type, local_now)
            context = _report_context(db, start, end, local_now)
            queued += enqueue_event(db, event_type, context, event_key, channel_id=channel.id)
    return queued


def _telegram_post(token: str, method: str, payload: Mapping[str, object]) -> dict[str, object]:
    with httpx.Client(timeout=15.0) as client:
        try:
            response = client.post(f"{TELEGRAM_API_BASE}/bot{token}/{method}", json=dict(payload))
        except httpx.HTTPError:
            # Telegram embeds the secret bot token in the request URL, so never
            # persist or log the original HTTP exception string.
            raise RuntimeError("Could not reach Telegram. Check the network and try again.") from None
    body: dict[str, object] = {}
    try:
        candidate = response.json()
        if isinstance(candidate, dict):
            body = candidate
    except ValueError:
        pass
    if not response.is_success or body.get("ok") is False:
        detail = str(body.get("description") or "Telegram rejected the request")
        raise RuntimeError(f"{detail} ({response.status_code})")
    return body


def send_telegram(
    channel: NotificationChannelDb,
    message: str,
    *,
    reply_to_message_id: Optional[int] = None,
    message_thread_id: Optional[int] = None,
) -> None:
    if not channel.secret_enc or not channel.destination:
        raise ValueError("Telegram bot token and destination are required")
    payload: dict[str, object] = {
        "chat_id": channel.destination,
        "text": message,
        "disable_web_page_preview": True,
    }
    topic_id = message_thread_id if message_thread_id is not None else channel.thread_id
    if topic_id is not None:
        payload["message_thread_id"] = topic_id
    if reply_to_message_id is not None:
        payload["reply_parameters"] = {
            "message_id": reply_to_message_id,
            "allow_sending_without_reply": True,
        }
    _telegram_post(crypto.decrypt(channel.secret_enc), "sendMessage", payload)


def _format_zoned_time(value: Optional[datetime], timezone_name: str) -> str:
    from zoneinfo import ZoneInfo

    if value is None:
        return "Unknown"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo(validate_timezone(timezone_name))).strftime("%Y-%m-%d %H:%M %Z")


def _account_status_label(account: AccountDb, now: datetime) -> str:
    if account.status == AccountStatus.DISABLED:
        return "Disabled"
    if account.provider_health == ProviderHealth.REAUTH_REQUIRED:
        return "Reauthentication required"
    if account.cooldown_until is not None:
        cooldown_until = account.cooldown_until
        if cooldown_until.tzinfo is None:
            cooldown_until = cooldown_until.replace(tzinfo=timezone.utc)
        if cooldown_until > now:
            return "Cooling down"
    blocking_window = rotation.blocking_quota_window(account)
    if blocking_window is not None:
        return f"Rotation threshold reached ({blocking_window.label})"
    if rotation.is_usable(account, now):
        return "Usable"
    if rotation.is_available(account, now):
        return f"Available ({account.provider_health.value.lower().replace('_', ' ')})"
    return "Unavailable"


def _is_available_pool_account(account: AccountDb) -> bool:
    """Match the dashboard's active pool: enabled and not awaiting reauthentication."""
    return account.status == AccountStatus.ACTIVE and account.provider_health != ProviderHealth.REAUTH_REQUIRED


def account_status_messages(
    db: Session,
    scope: str,
    timezone_name: str,
    now: Optional[datetime] = None,
) -> list[str]:
    """Render Telegram-safe account status messages for the requested pool scope."""
    normalized_scope = scope.strip().lower()
    if normalized_scope not in {"all", "usable", "available"}:
        raise ValueError("Status scope must be all, usable, or available")
    current = now or utcnow()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    validate_timezone(timezone_name)
    accounts = db.query(AccountDb).order_by(AccountDb.priority.asc(), AccountDb.created_at.asc()).all()
    usable_count = sum(rotation.is_usable(account, current) for account in accounts)
    available_count = sum(_is_available_pool_account(account) for account in accounts)
    if normalized_scope == "usable":
        selected = [account for account in accounts if rotation.is_usable(account, current)]
    elif normalized_scope == "available":
        selected = [account for account in accounts if _is_available_pool_account(account)]
    else:
        selected = accounts

    header = (
        f"🤖 Codex Proxy account status · {normalized_scope}\n\n"
        f"Showing: {len(selected)} of {len(accounts)} accounts\n"
        f"Usable now: {usable_count} · Available in pool: {available_count}\n"
        f"Timezone: {timezone_name}"
    )
    if not selected:
        return [f"{header}\n\nNo {normalized_scope} accounts found."]

    blocks: list[str] = []
    for account in selected:
        five_hour_remaining = None if account.five_hour_used_pct is None else f"{max(0.0, 1.0 - account.five_hour_used_pct) * 100:.1f}%"
        weekly_remaining = None if account.weekly_used_pct is None else f"{max(0.0, 1.0 - account.weekly_used_pct) * 100:.1f}%"
        monthly_remaining = None if account.monthly_used_pct is None else f"{max(0.0, 1.0 - account.monthly_used_pct) * 100:.1f}%"
        five_hour_reset_line = (
            f"5-hour reset: {_format_zoned_time(account.five_hour_reset_at, timezone_name)}\n" if account.five_hour_reset_at is not None else ""
        )
        weekly_reset_line = (
            f"Weekly reset: {_format_zoned_time(account.weekly_reset_at, timezone_name)}\n" if account.weekly_reset_at is not None else ""
        )
        monthly_reset_line = (
            f"Monthly reset: {_format_zoned_time(account.monthly_reset_at, timezone_name)}\n" if account.monthly_reset_at is not None else ""
        )
        five_hour_line = f"5-hour remaining: {five_hour_remaining}\n{five_hour_reset_line}" if five_hour_remaining is not None else ""
        weekly_line = f"Weekly remaining: {weekly_remaining}\n{weekly_reset_line}" if weekly_remaining is not None else ""
        monthly_line = f"Monthly remaining: {monthly_remaining}\n{monthly_reset_line}" if monthly_remaining is not None else ""
        status = _account_status_label(account, current)
        status_line = "" if normalized_scope == "usable" else f"Status: {status}\n"
        reset_credit_line = "Limit reset credit: Available\n" if (account.reset_credits_available or 0) > 0 else ""
        icon = "🟢" if rotation.is_usable(account, current) else "🟡" if rotation.is_available(account, current) else "🔴"
        block = (
            f"{icon} {account.label}\n"
            f"Email: {account.account_email or 'Unavailable'}\n"
            f"{status_line}"
            f"{five_hour_line}"
            f"{weekly_line}"
            f"{monthly_line}"
            f"5-hour rotation threshold: {account.five_hour_rotation_threshold * 100:.1f}%\n"
            f"Weekly rotation threshold: {account.weekly_rotation_threshold * 100:.1f}%\n"
            f"{reset_credit_line}"
        )
        blocks.append(block.rstrip())

    messages: list[str] = []
    current_message = header
    for block in blocks:
        candidate = f"{current_message}\n\n{block}"
        if len(candidate) <= _TELEGRAM_MESSAGE_LIMIT:
            current_message = candidate
            continue
        messages.append(current_message)
        current_message = f"🤖 Codex Proxy account status · {normalized_scope} (continued)\n\n{block}"
    messages.append(current_message)
    return messages


def _telegram_updates(channel: NotificationChannelDb, offset: int) -> list[dict[str, object]]:
    if not channel.secret_enc:
        return []
    body = _telegram_post(
        crypto.decrypt(channel.secret_enc),
        "getUpdates",
        {"offset": offset, "timeout": 0, "allowed_updates": ["message"]},
    )
    result = body.get("result")
    return [update for update in result if isinstance(update, dict)] if isinstance(result, list) else []


def _register_telegram_commands(channel: NotificationChannelDb) -> None:
    if not channel.secret_enc:
        return
    _telegram_post(
        crypto.decrypt(channel.secret_enc),
        "setMyCommands",
        {
            "commands": [
                {
                    "command": TELEGRAM_STATUS_COMMAND,
                    "description": "Account status: /codex status usable",
                }
            ]
        },
    )


def poll_telegram_commands_once(db: Session, offsets: MutableMapping[str, int]) -> int:
    """Poll enabled Telegram destinations once and answer recognized status commands."""
    channels = (
        db.query(NotificationChannelDb)
        .filter(NotificationChannelDb.channel_type == TELEGRAM_CHANNEL_TYPE, NotificationChannelDb.enabled.is_(True))
        .all()
    )
    handled = 0
    for channel in channels:
        if not channel.secret_enc or not channel.destination:
            continue
        channel_key = str(channel.id)
        if channel_key not in offsets:
            # Register the slash command and discard pre-startup backlog. This
            # prevents a worker restart from replaying an old status request.
            try:
                _register_telegram_commands(channel)
            except Exception:  # noqa: BLE001
                # Command discovery is convenient but not required for parsing
                # manually entered commands. Keep polling if registration fails.
                logger.warning("Telegram command registration failed: %s", channel.id)
            initial = _telegram_updates(channel, -1)
            offsets[channel_key] = max((int(update.get("update_id", -1)) for update in initial), default=-1) + 1
            continue

        updates = _telegram_updates(channel, offsets[channel_key])
        for update in updates:
            update_id = int(update.get("update_id", offsets[channel_key]))
            offsets[channel_key] = max(offsets[channel_key], update_id + 1)
            message = update.get("message")
            if not isinstance(message, dict):
                continue
            chat = message.get("chat")
            if not isinstance(chat, dict) or str(chat.get("id")) != str(channel.destination):
                continue
            incoming_thread = message.get("message_thread_id")
            if channel.thread_id is not None and incoming_thread != channel.thread_id:
                continue
            text = message.get("text")
            if not isinstance(text, str):
                continue
            match = _STATUS_COMMAND.fullmatch(text)
            starts_like_command = re.match(
                r"^\s*/codex(?:@[a-z0-9_]+)?\b",
                text,
                re.IGNORECASE,
            )
            if match is None and starts_like_command is None:
                continue
            reply_to = message.get("message_id")
            reply_id = int(reply_to) if isinstance(reply_to, int) else None
            topic_id = int(incoming_thread) if isinstance(incoming_thread, int) else None
            if match is None:
                replies = ["Usage: /codex status all|usable|available"]
            else:
                scope = (match.group(1) or "usable").lower()
                replies = account_status_messages(
                    db,
                    scope,
                    channel.timezone or DEFAULT_NOTIFICATION_TIMEZONE,
                )
            for reply in replies:
                send_telegram(
                    channel,
                    reply,
                    reply_to_message_id=reply_id,
                    message_thread_id=topic_id,
                )
            handled += 1
    return handled


def deliver_pending_once(db: Session) -> bool:
    """Deliver one due outbox row. Returns False when no work is available."""
    now = utcnow()
    delivery = (
        db.query(NotificationDeliveryDb)
        .join(NotificationChannelDb, NotificationChannelDb.id == NotificationDeliveryDb.channel_id)
        .join(
            NotificationRuleDb,
            (NotificationRuleDb.channel_id == NotificationDeliveryDb.channel_id)
            & (NotificationRuleDb.event_type == NotificationDeliveryDb.event_type),
        )
        .filter(
            NotificationDeliveryDb.status.in_(("pending", "failed")),
            NotificationDeliveryDb.attempts < _MAX_ATTEMPTS,
            NotificationDeliveryDb.available_at <= now,
            NotificationChannelDb.enabled.is_(True),
            NotificationRuleDb.enabled.is_(True),
        )
        .order_by(NotificationDeliveryDb.created_at.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if delivery is None:
        return False
    channel = db.query(NotificationChannelDb).filter(NotificationChannelDb.id == delivery.channel_id).one()
    try:
        if channel.channel_type != TELEGRAM_CHANNEL_TYPE:
            raise ValueError(f"Unsupported notification channel: {channel.channel_type}")
        send_telegram(channel, delivery.message)
        delivery.status = "sent"
        delivery.sent_at = now
        delivery.last_error = None
        channel.last_success_at = now
        channel.last_error = None
    except Exception as exc:  # noqa: BLE001
        delivery.attempts += 1
        delivery.status = "failed"
        delivery.last_error = str(exc)[:2000]
        delivery.available_at = now + timedelta(seconds=min(300, 5 * (2 ** (delivery.attempts - 1))))
        channel.last_error_at = now
        channel.last_error = str(exc)[:2000]
        logger.warning("Notification delivery failed: %s", type(exc).__name__)
    db.commit()
    return True
