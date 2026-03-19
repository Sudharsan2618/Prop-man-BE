"""LuxeLife API — Property onboarding workflow model."""

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, TimestampMixin, generate_cuid


class OnboardingWorkflowState(str, enum.Enum):
    VISIT_BOOKED = "visit_booked"
    VISIT_APPROVED = "visit_approved"
    VISIT_REJECTED = "visit_rejected"
    AGREEMENT_GENERATED = "agreement_generated"
    TENANT_SIGNED = "tenant_signed"
    ADVANCE_SUBMITTED = "advance_submitted"
    ADVANCE_APPROVED = "advance_approved"
    POLICE_VERIFICATION_COMPLETED = "police_verification_completed"
    ORIGINAL_AGREEMENT_UPLOADED = "original_agreement_uploaded"
    TENANT_ACTIVATED = "tenant_activated"


class ChecklistApprovalStatus(str, enum.Enum):
    NOT_SUBMITTED = "not_submitted"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"


class PropertyOnboardingWorkflow(Base, TimestampMixin):
    """Tracks lifecycle checkpoints for tenant onboarding on a property."""

    __tablename__ = "property_onboarding_workflows"
    __table_args__ = (
        Index(
            "ix_property_onboarding_workflows_owner_state_created_at",
            "owner_id",
            "state",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(30), primary_key=True, default=generate_cuid)
    state: Mapped[OnboardingWorkflowState] = mapped_column(
        Enum(OnboardingWorkflowState, name="onboarding_workflow_state_enum", create_constraint=True),
        default=OnboardingWorkflowState.VISIT_BOOKED,
        index=True,
    )

    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    agreement_id: Mapped[str | None] = mapped_column(ForeignKey("agreements.id", ondelete="SET NULL"), nullable=True)
    slot_id: Mapped[str | None] = mapped_column(ForeignKey("admin_slots.id", ondelete="SET NULL"), nullable=True)

    visit_booked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    visit_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    visit_rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    agreement_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tenant_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    advance_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    advance_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    police_verification_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    original_agreement_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tenant_activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    police_verification_doc_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    police_verification_status: Mapped[ChecklistApprovalStatus] = mapped_column(
        Enum(ChecklistApprovalStatus, name="checklist_approval_status_enum", create_constraint=True),
        default=ChecklistApprovalStatus.NOT_SUBMITTED,
    )
    police_verification_reviewed_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    police_verification_rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    original_agreement_doc_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    original_agreement_status: Mapped[ChecklistApprovalStatus] = mapped_column(
        Enum(ChecklistApprovalStatus, name="checklist_approval_status_enum", create_constraint=True),
        default=ChecklistApprovalStatus.NOT_SUBMITTED,
    )
    original_agreement_reviewed_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    original_agreement_rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    last_action_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    last_action_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
