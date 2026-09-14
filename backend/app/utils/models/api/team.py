"""Dashboard team-member and explicit permission API models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DashboardMember(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    permissions: list[str]
    active: bool
    created_at: datetime
    last_login_at: Optional[datetime]


class DashboardMemberResponse(BaseModel):
    member: DashboardMember


class ListDashboardMembersResponse(BaseModel):
    members: list[DashboardMember]


class CreateDashboardMemberRequest(BaseModel):
    username: str = Field(min_length=3, max_length=120, pattern=r"^[A-Za-z0-9_.@+-]+$")
    password: str = Field(min_length=12, max_length=200)
    permissions: list[str] = Field(min_length=1)


class UpdateDashboardMemberRequest(BaseModel):
    password: Optional[str] = Field(default=None, min_length=12, max_length=200)
    permissions: Optional[list[str]] = Field(default=None, min_length=1)
    active: Optional[bool] = None


class AuthProfileResponse(BaseModel):
    username: str
    permissions: list[str]
    root: bool = False
