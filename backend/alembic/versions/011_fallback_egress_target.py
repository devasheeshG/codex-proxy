"""Add per-fallback egress target binding."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: Union[str, Sequence[str], None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "egress_target_id" not in {column["name"] for column in inspector.get_columns("openai_fallbacks")}:
        op.add_column("openai_fallbacks", sa.Column("egress_target_id", sa.VARCHAR(length=128), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "egress_target_id" in {column["name"] for column in inspector.get_columns("openai_fallbacks")}:
        op.drop_column("openai_fallbacks", "egress_target_id")
