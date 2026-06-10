"""LuxeLife API — Onboarding workflow routes."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.property import Property
from app.services.onboarding_workflow_service import OnboardingWorkflowService
from app.services.property_scope import (
    get_managed_property_ids,
    is_property_manager,
)

router = APIRouter(prefix="/onboarding-workflows", tags=["Onboarding Workflows"])


class DocumentSubmitRequest(BaseModel):
    document_url: str = Field(..., min_length=1, max_length=512)


class ReviewChecklistRequest(BaseModel):
    approve: bool
    rejection_reason: str | None = Field(default=None, max_length=500)


class CancelWorkflowRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


@router.get("")
async def list_workflows(
    state: str | None = Query(None),
    property_id: str | None = Query(None),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    role = user.active_role.api_value
    if role == "super_admin":
        return {
            "success": True,
            "data": await OnboardingWorkflowService.list_workflows(
                db,
                state=state,
                property_id=property_id,
            ),
        }
    if role == "manager":
        managed = await get_managed_property_ids(db, user.id)
        return {
            "success": True,
            "data": await OnboardingWorkflowService.list_workflows(
                db,
                state=state,
                property_id=property_id,
                restrict_to_property_ids=managed,
            ),
        }
    if role == "owner":
        return {
            "success": True,
            "data": await OnboardingWorkflowService.list_workflows(
                db,
                owner_id=user.id,
                state=state,
                property_id=property_id,
            ),
        }
    if role == "tenant":
        return {
            "success": True,
            "data": await OnboardingWorkflowService.list_workflows(
                db,
                tenant_id=user.id,
                state=state,
                property_id=property_id,
            ),
        }
    raise HTTPException(403, "Unsupported role")


@router.get("/{workflow_id}")
async def get_workflow(
    workflow_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a single onboarding workflow scoped by role."""
    wf = await OnboardingWorkflowService.get_workflow(db, workflow_id=workflow_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")

    role = user.active_role.api_value
    if role == "tenant" and wf["tenant_id"] != user.id:
        raise HTTPException(403, "Not your workflow")
    if role == "owner" and wf["owner_id"] != user.id:
        raise HTTPException(403, "Not your property's workflow")
    if role == "manager":
        if not await is_property_manager(db, wf["property_id"], user.id):
            raise HTTPException(403, "Not a manager for this property")
    return {"success": True, "data": wf}


@router.post("/{workflow_id}/cancel")
async def cancel_workflow(
    workflow_id: str,
    body: CancelWorkflowRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel an in-progress onboarding workflow. Manager (assigned) or SA only."""
    if user.active_role.api_value not in {"manager", "super_admin"}:
        raise HTTPException(403, "Only managers or super admins can cancel onboardings")
    wf = await OnboardingWorkflowService.get_workflow(db, workflow_id=workflow_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")
    if user.active_role.api_value == "manager":
        if not await is_property_manager(db, wf["property_id"], user.id):
            raise HTTPException(403, "Not a manager for this property")
    try:
        result = await OnboardingWorkflowService.cancel_workflow(
            db, workflow_id=workflow_id, actor_id=user.id, reason=body.reason,
        )
        await db.commit()
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{workflow_id}/police-verification/submit")
async def submit_police_verification(
    workflow_id: str,
    body: DocumentSubmitRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.active_role.api_value != "tenant":
        raise HTTPException(403, "Only tenants can submit police verification documents")
    try:
        result = await OnboardingWorkflowService.submit_police_verification(
            db,
            workflow_id=workflow_id,
            tenant_id=user.id,
            document_url=body.document_url,
        )
        await db.commit()
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))


@router.post("/{workflow_id}/police-verification/review")
async def review_police_verification(
    workflow_id: str,
    body: ReviewChecklistRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.active_role.api_value not in {"manager", "super_admin"}:
        raise HTTPException(403, "Only admins can review police verification")
    try:
        result = await OnboardingWorkflowService.review_police_verification(
            db,
            workflow_id=workflow_id,
            admin_id=user.id,
            approve=body.approve,
            rejection_reason=body.rejection_reason,
        )
        await db.commit()
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{workflow_id}/original-agreement/submit")
async def submit_original_agreement(
    workflow_id: str,
    body: DocumentSubmitRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.active_role.api_value != "tenant":
        raise HTTPException(403, "Only tenants can submit original agreement documents")
    try:
        result = await OnboardingWorkflowService.submit_original_agreement(
            db,
            workflow_id=workflow_id,
            tenant_id=user.id,
            document_url=body.document_url,
        )
        await db.commit()
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))


@router.post("/{workflow_id}/original-agreement/review")
async def review_original_agreement(
    workflow_id: str,
    body: ReviewChecklistRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.active_role.api_value not in {"manager", "super_admin"}:
        raise HTTPException(403, "Only admins can review original agreement documents")
    try:
        result = await OnboardingWorkflowService.review_original_agreement(
            db,
            workflow_id=workflow_id,
            admin_id=user.id,
            approve=body.approve,
            rejection_reason=body.rejection_reason,
        )
        await db.commit()
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(400, str(e))
