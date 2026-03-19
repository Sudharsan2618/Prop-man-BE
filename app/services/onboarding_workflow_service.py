"""LuxeLife API — Property onboarding workflow service."""

from datetime import datetime, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.onboarding_workflow import (
    ChecklistApprovalStatus,
    OnboardingWorkflowState,
    PropertyOnboardingWorkflow,
)


class OnboardingWorkflowService:
    """Persists state transitions for tenant onboarding lifecycle."""

    @staticmethod
    async def mark_visit_booked(
        db: AsyncSession,
        *,
        property_id: str,
        tenant_id: str,
        owner_id: str,
        slot_id: str,
        actor_id: str,
    ) -> PropertyOnboardingWorkflow:
        workflow = await _get_or_create_workflow(db, property_id=property_id, tenant_id=tenant_id, owner_id=owner_id)
        workflow.slot_id = slot_id
        workflow.state = OnboardingWorkflowState.VISIT_BOOKED
        workflow.visit_booked_at = datetime.now(timezone.utc)
        workflow.last_action_by = actor_id
        workflow.last_action_notes = "Visit booked"
        await db.flush()
        return workflow

    @staticmethod
    async def mark_visit_result(
        db: AsyncSession,
        *,
        property_id: str,
        tenant_id: str,
        owner_id: str,
        slot_id: str,
        actor_id: str,
        approved: bool,
    ) -> PropertyOnboardingWorkflow:
        workflow = await _get_or_create_workflow(db, property_id=property_id, tenant_id=tenant_id, owner_id=owner_id)
        workflow.slot_id = slot_id
        workflow.state = (
            OnboardingWorkflowState.VISIT_APPROVED
            if approved
            else OnboardingWorkflowState.VISIT_REJECTED
        )
        now = datetime.now(timezone.utc)
        if approved:
            workflow.visit_approved_at = now
            workflow.last_action_notes = "Visit approved"
        else:
            workflow.visit_rejected_at = now
            workflow.last_action_notes = "Visit rejected"
        workflow.last_action_by = actor_id
        await db.flush()
        return workflow

    @staticmethod
    async def mark_agreement_generated(
        db: AsyncSession,
        *,
        property_id: str,
        tenant_id: str,
        owner_id: str,
        agreement_id: str,
        actor_id: str,
    ) -> PropertyOnboardingWorkflow:
        workflow = await _get_or_create_workflow(db, property_id=property_id, tenant_id=tenant_id, owner_id=owner_id)
        workflow.agreement_id = agreement_id
        workflow.state = OnboardingWorkflowState.AGREEMENT_GENERATED
        workflow.agreement_generated_at = datetime.now(timezone.utc)
        workflow.last_action_by = actor_id
        workflow.last_action_notes = "Agreement generated"
        await db.flush()
        return workflow

    @staticmethod
    async def mark_tenant_signed(
        db: AsyncSession,
        *,
        agreement_id: str,
        actor_id: str,
    ) -> PropertyOnboardingWorkflow | None:
        workflow = await _get_by_agreement(db, agreement_id)
        if not workflow:
            return None
        workflow.state = OnboardingWorkflowState.TENANT_SIGNED
        workflow.tenant_signed_at = datetime.now(timezone.utc)
        workflow.last_action_by = actor_id
        workflow.last_action_notes = "Tenant signed agreement"
        await db.flush()
        return workflow

    @staticmethod
    async def mark_advance_submitted(
        db: AsyncSession,
        *,
        property_id: str,
        tenant_id: str,
        actor_id: str,
    ) -> PropertyOnboardingWorkflow | None:
        workflow = await _get_by_property_tenant(db, property_id=property_id, tenant_id=tenant_id)
        if not workflow:
            return None
        workflow.state = OnboardingWorkflowState.ADVANCE_SUBMITTED
        workflow.advance_submitted_at = datetime.now(timezone.utc)
        workflow.last_action_by = actor_id
        workflow.last_action_notes = "Advance receipt submitted"
        await db.flush()
        return workflow

    @staticmethod
    async def mark_advance_approved(
        db: AsyncSession,
        *,
        agreement_id: str,
        actor_id: str,
    ) -> PropertyOnboardingWorkflow | None:
        workflow = await _get_by_agreement(db, agreement_id)
        if not workflow:
            return None
        workflow.state = OnboardingWorkflowState.ADVANCE_APPROVED
        workflow.advance_approved_at = datetime.now(timezone.utc)
        workflow.last_action_by = actor_id
        workflow.last_action_notes = "Advance approved"
        await db.flush()
        return workflow

    @staticmethod
    async def mark_tenant_activated(
        db: AsyncSession,
        *,
        agreement_id: str,
        actor_id: str,
    ) -> PropertyOnboardingWorkflow | None:
        workflow = await _get_by_agreement(db, agreement_id)
        if not workflow:
            return None
        workflow.state = OnboardingWorkflowState.TENANT_ACTIVATED
        workflow.tenant_activated_at = datetime.now(timezone.utc)
        workflow.last_action_by = actor_id
        workflow.last_action_notes = "Tenant activated"
        await db.flush()
        return workflow

    @staticmethod
    async def list_workflows(
        db: AsyncSession,
        *,
        owner_id: str | None = None,
        tenant_id: str | None = None,
        property_id: str | None = None,
        state: str | None = None,
    ) -> list[dict]:
        query = select(PropertyOnboardingWorkflow)
        if owner_id:
            query = query.where(PropertyOnboardingWorkflow.owner_id == owner_id)
        if tenant_id:
            query = query.where(PropertyOnboardingWorkflow.tenant_id == tenant_id)
        if property_id:
            query = query.where(PropertyOnboardingWorkflow.property_id == property_id)
        if state:
            query = query.where(PropertyOnboardingWorkflow.state == OnboardingWorkflowState(state))

        query = query.order_by(PropertyOnboardingWorkflow.created_at.desc())
        result = await db.execute(query)
        return [_to_dict(wf) for wf in result.scalars().all()]

    @staticmethod
    async def submit_police_verification(
        db: AsyncSession,
        *,
        workflow_id: str,
        tenant_id: str,
        document_url: str,
    ) -> dict:
        workflow = await _get_by_id(db, workflow_id)
        if not workflow:
            raise ValueError("Workflow not found")
        if workflow.tenant_id != tenant_id:
            raise PermissionError("You can only submit documents for your own workflow")

        workflow.police_verification_doc_url = document_url
        workflow.police_verification_status = ChecklistApprovalStatus.SUBMITTED
        workflow.police_verification_rejection_reason = None
        workflow.last_action_by = tenant_id
        workflow.last_action_notes = "Police verification document submitted"
        await db.flush()
        await db.refresh(workflow)
        return _to_dict(workflow)

    @staticmethod
    async def review_police_verification(
        db: AsyncSession,
        *,
        workflow_id: str,
        admin_id: str,
        approve: bool,
        rejection_reason: str | None = None,
    ) -> dict:
        workflow = await _get_by_id(db, workflow_id)
        if not workflow:
            raise ValueError("Workflow not found")
        if not workflow.police_verification_doc_url:
            raise ValueError("Police verification document not submitted")
        if not approve and not rejection_reason:
            raise ValueError("Rejection reason is required")

        if approve:
            workflow.police_verification_status = ChecklistApprovalStatus.APPROVED
            workflow.police_verification_completed_at = datetime.now(timezone.utc)
            workflow.state = OnboardingWorkflowState.POLICE_VERIFICATION_COMPLETED
            workflow.police_verification_rejection_reason = None
            workflow.last_action_notes = "Police verification approved"
            _try_activate_tenant(workflow)
        else:
            workflow.police_verification_status = ChecklistApprovalStatus.REJECTED
            workflow.police_verification_rejection_reason = rejection_reason
            workflow.last_action_notes = "Police verification rejected"

        workflow.police_verification_reviewed_by = admin_id
        workflow.last_action_by = admin_id
        await db.flush()
        await db.refresh(workflow)
        return _to_dict(workflow)

    @staticmethod
    async def submit_original_agreement(
        db: AsyncSession,
        *,
        workflow_id: str,
        tenant_id: str,
        document_url: str,
    ) -> dict:
        workflow = await _get_by_id(db, workflow_id)
        if not workflow:
            raise ValueError("Workflow not found")
        if workflow.tenant_id != tenant_id:
            raise PermissionError("You can only submit documents for your own workflow")

        workflow.original_agreement_doc_url = document_url
        workflow.original_agreement_status = ChecklistApprovalStatus.SUBMITTED
        workflow.original_agreement_rejection_reason = None
        workflow.last_action_by = tenant_id
        workflow.last_action_notes = "Original agreement document submitted"
        await db.flush()
        await db.refresh(workflow)
        return _to_dict(workflow)

    @staticmethod
    async def review_original_agreement(
        db: AsyncSession,
        *,
        workflow_id: str,
        admin_id: str,
        approve: bool,
        rejection_reason: str | None = None,
    ) -> dict:
        workflow = await _get_by_id(db, workflow_id)
        if not workflow:
            raise ValueError("Workflow not found")
        if not workflow.original_agreement_doc_url:
            raise ValueError("Original agreement document not submitted")
        if not approve and not rejection_reason:
            raise ValueError("Rejection reason is required")

        if approve:
            workflow.original_agreement_status = ChecklistApprovalStatus.APPROVED
            workflow.original_agreement_uploaded_at = datetime.now(timezone.utc)
            workflow.state = OnboardingWorkflowState.ORIGINAL_AGREEMENT_UPLOADED
            workflow.original_agreement_rejection_reason = None
            workflow.last_action_notes = "Original agreement approved"
            _try_activate_tenant(workflow)
        else:
            workflow.original_agreement_status = ChecklistApprovalStatus.REJECTED
            workflow.original_agreement_rejection_reason = rejection_reason
            workflow.last_action_notes = "Original agreement rejected"

        workflow.original_agreement_reviewed_by = admin_id
        workflow.last_action_by = admin_id
        await db.flush()
        await db.refresh(workflow)
        return _to_dict(workflow)


async def _get_or_create_workflow(
    db: AsyncSession,
    *,
    property_id: str,
    tenant_id: str,
    owner_id: str,
) -> PropertyOnboardingWorkflow:
    existing = await _get_by_property_tenant(db, property_id=property_id, tenant_id=tenant_id)
    if existing:
        if not existing.owner_id:
            existing.owner_id = owner_id
        return existing

    workflow = PropertyOnboardingWorkflow(
        property_id=property_id,
        tenant_id=tenant_id,
        owner_id=owner_id,
        state=OnboardingWorkflowState.VISIT_BOOKED,
    )
    db.add(workflow)
    await db.flush()
    return workflow


async def _get_by_property_tenant(
    db: AsyncSession,
    *,
    property_id: str,
    tenant_id: str,
) -> PropertyOnboardingWorkflow | None:
    result = await db.execute(
        select(PropertyOnboardingWorkflow).where(
            and_(
                PropertyOnboardingWorkflow.property_id == property_id,
                PropertyOnboardingWorkflow.tenant_id == tenant_id,
            )
        )
    )
    return result.scalar_one_or_none()


async def _get_by_id(db: AsyncSession, workflow_id: str) -> PropertyOnboardingWorkflow | None:
    result = await db.execute(
        select(PropertyOnboardingWorkflow).where(PropertyOnboardingWorkflow.id == workflow_id)
    )
    return result.scalar_one_or_none()


def _to_dict(workflow: PropertyOnboardingWorkflow) -> dict:
    return {
        "id": workflow.id,
        "state": workflow.state.value,
        "property_id": workflow.property_id,
        "tenant_id": workflow.tenant_id,
        "owner_id": workflow.owner_id,
        "agreement_id": workflow.agreement_id,
        "slot_id": workflow.slot_id,
        "visit_booked_at": workflow.visit_booked_at.isoformat() if workflow.visit_booked_at else None,
        "visit_approved_at": workflow.visit_approved_at.isoformat() if workflow.visit_approved_at else None,
        "visit_rejected_at": workflow.visit_rejected_at.isoformat() if workflow.visit_rejected_at else None,
        "agreement_generated_at": workflow.agreement_generated_at.isoformat() if workflow.agreement_generated_at else None,
        "tenant_signed_at": workflow.tenant_signed_at.isoformat() if workflow.tenant_signed_at else None,
        "advance_submitted_at": workflow.advance_submitted_at.isoformat() if workflow.advance_submitted_at else None,
        "advance_approved_at": workflow.advance_approved_at.isoformat() if workflow.advance_approved_at else None,
        "police_verification_completed_at": workflow.police_verification_completed_at.isoformat() if workflow.police_verification_completed_at else None,
        "original_agreement_uploaded_at": workflow.original_agreement_uploaded_at.isoformat() if workflow.original_agreement_uploaded_at else None,
        "tenant_activated_at": workflow.tenant_activated_at.isoformat() if workflow.tenant_activated_at else None,
        "police_verification_doc_url": workflow.police_verification_doc_url,
        "police_verification_status": workflow.police_verification_status.value,
        "police_verification_reviewed_by": workflow.police_verification_reviewed_by,
        "police_verification_rejection_reason": workflow.police_verification_rejection_reason,
        "original_agreement_doc_url": workflow.original_agreement_doc_url,
        "original_agreement_status": workflow.original_agreement_status.value,
        "original_agreement_reviewed_by": workflow.original_agreement_reviewed_by,
        "original_agreement_rejection_reason": workflow.original_agreement_rejection_reason,
        "last_action_by": workflow.last_action_by,
        "last_action_notes": workflow.last_action_notes,
        "created_at": workflow.created_at.isoformat() if workflow.created_at else None,
        "updated_at": workflow.updated_at.isoformat() if workflow.updated_at else None,
    }


def _try_activate_tenant(workflow: PropertyOnboardingWorkflow) -> None:
    """Promote workflow to tenant_activated only when all mandatory gates are approved."""
    if (
        workflow.advance_approved_at
        and workflow.police_verification_status == ChecklistApprovalStatus.APPROVED
        and workflow.original_agreement_status == ChecklistApprovalStatus.APPROVED
    ):
        workflow.state = OnboardingWorkflowState.TENANT_ACTIVATED
        if not workflow.tenant_activated_at:
            workflow.tenant_activated_at = datetime.now(timezone.utc)
        workflow.last_action_notes = "Tenant activated"


async def _get_by_agreement(db: AsyncSession, agreement_id: str) -> PropertyOnboardingWorkflow | None:
    result = await db.execute(
        select(PropertyOnboardingWorkflow).where(
            PropertyOnboardingWorkflow.agreement_id == agreement_id
        )
    )
    return result.scalar_one_or_none()
