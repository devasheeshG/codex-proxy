# Path: app/scripts/migrate.py
# Description: Normalize the former schema-equivalent Alembic head, then upgrade to the canonical head.

import json

from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command
from app.utils.postgres import OpenAIFallbackDb, ProxyEventDb
from app.utils.postgres.base import engine, init_database

CANONICAL_REVISION = "001"
LEGACY_EQUIVALENT_HEADS = frozenset({"0009", "002", "003", "004", "005", "006"})
KNOWN_CHAIN_REVISIONS = frozenset({"001"})


def normalize_legacy_head() -> None:
    """Stamp known pre-squash heads to the single canonical revision."""
    init_database()
    with engine.begin() as connection:
        if not inspect(connection).has_table("alembic_version"):
            return
        revisions = connection.execute(text("SELECT version_num FROM alembic_version FOR UPDATE")).scalars().all()
        if len(revisions) == 1 and revisions[0] in KNOWN_CHAIN_REVISIONS:
            return
        if len(revisions) != 1 or revisions[0] not in LEGACY_EQUIVALENT_HEADS:
            rendered = ", ".join(revisions) if revisions else "empty"
            raise RuntimeError(
                f"Unsupported Alembic state ({rendered}); expected one of "
                f"{sorted(KNOWN_CHAIN_REVISIONS)} or one of {sorted(LEGACY_EQUIVALENT_HEADS)}."
            )
        connection.execute(
            text("UPDATE alembic_version SET version_num = :canonical WHERE version_num = :legacy"),
            {"canonical": CANONICAL_REVISION, "legacy": revisions[0]},
        )


def sync_canonical_schema() -> None:
    """Apply additive schema fields introduced after the history was squashed to revision 001."""
    with engine.begin() as connection:
        OpenAIFallbackDb.__table__.create(connection, checkfirst=True)
        inspector = inspect(connection)
        if not inspector.has_table("users"):
            return
        user_columns = {column["name"] for column in inspector.get_columns("users")}
        if "allowed_models_json" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN allowed_models_json TEXT"))
        user_policy_columns = {
            "priority": "INTEGER NOT NULL DEFAULT 1",
            "fallback_enabled": "BOOLEAN NOT NULL DEFAULT FALSE",
            "lifetime_token_budget": "BIGINT",
            "monthly_spend_budget_usd": "DOUBLE PRECISION",
            "lifetime_spend_budget_usd": "DOUBLE PRECISION",
            "model_overrides_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for column_name, column_type in user_policy_columns.items():
            if column_name not in user_columns:
                connection.execute(text(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}"))
        # Revision 006 granted the built-in auto-review model to every
        # restricted user. Preserve that data migration for databases that
        # were stamped from an earlier historical head before the squash.
        for row in connection.execute(text("SELECT id, allowed_models_json FROM users WHERE allowed_models_json IS NOT NULL")).mappings():
            try:
                values = json.loads(row["allowed_models_json"])
            except (TypeError, ValueError):
                continue
            if not isinstance(values, list):
                continue
            normalized = {str(value).strip().lower() for value in values if isinstance(value, str) and value.strip()}
            if "codex-auto-review" not in normalized:
                normalized.add("codex-auto-review")
                connection.execute(
                    text("UPDATE users SET allowed_models_json = :models WHERE id = :user_id"),
                    {"models": json.dumps(sorted(normalized), separators=(",", ":")), "user_id": row["id"]},
                )
        # Before UltraFast existed, selecting both available modes represented
        # unrestricted request-mode access. Preserve that intent for existing
        # users while leaving every narrower policy unchanged.
        connection.execute(
            text(
                "UPDATE users SET allowed_request_modes_json = "
                '\'["standard","fast","ultrafast"]\' '
                'WHERE allowed_request_modes_json::jsonb = \'["standard","fast"]\'::jsonb'
            )
        )
        connection.execute(text('ALTER TABLE users ALTER COLUMN allowed_request_modes_json SET DEFAULT \'["standard","fast","ultrafast"]\''))

        # Existing installations are already stamped at the squashed 001 head,
        # so additive fields introduced after the squash must be reconciled
        # here. The former monthly/session columns are intentionally left in
        # place on upgraded databases: their stored window identity is
        # ambiguous, and a successful quota probe safely populates these new
        # explicit fields without copying potentially incorrect data.
        account_columns = {column["name"] for column in inspector.get_columns("accounts")}
        quota_columns = {
            "five_hour_used_pct": "DOUBLE PRECISION",
            "five_hour_reset_at": "TIMESTAMP WITH TIME ZONE",
            "weekly_used_pct": "DOUBLE PRECISION",
            "weekly_reset_at": "TIMESTAMP WITH TIME ZONE",
            "monthly_used_pct": "DOUBLE PRECISION",
            "monthly_reset_at": "TIMESTAMP WITH TIME ZONE",
        }
        for column_name, column_type in quota_columns.items():
            if column_name not in account_columns:
                connection.execute(text(f"ALTER TABLE accounts ADD COLUMN {column_name} {column_type}"))

        threshold_columns = {
            "five_hour_rotation_threshold": "DOUBLE PRECISION",
            "weekly_rotation_threshold": "DOUBLE PRECISION",
        }
        for column_name, column_type in threshold_columns.items():
            if column_name not in account_columns:
                connection.execute(text(f"ALTER TABLE accounts ADD COLUMN {column_name} {column_type} NOT NULL DEFAULT 1.0"))
                connection.execute(text(f"UPDATE accounts SET {column_name} = rotation_threshold WHERE rotation_threshold IS NOT NULL"))
        if "authenticated_override" not in account_columns:
            connection.execute(text("ALTER TABLE accounts ADD COLUMN authenticated_override BOOLEAN NOT NULL DEFAULT FALSE"))
        warmup_columns = {
            "warmup_enabled": "BOOLEAN NOT NULL DEFAULT TRUE",
            "warmup_next_at": "TIMESTAMP WITH TIME ZONE",
            "warmup_last_at": "TIMESTAMP WITH TIME ZONE",
            "warmup_last_status": "VARCHAR(32)",
            "warmup_last_error": "TEXT",
        }
        for column_name, column_type in warmup_columns.items():
            if column_name not in account_columns:
                connection.execute(text(f"ALTER TABLE accounts ADD COLUMN {column_name} {column_type}"))

        inspector = inspect(connection)
        usage_columns = {column["name"] for column in inspector.get_columns("usage_records")}
        if "fallback_provider_id" not in usage_columns:
            connection.execute(
                text("ALTER TABLE usage_records ADD COLUMN fallback_provider_id UUID REFERENCES openai_fallbacks(id) ON DELETE SET NULL")
            )
        if "billed_cost_usd" not in usage_columns:
            connection.execute(text("ALTER TABLE usage_records ADD COLUMN billed_cost_usd DOUBLE PRECISION"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_usage_records_fallback_provider_id ON usage_records (fallback_provider_id)"))

        events_existed = inspect(connection).has_table("proxy_events")
        ProxyEventDb.__table__.create(connection, checkfirst=True)
        if not events_existed:
            connection.execute(
                text(
                    """
                    INSERT INTO proxy_events
                        (id, created_at, request_id, user_id, api_key_id, account_id,
                         fallback_provider_id, event_type, status_code, message, metadata_json)
                    SELECT gen_random_uuid(), u.created_at,
                           COALESCE(NULLIF(u.request_id, ''), 'req_legacy_' || replace(u.id::text, '-', '')),
                           u.user_id, u.api_key_id, u.account_id, u.fallback_provider_id,
                           'response.returned', u.status_code, 'Migrated from request history',
                           json_build_object('model', u.model, 'reasoning_level', u.reasoning_level,
                             'request_mode', u.request_mode, 'input_tokens', u.input_tokens,
                             'output_tokens', u.output_tokens, 'cached_input_tokens', u.cached_input_tokens,
                             'cache_write_tokens', u.cache_write_tokens, 'cost_usd', u.billed_cost_usd)::text
                    FROM usage_records u
                    WHERE NOT EXISTS (
                        SELECT 1 FROM proxy_events e
                        WHERE e.event_type = 'response.returned'
                          AND e.request_id = COALESCE(NULLIF(u.request_id, ''), 'req_legacy_' || replace(u.id::text, '-', ''))
                    )
                    """
                )
            )


def main() -> None:
    normalize_legacy_head()
    command.upgrade(Config("alembic.ini"), "head")
    sync_canonical_schema()


if __name__ == "__main__":
    main()
