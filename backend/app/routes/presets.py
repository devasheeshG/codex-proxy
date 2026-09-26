"""Admin CRUD for reusable user model/thinking policies."""

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.utils import presets, request_policy, security
from app.utils.models.api.users import _normalize_model_overrides, _normalize_model_values
from app.utils.postgres import PresetDb, UserDb, get_db

router = APIRouter(tags=["Presets"], prefix="/presets")


def _matrix(value, allowed):
    if not isinstance(value, dict):
        return value
    result = {}
    for model, choices in value.items():
        normalized = _normalize_model_values([model])
        if not isinstance(choices, list) or not choices or any(choice not in allowed for choice in choices):
            raise ValueError(f"Invalid choices for model {model}")
        result[normalized[0]] = list(dict.fromkeys(choices))
    return result


class PresetPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    allowed_models: list[str] | None = Field(default=None, min_length=1)
    allowed_reasoning_levels: list[request_policy.ReasoningLevel] = Field(
        default_factory=lambda: list(request_policy.ALL_REASONING_LEVELS), min_length=1
    )
    allowed_request_modes: list[request_policy.RequestMode] = Field(default_factory=lambda: list(request_policy.ALL_REQUEST_MODES), min_length=1)
    model_overrides: dict[str, str] = Field(default_factory=dict)
    model_reasoning_levels: dict[str, list[request_policy.ReasoningLevel]] = Field(default_factory=dict)
    model_request_modes: dict[str, list[request_policy.RequestMode]] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def check_name(cls, value):
        if not value.strip():
            raise ValueError("Preset name is required")
        return value.strip()

    @field_validator("allowed_models", mode="before")
    @classmethod
    def check_models(cls, value):
        return _normalize_model_values(value)

    @field_validator("model_overrides", mode="before")
    @classmethod
    def check_redirects(cls, value):
        return _normalize_model_overrides(value)

    @field_validator("model_reasoning_levels", mode="before")
    @classmethod
    def check_reasoning_matrix(cls, value):
        return _matrix(value, request_policy.ALL_REASONING_LEVELS)

    @field_validator("model_request_modes", mode="before")
    @classmethod
    def check_mode_matrix(cls, value):
        return _matrix(value, request_policy.ALL_REQUEST_MODES)


def _data(preset):
    return {
        "id": preset.id,
        "name": preset.name,
        "allowed_models": request_policy.decode_models(preset.allowed_models_json),
        "allowed_reasoning_levels": request_policy.decode_choices(preset.allowed_reasoning_levels_json, request_policy.ALL_REASONING_LEVELS),
        "allowed_request_modes": request_policy.decode_choices(preset.allowed_request_modes_json, request_policy.ALL_REQUEST_MODES),
        "model_overrides": request_policy.decode_model_overrides(preset.model_overrides_json),
        "model_reasoning_levels": json.loads(preset.model_reasoning_levels_json or "{}"),
        "model_request_modes": json.loads(preset.model_request_modes_json or "{}"),
    }


def _write(preset, payload):
    preset.name = payload.name.strip()
    preset.allowed_models_json = request_policy.encode_models(payload.allowed_models) if payload.allowed_models is not None else None
    preset.allowed_reasoning_levels_json = request_policy.encode_choices(payload.allowed_reasoning_levels, request_policy.ALL_REASONING_LEVELS)
    preset.allowed_request_modes_json = request_policy.encode_choices(payload.allowed_request_modes, request_policy.ALL_REQUEST_MODES)
    preset.model_overrides_json = request_policy.encode_model_overrides(payload.model_overrides)
    preset.model_reasoning_levels_json = json.dumps(payload.model_reasoning_levels, separators=(",", ":"))
    preset.model_request_modes_json = json.dumps(payload.model_request_modes, separators=(",", ":"))


@router.get("")
def list_presets(_: str = Depends(security.require_admin), db: Session = Depends(get_db)):
    rows = db.query(PresetDb).order_by(PresetDb.created_at, PresetDb.name).all()
    counts = dict(db.query(UserDb.preset_id, func.count(UserDb.id)).group_by(UserDb.preset_id).all())
    return {"presets": [{**_data(row), "user_count": counts.get(row.id, 0)} for row in rows]}


@router.post("", status_code=201)
def create_preset(payload: PresetPayload, _: str = Depends(security.require_admin), db: Session = Depends(get_db)):
    if db.query(PresetDb).filter(PresetDb.name == payload.name.strip()).first():
        raise HTTPException(409, "Preset name already exists")
    row = PresetDb(id=uuid.uuid4(), created_at=datetime.now(timezone.utc))
    _write(row, payload)
    db.add(row)
    db.commit()
    return _data(row)


@router.put("/{preset_id}")
def update_preset(preset_id: uuid.UUID, payload: PresetPayload, _: str = Depends(security.require_admin), db: Session = Depends(get_db)):
    row = db.query(PresetDb).filter(PresetDb.id == preset_id).with_for_update().first()
    if row is None:
        raise HTTPException(404, "Preset not found")
    collision = db.query(PresetDb).filter(PresetDb.name == payload.name.strip(), PresetDb.id != preset_id).first()
    if collision:
        raise HTTPException(409, "Preset name already exists")
    _write(row, payload)
    for user in db.query(UserDb).filter(UserDb.preset_id == preset_id).with_for_update():
        presets.apply_preset(user, row)
    db.commit()
    return _data(row)


@router.delete("/{preset_id}", status_code=204)
def delete_preset(preset_id: uuid.UUID, _: str = Depends(security.require_admin), db: Session = Depends(get_db)):
    row = db.query(PresetDb).filter(PresetDb.id == preset_id).first()
    if row is None:
        raise HTTPException(404, "Preset not found")
    if db.query(UserDb).filter(UserDb.preset_id == preset_id).first():
        raise HTTPException(409, "Reassign users before deleting this preset")
    db.delete(row)
    db.commit()
