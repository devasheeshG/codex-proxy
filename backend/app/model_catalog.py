"""Configured model catalog exposed by the proxy.

Model discovery must be deterministic and local: querying every pooled account
made each Codex CLI startup wait several seconds and amplified provider load.
The default lineup is kept here for backwards-compatible deployments, while
``ALLOWED_MODELS`` in ``.env`` can override it without rebuilding the image.
"""

from collections.abc import Iterable

from app.config import get_settings
from app.utils import request_policy

DEFAULT_MODEL_IDS: tuple[str, ...] = (
    "gpt-5.5",
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-6-astra",
    "gpt-6-sol",
    "gpt-6-luna",
    "gpt-6.1-sol",
    "codex-auto-review",
)

# Backwards-compatible import for integrations that used the old constant.
MODEL_IDS = DEFAULT_MODEL_IDS


def configured_model_ids(raw: str | None = None) -> tuple[str, ...]:
    """Return the normalized allowlist from ``ALLOWED_MODELS`` or defaults."""
    value = get_settings().ALLOWED_MODELS if raw is None else raw
    models = tuple(dict.fromkeys(item.strip().lower() for item in value.split(",") if item.strip()))
    return models or DEFAULT_MODEL_IDS


def codex_catalog(
    model_ids: Iterable[str] | None = None,
    *,
    allow_extended_context: bool = False,
) -> dict[str, list[dict[str, object]]]:
    """Return a per-user Codex catalog with an explicit context ceiling."""
    ids = configured_model_ids() if model_ids is None else tuple(model_ids)
    context_window = request_policy.EXTENDED_CONTEXT_WINDOW if allow_extended_context else request_policy.DEFAULT_CONTEXT_WINDOW
    models = []
    for model_id in ids:
        model = {
            "slug": model_id,
            "context_window": context_window,
            "max_context_window": context_window,
            "effective_context_window_percent": 95,
        }
        if allow_extended_context:
            model["auto_compact_token_limit"] = request_policy.EXTENDED_CONTEXT_AUTO_COMPACT_TOKEN_LIMIT
        models.append(model)
    return {"models": models}
