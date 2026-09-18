"""Small, non-sensitive lookup lists used by analytics filters."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.model_catalog import configured_model_ids
from app.utils import security
from app.utils.postgres import UserDb, get_db

router = APIRouter(tags=["Dashboard lookups"], prefix="/lookups")


@router.get("/users")
def list_user_lookups(
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict:
    """Return only identity fields needed by overview and event filters."""
    users = db.query(UserDb.id, UserDb.name).order_by(UserDb.name.asc()).all()
    return {"users": [{"id": str(user_id), "name": name} for user_id, name in users]}


@router.get("/models")
def list_model_lookups(_: str = Depends(security.require_admin)) -> dict:  # noqa: B008
    """Return the configured model catalog used by analytics filters."""
    return {"models": list(configured_model_ids())}
