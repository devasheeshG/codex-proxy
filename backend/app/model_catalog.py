"""Fixed model catalog exposed by the proxy.

Model discovery must be deterministic and local: querying every pooled account
made each Codex CLI startup wait several seconds and amplified provider load.
Update this tuple deliberately when the supported GPT 5.6/6 lineup changes.
`codex-auto-review` is the internal Codex review model used by browser tooling;
it must remain discoverable even though it is not part of the public GPT lineup.
"""

MODEL_IDS: tuple[str, ...] = (
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-6-astra",
    "codex-auto-review",
)


def codex_catalog() -> dict[str, list[dict[str, str]]]:
    """Return a fresh Codex-shaped catalog so callers can safely filter it."""
    return {"models": [{"slug": model_id} for model_id in MODEL_IDS]}
