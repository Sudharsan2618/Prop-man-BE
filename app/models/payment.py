"""
LuxeLife API — Payment model.

Tracks rent payments, service payments, and security deposits.
All payments happen offline — admin verifies and marks as paid.
All monetary amounts are stored in INR as whole numbers.
"""

import enum
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base, TimestampMixin, generate_cuid


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    """Persist enum values (lowercase) instead of enum member names."""
    return [item.value for item in enum_cls]


class PaymentType(str, enum.Enum):
    RENT = "rent"
    SERVICE = "service"
    SECURITY_DEPOSIT = "security_deposit"
    ADVANCE = "advance"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    AWAITING_VERIFICATION = "awaiting_verification"  # tenant uploaded receipt
    PAID = "paid"
    OVERDUE = "overdue"
    REJECTED = "rejected"  # admin rejected the receipt
    ESCROWED = "escrowed"  # legacy compatibility
    FAILED = "failed"  # legacy compatibility
    REFUNDED = "refunded"


class Payment(Base, TimestampMixin):
    """A financial transaction on the LuxeLife platform — offline only."""

    __tablename__ = "payments"
    __table_args__ = (
        Index("ix_payments_owner_status_created_at", "owner_id", "status", "created_at"),
        Index("ix_payments_tenant_status_due_date", "tenant_id", "status", "due_date"),
    )

    # ── Identity ──
    id: Mapped[str] = mapped_column(
        String(30), primary_key=True, default=generate_cuid
    )
    type: Mapped[PaymentType] = mapped_column(
        Enum(
            PaymentType,
            name="payment_type_enum",
            create_constraint=True,
            values_callable=_enum_values,
            validate_strings=True,
        )
    )
    label: Mapped[str] = mapped_column(String(200))
    amount: Mapped[int] = mapped_column(Integer)  # in INR

    # ── Breakdown (JSONB for flexibility) ──
    breakdown: Mapped[dict] = mapped_column(JSONB, default=dict)

    # ── Status ──
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(
            PaymentStatus,
            name="payment_status_enum",
            create_constraint=True,
            values_callable=_enum_values,
            validate_strings=True,
        ),
        default=PaymentStatus.PENDING,
        index=True,
    )

    # ── Dates ──
    due_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    paid_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Tenant receipt upload ──
    screenshot_url: Mapped[str | None] = mapped_column(
        String(512), nullable=True
    )  # tenant uploads a payment proof screenshot

    # ── Admin verification ──
    verified_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    admin_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Payout tracking ──
    # payout_processed: Mapped[bool] = mapped_column(Boolean, default=False)

    # ── Foreign Keys ──
    property_id: Mapped[str] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # ── Relationships ──
    property = relationship("Property", back_populates="payments")
    verifier = relationship("User", foreign_keys=[verified_by])

    def __repr__(self) -> str:
        return f"<Payment id={self.id} type={self.type.value} status={self.status.value} amount={self.amount}>"
