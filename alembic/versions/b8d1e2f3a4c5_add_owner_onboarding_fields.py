"""add_owner_onboarding_fields

Revision ID: b8d1e2f3a4c5
Revises: f4c2a9b1d3e4
Create Date: 2026-03-18 15:28:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b8d1e2f3a4c5"
down_revision: Union[str, None] = "f4c2a9b1d3e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    onboarding_status_enum = sa.Enum(
        "CREATED",
        "ENROLLED",
        name="onboarding_status_enum",
        create_constraint=True,
    )
    onboarding_status_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "users",
        sa.Column(
            "onboarding_status",
            onboarding_status_enum,
            nullable=False,
            server_default="ENROLLED",
        ),
    )
    op.add_column(
        "users",
        sa.Column("must_reset_password", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("users", sa.Column("invited_by_admin_id", sa.String(length=30), nullable=True))
    op.add_column("users", sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=True))

    op.alter_column("users", "phone", existing_type=sa.String(length=20), nullable=True)
    op.create_foreign_key(
        "fk_users_invited_by_admin_id",
        "users",
        "users",
        ["invited_by_admin_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_users_onboarding_status"), "users", ["onboarding_status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_users_onboarding_status"), table_name="users")
    op.drop_constraint("fk_users_invited_by_admin_id", "users", type_="foreignkey")
    op.alter_column("users", "phone", existing_type=sa.String(length=20), nullable=False)

    op.drop_column("users", "enrolled_at")
    op.drop_column("users", "invited_at")
    op.drop_column("users", "invited_by_admin_id")
    op.drop_column("users", "must_reset_password")
    op.drop_column("users", "onboarding_status")

    onboarding_status_enum = sa.Enum(
        "CREATED",
        "ENROLLED",
        name="onboarding_status_enum",
        create_constraint=True,
    )
    onboarding_status_enum.drop(op.get_bind(), checkfirst=True)
