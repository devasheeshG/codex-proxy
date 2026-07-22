"""Admin API models for configurable notification channels and event rules."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class TelegramSettings(BaseModel):
    enabled: bool
    configured: bool
    chat_id: Optional[str]
    message_thread_id: Optional[int]
    timezone: str
    last_success_at: Optional[datetime]
    last_error_at: Optional[datetime]
    last_error: Optional[str]


class UpdateTelegramSettingsRequest(BaseModel):
    enabled: bool = False
    bot_token: Optional[str] = Field(default=None, max_length=256)
    chat_id: Optional[str] = Field(default=None, max_length=128)
    message_thread_id: Optional[int] = Field(default=None, ge=1)
    timezone: Optional[str] = Field(default=None, max_length=64)

    @field_validator("bot_token", "chat_id", "timezone")
    @classmethod
    def strip_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class NotificationRule(BaseModel):
    event_type: str
    title: str
    description: str
    enabled: bool
    template: str
    default_template: str
    cooldown_seconds: int
    variables: list[str]


class UpdateNotificationRuleRequest(BaseModel):
    enabled: bool
    template: str = Field(min_length=1, max_length=4096)
    cooldown_seconds: int = Field(default=0, ge=0, le=86400)


class NotificationSettingsResponse(BaseModel):
    telegram: TelegramSettings
    rules: list[NotificationRule]


class TestNotificationResponse(BaseModel):
    delivered: bool
    detail: str
