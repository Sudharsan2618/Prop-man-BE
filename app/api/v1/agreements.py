"""
LuxeLife API — Agreement routes.

Admin-driven agreement lifecycle:
- Auto-generated after admin approves visit
- Tenant signs digitally
- Admin confirms advance → agreement becomes ACTIVE
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.services.agreement_service import AgreementService

router = APIRouter(prefix="/agreements", tags=["Agreements"])


# ── Schemas ──

class SignAgreementRequest(BaseModel):
    signature: str = Field(..., min_length=1, max_length=512)


class ConfirmAdvanceRequest(BaseModel):
    notes: str | None = None


# ── Routes ──

@router.get("", summary="List agreements")
async def list_agreements(
    status: str | None = Query(None),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List agreements for the current user."""
    role = user.active_role.value
    if role == "tenant":
        return {"success": True, "data": await AgreementService.list_agreements(db, tenant_id=user.id, status=status)}
    elif role == "owner":
        return {"success": True, "data": await AgreementService.list_agreements(db, owner_id=user.id, status=status)}
    elif role == "admin":
        return {"success": True, "data": await AgreementService.list_agreements(db, status=status)}
    else:
        raise HTTPException(403, "Invalid role")


@router.get("/{agreement_id}", summary="Get agreement details")
async def get_agreement(
    agreement_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single agreement by ID."""
    data = await AgreementService.get_agreement(db, agreement_id)
    if not data:
        raise HTTPException(404, "Agreement not found")
    # Check access
    role = user.active_role.value
    if role == "admin" or data.get("tenant_id") == user.id or data.get("owner_id") == user.id:
        return {"success": True, "data": data}
    raise HTTPException(403, "Access denied")


@router.post("/{agreement_id}/sign", summary="Sign agreement")
async def sign_agreement(
    agreement_id: str,
    body: SignAgreementRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Tenant (or owner) digitally signs the agreement."""
    try:
        result = await AgreementService.sign_agreement(
            db, agreement_id=agreement_id, user_id=user.id, signature=body.signature,
        )
        await db.commit()
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))


@router.post("/{agreement_id}/confirm-advance", summary="Confirm advance payment (admin)")
async def confirm_advance(
    agreement_id: str,
    body: ConfirmAdvanceRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin confirms offline advance payment → agreement becomes ACTIVE."""
    if user.active_role.value != "admin":
        raise HTTPException(403, "Only admins can confirm advance payments")
    try:
        result = await AgreementService.admin_confirm_advance(
            db, agreement_id=agreement_id, admin_id=user.id, notes=body.notes,
        )
        await db.commit()
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
