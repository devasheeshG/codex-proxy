# Path: app/utils/models/api/auth.py
# Description: Pydantic models for the admin authentication routes.

from __future__ import annotations

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    token: str
    token_type: str = "bearer"
