"""LuxeLife API — Visit Request models (DB v2.2)."""

import enum
from datetime import date, datetime, time

from sqlalchemy import Date, DateTime, Enum, ForeignKey, String, Text, Time, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base, TimestampMixin, generate_cuid


class VisitRequestStatus(str, enum.Enum):
    PENDING = "PENDING"
    NEGOTIATING = "NEGOTIATING"
    CONFIRMED = "CONFIRMED"
    APPOINTMENT_SCHEDULED = "APPOINTMENT_SCHEDULED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"

    @property
    def api_value(self) -> str:
        return self.value.lower()


class VisitResult(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

    @property
    def api_value(self) -> str:
        return self.value.lower()


class VisitProposalRole(str, enum.Enum):
    TENANT = "TENANT"
    MANAGER = "MANAGER"
    SUPER_ADMIN = "SUPER_ADMIN"

    @property
    def api_value(self) -> str:
        return self.value.lower()


class VisitProposalResponse(str, enum.Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"

    @property
    def api_value(self) -> str:
        return self.value.lower()


class VisitRequest(Base, TimestampMixin):
    __tablename__ = "visit_requests"

    id: Mapped[str] = mapped_column(String(30), primary_key=True, default=generate_cuid)

    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    manager_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    status: Mapped[VisitRequestStatus] = mapped_column(
        Enum(VisitRequestStatus, name="visit_request_status_enum", create_constraint=True),
        default=VisitRequestStatus.PENDING,
        index=True,
    )
    visit_result: Mapped[VisitResult] = mapped_column(
        Enum(VisitResult, name="visit_result_enum", create_constraint=True),
        default=VisitResult.PENDING,
    )

    requested_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    requested_start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    requested_end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    requested_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    scheduled_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    scheduled_start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    scheduled_end_time: Mapped[time | None] = mapped_column(Time, nullable=True)

    visit_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Negotiation pointers
    current_proposal_id: Mapped[str | None] = mapped_column(
        ForeignKey("visit_request_proposals.id", ondelete="SET NULL"),
        nullable=True,
    )
    # 'TENANT' | 'MANAGER' | 'SUPER_ADMIN' | NULL (once confirmed)
    pending_with: Mapped[str | None] = mapped_column(String(20), nullable=True)

    proposals = relationship(
        "VisitRequestProposal",
        back_populates="visit_request",
        cascade="all, delete-orphan",
        foreign_keys="VisitRequestProposal.visit_request_id",
        order_by="VisitRequestProposal.created_at.desc()",
    )


class VisitRequestProposal(Base):
    __tablename__ = "visit_request_proposals"

    id: Mapped[str] = mapped_column(String(30), primary_key=True, default=generate_cuid)

    visit_request_id: Mapped[str] = mapped_column(
        ForeignKey("visit_requests.id", ondelete="CASCADE"), index=True
    )
    proposed_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    proposed_by_role: Mapped[VisitProposalRole] = mapped_column(
        Enum(VisitProposalRole, name="visit_proposal_role_enum", create_constraint=True),
    )

    proposed_date: Mapped[date] = mapped_column(Date, nullable=False)
    proposed_start_time: Mapped[time] = mapped_column(Time, nullable=False)
    proposed_end_time: Mapped[time] = mapped_column(Time, nullable=False)

    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    response: Mapped[VisitProposalResponse] = mapped_column(
        Enum(VisitProposalResponse, name="visit_proposal_response_enum", create_constraint=True),
        default=VisitProposalResponse.PENDING,
    )
    responded_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    visit_request = relationship(
        "VisitRequest",
        back_populates="proposals",
        foreign_keys=[visit_request_id],
    )
