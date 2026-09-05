"""Guarded live-schema reconciliation tests for the squashed canonical migration."""

from sqlalchemy import inspect, text

from app.scripts import migrate
from app.utils.postgres.base import engine


def test_sync_canonical_schema_adds_post_squash_columns_to_existing_tables():
    user_policy_columns = {
        "lifetime_token_budget",
        "monthly_spend_budget_usd",
        "lifetime_spend_budget_usd",
        "model_overrides_json",
    }
    quota_columns = {
        "five_hour_used_pct",
        "five_hour_reset_at",
        "weekly_used_pct",
        "weekly_reset_at",
        "monthly_used_pct",
        "monthly_reset_at",
        "five_hour_rotation_threshold",
        "weekly_rotation_threshold",
        "authenticated_override",
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, name, active, allowed_request_modes_json, created_at) "
                "VALUES ('00000000-0000-0000-0000-0000000000fa', 'legacy-all-modes', TRUE, "
                '\'["standard","fast"]\', NOW())'
            )
        )
        connection.execute(text("ALTER TABLE users DROP COLUMN allowed_models_json"))
        for column_name in user_policy_columns:
            connection.execute(text(f"ALTER TABLE users DROP COLUMN {column_name}"))
        for column_name in quota_columns:
            connection.execute(text(f"ALTER TABLE accounts DROP COLUMN {column_name}"))
        connection.execute(text("ALTER TABLE usage_records DROP COLUMN fallback_provider_id"))
        connection.execute(text("ALTER TABLE usage_records DROP COLUMN billed_cost_usd"))
        connection.execute(text("DROP TABLE openai_fallbacks"))
    assert "allowed_models_json" not in {column["name"] for column in inspect(engine).get_columns("users")}
    assert user_policy_columns.isdisjoint({column["name"] for column in inspect(engine).get_columns("users")})
    assert quota_columns.isdisjoint({column["name"] for column in inspect(engine).get_columns("accounts")})
    assert not inspect(engine).has_table("openai_fallbacks")

    migrate.sync_canonical_schema()

    user_columns = {column["name"] for column in inspect(engine).get_columns("users")}
    account_columns = {column["name"] for column in inspect(engine).get_columns("accounts")}
    usage_columns = {column["name"] for column in inspect(engine).get_columns("usage_records")}
    assert "allowed_models_json" in user_columns
    assert user_policy_columns <= user_columns
    assert quota_columns <= account_columns
    assert "authenticated_override" in account_columns
    assert inspect(engine).has_table("openai_fallbacks")
    assert {"fallback_provider_id", "billed_cost_usd"} <= usage_columns
    with engine.begin() as connection:
        modes = connection.execute(text("SELECT allowed_request_modes_json FROM users WHERE name = 'legacy-all-modes'")).scalar_one()
    assert modes == '["standard","fast","ultrafast"]'
