"""Configured model catalog exposed by the proxy.

Model discovery must be deterministic and local: querying every pooled account
made each Codex CLI startup wait several seconds and amplified provider load.
The default lineup is kept here for backwards-compatible deployments, while
``ALLOWED_MODELS`` in ``.env`` can override it without rebuilding the image.
"""

from collections.abc import Iterable

from app.config import get_settings

DEFAULT_MODEL_IDS: tuple[str, ...] = (
    "gpt-6-astra",
    "gpt-6-sol",
    "gpt-6-luna",
    "codex-auto-review",
)

# Backwards-compatible import for integrations that used the old constant.
MODEL_IDS = DEFAULT_MODEL_IDS


def configured_model_ids(raw: str | None = None) -> tuple[str, ...]:
    """Return the normalized allowlist from ``ALLOWED_MODELS`` or defaults."""
    value = get_settings().ALLOWED_MODELS if raw is None else raw
    models = tuple(
        dict.fromkeys(item.strip().lower() for item in value.split(",") if item.strip() and not item.strip().lower().startswith("gpt-5.6"))
    )
    return models or DEFAULT_MODEL_IDS


def codex_catalog(model_ids: Iterable[str] | None = None) -> dict[str, list[dict[str, str]]]:
    """Return a fresh Codex-shaped catalog so callers can safely filter it."""
    ids = configured_model_ids() if model_ids is None else tuple(model_ids)
    return {"models": [{"slug": model_id} for model_id in ids]}
