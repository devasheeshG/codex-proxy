# Path: app/routes/auth.py
# Description: Admin authentication route -- issues a session JWT for the dashboard.

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.logger import get_logger
from app.utils import security
from app.utils.models.api import AuthProfileResponse, LoginRequest, TokenResponse
from app.utils.postgres import get_db

# Get the logger
logger = get_logger()

router = APIRouter(tags=["Admin Auth"], prefix="/auth")


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={
        200: {"description": "Authenticated; session token returned"},
        401: {"description": "Invalid username or password"},
    },
)
def login(request: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:  # noqa: B008
    """Authenticate the break-glass owner or a database-backed team member."""
    if security.is_root_credentials(request.username, request.password):
        token = security.issue_admin_token(subject=request.username, role="owner", permissions=["*"])
        logger.info("Dashboard owner logged in")
        return TokenResponse(token=token)

    member = security.authenticate_dashboard_member(request.username, request.password, db)
    if member is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    from datetime import datetime, timezone

    member.last_login_at = datetime.now(timezone.utc)
    db.commit()
    values = json.loads(member.permissions_json or "[]")
    permissions = security.normalize_permissions(values)
    token = security.issue_admin_token(
        subject=member.username,
        member_id=str(member.id),
        role="member",
        permissions=list(permissions),
    )
    logger.info("Dashboard team member logged in")
    return TokenResponse(token=token)


@router.get("/me", response_model=AuthProfileResponse)
def profile(principal=Depends(security.require_admin_principal)) -> AuthProfileResponse:  # noqa: B008
    """Return the effective dashboard permissions for the current session."""
    return AuthProfileResponse(
        username=principal.subject,
        permissions=sorted(principal.permissions),
        root="*" in principal.permissions,
    )
