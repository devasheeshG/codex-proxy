# Path: app/utils/models/api/users.py
# Description: Pydantic models for proxy user management (users own one or more API keys) plus per-user usage rollups.

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import Query
from pydantic import BaseModel, Field, field_validator

from app.utils import request_policy


def _normalize_model_values(values):
    if values is None:
        return None
    if not isinstance(values, list):
        return values
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("Model IDs must be non-empty strings")
    return request_policy.normalize_model_ids(values)


def _normalize_model_overrides(values):
    if not isinstance(values, dict):
        return values
    if any(
        not isinstance(source, str) or not source.strip() or not isinstance(target, str) or not target.strip() for source, target in values.items()
    ):
        raise ValueError("Model override sources and targets must be non-empty strings")
    normalized = request_policy.normalize_model_overrides(values)
    if len(normalized) != len(values):
        raise ValueError("Model overrides must use unique model IDs and cannot map a model to itself")
    return normalized


class User(BaseModel):
    """A proxy user. Identity only -- the actual credentials live on its API keys."""

    id: uuid.UUID
    name: str
    active: bool
    priority: int
    fallback_enabled: bool
    key_count: int
    rate_limit_per_minute: Optional[int]  # requests/min across all the user's keys; null/0 = unlimited
    monthly_token_budget: Optional[int]  # tokens/calendar month across all the user's keys; null/0 = unlimited
    lifetime_token_budget: Optional[int]  # one-time token cap across the user's entire retained history
    monthly_spend_budget_usd: Optional[float]
    lifetime_spend_budget_usd: Optional[float]
    allowed_request_modes: List[request_policy.RequestMode]
    allowed_reasoning_levels: List[request_policy.ReasoningLevel]
    allowed_models: Optional[List[str]]  # null = all current and future models
    model_overrides: Dict[str, str]
    last_used_at: Optional[datetime]
    created_at: datetime
    total_tokens: int
    total_requests: int
    monthly_tokens_used: int
    total_spend_usd: float
    monthly_spend_usd: float
    monthly_reset_at: datetime

    @classmethod
    def from_db(
        cls,
        user_db,
        key_count: int,
        total_tokens: int,
        total_requests: int,
        monthly_tokens_used: int,
        total_spend_usd: float,
        monthly_spend_usd: float,
        monthly_reset_at: datetime,
    ) -> User:
        return cls(
            id=user_db.id,
            name=user_db.name,
            active=user_db.active,
            priority=user_db.priority,
            fallback_enabled=user_db.fallback_enabled,
            key_count=key_count,
            rate_limit_per_minute=user_db.rate_limit_per_minute,
            monthly_token_budget=user_db.monthly_token_budget,
            lifetime_token_budget=user_db.lifetime_token_budget,
            monthly_spend_budget_usd=user_db.monthly_spend_budget_usd,
            lifetime_spend_budget_usd=user_db.lifetime_spend_budget_usd,
            allowed_request_modes=request_policy.decode_choices(
                user_db.allowed_request_modes_json,
                request_policy.ALL_REQUEST_MODES,
            ),
            allowed_reasoning_levels=request_policy.decode_choices(
                user_db.allowed_reasoning_levels_json,
                request_policy.ALL_REASONING_LEVELS,
            ),
            allowed_models=request_policy.decode_models(user_db.allowed_models_json),
            model_overrides=request_policy.decode_model_overrides(user_db.model_overrides_json),
            last_used_at=user_db.last_used_at,
            created_at=user_db.created_at,
            total_tokens=total_tokens,
            total_requests=total_requests,
            monthly_tokens_used=monthly_tokens_used,
            total_spend_usd=round(total_spend_usd, 6),
            monthly_spend_usd=round(monthly_spend_usd, 6),
            monthly_reset_at=monthly_reset_at,
        )


class ApiKey(BaseModel):
    """One API key belonging to a user. The plaintext is shown only at creation; only the prefix is kept after."""

    id: uuid.UUID
    user_id: uuid.UUID
    label: Optional[str]
    key_prefix: str
    active: bool
    rate_limit_per_minute: Optional[int]  # null = use the global default; 0 = unlimited
    monthly_token_budget: Optional[int]  # null/0 = unlimited
    last_used_at: Optional[datetime]
    created_at: datetime

    @classmethod
    def from_db(cls, key_db) -> ApiKey:
        return cls(
            id=key_db.id,
            user_id=key_db.user_id,
            label=key_db.label,
            key_prefix=key_db.key_prefix,
            active=key_db.active,
            rate_limit_per_minute=key_db.rate_limit_per_minute,
            monthly_token_budget=key_db.monthly_token_budget,
            last_used_at=key_db.last_used_at,
            created_at=key_db.created_at,
        )


# GET /users


class ListUsersRequest(BaseModel):
    limit: int
    offset: int

    @classmethod
    def get_request(
        cls,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> ListUsersRequest:
        return cls(limit=limit, offset=offset)


class ListUsersResponse(BaseModel):
    users: List[User]
    total: int


class BulkPriorityRequest(BaseModel):
    user_ids: List[uuid.UUID] = Field(min_length=1)
    priority: int = Field(ge=1, le=1000)


class ModelOptionsResponse(BaseModel):
    models: List[str]


# POST /users


class CreateUserRequest(BaseModel):
    name: str
    priority: int = Field(default=1, ge=1, le=1000)
    fallback_enabled: bool = False
    rate_limit_per_minute: Optional[int] = None  # requests/min across all the user's keys; null/0 = unlimited
    monthly_token_budget: Optional[int] = Field(default=None, ge=0)
    lifetime_token_budget: Optional[int] = Field(default=None, ge=0)
    monthly_spend_budget_usd: Optional[float] = Field(default=None, ge=0, le=10_000_000)
    lifetime_spend_budget_usd: Optional[float] = Field(default=None, ge=0, le=10_000_000)
    allowed_request_modes: List[request_policy.RequestMode] = Field(
        default_factory=lambda: list(request_policy.ALL_REQUEST_MODES),
        min_length=1,
    )
    allowed_reasoning_levels: List[request_policy.ReasoningLevel] = Field(
        default_factory=lambda: list(request_policy.ALL_REASONING_LEVELS),
        min_length=1,
    )
    allowed_models: Optional[List[str]] = Field(default=None, min_length=1)
    model_overrides: Dict[str, str] = Field(default_factory=dict)

    @field_validator("allowed_request_modes", "allowed_reasoning_levels", mode="before")
    @classmethod
    def normalize_policy_values(cls, values):
        if isinstance(values, list):
            return [value.strip().lower() if isinstance(value, str) else value for value in values]
        return values

    @field_validator("allowed_models", mode="before")
    @classmethod
    def normalize_models(cls, values):
        return _normalize_model_values(values)

    @field_validator("model_overrides", mode="before")
    @classmethod
    def normalize_overrides(cls, values):
        return _normalize_model_overrides(values)


# PUT /users/{user_id}


class UpdateUserRequest(BaseModel):
    # Omitted fields are left unchanged. For the numeric limits, send 0 to clear a limit (unlimited).
    name: Optional[str] = None
    active: Optional[bool] = None
    priority: Optional[int] = Field(default=None, ge=1, le=1000)
    fallback_enabled: Optional[bool] = None
    rate_limit_per_minute: Optional[int] = None
    monthly_token_budget: Optional[int] = Field(default=None, ge=0)
    lifetime_token_budget: Optional[int] = Field(default=None, ge=0)
    monthly_spend_budget_usd: Optional[float] = Field(default=None, ge=0, le=10_000_000)
    lifetime_spend_budget_usd: Optional[float] = Field(default=None, ge=0, le=10_000_000)
    allowed_request_modes: Optional[List[request_policy.RequestMode]] = Field(default=None, min_length=1)
    allowed_reasoning_levels: Optional[List[request_policy.ReasoningLevel]] = Field(default=None, min_length=1)
    allowed_models: Optional[List[str]] = Field(default=None, min_length=1)
    model_overrides: Optional[Dict[str, str]] = None

    @field_validator("allowed_request_modes", "allowed_reasoning_levels", mode="before")
    @classmethod
    def normalize_policy_values(cls, values):
        if isinstance(values, list):
            return [value.strip().lower() if isinstance(value, str) else value for value in values]
        return values

    @field_validator("allowed_models", mode="before")
    @classmethod
    def normalize_models(cls, values):
        return _normalize_model_values(values)

    @field_validator("model_overrides", mode="before")
    @classmethod
    def normalize_overrides(cls, values):
        return _normalize_model_overrides(values)


class UserResponse(BaseModel):
    user: User


# POST /users/{user_id}/keys


class CreateApiKeyRequest(BaseModel):
    # A label is required so every credential is identifiable in the dashboard and audit logs.
    label: str = Field(..., min_length=1, max_length=120)
    rate_limit_per_minute: Optional[int] = None  # requests/min; null = global default, 0 = unlimited
    monthly_token_budget: Optional[int] = None  # tokens/calendar month; null/0 = unlimited

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Key label is required")
        return value


# PUT /users/{user_id}/keys/{key_id}


class UpdateApiKeyRequest(BaseModel):
    # Omitted fields are left unchanged. For the numeric limits, send 0 to clear a limit (unlimited).
    label: Optional[str] = None
    active: Optional[bool] = None
    rate_limit_per_minute: Optional[int] = None
    monthly_token_budget: Optional[int] = None


class ApiKeyResponse(BaseModel):
    api_key: ApiKey


class ListApiKeysResponse(BaseModel):
    keys: List[ApiKey]


class ApiKeyCreatedResponse(BaseModel):
    api_key: ApiKey
    # Plaintext key -- shown exactly once, never stored.
    secret: str
