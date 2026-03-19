"""add_notification_data_column

Revision ID: f1a2b3c4d5e6
Revises: e6a7b8c9d0e1
Create Date: 2026-03-18 17:28:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("data", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("notifications", "data")
