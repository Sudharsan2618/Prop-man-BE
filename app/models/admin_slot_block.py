"""
LuxeLife API — Admin Calendar block rule model.

Represents recurring/temporary time windows where a specific admin is unavailable
for visit bookings.
"""

from datetime import date, time

from sqlalchemy import Date, ForeignKey, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base, TimestampMixin, generate_cuid


class AdminSlotBlock(Base, TimestampMixin):
    """Admin-defined blocked window rule for availability generation."""

    __tablename__ = "admin_slot_blocks"

    id: Mapped[str] = mapped_column(String(30), primary_key=True, default=generate_cuid)

    admin_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )

    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)

    effective_from: Mapped[date] = mapped_column(Date, index=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)

    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata", server_default="Asia/Kolkata")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    admin = relationship("User", foreign_keys=[admin_id], backref="admin_slot_blocks")

    def __repr__(self) -> str:
        return (
            f"<AdminSlotBlock id={self.id} admin_id={self.admin_id} "
            f"from={self.effective_from} to={self.effective_to}>"
        )
