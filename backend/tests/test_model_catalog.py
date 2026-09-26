from app.config import get_settings
from app.model_catalog import DEFAULT_MODEL_IDS, configured_model_ids


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
