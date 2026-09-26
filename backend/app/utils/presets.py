"""Materialized preset policies; per-user columns remain the routing source of truth."""

import json

POLICY_COLUMNS = {
    "allowed_models": "allowed_models_json",
    "allowed_reasoning_levels": "allowed_reasoning_levels_json",
    "allowed_request_modes": "allowed_request_modes_json",
    "model_overrides": "model_overrides_json",
    "model_reasoning_levels": "model_reasoning_levels_json",
    "model_request_modes": "model_request_modes_json",
}


def overridden_fields(user) -> set[str]:
    try:
        values = json.loads(user.preset_overrides_json or "[]")
    except (TypeError, ValueError):
        return set()
    return set(values) & POLICY_COLUMNS.keys() if isinstance(values, list) else set()


def set_overridden_fields(user, fields: set[str]) -> None:
    user.preset_overrides_json = json.dumps(sorted(fields))


def apply_preset(user, preset, *, preserve_overrides: bool = True) -> None:
    overrides = overridden_fields(user) if preserve_overrides else set()
    for field, column in POLICY_COLUMNS.items():
        if field not in overrides:
            setattr(user, column, getattr(preset, column))
    user.preset_id = preset.id
    set_overridden_fields(user, overrides)
