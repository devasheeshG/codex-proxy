# Path: app/routes/auth.py
# Description: Admin authentication route -- issues a session JWT for the dashboard.

from fastapi import APIRouter, HTTPException, status

from app.logger import get_logger
from app.utils import security
from app.utils.models.api import LoginRequest, TokenResponse

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
def login(request: LoginRequest) -> TokenResponse:
    """Authenticate the admin against the configured credentials and return a session token."""
    if not security.verify_admin_credentials(request.username, request.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    logger.info(f"Admin '{request.username}' logged in")
    return TokenResponse(token=security.issue_admin_token())
