"""
LuxeLife API — Admin Calendar routes.

Manages admin availability slots and tenant visit bookings.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.services.calendar_service import CalendarService

router = APIRouter(prefix="/calendar", tags=["Calendar"])


# ── Schemas ──

class SlotCreate(BaseModel):
    date: str = Field(..., description="Date in YYYY-MM-DD format")
    start_time: str = Field(..., description="Start time in HH:MM format")
    end_time: str = Field(..., description="End time in HH:MM format")


class BulkSlotCreate(BaseModel):
    slots: list[SlotCreate] = Field(..., min_length=1, max_length=20)


class BookSlotRequest(BaseModel):
    property_id: str = Field(..., min_length=1)


class CompleteVisitRequest(BaseModel):
    approve: bool
    notes: str | None = None
    rejection_reason: str | None = None


class CreateBlockRuleRequest(BaseModel):
    start_time: str = Field(..., description="Start time in HH:MM format")
    end_time: str = Field(..., description="End time in HH:MM format")
    duration_type: str = Field(..., description="forever | days | weeks | months | custom")
    duration_value: int | None = Field(None, ge=1)
    effective_from: str | None = Field(None, description="YYYY-MM-DD; defaults to today (IST)")
    effective_to: str | None = Field(None, description="YYYY-MM-DD; required for custom")
    reason: str | None = Field(None, max_length=500)


# ── Admin: Manage Slots ──

@router.post("/slots", summary="Create availability slots (admin)")
async def create_slots(
    body: BulkSlotCreate,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin creates one or more availability slots."""
    if user.active_role.value != "admin":
        raise HTTPException(403, "Only admins can create slots")
    try:
        result = await CalendarService.create_slots(
            db, admin_id=user.id, slots=[s.model_dump() for s in body.slots]
        )
        await db.commit()
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/slots", summary="List slots")
async def list_slots(
    status: str | None = Query(None, description="Filter by status: available, booked, completed, cancelled"),
    from_date: str | None = Query(None, description="From date YYYY-MM-DD"),
    to_date: str | None = Query(None, description="To date YYYY-MM-DD"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List slots. Admins see all their slots, tenants see available slots."""
    if user.active_role.value == "admin":
        return await CalendarService.list_slots(
            db,
            admin_id=user.id,
            status=status,
            from_date=date.fromisoformat(from_date) if from_date else None,
            to_date=date.fromisoformat(to_date) if to_date else None,
            page=page, limit=limit,
        )
    else:
        return await CalendarService.get_available_slots(
            db,
            from_date=date.fromisoformat(from_date) if from_date else None,
            to_date=date.fromisoformat(to_date) if to_date else None,
            page=page, limit=limit,
        )


@router.post("/blocks", summary="Create block rule (admin)")
async def create_block_rule(
    body: CreateBlockRuleRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.active_role.value != "admin":
        raise HTTPException(403, "Only admins can create block rules")

    try:
        result = await CalendarService.create_block_rule(
            db,
            admin_id=user.id,
            start_time_str=body.start_time,
            end_time_str=body.end_time,
            duration_type=body.duration_type,
            duration_value=body.duration_value,
            effective_from=date.fromisoformat(body.effective_from) if body.effective_from else None,
            effective_to=date.fromisoformat(body.effective_to) if body.effective_to else None,
            reason=body.reason,
        )
        await db.commit()
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/blocks", summary="List block rules (admin)")
async def list_block_rules(
    active_only: bool = Query(False),
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=200),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.active_role.value != "admin":
        raise HTTPException(403, "Only admins can view block rules")

    return await CalendarService.list_block_rules(
        db,
        admin_id=user.id,
        active_only=active_only,
        page=page,
        limit=limit,
    )


@router.delete("/blocks/{block_id}", summary="Delete block rule (admin)")
async def delete_block_rule(
    block_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.active_role.value != "admin":
        raise HTTPException(403, "Only admins can delete block rules")

    try:
        result = await CalendarService.delete_block_rule(db, admin_id=user.id, block_id=block_id)
        await db.commit()
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))


@router.delete("/slots/{slot_id}", summary="Delete/cancel a slot (admin)")
async def delete_slot(
    slot_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin cancels a slot. If booked, tenant is notified."""
    if user.active_role.value != "admin":
        raise HTTPException(403, "Only admins can delete slots")
    try:
        result = await CalendarService.delete_slot(db, slot_id, user.id)
        await db.commit()
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))


# ── Tenant: Book Visits ──

@router.post("/slots/{slot_id}/book", summary="Book a visit slot (tenant)")
async def book_slot(
    slot_id: str,
    body: BookSlotRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Tenant books an available slot for a property visit."""
    try:
        result = await CalendarService.book_slot(
            db, slot_id=slot_id, tenant_id=user.id, property_id=body.property_id,
        )
        await db.commit()
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/slots/{slot_id}/cancel", summary="Cancel a booking")
async def cancel_booking(
    slot_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a booking (tenant or admin)."""
    try:
        result = await CalendarService.cancel_booking(db, slot_id, user.id)
        await db.commit()
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))


# ── Admin: Complete Visit ──

@router.post("/slots/{slot_id}/complete", summary="Complete visit and approve/reject (admin)")
async def complete_visit(
    slot_id: str,
    body: CompleteVisitRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin marks visit as completed and approves or rejects the tenant."""
    if user.active_role.value != "admin":
        raise HTTPException(403, "Only admins can complete visits")
    try:
        result = await CalendarService.complete_visit(
            db, slot_id=slot_id, admin_id=user.id,
            approve=body.approve, notes=body.notes,
            rejection_reason=body.rejection_reason,
        )
        await db.commit()
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))


# ── Tenant: My Visits ──

@router.get("/my-visits", summary="List my booked visits (tenant)")
async def my_visits(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all visits booked by the current tenant."""
    return await CalendarService.list_tenant_visits(
        db,
        tenant_id=user.id,
        page=1,
        limit=50,
    )
