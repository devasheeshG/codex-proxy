"""Remove the unused dashboard display-name column."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, Sequence[str], None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "display_name" in {column["name"] for column in inspector.get_columns("dashboard_members")}:
        op.drop_column("dashboard_members", "display_name")


def downgrade() -> None:
    op.add_column(
        "dashboard_members",
        sa.Column("display_name", sa.VARCHAR(length=200), nullable=False, server_default=""),
    )
    op.alter_column("dashboard_members", "display_name", server_default=None)
