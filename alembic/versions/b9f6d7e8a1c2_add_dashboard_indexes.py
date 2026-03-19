"""add_dashboard_indexes

Revision ID: b9f6d7e8a1c2
Revises: f4c2a9b1d3e4
Create Date: 2026-03-19 01:20:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b9f6d7e8a1c2"
down_revision: Union[str, None] = "f4c2a9b1d3e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_payments_owner_status_created_at ON payments (owner_id, status, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_payments_tenant_status_due_date ON payments (tenant_id, status, due_date)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_jobs_provider_status_created_at ON jobs (provider_id, status, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_property_onboarding_workflows_owner_state_created_at ON property_onboarding_workflows (owner_id, state, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notifications_user_created_at ON notifications (user_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_notifications_user_created_at")
    op.execute("DROP INDEX IF EXISTS ix_property_onboarding_workflows_owner_state_created_at")
    op.execute("DROP INDEX IF EXISTS ix_jobs_provider_status_created_at")
    op.execute("DROP INDEX IF EXISTS ix_payments_tenant_status_due_date")
    op.execute("DROP INDEX IF EXISTS ix_payments_owner_status_created_at")
