"""Add database-backed dashboard members for fine-grained permissions."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, Sequence[str], None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dashboard_members",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("username", sa.VARCHAR(length=120), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("permissions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_dashboard_members_id"),
        sa.UniqueConstraint("username", name="uq_dashboard_members_username"),
    )
    op.create_index("ix_dashboard_members_active", "dashboard_members", ["active"])


def downgrade() -> None:
    op.drop_index("ix_dashboard_members_active", table_name="dashboard_members")
    op.drop_table("dashboard_members")
