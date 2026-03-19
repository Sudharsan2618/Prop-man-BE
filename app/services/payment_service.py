"""
LuxeLife API — Payment service.

Handles offline payment workflows:
- Admin creates payment records (advance, rent)
- Tenant uploads receipt screenshots for rent
- Admin verifies and marks payments as paid
"""

from datetime import datetime, timezone

import structlog
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.responses import paginated_response, success_response
from app.models.payment import Payment, PaymentStatus, PaymentType
from app.models.notification import Notification

logger = structlog.get_logger()


class PaymentService:
    """Offline payment management."""

    @staticmethod
    async def create_payment(
        db: AsyncSession,
        *,
        payment_type: PaymentType,
        label: str,
        amount: int,
        property_id: str,
        tenant_id: str,
        owner_id: str,
        due_date: datetime | None = None,
        breakdown: dict | None = None,
    ) -> Payment:
        """Create a new pending payment record."""
        payment = Payment(
            type=payment_type,
            label=label,
            amount=amount,
            property_id=property_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            due_date=due_date,
            breakdown=breakdown or {},
            status=PaymentStatus.PENDING,
        )
        db.add(payment)
        await db.flush()
        logger.info("Payment created", payment_id=payment.id, type=payment_type.value, amount=amount)
        return payment

    @staticmethod
    async def get_payment(db: AsyncSession, payment_id: str) -> Payment | None:
        """Get a payment by ID."""
        result = await db.execute(select(Payment).where(Payment.id == payment_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_payments_by_tenant(
        db: AsyncSession,
        tenant_id: str,
        page: int = 1,
        limit: int = 20,
        status: str | None = None,
        payment_type: str | None = None,
    ) -> dict:
        """List payments for a tenant."""
        conditions = [Payment.tenant_id == tenant_id]
        if status:
            conditions.append(Payment.status == PaymentStatus(status))
        if payment_type:
            conditions.append(Payment.type == PaymentType(payment_type))

        query = select(Payment).where(and_(*conditions)).order_by(Payment.created_at.desc())
        # Count total
        from sqlalchemy import func
        count_q = select(func.count()).select_from(Payment).where(and_(*conditions))
        total = (await db.execute(count_q)).scalar() or 0

        result = await db.execute(query.offset((page - 1) * limit).limit(limit))
        payments = result.scalars().all()
        return paginated_response(
            items=[_payment_to_dict(p) for p in payments],
            total=total, page=page, limit=limit,
        )

    @staticmethod
    async def get_payments_by_owner(
        db: AsyncSession,
        owner_id: str,
        page: int = 1,
        limit: int = 20,
        status: str | None = None,
        payment_type: str | None = None,
    ) -> dict:
        """List payments for an owner."""
        conditions = [Payment.owner_id == owner_id]
        if status:
            conditions.append(Payment.status == PaymentStatus(status))
        if payment_type:
            conditions.append(Payment.type == PaymentType(payment_type))

        query = select(Payment).where(and_(*conditions)).order_by(Payment.created_at.desc())
        from sqlalchemy import func
        count_q = select(func.count()).select_from(Payment).where(and_(*conditions))
        total = (await db.execute(count_q)).scalar() or 0

        result = await db.execute(query.offset((page - 1) * limit).limit(limit))
        payments = result.scalars().all()
        return paginated_response(
            items=[_payment_to_dict(p) for p in payments],
            total=total, page=page, limit=limit,
        )

    @staticmethod
    async def get_pending_verifications(
        db: AsyncSession, page: int = 1, limit: int = 20
    ) -> dict:
        """List payments awaiting admin verification."""
        query = (
            select(Payment)
            .where(Payment.status == PaymentStatus.AWAITING_VERIFICATION)
            .order_by(Payment.created_at.desc())
        )
        from sqlalchemy import func
        count_q = select(func.count()).select_from(Payment).where(
            Payment.status == PaymentStatus.AWAITING_VERIFICATION
        )
        total = (await db.execute(count_q)).scalar() or 0

        result = await db.execute(query.offset((page - 1) * limit).limit(limit))
        payments = result.scalars().all()
        return paginated_response(
            items=[_payment_to_dict(p) for p in payments],
            total=total, page=page, limit=limit,
        )

    @staticmethod
    async def tenant_upload_receipt(
        db: AsyncSession,
        payment_id: str,
        tenant_id: str,
        screenshot_url: str,
    ) -> dict:
        """Tenant uploads a payment receipt screenshot."""
        payment = await PaymentService.get_payment(db, payment_id)
        if not payment:
            raise ValueError("Payment not found")
        if payment.tenant_id != tenant_id:
            raise PermissionError("You can only upload receipts for your own payments")
        if payment.status not in (PaymentStatus.PENDING, PaymentStatus.OVERDUE, PaymentStatus.REJECTED):
            raise ValueError(f"Cannot upload receipt for payment in '{payment.status.value}' status")

        payment.screenshot_url = screenshot_url
        payment.status = PaymentStatus.AWAITING_VERIFICATION
        await db.flush()

        if payment.type in (PaymentType.ADVANCE, PaymentType.SECURITY_DEPOSIT):
            from app.services.onboarding_workflow_service import OnboardingWorkflowService

            await OnboardingWorkflowService.mark_advance_submitted(
                db,
                property_id=payment.property_id,
                tenant_id=tenant_id,
                actor_id=tenant_id,
            )

        # Notify admin(s)
        from sqlalchemy import select as sel
        from app.models.user import Role, User
        admins = (await db.execute(
            sel(User).where(User.active_role == Role.ADMIN)
        )).scalars().all()
        for admin in admins:
            notif = Notification(
                user_id=admin.id,
                type="payment_receipt",
                title="Payment Receipt Uploaded",
                body=f"Tenant has uploaded a receipt for payment {payment.label} (₹{payment.amount:,})",
                data={"payment_id": payment.id},
            )
            db.add(notif)

        logger.info("Receipt uploaded", payment_id=payment_id, tenant_id=tenant_id)
        return success_response(data=_payment_to_dict(payment))

    @staticmethod
    async def admin_verify_payment(
        db: AsyncSession,
        payment_id: str,
        admin_id: str,
        *,
        approve: bool = True,
        notes: str | None = None,
        rejection_reason: str | None = None,
    ) -> dict:
        """Admin verifies (or rejects) a payment."""
        payment = await PaymentService.get_payment(db, payment_id)
        if not payment:
            raise ValueError("Payment not found")

        if approve:
            payment.status = PaymentStatus.PAID
            payment.verified_by = admin_id
            payment.verified_at = datetime.now(timezone.utc)
            payment.paid_date = datetime.now(timezone.utc)
            payment.admin_notes = notes

            # Notify tenant
            notif = Notification(
                user_id=payment.tenant_id,
                type="payment_verified",
                title="Payment Verified",
                body=f"Your payment of ₹{payment.amount:,} for {payment.label} has been verified.",
                data={"payment_id": payment.id},
            )
            db.add(notif)

            # Notify owner
            notif_owner = Notification(
                user_id=payment.owner_id,
                type="rent_received",
                title="Rent Payment Received",
                body=f"₹{payment.amount:,} for {payment.label} has been confirmed.",
                data={"payment_id": payment.id},
            )
            db.add(notif_owner)

            # If this is an advance/security deposit, also activate the agreement
            if payment.type in (PaymentType.ADVANCE, PaymentType.SECURITY_DEPOSIT):
                await PaymentService._activate_agreement_for_payment(db, payment, admin_id)

            logger.info("Payment verified", payment_id=payment_id, admin_id=admin_id)
        else:
            if not rejection_reason:
                raise ValueError("Rejection reason is required")
            payment.status = PaymentStatus.REJECTED
            payment.rejection_reason = rejection_reason
            payment.admin_notes = notes

            # Notify tenant of rejection
            notif = Notification(
                user_id=payment.tenant_id,
                type="payment_rejected",
                title="Payment Receipt Rejected",
                body=f"Your receipt for {payment.label} was rejected: {rejection_reason}",
                data={"payment_id": payment.id},
            )
            db.add(notif)

            logger.info("Payment rejected", payment_id=payment_id, admin_id=admin_id)

        await db.flush()
        return success_response(data=_payment_to_dict(payment))

    @staticmethod
    async def _activate_agreement_for_payment(
        db: AsyncSession,
        payment: Payment,
        admin_id: str,
    ) -> None:
        """When an advance/security_deposit payment is verified, activate the linked agreement."""
        from app.models.agreement import Agreement, AgreementStatus
        from app.models.property import Occupancy, Property
        from app.services.onboarding_workflow_service import OnboardingWorkflowService

        # Find agreement that references this payment
        result = await db.execute(
            select(Agreement).where(Agreement.deposit_payment_id == payment.id)
        )
        agreement = result.scalar_one_or_none()
        if not agreement:
            logger.warning("No agreement found for advance payment", payment_id=payment.id)
            return
        if agreement.status != AgreementStatus.AWAITING_PAYMENT:
            logger.info("Agreement already processed", agreement_id=agreement.id, status=agreement.status.value)
            return

        # Activate agreement
        agreement.status = AgreementStatus.ACTIVE
        agreement.advance_confirmed = True

        # Assign tenant to property
        prop_result = await db.execute(select(Property).where(Property.id == agreement.property_id))
        prop = prop_result.scalar_one_or_none()
        if prop:
            prop.tenant_id = agreement.tenant_id
            prop.occupancy = Occupancy.OCCUPIED
            prop.lease_start = agreement.lease_start
            prop.lease_end = agreement.lease_end

        # Update onboarding workflow
        await OnboardingWorkflowService.mark_advance_approved(
            db,
            agreement_id=agreement.id,
            actor_id=admin_id,
        )

        # Notify tenant
        notif = Notification(
            user_id=agreement.tenant_id,
            type="agreement_active",
            title="Welcome to Your New Home!",
            body=f"Your rental agreement is now active. Move-in date: {agreement.lease_start.strftime('%B %d, %Y') if agreement.lease_start else 'TBD'}",
            data={"agreement_id": agreement.id},
        )
        db.add(notif)

        # Notify owner
        notif_owner = Notification(
            user_id=agreement.owner_id,
            type="new_tenant",
            title="New Tenant Confirmed",
            body="Advance payment confirmed. Agreement is now active.",
            data={"agreement_id": agreement.id},
        )
        db.add(notif_owner)

        logger.info("Agreement activated via payment verification", agreement_id=agreement.id, payment_id=payment.id)

    @staticmethod
    async def admin_mark_paid(
        db: AsyncSession,
        payment_id: str,
        admin_id: str,
        notes: str | None = None,
    ) -> dict:
        """Admin directly marks a payment as paid (for advance/deposit — no screenshot needed)."""
        payment = await PaymentService.get_payment(db, payment_id)
        if not payment:
            raise ValueError("Payment not found")
        if payment.status == PaymentStatus.PAID:
            raise ValueError("Payment is already marked as paid")

        payment.status = PaymentStatus.PAID
        payment.verified_by = admin_id
        payment.verified_at = datetime.now(timezone.utc)
        payment.paid_date = datetime.now(timezone.utc)
        payment.admin_notes = notes
        await db.flush()

        # Notify tenant
        notif = Notification(
            user_id=payment.tenant_id,
            type="payment_confirmed",
            title="Payment Confirmed",
            body=f"Your {payment.label} payment of ₹{payment.amount:,} has been confirmed by admin.",
            data={"payment_id": payment.id},
        )
        db.add(notif)

        # Notify owner
        notif_owner = Notification(
            user_id=payment.owner_id,
            type="payment_received",
            title="Payment Received",
            body=f"₹{payment.amount:,} for {payment.label} has been confirmed.",
            data={"payment_id": payment.id},
        )
        db.add(notif_owner)

        logger.info("Payment marked paid by admin", payment_id=payment_id, admin_id=admin_id)
        return success_response(data=_payment_to_dict(payment))


def _payment_to_dict(p: Payment) -> dict:
    """Convert Payment ORM object to response dict."""
    return {
        "id": p.id,
        "type": p.type.value,
        "label": p.label,
        "amount": p.amount,
        "breakdown": p.breakdown,
        "status": p.status.value,
        "due_date": p.due_date.isoformat() if p.due_date else None,
        "paid_date": p.paid_date.isoformat() if p.paid_date else None,
        "screenshot_url": p.screenshot_url,
        "verified_by": p.verified_by,
        "verified_at": p.verified_at.isoformat() if p.verified_at else None,
        "admin_notes": p.admin_notes,
        "rejection_reason": p.rejection_reason,
        "property_id": p.property_id,
        "tenant_id": p.tenant_id,
        "owner_id": p.owner_id,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }
