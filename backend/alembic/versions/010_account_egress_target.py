"""Add the optional per-account egress target binding."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, Sequence[str], None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "egress_target_id" not in {column["name"] for column in inspector.get_columns("accounts")}:
        op.add_column("accounts", sa.Column("egress_target_id", sa.VARCHAR(length=128), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "egress_target_id" in {column["name"] for column in inspector.get_columns("accounts")}:
        op.drop_column("accounts", "egress_target_id")
