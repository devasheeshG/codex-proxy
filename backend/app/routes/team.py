"""Dashboard team-member management routes."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.utils import security
from app.utils.models.api import (
    CreateDashboardMemberRequest,
    DashboardMember,
    DashboardMemberResponse,
    ListDashboardMembersResponse,
    UpdateDashboardMemberRequest,
)
from app.utils.postgres import DashboardMemberDb, get_db

router = APIRouter(tags=["Dashboard Team"], prefix="/team")


def _member_response(member: DashboardMemberDb) -> DashboardMember:
    try:
        permissions = sorted(json.loads(member.permissions_json or "[]"))
    except (TypeError, ValueError):
        permissions = []
    return DashboardMember(
        id=member.id,
        username=member.username,
        display_name=member.display_name,
        role=member.role,
        permissions=permissions,
        active=member.active,
        created_at=member.created_at,
        last_login_at=member.last_login_at,
    )


def _validate_role(role: str) -> frozenset[str]:
    if role == "owner" or role not in security.ROLE_PERMISSIONS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown or reserved role")
    return security.permissions_for_role(role)


def _require_role_assignment(principal) -> None:
    if "*" not in principal.permissions and "team:roles:write" not in principal.permissions:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing permission: team:roles:write")


@router.get("/roles")
def list_roles(_: object = Depends(security.require_admin_principal)) -> dict:  # noqa: B008
    return {
        "roles": [
            {"id": role, "label": security.ROLE_LABELS[role], "permissions": sorted(permissions)}
            for role, permissions in security.ROLE_PERMISSIONS.items()
            if role != "owner"
        ],
        "permissions": sorted(security.PERMISSIONS),
    }


@router.get("/members", response_model=ListDashboardMembersResponse)
def list_members(
    _: object = Depends(security.require_admin_principal),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ListDashboardMembersResponse:
    members = db.query(DashboardMemberDb).order_by(DashboardMemberDb.created_at.asc()).all()
    return ListDashboardMembersResponse(members=[_member_response(member) for member in members])


@router.post("/members", response_model=DashboardMemberResponse, status_code=status.HTTP_201_CREATED)
def create_member(
    request: CreateDashboardMemberRequest,
    principal=Depends(security.require_admin_principal),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> DashboardMemberResponse:
    _require_role_assignment(principal)
    role_permissions = _validate_role(request.role)
    username = request.username.strip().lower()
    display_name = request.display_name.strip()
    if username == get_settings().ADMIN_USERNAME.strip().lower():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Dashboard username is reserved")
    if not display_name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Display name is required")
    if db.query(DashboardMemberDb).filter(DashboardMemberDb.username == username).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Dashboard username already exists")
    now = datetime.now(timezone.utc)
    member = DashboardMemberDb(
        id=uuid.uuid4(),
        username=username,
        display_name=display_name,
        password_hash=security.hash_password(request.password),
        role=request.role,
        permissions_json=json.dumps(sorted(role_permissions), separators=(",", ":")),
        active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(member)
    db.commit()
    return DashboardMemberResponse(member=_member_response(member))


@router.put("/members/{member_id}", response_model=DashboardMemberResponse)
def update_member(
    member_id: uuid.UUID,
    request: UpdateDashboardMemberRequest,
    principal=Depends(security.require_admin_principal),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> DashboardMemberResponse:
    member = db.get(DashboardMemberDb, member_id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard member not found")
    if request.role is not None:
        _require_role_assignment(principal)
        role_permissions = _validate_role(request.role)
        member.role = request.role
        member.permissions_json = json.dumps(sorted(role_permissions), separators=(",", ":"))
    if request.display_name is not None:
        display_name = request.display_name.strip()
        if not display_name:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Display name is required")
        member.display_name = display_name
    if request.password is not None:
        member.password_hash = security.hash_password(request.password)
    if request.active is not None:
        if principal.member_id == member.id and request.active is False:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="You cannot disable your own account")
        member.active = request.active
    member.updated_at = datetime.now(timezone.utc)
    db.commit()
    return DashboardMemberResponse(member=_member_response(member))


@router.delete("/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_member(
    member_id: uuid.UUID,
    principal=Depends(security.require_admin_principal),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> None:
    if principal.member_id == member_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="You cannot delete your own account")
    member = db.get(DashboardMemberDb, member_id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard member not found")
    db.delete(member)
    db.commit()
