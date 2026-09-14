"""Dashboard team-member and permission API models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DashboardMember(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    display_name: str
    role: str
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
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=200)
    role: str = Field(default="read_only", min_length=1, max_length=64)


class UpdateDashboardMemberRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    password: Optional[str] = Field(default=None, min_length=12, max_length=200)
    role: Optional[str] = Field(default=None, min_length=1, max_length=64)
    active: Optional[bool] = None


class AuthProfileResponse(BaseModel):
    username: str
    display_name: str
    role: str
    permissions: list[str]
    root: bool = False
