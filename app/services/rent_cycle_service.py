"""
LuxeLife API — Rent cycle service.

Automatically creates monthly rent payment records for active agreements.
"""

from datetime import datetime, timezone, date
import structlog
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agreement import Agreement, AgreementStatus
from app.models.payment import Payment, PaymentType, PaymentStatus
from app.models.notification import Notification

logger = structlog.get_logger()

class RentCycleService:
    """Service to manage monthly rent cycles."""

    @staticmethod
    async def generate_monthly_rent_records(db: AsyncSession):
        """
        Scan all active agreements and create rent payment records for the current month.
        This could be run by a cron job or triggered by an admin.
        """
        today = date.today()
        month_label = today.strftime("%B %Y")
        
        # Fetch active agreements
        result = await db.execute(
            select(Agreement).where(Agreement.status == AgreementStatus.ACTIVE)
        )
        active_agreements = result.scalars().all()
        
        created_count = 0
        for ag in active_agreements:
            # Check if rent record already exists for this agreement and month
            # Using a simplified check: label matches "Rent - [Month] [Year]"
            label = f"Rent - {month_label}"
            
            existing = await db.execute(
                select(Payment).where(
                    and_(
                        Payment.tenant_id == ag.tenant_id,
                        Payment.property_id == ag.property_id,
                        Payment.type == PaymentType.RENT,
                        Payment.label == label
                    )
                )
            )
            
            if not existing.scalar_one_or_none():
                # Create rent record
                rent_payment = Payment(
                    tenant_id=ag.tenant_id,
                    owner_id=ag.owner_id,
                    property_id=ag.property_id,
                    amount=ag.rent_amount,
                    type=PaymentType.RENT,
                    status=PaymentStatus.PENDING,
                    label=label
                )
                db.add(rent_payment)
                
                # Notify tenant
                notif = Notification(
                    user_id=ag.tenant_id,
                    type="rent_due",
                    title="Rent Due 🏠",
                    body=f"Your rent for {month_label} is due. Please upload your payment screenshot once paid.",
                    data={"payment_id": rent_payment.id, "amount": ag.rent_amount}
                )
                db.add(notif)
                created_count += 1
        
        await db.flush()
        logger.info("Monthly rent records generated", count=created_count, month=month_label)
        return created_count

    @staticmethod
    async def get_tenant_rent_status(db: AsyncSession, tenant_id: str):
        """Get the current rent status for a tenant (pending, paid, etc)."""
        # Fetch latest rent payment
        result = await db.execute(
            select(Payment)
            .where(and_(Payment.tenant_id == tenant_id, Payment.type == PaymentType.RENT))
            .order_by(Payment.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
