"""
LuxeLife API — Agreement service.

Handles agreement lifecycle:
- Auto-generate agreement from property/tenant/owner data
- Sign agreement
- Admin confirms advance payment → agreement becomes ACTIVE
"""

from datetime import datetime, timezone, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.responses import success_response
from app.models.agreement import Agreement, AgreementStatus
from app.models.property import Occupancy, Property
from app.models.user import Role, User
from app.models.notification import Notification
from app.models.payment import Payment, PaymentType, PaymentStatus
from app.services.onboarding_workflow_service import OnboardingWorkflowService

logger = structlog.get_logger()


AGREEMENT_TEMPLATE = """
RESIDENTIAL LEASE AGREEMENT

This Residential Lease Agreement ("Agreement") is entered into on {date},
by and between:

LANDLORD: {owner_name}
(Hereinafter referred to as "Landlord")

TENANT: {tenant_name}
(Hereinafter referred to as "Tenant")

PROPERTY: {property_name}, {property_unit}
ADDRESS: {property_address}, {property_city}, {property_state} - {property_pincode}

LEASE TERMS:
1. LEASE PERIOD: This lease is effective from {lease_start} to {lease_end}
   ({lease_duration} months).

2. MONTHLY RENT: Rs.{rent} (Rupees {rent_words} only), payable on or before
   the 5th of each month.

3. SECURITY DEPOSIT: Rs.{deposit} (Rupees {deposit_words} only), to be paid
   before taking possession, refundable on termination subject to deductions.

4. MAINTENANCE CHARGES: Rs.{maintenance}/month (if applicable).

5. PROPERTY TYPE: {property_type} | {bhk} | {sqft} sq.ft.
   Furnishing: {furnishing}

GENERAL TERMS AND CONDITIONS:

6. The Tenant shall use the premises for residential purposes only.

7. The Tenant shall not sublet, assign, or transfer this lease without
   written consent of the Landlord.

8. The Tenant shall maintain the premises in good condition and shall be
   responsible for any damages caused during the tenancy period.

9. The Landlord shall ensure all structural repairs and common area
   maintenance.

10. Either party may terminate this agreement by giving 2 months written
    notice to the other party.

11. On termination, the Tenant shall vacate and hand over the premises in
    the same condition as received, subject to normal wear and tear.

12. The security deposit shall be refunded within 30 days of vacating,
    after deducting any outstanding rent, damages, or other charges.

13. This Agreement shall be governed by the laws of India and any disputes
    shall be subject to the jurisdiction of courts in {property_city}.

AGREED AND SIGNED:

Landlord: ________________________
Date: ___________________________

Tenant: _________________________
Date: ___________________________
""".strip()


def _num_to_words_inr(n: int) -> str:
    """Simple Indian number to words (for agreement text)."""
    if n == 0:
        return "Zero"
    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
            "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
            "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def _chunk(num):
        if num == 0: return ""
        if num < 20: return ones[num]
        if num < 100: return tens[num // 10] + (" " + ones[num % 10] if num % 10 else "")
        return ones[num // 100] + " Hundred" + (" and " + _chunk(num % 100) if num % 100 else "")

    parts = []
    if n >= 10000000:
        parts.append(_chunk(n // 10000000) + " Crore")
        n %= 10000000
    if n >= 100000:
        parts.append(_chunk(n // 100000) + " Lakh")
        n %= 100000
    if n >= 1000:
        parts.append(_chunk(n // 1000) + " Thousand")
        n %= 1000
    if n > 0:
        parts.append(_chunk(n))
    return " ".join(parts)


class AgreementService:
    """Agreement lifecycle management."""

    @staticmethod
    async def auto_generate_agreement(
        db: AsyncSession,
        *,
        property_id: str,
        tenant_id: str,
        admin_id: str,
        lease_duration_months: int = 11,
    ) -> Agreement:
        """Auto-generate a rental agreement after admin approves a visit."""
        prop_result = await db.execute(
            select(Property).options(selectinload(Property.owner)).where(Property.id == property_id)
        )
        prop = prop_result.scalar_one_or_none()
        if not prop:
            raise ValueError("Property not found")

        tenant_result = await db.execute(select(User).where(User.id == tenant_id))
        tenant = tenant_result.scalar_one_or_none()
        if not tenant:
            raise ValueError("Tenant not found")

        # Check for existing active agreement
        existing = await db.execute(
            select(Agreement).where(
                Agreement.property_id == property_id,
                Agreement.tenant_id == tenant_id,
                Agreement.status.in_([
                    AgreementStatus.AWAITING_SIGNATURE,
                    AgreementStatus.SIGNED,
                    AgreementStatus.ACTIVE,
                ]),
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError("An active or pending agreement already exists for this property and tenant")

        owner = prop.owner
        lease_start = datetime.now(timezone.utc) + timedelta(days=7)
        lease_end = lease_start + timedelta(days=lease_duration_months * 30)

        terms = AGREEMENT_TEMPLATE.format(
            date=datetime.now(timezone.utc).strftime("%B %d, %Y"),
            owner_name=owner.name if owner else "Property Owner",
            tenant_name=tenant.name,
            property_name=prop.name,
            property_unit=prop.unit,
            property_address=prop.address,
            property_city=prop.city,
            property_state=prop.state,
            property_pincode=prop.pincode,
            lease_start=lease_start.strftime("%B %d, %Y"),
            lease_end=lease_end.strftime("%B %d, %Y"),
            lease_duration=lease_duration_months,
            rent=f"{prop.rent:,}",
            rent_words=_num_to_words_inr(prop.rent),
            deposit=f"{prop.security_deposit:,}",
            deposit_words=_num_to_words_inr(prop.security_deposit),
            maintenance=f"{prop.maintenance_charges:,}",
            property_type=prop.type.value.replace("_", " ").title(),
            bhk=prop.bhk,
            sqft=prop.sqft,
            furnishing=prop.furnishing.value.replace("_", " ").title(),
        )

        agreement = Agreement(
            status=AgreementStatus.AWAITING_SIGNATURE,
            rent_amount=prop.rent,
            security_deposit=prop.security_deposit,
            maintenance_charges=prop.maintenance_charges,
            lease_start=lease_start,
            lease_end=lease_end,
            lease_duration_months=lease_duration_months,
            terms_text=terms,
            property_id=property_id,
            tenant_id=tenant_id,
            owner_id=prop.owner_id,
            approved_by=admin_id,
        )
        db.add(agreement)
        await db.flush()

        # Notify tenant
        notif = Notification(
            user_id=tenant_id,
            type="agreement_ready",
            title="Rental Agreement Ready!",
            body=f"Your rental agreement for {prop.name} is ready for your signature.",
            data={"agreement_id": agreement.id, "property_id": property_id},
        )
        db.add(notif)

        # Notify owner
        notif_owner = Notification(
            user_id=prop.owner_id,
            type="agreement_generated",
            title="New Rental Agreement",
            body=f"A rental agreement has been generated for {prop.name} with tenant {tenant.name}.",
            data={"agreement_id": agreement.id},
        )
        db.add(notif_owner)

        logger.info("Agreement auto-generated", agreement_id=agreement.id, property_id=property_id)
        await OnboardingWorkflowService.mark_agreement_generated(
            db,
            property_id=property_id,
            tenant_id=tenant_id,
            owner_id=prop.owner_id,
            agreement_id=agreement.id,
            actor_id=admin_id,
        )
        return agreement

    @staticmethod
    async def sign_agreement(
        db: AsyncSession,
        agreement_id: str,
        user_id: str,
        signature: str,
    ) -> dict:
        """Tenant signs the agreement."""
        agreement = await _get_agreement_with_relations(db, agreement_id)
        if not agreement:
            raise ValueError("Agreement not found")

        if agreement.status not in (AgreementStatus.AWAITING_SIGNATURE, AgreementStatus.SIGNED):
            raise ValueError(f"Agreement cannot be signed in '{agreement.status.value}' status")

        if user_id == agreement.tenant_id:
            if agreement.tenant_signature:
                raise ValueError("You have already signed this agreement")
            agreement.tenant_signature = signature
            agreement.tenant_signed_at = datetime.now(timezone.utc)
        elif user_id == agreement.owner_id:
            if agreement.owner_signature:
                raise ValueError("You have already signed this agreement")
            agreement.owner_signature = signature
            agreement.owner_signed_at = datetime.now(timezone.utc)
        else:
            raise PermissionError("You are not a party to this agreement")

        # If tenant signed, move to AWAITING_PAYMENT (waiting for advance confirmation)
        if agreement.tenant_signature:
            agreement.status = AgreementStatus.AWAITING_PAYMENT

            # Create advance payment record for admin to confirm
            advance = Payment(
                type=PaymentType.ADVANCE,
                label=f"Security Deposit - {agreement.property_id}",
                amount=agreement.security_deposit,
                property_id=agreement.property_id,
                tenant_id=agreement.tenant_id,
                owner_id=agreement.owner_id,
                status=PaymentStatus.PENDING,
            )
            db.add(advance)
            await db.flush()
            agreement.deposit_payment_id = advance.id

            await OnboardingWorkflowService.mark_tenant_signed(
                db,
                agreement_id=agreement.id,
                actor_id=user_id,
            )

            # Notify admin(s) about advance pending
            admins = (await db.execute(
                select(User).where(User.active_role == Role.ADMIN)
            )).scalars().all()
            for admin in admins:
                notif = Notification(
                    user_id=admin.id,
                    type="advance_pending",
                    title="Advance Payment Pending",
                    body=f"Tenant signed agreement. Advance of Rs.{agreement.security_deposit:,} pending confirmation.",
                    data={"agreement_id": agreement.id, "payment_id": advance.id},
                )
                db.add(notif)

        await db.flush()
        return success_response(data=_agreement_to_dict(agreement))

    @staticmethod
    async def admin_confirm_advance(
        db: AsyncSession,
        agreement_id: str,
        admin_id: str,
        notes: str | None = None,
    ) -> dict:
        """Admin confirms advance payment -> agreement becomes ACTIVE."""
        agreement = await _get_agreement_with_relations(db, agreement_id)
        if not agreement:
            raise ValueError("Agreement not found")
        if agreement.status != AgreementStatus.AWAITING_PAYMENT:
            raise ValueError(f"Agreement not in 'awaiting_payment' status (current: {agreement.status.value})")

        agreement.status = AgreementStatus.ACTIVE
        agreement.advance_confirmed = True

        # Mark the deposit payment as paid
        if agreement.deposit_payment_id:
            from app.services.payment_service import PaymentService
            await PaymentService.admin_mark_paid(db, agreement.deposit_payment_id, admin_id, notes)

        # Assign tenant to property
        prop_result = await db.execute(select(Property).where(Property.id == agreement.property_id))
        prop = prop_result.scalar_one_or_none()
        if prop:
            prop.tenant_id = agreement.tenant_id
            prop.occupancy = Occupancy.OCCUPIED
            prop.lease_start = agreement.lease_start
            prop.lease_end = agreement.lease_end

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

        await db.flush()
        logger.info("Agreement activated", agreement_id=agreement_id, admin_id=admin_id)
        return success_response(data=_agreement_to_dict(agreement))

    @staticmethod
    async def get_agreement(db: AsyncSession, agreement_id: str) -> dict | None:
        """Get a single agreement."""
        agreement = await _get_agreement_with_relations(db, agreement_id)
        if not agreement:
            return None
        return _agreement_to_dict(agreement)

    @staticmethod
    async def list_agreements(
        db: AsyncSession,
        *,
        tenant_id: str | None = None,
        owner_id: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """List agreements with optional filters."""
        query = select(Agreement).options(
            selectinload(Agreement.property),
            selectinload(Agreement.tenant),
            selectinload(Agreement.owner),
        )
        if tenant_id:
            query = query.where(Agreement.tenant_id == tenant_id)
        if owner_id:
            query = query.where(Agreement.owner_id == owner_id)
        if status:
            query = query.where(Agreement.status == AgreementStatus(status))
        query = query.order_by(Agreement.created_at.desc())
        result = await db.execute(query)
        return [_agreement_to_dict(a) for a in result.scalars().all()]


async def _get_agreement(db: AsyncSession, agreement_id: str) -> Agreement | None:
    result = await db.execute(select(Agreement).where(Agreement.id == agreement_id))
    return result.scalar_one_or_none()


async def _get_agreement_with_relations(db: AsyncSession, agreement_id: str) -> Agreement | None:
    result = await db.execute(
        select(Agreement).options(
            selectinload(Agreement.property),
            selectinload(Agreement.tenant),
            selectinload(Agreement.owner),
        ).where(Agreement.id == agreement_id)
    )
    return result.scalar_one_or_none()


def _agreement_to_dict(a: Agreement) -> dict:
    """Convert Agreement ORM to response dict."""
    result = {
        "id": a.id,
        "status": a.status.value,
        "rent_amount": a.rent_amount,
        "security_deposit": a.security_deposit,
        "maintenance_charges": a.maintenance_charges,
        "lease_start": a.lease_start.isoformat() if a.lease_start else None,
        "lease_end": a.lease_end.isoformat() if a.lease_end else None,
        "lease_duration_months": a.lease_duration_months,
        "terms_text": a.terms_text,
        "tenant_signature": a.tenant_signature,
        "owner_signature": a.owner_signature,
        "tenant_signed_at": a.tenant_signed_at.isoformat() if a.tenant_signed_at else None,
        "owner_signed_at": a.owner_signed_at.isoformat() if a.owner_signed_at else None,
        "pdf_url": a.pdf_url,
        "approved_by": a.approved_by,
        "advance_confirmed": a.advance_confirmed,
        "property_id": a.property_id,
        "tenant_id": a.tenant_id,
        "owner_id": a.owner_id,
        "deposit_payment_id": a.deposit_payment_id,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }
    # Add related names only if already eagerly loaded (avoid lazy load in async)
    from sqlalchemy import inspect as sa_inspect
    loaded = sa_inspect(a).dict
    if 'property' in loaded and loaded['property'] is not None:
        result["property_name"] = loaded['property'].name
        result["property_address"] = loaded['property'].address
        result["property_unit"] = loaded['property'].unit
    if 'tenant' in loaded and loaded['tenant'] is not None:
        result["tenant_name"] = loaded['tenant'].name
    if 'owner' in loaded and loaded['owner'] is not None:
        result["owner_name"] = loaded['owner'].name
    return result
