# Path: app/utils/security/dependencies.py
# Description: FastAPI dependencies for admin sessions and proxy-user authentication.

from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.utils.postgres import ApiKeyDb, UserDb, get_db

from .keys import hash_key
from .tokens import decode_admin_token


def require_admin(authorization: Optional[str] = Header(default=None)) -> str:
    """FastAPI dependency: require a valid admin JWT, return the admin subject."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin authentication required")

    token = authorization.split(" ", 1)[1].strip()
    claims = decode_admin_token(token)
    if claims is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin session")

    return str(claims.get("sub"))


def authenticate_user(
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None, alias="x-api-key"),
    db: Session = Depends(get_db),  # noqa: B008
) -> ApiKeyDb:
    """FastAPI dependency for the proxy path: resolve the calling API key (and thus its user), or raise 401.

    The generated Codex provider profile sends the key as `Authorization: Bearer <key>`.
    `x-api-key` and a bare Authorization token are accepted for other compatible clients.
    The returned ApiKeyDb carries `user_id`, which the proxy uses to attribute usage.
    """
    presented: Optional[str] = None
    if authorization:
        parts = authorization.split(" ", 1)
        presented = parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else authorization.strip()
    elif x_api_key:
        presented = x_api_key.strip()

    if not presented:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key (Authorization: Bearer ... or x-api-key)",
        )

    key = db.query(ApiKeyDb).filter(ApiKeyDb.key_hash == hash_key(presented)).first()
    if key is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    if not key.active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="API key is disabled")

    user = db.query(UserDb).filter(UserDb.id == key.user_id).first()
    if user is None or not user.active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is disabled")

    return key
