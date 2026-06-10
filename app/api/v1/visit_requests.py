"""
LuxeLife API — Visit Request routes (DB v2.2).

Negotiation flow:
  tenant POST /visit-requests              -> first proposal
  any party POST /visit-requests/{id}/propose   -> counter-propose
  any party POST /visit-requests/{id}/accept    -> accept current proposal -> CONFIRMED
  any party POST /visit-requests/{id}/reject    -> close with reason -> REJECTED
  any party POST /visit-requests/{id}/reschedule-> re-open a CONFIRMED visit
  any party POST /visit-requests/{id}/cancel    -> cancel an in-flight visit
  manager  POST /visit-requests/{id}/complete   -> mark COMPLETED + approve/reject tenant
  GET      /visit-requests                       -> list scoped by role
  GET      /visit-requests/{id}                  -> request + proposal history
"""

from datetime import date, time as dt_time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.services.visit_request_service import VisitRequestService

router = APIRouter(prefix="/visit-requests", tags=["Visit Requests"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class CreateVisitRequestBody(BaseModel):
    property_id: str = Field(..., min_length=1)
    requested_date: str = Field(..., description="YYYY-MM-DD")
    start_time: str = Field(..., description="HH:MM (24h)")
    end_time: str = Field(..., description="HH:MM (24h)")
    message: str | None = Field(None, max_length=500)


class ProposeBody(BaseModel):
    proposed_date: str = Field(..., description="YYYY-MM-DD")
    start_time: str = Field(..., description="HH:MM (24h)")
    end_time: str = Field(..., description="HH:MM (24h)")
    message: str | None = Field(None, max_length=500)


class RejectBody(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class CancelBody(BaseModel):
    reason: str | None = Field(None, max_length=500)


class CompleteBody(BaseModel):
    approve: bool
    notes: str | None = None
    rejection_reason: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except Exception:
        raise HTTPException(400, "Invalid date — expected YYYY-MM-DD")


def _parse_time(s: str) -> dt_time:
    try:
        parts = s.split(":")
        return dt_time(int(parts[0]), int(parts[1]))
    except Exception:
        raise HTTPException(400, "Invalid time — expected HH:MM (24h)")


def _err(exc) -> HTTPException:
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    return HTTPException(400, str(exc))


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("", status_code=201, summary="Tenant creates a visit request")
async def create(
    body: CreateVisitRequestBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.active_role.api_value != "tenant":
        raise HTTPException(403, "Only tenants can create visit requests")
    try:
        return await VisitRequestService.create_visit_request(
            db,
            property_id=body.property_id,
            tenant_id=user.id,
            requested_date=_parse_date(body.requested_date),
            start_time=_parse_time(body.start_time),
            end_time=_parse_time(body.end_time),
            message=body.message,
        )
    except ValueError as e:
        raise _err(e)


@router.post("/{visit_request_id}/propose", summary="Counter-propose a new time")
async def propose(
    visit_request_id: str,
    body: ProposeBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await VisitRequestService.propose(
            db,
            visit_request_id=visit_request_id,
            actor=user,
            proposed_date=_parse_date(body.proposed_date),
            start_time=_parse_time(body.start_time),
            end_time=_parse_time(body.end_time),
            message=body.message,
        )
    except (ValueError, PermissionError) as e:
        raise _err(e)


@router.post("/{visit_request_id}/accept", summary="Accept the current proposal")
async def accept(
    visit_request_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await VisitRequestService.accept(
            db, visit_request_id=visit_request_id, actor=user
        )
    except (ValueError, PermissionError) as e:
        raise _err(e)


@router.post("/{visit_request_id}/reject", summary="Reject the current proposal (closes request)")
async def reject(
    visit_request_id: str,
    body: RejectBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await VisitRequestService.reject(
            db, visit_request_id=visit_request_id, actor=user, reason=body.reason
        )
    except (ValueError, PermissionError) as e:
        raise _err(e)


@router.post("/{visit_request_id}/reschedule", summary="Re-open a confirmed visit with a new proposal")
async def reschedule(
    visit_request_id: str,
    body: ProposeBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await VisitRequestService.reschedule(
            db,
            visit_request_id=visit_request_id,
            actor=user,
            proposed_date=_parse_date(body.proposed_date),
            start_time=_parse_time(body.start_time),
            end_time=_parse_time(body.end_time),
            message=body.message,
        )
    except (ValueError, PermissionError) as e:
        raise _err(e)


@router.post("/{visit_request_id}/cancel", summary="Cancel an in-flight visit")
async def cancel(
    visit_request_id: str,
    body: CancelBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await VisitRequestService.cancel(
            db, visit_request_id=visit_request_id, actor=user, reason=body.reason
        )
    except (ValueError, PermissionError) as e:
        raise _err(e)


@router.post("/{visit_request_id}/complete", summary="Manager marks visit complete + approve/reject tenant")
async def complete(
    visit_request_id: str,
    body: CompleteBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await VisitRequestService.complete(
            db,
            visit_request_id=visit_request_id,
            actor=user,
            approve=body.approve,
            notes=body.notes,
            rejection_reason=body.rejection_reason,
        )
    except (ValueError, PermissionError) as e:
        raise _err(e)


@router.get("/{visit_request_id}", summary="Get a visit request with its full proposal history")
async def get_one(
    visit_request_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await VisitRequestService.get_visit_request(
            db, visit_request_id=visit_request_id, user=user
        )
    except (ValueError, PermissionError) as e:
        raise _err(e)


@router.get("", summary="List visit requests (scoped by role)")
async def list_all(
    status: str | None = Query(None),
    property_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await VisitRequestService.list_visit_requests(
            db,
            user=user,
            status=status,
            property_id=property_id,
            page=page,
            limit=limit,
        )
    except ValueError as e:
        raise _err(e)
