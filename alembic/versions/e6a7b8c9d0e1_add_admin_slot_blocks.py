"""add_admin_slot_blocks

Revision ID: e6a7b8c9d0e1
Revises: d3f4a5b6c7d8
Create Date: 2026-03-18 17:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e6a7b8c9d0e1"
down_revision: Union[str, None] = "d3f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_slot_blocks",
        sa.Column("id", sa.String(length=30), nullable=False),
        sa.Column("admin_id", sa.String(length=30), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Kolkata"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["admin_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_admin_slot_blocks_admin_id"), "admin_slot_blocks", ["admin_id"], unique=False)
    op.create_index(op.f("ix_admin_slot_blocks_effective_from"), "admin_slot_blocks", ["effective_from"], unique=False)
    op.create_index(op.f("ix_admin_slot_blocks_effective_to"), "admin_slot_blocks", ["effective_to"], unique=False)
    op.create_index("ix_admin_slot_blocks_admin_date_range", "admin_slot_blocks", ["admin_id", "effective_from", "effective_to"], unique=False)
    op.create_index("ix_admin_slot_blocks_admin_time_range", "admin_slot_blocks", ["admin_id", "start_time", "end_time"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_admin_slot_blocks_admin_time_range", table_name="admin_slot_blocks")
    op.drop_index("ix_admin_slot_blocks_admin_date_range", table_name="admin_slot_blocks")
    op.drop_index(op.f("ix_admin_slot_blocks_effective_to"), table_name="admin_slot_blocks")
    op.drop_index(op.f("ix_admin_slot_blocks_effective_from"), table_name="admin_slot_blocks")
    op.drop_index(op.f("ix_admin_slot_blocks_admin_id"), table_name="admin_slot_blocks")
    op.drop_table("admin_slot_blocks")
