# Path: app/utils/security/tokens.py
# Description: Admin session JWT issue/verify and constant-time credential checking.

import hmac
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from app.config import get_settings

# Get the settings
settings = get_settings()


def verify_admin_credentials(username: str, password: str) -> bool:
    """Constant-time check of admin login credentials against settings."""
    user_ok = hmac.compare_digest(username, settings.ADMIN_USERNAME)
    pass_ok = hmac.compare_digest(password, settings.ADMIN_PASSWORD)
    return user_ok and pass_ok


def issue_admin_token() -> str:
    """Issue a signed JWT for an authenticated admin session."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": settings.ADMIN_USERNAME,
        "role": "admin",
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_admin_token(token: str) -> Optional[dict]:
    """Decode and verify an admin session token. Returns the claims if valid and admin-scoped, else None."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("role") != "admin":
        return None
    return payload
