# Path: app/utils/request_policy.py
# Description: Canonical request modes/reasoning levels and per-user policy serialization helpers.

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Dict, List, Literal, Optional, Tuple

RequestMode = Literal["standard", "fast", "ultrafast"]
ReasoningLevel = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]

ALL_REQUEST_MODES: Tuple[RequestMode, ...] = ("standard", "fast", "ultrafast")
ALL_REASONING_LEVELS: Tuple[ReasoningLevel, ...] = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)

DEFAULT_REQUEST_MODES_JSON = json.dumps(ALL_REQUEST_MODES, separators=(",", ":"))
DEFAULT_REASONING_LEVELS_JSON = json.dumps(ALL_REASONING_LEVELS, separators=(",", ":"))


def request_mode_from_request(body: Mapping) -> RequestMode:
    """Map Responses API service tiers to the modes exposed by Codex Proxy."""
    service_tier = body.get("service_tier")
    normalized = service_tier.strip().lower() if isinstance(service_tier, str) else ""
    if normalized == "ultrafast":
        return "ultrafast"
    if normalized in {"fast", "priority"}:
        return "fast"
    return "standard"


def encode_choices(values: Iterable[str], allowed: Tuple[str, ...]) -> str:
    """Persist validated choices in a stable order for predictable API responses."""
    selected = {value.strip().lower() for value in values}
    return json.dumps([value for value in allowed if value in selected], separators=(",", ":"))


def decode_choices(raw: Optional[str], allowed: Tuple[str, ...]) -> List[str]:
    """Read a stored policy, falling back to unrestricted access for legacy rows."""
    try:
        parsed = json.loads(raw) if raw is not None else None
    except (TypeError, ValueError):
        parsed = None
    if not isinstance(parsed, list):
        return list(allowed)
    selected = {value.strip().lower() for value in parsed if isinstance(value, str)}
    decoded = [value for value in allowed if value in selected]
    return decoded or list(allowed)


def normalize_model_ids(values: Iterable[str]) -> List[str]:
    """Return unique, canonical model IDs in a stable order."""
    return sorted({value.strip().lower() for value in values if value.strip()})


def encode_models(values: Iterable[str]) -> str:
    """Persist a restricted model allowlist; unrestricted access is stored as SQL NULL."""
    return json.dumps(normalize_model_ids(values), separators=(",", ":"))


def decode_models(raw: Optional[str]) -> Optional[List[str]]:
    """Read a model allowlist, treating legacy, invalid, and empty values as unrestricted."""
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, list):
        return None
    decoded = normalize_model_ids(value for value in parsed if isinstance(value, str))
    return decoded or None


def normalize_model_overrides(values: Mapping[str, str]) -> Dict[str, str]:
    """Return canonical requested-model -> upstream-model rewrites."""
    normalized: Dict[str, str] = {}
    for source, target in values.items():
        requested = source.strip().lower()
        upstream = target.strip().lower()
        if requested and upstream and requested != upstream:
            normalized[requested] = upstream
    return dict(sorted(normalized.items()))


def encode_model_overrides(values: Mapping[str, str]) -> str:
    return json.dumps(normalize_model_overrides(values), separators=(",", ":"))


def decode_model_overrides(raw: Optional[str]) -> Dict[str, str]:
    """Read model rewrites, falling back safely for legacy or invalid rows."""
    try:
        parsed = json.loads(raw) if raw is not None else {}
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return normalize_model_overrides({source: target for source, target in parsed.items() if isinstance(source, str) and isinstance(target, str)})
