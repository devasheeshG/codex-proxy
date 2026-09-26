# Path: app/utils/models/api/accounts.py
# Description: Pydantic models for pooled subscription account management routes.

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .base import AccountStatus, ProviderHealth


class Account(BaseModel):
    """A pooled Codex subscription account and its current rotation state."""

    id: uuid.UUID
    label: str
    account_email: Optional[str]
    tier: Optional[str]
    chatgpt_account_id: Optional[str]
    workspace_name: Optional[str]
    authenticated_override: bool
    status: AccountStatus
    provider_health: ProviderHealth
    provider_health_code: Optional[str]
    provider_health_message: Optional[str]
    provider_health_checked_at: Optional[datetime]
    provider_health_last_success_at: Optional[datetime]
    provider_health_failure_count: int
    five_hour_used_pct: Optional[float]
    five_hour_reset_at: Optional[datetime]
    weekly_used_pct: Optional[float]
    weekly_reset_at: Optional[datetime]
    monthly_used_pct: Optional[float]
    monthly_reset_at: Optional[datetime]
    cooldown_until: Optional[datetime]
    reset_credits_available: int
    auto_limit_reset_enabled: bool
    quota_refreshed_at: Optional[datetime]
    warmup_enabled: bool
    warmup_next_at: Optional[datetime]
    warmup_last_at: Optional[datetime]
    warmup_last_status: Optional[str]
    warmup_last_error: Optional[str]
    five_hour_rotation_threshold: float
    weekly_rotation_threshold: float
    rotation_threshold: float  # deprecated single-threshold alias
    cooldown_seconds: int  # rest period after a 429 with no usable retry-after
    max_failover_attempts: int  # accounts to try per request when starting on this one
    egress_target_id: Optional[str]
    priority: int  # 1 is tried first; unavailable accounts fall through to the next priority
    total_spend_usd: float
    monthly_spend_usd: float
    last_used_at: Optional[datetime]
    created_at: datetime

    @classmethod
    def from_db(cls, account_db, total_spend_usd: float = 0.0, monthly_spend_usd: float = 0.0) -> Account:
        return cls(
            id=account_db.id,
            label=account_db.label,
            account_email=account_db.account_email,
            tier=account_db.tier,
            chatgpt_account_id=account_db.chatgpt_account_id,
            workspace_name=account_db.workspace_name,
            authenticated_override=bool(account_db.authenticated_override),
            status=account_db.status,
            provider_health=account_db.provider_health,
            provider_health_code=account_db.provider_health_code,
            provider_health_message=account_db.provider_health_message,
            provider_health_checked_at=account_db.provider_health_checked_at,
            provider_health_last_success_at=account_db.provider_health_last_success_at,
            provider_health_failure_count=account_db.provider_health_failure_count or 0,
            five_hour_used_pct=account_db.five_hour_used_pct,
            five_hour_reset_at=account_db.five_hour_reset_at,
            weekly_used_pct=account_db.weekly_used_pct,
            weekly_reset_at=account_db.weekly_reset_at,
            monthly_used_pct=account_db.monthly_used_pct,
            monthly_reset_at=account_db.monthly_reset_at,
            cooldown_until=account_db.cooldown_until,
            reset_credits_available=account_db.reset_credits_available or 0,
            auto_limit_reset_enabled=bool(account_db.auto_limit_reset_enabled),
            quota_refreshed_at=account_db.quota_refreshed_at,
            warmup_enabled=bool(account_db.warmup_enabled),
            warmup_next_at=account_db.warmup_next_at,
            warmup_last_at=account_db.warmup_last_at,
            warmup_last_status=account_db.warmup_last_status,
            warmup_last_error=account_db.warmup_last_error,
            five_hour_rotation_threshold=account_db.five_hour_rotation_threshold,
            weekly_rotation_threshold=account_db.weekly_rotation_threshold,
            rotation_threshold=account_db.rotation_threshold,
            cooldown_seconds=account_db.cooldown_seconds,
            max_failover_attempts=account_db.max_failover_attempts,
            egress_target_id=account_db.egress_target_id,
            priority=account_db.priority,
            total_spend_usd=round(total_spend_usd, 6),
            monthly_spend_usd=round(monthly_spend_usd, 6),
            last_used_at=account_db.last_used_at,
            created_at=account_db.created_at,
        )


# PUT /accounts/{account_id}


class UpdateAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Optional[str] = None
    workspace_name: Optional[str] = Field(default=None, max_length=200)
    authenticated_override: Optional[bool] = None
    warmup_enabled: Optional[bool] = None
    auto_limit_reset_enabled: Optional[bool] = None
    # Rotation policy. Send a value to change it; omit or null leaves it unchanged.
    five_hour_rotation_threshold: Optional[float] = Field(default=None, ge=0, le=1)
    weekly_rotation_threshold: Optional[float] = Field(default=None, ge=0, le=1)
    # Deprecated: sets both provider-window thresholds for older clients.
    rotation_threshold: Optional[float] = Field(default=None, ge=0, le=1)
    cooldown_seconds: Optional[int] = Field(default=None, ge=1, le=86_400)
    max_failover_attempts: Optional[int] = Field(default=None, ge=1, le=100)
    # Null is normalized to the first enabled target; an explicit id pins the account.
    egress_target_id: Optional[str] = Field(default=None, max_length=128)
    priority: Optional[int] = Field(default=None, ge=1)


class ListAccountsResponse(BaseModel):
    accounts: List[Account]


class EgressTargetInfo(BaseModel):
    id: str
    label: str
    kind: str
    interface_name: Optional[str]
    private_ip: Optional[str]
    public_ip: Optional[str]
    max_concurrency: int
    enabled: bool


class ListEgressTargetsResponse(BaseModel):
    targets: List[EgressTargetInfo]


class ReorderAccountsRequest(BaseModel):
    account_ids: List[uuid.UUID]


class BulkPriorityRequest(BaseModel):
    account_ids: List[uuid.UUID] = Field(min_length=1)
    priority: int = Field(ge=1)


class AccountResponse(BaseModel):
    account: Account


# POST /accounts/oauth/start


class OAuthStartResponse(BaseModel):
    verification_url: str
    user_code: str
    flow_token: str
    interval: int


# POST /accounts/oauth/complete


class ReauthCompleteRequest(BaseModel):
    flow_token: str


class OAuthCompleteRequest(BaseModel):
    label: str
    flow_token: str
    workspace_name: Optional[str] = Field(default=None, max_length=200)


class RateLimitResetCredit(BaseModel):
    id: str
    reset_type: str
    status: str
    is_supported_by_plan: Optional[bool] = None
    granted_at: datetime
    expires_at: Optional[datetime] = None
    title: Optional[str] = None
    description: Optional[str] = None


class RateLimitResetCreditsResponse(BaseModel):
    available_count: int
    credits: List[RateLimitResetCredit]


class ConsumeLimitResetRequest(BaseModel):
    credit_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class ConsumeLimitResetResponse(BaseModel):
    code: str
    windows_reset: int
    idempotency_key: str
    account: Account
