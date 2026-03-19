"""
LuxeLife API — Payment routes.

Offline payment management:
- List payments (tenant, owner, admin)
- Tenant uploads receipt screenshot
- Admin verifies/rejects payment
- Admin directly marks advance as paid
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.services.payment_service import PaymentService
from app.services.rent_cycle_service import RentCycleService

router = APIRouter(prefix="/payments", tags=["Payments"])


# ── Schemas ──

class UploadReceiptRequest(BaseModel):
    screenshot_url: str = Field(..., min_length=1, max_length=512)


class VerifyPaymentRequest(BaseModel):
    approve: bool
    notes: str | None = None
    rejection_reason: str | None = None


class MarkPaidRequest(BaseModel):
    notes: str | None = None


# ── Routes ──

@router.get("", summary="List my payments")
async def list_payments(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    payment_type: str | None = Query(None, alias="type"),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List payments for the current user (tenant sees their payments, owner sees theirs)."""
    role = user.active_role.value
    if role == "tenant":
        return await PaymentService.get_payments_by_tenant(
            db,
            user.id,
            page,
            limit,
            status=status,
            payment_type=payment_type,
        )
    elif role == "owner":
        return await PaymentService.get_payments_by_owner(
            db,
            user.id,
            page,
            limit,
            status=status,
            payment_type=payment_type,
        )
    elif role == "admin":
        return await PaymentService.get_pending_verifications(db, page, limit)
    else:
        raise HTTPException(403, "Invalid role")


@router.get("/pending-verifications", summary="List payments awaiting verification (admin)")
async def pending_verifications(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin views all payments awaiting receipt verification."""
    if user.active_role.value != "admin":
        raise HTTPException(403, "Only admins can view pending verifications")
    return await PaymentService.get_pending_verifications(db, page, limit)


@router.get("/{payment_id}", summary="Get payment details")
async def get_payment(
    payment_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single payment by ID."""
    payment = await PaymentService.get_payment(db, payment_id)
    if not payment:
        raise HTTPException(404, "Payment not found")
    # Check access
    role = user.active_role.value
    if role == "admin" or payment.tenant_id == user.id or payment.owner_id == user.id:
        from app.services.payment_service import _payment_to_dict
        return {"success": True, "data": _payment_to_dict(payment)}
    raise HTTPException(403, "Access denied")


@router.post("/{payment_id}/upload-receipt", summary="Upload payment receipt (tenant)")
async def upload_receipt(
    payment_id: str,
    body: UploadReceiptRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Tenant uploads a screenshot of their payment receipt."""
    try:
        result = await PaymentService.tenant_upload_receipt(
            db, payment_id=payment_id, tenant_id=user.id,
            screenshot_url=body.screenshot_url,
        )
        await db.commit()
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))


@router.post("/{payment_id}/verify", summary="Verify or reject payment (admin)")
async def verify_payment(
    payment_id: str,
    body: VerifyPaymentRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin verifies (approves) or rejects a payment receipt."""
    if user.active_role.value != "admin":
        raise HTTPException(403, "Only admins can verify payments")
    try:
        result = await PaymentService.admin_verify_payment(
            db, payment_id=payment_id, admin_id=user.id,
            approve=body.approve, notes=body.notes,
            rejection_reason=body.rejection_reason,
        )
        await db.commit()
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{payment_id}/mark-paid", summary="Mark payment as paid (admin)")
async def mark_paid(
    payment_id: str,
    body: MarkPaidRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin directly marks a payment as paid (for advance/deposit — no screenshot needed)."""
    if user.active_role.value != "admin":
        raise HTTPException(403, "Only admins can mark payments as paid")
    try:
        result = await PaymentService.admin_mark_paid(
            db, payment_id=payment_id, admin_id=user.id, notes=body.notes,
        )
        await db.commit()
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/generate-rent", summary="Generate monthly rent records (admin)")
async def generate_rent(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin triggers generation of monthly rent records for all active agreements."""
    if user.active_role.value != "admin":
        raise HTTPException(403, "Only admins can trigger rent generation")
    
    count = await RentCycleService.generate_monthly_rent_records(db)
    await db.commit()
    return {"success": True, "message": f"Generated {count} rent records for the current month."}
