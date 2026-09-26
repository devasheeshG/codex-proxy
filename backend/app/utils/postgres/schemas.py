# Path: app/utils/postgres/schemas.py
# Description: This file contains the database schema of the application.

from sqlalchemy import (
    UUID,
    VARCHAR,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    and_,
    func,
)

from app import config
from app.utils import request_policy
from app.utils.models.api import AccountStatus, ProviderHealth

from .base import DatabaseBase


class AccountDb(DatabaseBase):
    __tablename__ = "accounts"

    id = Column(UUID(as_uuid=True), nullable=False)
    label = Column(VARCHAR, nullable=False)
    account_email = Column(VARCHAR, nullable=True)
    tier = Column(VARCHAR, nullable=True)  # ChatGPT plan type, e.g. plus, pro, business
    chatgpt_account_id = Column(VARCHAR, nullable=True)
    chatgpt_account_user_id = Column(VARCHAR, nullable=True)
    chatgpt_user_id = Column(VARCHAR, nullable=True)
    workspace_name = Column(VARCHAR(200), nullable=True)
    # Admin-only presentation override; does not affect authentication or routing eligibility.
    authenticated_override = Column(Boolean, nullable=False, default=False, server_default="false")
    chatgpt_account_is_fedramp = Column(Boolean, nullable=False, default=False, server_default="false")
    access_token_enc = Column(Text, nullable=False)
    refresh_token_enc = Column(Text, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(
        Enum(AccountStatus, name="account_status", native_enum=True, values_callable=lambda enum_cls: [e.value for e in enum_cls]),
        nullable=False,
    )
    cooldown_until = Column(DateTime(timezone=True), nullable=True)
    provider_health = Column(
        Enum(ProviderHealth, name="provider_health", native_enum=True, values_callable=lambda enum_cls: [e.value for e in enum_cls]),
        nullable=False,
        default=ProviderHealth.UNKNOWN,
        server_default=ProviderHealth.UNKNOWN.value,
    )
    provider_health_code = Column(VARCHAR, nullable=True)
    provider_health_message = Column(VARCHAR, nullable=True)
    provider_health_checked_at = Column(DateTime(timezone=True), nullable=True)
    provider_health_last_success_at = Column(DateTime(timezone=True), nullable=True)
    provider_health_failure_count = Column(Integer, nullable=False, default=0, server_default="0")
    # ChatGPT-authenticated Codex accounts expose a five-hour primary window
    # and an optional seven-day secondary window. Keep the provider windows
    # explicit so the dashboard and rotation engine never infer semantics from
    # the provider's primary/secondary ordering.
    five_hour_used_pct = Column(Float, nullable=True)
    five_hour_reset_at = Column(DateTime(timezone=True), nullable=True)
    weekly_used_pct = Column(Float, nullable=True)
    weekly_reset_at = Column(DateTime(timezone=True), nullable=True)
    monthly_used_pct = Column(Float, nullable=True)
    monthly_reset_at = Column(DateTime(timezone=True), nullable=True)
    reset_credits_available = Column(Integer, nullable=False, default=0, server_default="0")
    # Provider reset credits are only redeemed automatically when explicitly enabled per account.
    auto_limit_reset_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    quota_refreshed_at = Column(DateTime(timezone=True), nullable=True)
    # Per-window rotation policies (tuned in the dashboard); seeded with config defaults on every new account.
    five_hour_rotation_threshold = Column(
        Float,
        nullable=False,
        default=config.DEFAULT_FIVE_HOUR_ROTATION_THRESHOLD,
        server_default=str(config.DEFAULT_FIVE_HOUR_ROTATION_THRESHOLD),
    )
    weekly_rotation_threshold = Column(
        Float,
        nullable=False,
        default=config.DEFAULT_WEEKLY_ROTATION_THRESHOLD,
        server_default=str(config.DEFAULT_WEEKLY_ROTATION_THRESHOLD),
    )
    # Deprecated alias retained for clients upgrading from the single-threshold API.
    rotation_threshold = Column(
        Float, nullable=False, default=config.DEFAULT_ROTATION_THRESHOLD, server_default=str(config.DEFAULT_ROTATION_THRESHOLD)
    )
    cooldown_seconds = Column(
        Integer, nullable=False, default=config.DEFAULT_COOLDOWN_SECONDS, server_default=str(config.DEFAULT_COOLDOWN_SECONDS)
    )  # rest period after a 429 with no usable retry-after
    max_failover_attempts = Column(
        Integer, nullable=False, default=config.DEFAULT_MAX_FAILOVER_ATTEMPTS, server_default=str(config.DEFAULT_MAX_FAILOVER_ATTEMPTS)
    )  # accounts to try per request when starting on this one
    # Null is normalized to the first enabled target. A value pins every
    # upstream operation for this account to the stable configured target id.
    egress_target_id = Column(VARCHAR(128), nullable=True)
    priority = Column(Integer, nullable=False, default=1)
    model_catalog_json = Column(Text, nullable=True)
    model_catalog_refreshed_at = Column(DateTime(timezone=True), nullable=True)
    # Automatic warm-up keeps provider windows staggered without changing normal routing.
    warmup_enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    warmup_next_at = Column(DateTime(timezone=True), nullable=True)
    warmup_last_at = Column(DateTime(timezone=True), nullable=True)
    warmup_last_status = Column(VARCHAR(32), nullable=True)
    warmup_last_error = Column(Text, nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_accounts_id"),
        UniqueConstraint("chatgpt_account_user_id", name="uq_accounts_chatgpt_account_user_id"),
        UniqueConstraint("chatgpt_account_id", "chatgpt_user_id", name="uq_accounts_workspace_user"),
        Index(
            "uq_accounts_workspace_email",
            chatgpt_account_id,
            func.lower(account_email),
            unique=True,
            postgresql_where=and_(chatgpt_account_id.isnot(None), account_email.isnot(None)),
        ),
        Index("ix_accounts_status", "status"),
        Index("ix_accounts_priority", "priority"),
    )

    def __repr__(self):
        return f"<Account(id={self.id}, label={self.label}, status={self.status})>"


class UserDb(DatabaseBase):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), nullable=False)
    name = Column(VARCHAR, nullable=False)
    active = Column(Boolean, nullable=False)
    priority = Column(Integer, nullable=False, default=1, server_default="1")
    fallback_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    # max requests/min across ALL of this user's keys; unlike keys (where null = global default), users have NO
    # global default -- null/0 = no user-level cap (unlimited).
    rate_limit_per_minute = Column(Integer, nullable=True)
    # token cap per calendar month across all the user's keys; null/0 = unlimited (no global default for users).
    monthly_token_budget = Column(BigInteger, nullable=True)
    # Lifetime (one-time) cap. Unlike the monthly budget this never resets.
    lifetime_token_budget = Column(BigInteger, nullable=True)
    # API-equivalent spend caps across all keys. Null/0 means unlimited.
    monthly_spend_budget_usd = Column(Float, nullable=True)
    lifetime_spend_budget_usd = Column(Float, nullable=True)
    # JSON lists keep the policy portable across Postgres-backed runtime and schema-level test fixtures.
    allowed_request_modes_json = Column(
        Text,
        nullable=False,
        default=request_policy.DEFAULT_REQUEST_MODES_JSON,
        server_default=request_policy.DEFAULT_REQUEST_MODES_JSON,
    )
    allowed_reasoning_levels_json = Column(
        Text,
        nullable=False,
        default=request_policy.DEFAULT_REASONING_LEVELS_JSON,
        server_default=request_policy.DEFAULT_REASONING_LEVELS_JSON,
    )
    # Null means every current and future model is allowed. A JSON list restricts
    # both model discovery and Responses API requests to those exact model IDs.
    allowed_models_json = Column(Text, nullable=True)
    # Requested model ID -> upstream model ID, scoped to this user.
    model_overrides_json = Column(Text, nullable=False, default="{}", server_default="{}")
    preset_id = Column(UUID(as_uuid=True), nullable=True)
    preset_overrides_json = Column(Text, nullable=False, default="[]", server_default="[]")
    model_reasoning_levels_json = Column(Text, nullable=False, default="{}", server_default="{}")
    model_request_modes_json = Column(Text, nullable=False, default="{}", server_default="{}")
    created_at = Column(DateTime(timezone=True), nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_users_id"),
        Index("ix_users_name", "name"),
    )

    def __repr__(self):
        return f"<User(id={self.id}, name={self.name})>"


class PresetDb(DatabaseBase):
    __tablename__ = "presets"

    id = Column(UUID(as_uuid=True), primary_key=True)
    name = Column(VARCHAR(120), nullable=False, unique=True)
    allowed_models_json = Column(Text, nullable=True)
    allowed_reasoning_levels_json = Column(Text, nullable=False, server_default=request_policy.DEFAULT_REASONING_LEVELS_JSON)
    allowed_request_modes_json = Column(Text, nullable=False, server_default=request_policy.DEFAULT_REQUEST_MODES_JSON)
    model_overrides_json = Column(Text, nullable=False, server_default="{}")
    model_reasoning_levels_json = Column(Text, nullable=False, server_default="{}")
    model_request_modes_json = Column(Text, nullable=False, server_default="{}")
    created_at = Column(DateTime(timezone=True), nullable=False)


class DashboardMemberDb(DatabaseBase):
    """A human operator who can access the administrative dashboard."""

    __tablename__ = "dashboard_members"

    id = Column(UUID(as_uuid=True), nullable=False)
    username = Column(VARCHAR(120), nullable=False)
    password_hash = Column(Text, nullable=False)
    permissions_json = Column(Text, nullable=False, default="[]", server_default="[]")
    active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    last_login_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_dashboard_members_id"),
        UniqueConstraint("username", name="uq_dashboard_members_username"),
        Index("ix_dashboard_members_active", "active"),
    )

    def __repr__(self):
        return f"<DashboardMember(id={self.id}, username={self.username})>"


class ProxyEventDb(DatabaseBase):
    """Append-only operational timeline for every proxied request and failover."""

    __tablename__ = "proxy_events"

    id = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    request_id = Column(VARCHAR(128), nullable=False)
    # Native Codex correlation identifiers copied from client_metadata. These
    # are nullable for historical events and non-Codex clients.
    codex_session_id = Column(VARCHAR(128), nullable=True)
    codex_thread_id = Column(VARCHAR(128), nullable=True)
    codex_turn_id = Column(VARCHAR(128), nullable=True)
    codex_root_turn_id = Column(VARCHAR(128), nullable=True)
    user_id = Column(UUID(as_uuid=True), nullable=True)
    api_key_id = Column(UUID(as_uuid=True), nullable=True)
    account_id = Column(UUID(as_uuid=True), nullable=True)
    fallback_provider_id = Column(UUID(as_uuid=True), nullable=True)
    event_type = Column(VARCHAR(64), nullable=False)
    status_code = Column(Integer, nullable=True)
    message = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_proxy_events_id"),
        Index("ix_proxy_events_created_at", "created_at"),
        Index("ix_proxy_events_request_id", "request_id"),
        Index("ix_proxy_events_codex_session_id", "codex_session_id"),
        Index("ix_proxy_events_codex_thread_id", "codex_thread_id"),
        Index("ix_proxy_events_codex_root_turn_id", "codex_root_turn_id"),
        Index("ix_proxy_events_user_id", "user_id"),
    )


class ApiKeyDb(DatabaseBase):
    __tablename__ = "api_keys"

    id = Column(UUID(as_uuid=True), nullable=False)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    label = Column(VARCHAR, nullable=True)
    key_prefix = Column(VARCHAR, nullable=False)
    key_hash = Column(VARCHAR, nullable=False)
    active = Column(Boolean, nullable=False)
    rate_limit_per_minute = Column(Integer, nullable=True)  # max requests/min for this key; null = global default
    monthly_token_budget = Column(BigInteger, nullable=True)  # token cap per calendar month; null = unlimited
    created_at = Column(DateTime(timezone=True), nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_api_keys_id"),
        ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_api_keys_user_id", ondelete="CASCADE"),
        UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
        Index("ix_api_keys_key_hash", "key_hash"),
        Index("ix_api_keys_user_id", "user_id"),
    )

    def __repr__(self):
        return f"<ApiKey(id={self.id}, user_id={self.user_id}, label={self.label})>"


class OpenAIFallbackDb(DatabaseBase):
    """Encrypted OpenAI-compatible API credential used only after subscriptions fail."""

    __tablename__ = "openai_fallbacks"

    id = Column(UUID(as_uuid=True), nullable=False)
    label = Column(VARCHAR(200), nullable=False)
    base_url = Column(VARCHAR(2048), nullable=False)
    api_key_enc = Column(Text, nullable=False)
    credential_hash = Column(VARCHAR(64), nullable=False)
    key_hint = Column(VARCHAR(32), nullable=False)
    status = Column(
        Enum(AccountStatus, name="account_status", native_enum=True, values_callable=lambda enum_cls: [e.value for e in enum_cls]),
        nullable=False,
        default=AccountStatus.ACTIVE,
        server_default=AccountStatus.ACTIVE.value,
    )
    provider_health = Column(
        Enum(ProviderHealth, name="provider_health", native_enum=True, values_callable=lambda enum_cls: [e.value for e in enum_cls]),
        nullable=False,
        default=ProviderHealth.UNKNOWN,
        server_default=ProviderHealth.UNKNOWN.value,
    )
    provider_health_message = Column(VARCHAR, nullable=True)
    provider_health_checked_at = Column(DateTime(timezone=True), nullable=True)
    cooldown_until = Column(DateTime(timezone=True), nullable=True)
    priority = Column(Integer, nullable=False, default=1, server_default="1")
    egress_target_id = Column(VARCHAR(128), nullable=True)
    monthly_spend_limit_usd = Column(Float, nullable=True)
    model_catalog_json = Column(Text, nullable=True)
    model_catalog_refreshed_at = Column(DateTime(timezone=True), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_openai_fallbacks_id"),
        UniqueConstraint("credential_hash", name="uq_openai_fallbacks_credential_hash"),
        Index("ix_openai_fallbacks_status_priority", "status", "priority"),
    )


class UsageRecordDb(DatabaseBase):
    __tablename__ = "usage_records"

    id = Column(UUID(as_uuid=True), nullable=False)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    api_key_id = Column(UUID(as_uuid=True), nullable=True)
    account_id = Column(UUID(as_uuid=True), nullable=True)
    fallback_provider_id = Column(UUID(as_uuid=True), nullable=True)
    model = Column(VARCHAR, nullable=False)
    input_tokens = Column(BigInteger, nullable=False)
    output_tokens = Column(BigInteger, nullable=False)
    cached_input_tokens = Column(BigInteger, nullable=False, server_default="0")
    cache_write_tokens = Column(BigInteger, nullable=False, server_default="0")
    reasoning_level = Column(VARCHAR, nullable=True)
    request_mode = Column(VARCHAR, nullable=False, default="standard", server_default="standard")
    status_code = Column(Integer, nullable=True)
    request_id = Column(VARCHAR, nullable=True)
    codex_session_id = Column(VARCHAR(128), nullable=True)
    codex_thread_id = Column(VARCHAR(128), nullable=True)
    codex_turn_id = Column(VARCHAR(128), nullable=True)
    codex_root_turn_id = Column(VARCHAR(128), nullable=True)
    billed_cost_usd = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_usage_records_id"),
        ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_usage_records_user_id", ondelete="CASCADE"),
        ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], name="fk_usage_records_api_key_id", ondelete="SET NULL"),
        ForeignKeyConstraint(["account_id"], ["accounts.id"], name="fk_usage_records_account_id", ondelete="SET NULL"),
        ForeignKeyConstraint(
            ["fallback_provider_id"],
            ["openai_fallbacks.id"],
            name="fk_usage_records_fallback_provider_id",
            ondelete="SET NULL",
        ),
        Index("ix_usage_records_user_id", "user_id"),
        Index("ix_usage_records_api_key_id", "api_key_id"),
        Index("ix_usage_records_codex_session_id", "codex_session_id"),
        Index("ix_usage_records_codex_thread_id", "codex_thread_id"),
        Index("ix_usage_records_codex_root_turn_id", "codex_root_turn_id"),
        Index("ix_usage_records_account_id", "account_id"),
        Index("ix_usage_records_fallback_provider_id", "fallback_provider_id"),
        Index("ix_usage_records_created_at", "created_at"),
    )

    def __repr__(self):
        return f"<UsageRecord(id={self.id}, user_id={self.user_id}, account_id={self.account_id})>"


class NotificationChannelDb(DatabaseBase):
    """A configured notification destination. Secrets are always encrypted at rest."""

    __tablename__ = "notification_channels"

    id = Column(UUID(as_uuid=True), nullable=False)
    channel_type = Column(VARCHAR, nullable=False)
    name = Column(VARCHAR, nullable=False)
    enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    secret_enc = Column(Text, nullable=True)
    destination = Column(VARCHAR, nullable=True)
    thread_id = Column(Integer, nullable=True)
    timezone = Column(VARCHAR, nullable=False, default="Asia/Kolkata", server_default="Asia/Kolkata")
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_error_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_notification_channels_id"),
        UniqueConstraint("channel_type", name="uq_notification_channels_type"),
        Index("ix_notification_channels_enabled", "enabled"),
    )


class NotificationRuleDb(DatabaseBase):
    """An event subscription and its operator-controlled message template."""

    __tablename__ = "notification_rules"

    id = Column(UUID(as_uuid=True), nullable=False)
    channel_id = Column(UUID(as_uuid=True), nullable=False)
    event_type = Column(VARCHAR, nullable=False)
    enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    template = Column(Text, nullable=False)
    cooldown_seconds = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_notification_rules_id"),
        ForeignKeyConstraint(
            ["channel_id"],
            ["notification_channels.id"],
            name="fk_notification_rules_channel_id",
            ondelete="CASCADE",
        ),
        UniqueConstraint("channel_id", "event_type", name="uq_notification_rules_channel_event"),
        Index("ix_notification_rules_event_type", "event_type"),
    )


class NotificationDeliveryDb(DatabaseBase):
    """Persistent outbox row used for deduplication, retries, and delivery visibility."""

    __tablename__ = "notification_deliveries"

    id = Column(UUID(as_uuid=True), nullable=False)
    channel_id = Column(UUID(as_uuid=True), nullable=False)
    event_type = Column(VARCHAR, nullable=False)
    event_key = Column(VARCHAR, nullable=False)
    message = Column(Text, nullable=False)
    status = Column(VARCHAR, nullable=False, default="pending", server_default="pending")
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    available_at = Column(DateTime(timezone=True), nullable=False)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_notification_deliveries_id"),
        ForeignKeyConstraint(
            ["channel_id"],
            ["notification_channels.id"],
            name="fk_notification_deliveries_channel_id",
            ondelete="CASCADE",
        ),
        UniqueConstraint("channel_id", "event_type", "event_key", name="uq_notification_deliveries_event"),
        Index("ix_notification_deliveries_dispatch", "status", "available_at"),
    )
