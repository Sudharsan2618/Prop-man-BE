"""
LuxeLife API — Admin Calendar Slot model.

Represents an admin's availability slot for property visits.
Tenants can book available slots to schedule property inspections.
"""

import enum
from datetime import date, time, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    Time,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base, TimestampMixin, generate_cuid


class SlotStatus(str, enum.Enum):
    AVAILABLE = "available"
    BOOKED = "booked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class VisitResult(str, enum.Enum):
    PENDING = "pending"          # visit not yet completed
    APPROVED = "approved"        # admin approved tenant
    REJECTED = "rejected"        # admin rejected tenant


class AdminSlot(Base, TimestampMixin):
    """An admin's availability slot for property visits."""

    __tablename__ = "admin_slots"

    # ── Identity ──
    id: Mapped[str] = mapped_column(
        String(30), primary_key=True, default=generate_cuid
    )

    # ── Time ──
    slot_date: Mapped[date] = mapped_column(Date, index=True)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)

    # ── Status ──
    status: Mapped[SlotStatus] = mapped_column(
        Enum(SlotStatus, name="slot_status_enum", create_constraint=True),
        default=SlotStatus.AVAILABLE,
        index=True,
    )

    # ── Visit result (set after visit is completed) ──
    visit_result: Mapped[VisitResult] = mapped_column(
        Enum(VisitResult, name="visit_result_enum", create_constraint=True),
        default=VisitResult.PENDING,
    )
    visit_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Who ──
    admin_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    booked_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    property_id: Mapped[str | None] = mapped_column(
        ForeignKey("properties.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # ── Timestamps ──
    booked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Relationships ──
    admin = relationship("User", foreign_keys=[admin_id], backref="admin_slots")
    tenant = relationship("User", foreign_keys=[booked_by], backref="booked_visits")
    property = relationship("Property", backref="visit_slots")

    def __repr__(self) -> str:
        return f"<AdminSlot id={self.id} date={self.slot_date} status={self.status.value}>"
