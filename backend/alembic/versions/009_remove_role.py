"""Remove the unused dashboard role column."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, Sequence[str], None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "role" in {column["name"] for column in inspector.get_columns("dashboard_members")}:
        op.drop_column("dashboard_members", "role")


def downgrade() -> None:
    op.add_column(
        "dashboard_members",
        sa.Column("role", sa.VARCHAR(length=64), nullable=False, server_default="member"),
    )
    op.alter_column("dashboard_members", "role", server_default=None)
