"""LuxeLife API — Property onboarding workflow model."""

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, TimestampMixin, generate_cuid


class OnboardingWorkflowState(str, enum.Enum):
    # NOTE: INTERESTED is a pre-VISIT_REQUESTED bookmark — set when a tenant
    # marks a property as interested but hasn't yet asked for a visit. Present
    # in DB enum `onboarding_workflow_state_enum`; must be kept in sync here
    # or SQLAlchemy fails to deserialize existing rows.
    INTERESTED = "INTERESTED"
    VISIT_REQUESTED = "VISIT_REQUESTED"
    VISIT_SCHEDULED = "VISIT_SCHEDULED"
    VISIT_APPROVED = "VISIT_APPROVED"
    VISIT_REJECTED = "VISIT_REJECTED"
    AGREEMENT_GENERATED = "AGREEMENT_GENERATED"
    TENANT_SIGNED = "TENANT_SIGNED"
    ADVANCE_SUBMITTED = "ADVANCE_SUBMITTED"
    ADVANCE_APPROVED = "ADVANCE_APPROVED"
    POLICE_VERIFICATION_COMPLETED = "POLICE_VERIFICATION_COMPLETED"
    ORIGINAL_AGREEMENT_UPLOADED = "ORIGINAL_AGREEMENT_UPLOADED"
    TENANT_ACTIVATED = "TENANT_ACTIVATED"
    CANCELLED = "CANCELLED"

    @classmethod
    def from_api(cls, value: str) -> "OnboardingWorkflowState":
        return cls(value.strip().replace("-", "_").upper())

    @property
    def api_value(self) -> str:
        return self.value.lower()


class ChecklistApprovalStatus(str, enum.Enum):
    NOT_SUBMITTED = "NOT_SUBMITTED"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

    @property
    def api_value(self) -> str:
        return self.value.lower()


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
        default=OnboardingWorkflowState.VISIT_REQUESTED,
        index=True,
    )

    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    manager_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    agreement_id: Mapped[str | None] = mapped_column(ForeignKey("agreements.id", ondelete="SET NULL"), nullable=True)
    visit_request_id: Mapped[str | None] = mapped_column(ForeignKey("visit_requests.id", ondelete="SET NULL"), nullable=True)

    visit_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    visit_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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

    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
