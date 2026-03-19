"""add_property_onboarding_workflow

Revision ID: d3f4a5b6c7d8
Revises: b8d1e2f3a4c5
Create Date: 2026-03-18 15:33:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d3f4a5b6c7d8"
down_revision: Union[str, None] = "b8d1e2f3a4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    checklist_status_enum = sa.Enum(
        "NOT_SUBMITTED",
        "SUBMITTED",
        "APPROVED",
        "REJECTED",
        name="checklist_approval_status_enum",
        create_constraint=True,
    )

    op.create_table(
        "property_onboarding_workflows",
        sa.Column("id", sa.String(length=30), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "VISIT_BOOKED",
                "VISIT_APPROVED",
                "VISIT_REJECTED",
                "AGREEMENT_GENERATED",
                "TENANT_SIGNED",
                "ADVANCE_SUBMITTED",
                "ADVANCE_APPROVED",
                "POLICE_VERIFICATION_COMPLETED",
                "ORIGINAL_AGREEMENT_UPLOADED",
                "TENANT_ACTIVATED",
                name="onboarding_workflow_state_enum",
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("property_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.String(length=30), nullable=False),
        sa.Column("owner_id", sa.String(length=30), nullable=False),
        sa.Column("agreement_id", sa.String(length=30), nullable=True),
        sa.Column("slot_id", sa.String(length=30), nullable=True),
        sa.Column("visit_booked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("visit_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("visit_rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("agreement_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("advance_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("advance_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("police_verification_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("original_agreement_uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("police_verification_doc_url", sa.String(length=512), nullable=True),
        sa.Column("police_verification_status", checklist_status_enum, nullable=False),
        sa.Column("police_verification_reviewed_by", sa.String(length=30), nullable=True),
        sa.Column("police_verification_rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("original_agreement_doc_url", sa.String(length=512), nullable=True),
        sa.Column("original_agreement_status", checklist_status_enum, nullable=False),
        sa.Column("original_agreement_reviewed_by", sa.String(length=30), nullable=True),
        sa.Column("original_agreement_rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("last_action_by", sa.String(length=30), nullable=True),
        sa.Column("last_action_notes", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["agreement_id"], ["agreements.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["last_action_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["original_agreement_reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["police_verification_reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["slot_id"], ["admin_slots.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_property_onboarding_workflows_state"), "property_onboarding_workflows", ["state"], unique=False)
    op.create_index(op.f("ix_property_onboarding_workflows_property_id"), "property_onboarding_workflows", ["property_id"], unique=False)
    op.create_index(op.f("ix_property_onboarding_workflows_tenant_id"), "property_onboarding_workflows", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_property_onboarding_workflows_owner_id"), "property_onboarding_workflows", ["owner_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_property_onboarding_workflows_owner_id"), table_name="property_onboarding_workflows")
    op.drop_index(op.f("ix_property_onboarding_workflows_tenant_id"), table_name="property_onboarding_workflows")
    op.drop_index(op.f("ix_property_onboarding_workflows_property_id"), table_name="property_onboarding_workflows")
    op.drop_index(op.f("ix_property_onboarding_workflows_state"), table_name="property_onboarding_workflows")
    op.drop_table("property_onboarding_workflows")
