# Path: app/utils/models/api/fallbacks.py
# Description: Admin API models for encrypted OpenAI-compatible fallback providers.

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from .base import AccountStatus, ProviderHealth


class OpenAIFallback(BaseModel):
    """A managed pay-as-you-go fallback without exposing its stored API key."""

    id: uuid.UUID
    label: str
    base_url: str
    key_hint: str
    status: AccountStatus
    provider_health: ProviderHealth
    provider_health_message: Optional[str]
    provider_health_checked_at: Optional[datetime]
    cooldown_until: Optional[datetime]
    priority: int
    monthly_spend_limit_usd: Optional[float]
    monthly_spend_usd: float
    monthly_spend_remaining_usd: Optional[float]
    monthly_spend_reset_at: datetime
    model_count: int
    model_catalog_refreshed_at: Optional[datetime]
    last_used_at: Optional[datetime]
    created_at: datetime


class CreateOpenAIFallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=200)
    base_url: HttpUrl
    api_key: str = Field(min_length=8, max_length=4096)
    monthly_spend_limit_usd: Optional[float] = Field(default=None, gt=0, le=10_000_000)
    priority: int = Field(default=1, ge=1, le=10_000)


class UpdateOpenAIFallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Optional[str] = Field(default=None, min_length=1, max_length=200)
    base_url: Optional[HttpUrl] = None
    api_key: Optional[str] = Field(default=None, min_length=8, max_length=4096)
    monthly_spend_limit_usd: Optional[float] = Field(default=None, gt=0, le=10_000_000)
    clear_monthly_spend_limit: bool = False
    priority: Optional[int] = Field(default=None, ge=1, le=10_000)


class OpenAIFallbackResponse(BaseModel):
    fallback: OpenAIFallback


class ListOpenAIFallbacksResponse(BaseModel):
    fallbacks: List[OpenAIFallback]
