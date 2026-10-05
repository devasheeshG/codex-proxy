from app.config import get_settings
from app.model_catalog import DEFAULT_MODEL_IDS, codex_catalog, configured_model_ids
from app.utils.request_policy import DEFAULT_CONTEXT_WINDOW, EXTENDED_CONTEXT_AUTO_COMPACT_TOKEN_LIMIT, EXTENDED_CONTEXT_WINDOW


def test_model_allowlist_reads_comma_separated_environment(monkeypatch):
    monkeypatch.setenv("ALLOWED_MODELS", " gpt-custom, gpt-custom, gpt-next ")
    get_settings.cache_clear()
    try:
        assert configured_model_ids() == ("gpt-custom", "gpt-next")
    finally:
        get_settings.cache_clear()


def test_blank_model_allowlist_keeps_safe_defaults():
    assert configured_model_ids("") == DEFAULT_MODEL_IDS


def test_gpt_5_6_is_configurable():
    assert configured_model_ids("gpt-5.6,gpt-5.6-sol,GPT-5.6-LUNA,gpt-6-sol") == (
        "gpt-5.6",
        "gpt-5.6-sol",
        "gpt-5.6-luna",
        "gpt-6-sol",
    )
    assert "gpt-5.6-sol" in DEFAULT_MODEL_IDS


def test_default_catalog_includes_verified_legacy_model_but_not_unavailable_model():
    assert "gpt-5.5" in DEFAULT_MODEL_IDS
    assert "gpt-6.1-sol" in DEFAULT_MODEL_IDS


def test_context_catalog_is_per_user_and_explicit():
    standard = codex_catalog(("gpt-6-sol",))
    extended = codex_catalog(("gpt-6-sol",), allow_extended_context=True)
    assert standard["models"][0]["context_window"] == DEFAULT_CONTEXT_WINDOW
    assert standard["models"][0]["max_context_window"] == DEFAULT_CONTEXT_WINDOW
    assert extended["models"][0]["context_window"] == EXTENDED_CONTEXT_WINDOW
    assert extended["models"][0]["max_context_window"] == EXTENDED_CONTEXT_WINDOW
    assert extended["models"][0]["auto_compact_token_limit"] == EXTENDED_CONTEXT_AUTO_COMPACT_TOKEN_LIMIT
