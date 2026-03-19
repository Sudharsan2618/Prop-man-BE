"""merge dashboard and notification heads

Revision ID: c7d8e9f0a1b2
Revises: b9f6d7e8a1c2, f1a2b3c4d5e6
Create Date: 2026-03-19 02:16:00.000000

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = ("b9f6d7e8a1c2", "f1a2b3c4d5e6")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Merge revision: no schema changes.
    pass


def downgrade() -> None:
    # Merge revision: no schema changes.
    pass
