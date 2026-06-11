"""LuxeLife API — Property onboarding workflow service."""

from datetime import datetime, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.onboarding_workflow import (
    ChecklistApprovalStatus,
    OnboardingWorkflowState,
    PropertyOnboardingWorkflow,
)
from app.models.property import Property, PropertyManager, PropertyManagerRole
from app.models.user import User


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
        workflow.visit_request_id = slot_id
        workflow.state = OnboardingWorkflowState.VISIT_REQUESTED
        workflow.visit_requested_at = datetime.now(timezone.utc)
        workflow.last_action_by = actor_id
        workflow.last_action_notes = "Visit requested"
        await db.flush()
        return workflow

    @staticmethod
    async def mark_visit_scheduled(
        db: AsyncSession,
        *,
        property_id: str,
        tenant_id: str,
        owner_id: str,
        visit_request_id: str,
        actor_id: str,
    ) -> PropertyOnboardingWorkflow:
        """Stamp visit_scheduled_at when both parties accept a proposal.

        Idempotent: re-stamps timestamp on every accept (e.g. after a reschedule)
        but only advances the workflow state if it is still in an early stage —
        we never regress past VISIT_APPROVED / AGREEMENT_GENERATED.
        """
        workflow = await _get_or_create_workflow(db, property_id=property_id, tenant_id=tenant_id, owner_id=owner_id)
        workflow.visit_request_id = visit_request_id
        workflow.visit_scheduled_at = datetime.now(timezone.utc)
        if workflow.state in (
            OnboardingWorkflowState.INTERESTED,
            OnboardingWorkflowState.VISIT_REQUESTED,
        ):
            workflow.state = OnboardingWorkflowState.VISIT_SCHEDULED
        workflow.last_action_by = actor_id
        workflow.last_action_notes = "Visit scheduled"
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
        workflow.visit_request_id = slot_id
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
        # Whichever of {advance, police, original-agreement} is approved last
        # is the one that completes the workflow. Without this call the row
        # gets stuck at ADVANCE_APPROVED whenever advance happens last.
        _try_activate_tenant(workflow)
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
        restrict_to_property_ids: list[str] | None = None,
    ) -> list[dict]:
        if restrict_to_property_ids is not None and len(restrict_to_property_ids) == 0:
            return []

        query = select(PropertyOnboardingWorkflow)
        if owner_id:
            query = query.where(PropertyOnboardingWorkflow.owner_id == owner_id)
        if tenant_id:
            query = query.where(PropertyOnboardingWorkflow.tenant_id == tenant_id)
        if property_id:
            query = query.where(PropertyOnboardingWorkflow.property_id == property_id)
        if restrict_to_property_ids is not None:
            query = query.where(PropertyOnboardingWorkflow.property_id.in_(restrict_to_property_ids))
        if state:
            query = query.where(PropertyOnboardingWorkflow.state == OnboardingWorkflowState.from_api(state))

        query = query.order_by(PropertyOnboardingWorkflow.created_at.desc())
        result = await db.execute(query)
        workflows = result.scalars().all()
        enrichment = await _build_enrichment(db, workflows)
        return [_to_enriched_dict(wf, enrichment) for wf in workflows]

    @staticmethod
    async def cancel_workflow(
        db: AsyncSession,
        *,
        workflow_id: str,
        actor_id: str,
        reason: str,
    ) -> dict:
        workflow = await _get_by_id(db, workflow_id)
        if not workflow:
            raise ValueError("Workflow not found")
        if workflow.state == OnboardingWorkflowState.CANCELLED:
            raise ValueError("Workflow is already cancelled")
        if workflow.state == OnboardingWorkflowState.TENANT_ACTIVATED:
            raise ValueError("Cannot cancel a completed onboarding")

        workflow.state = OnboardingWorkflowState.CANCELLED
        workflow.cancelled_at = datetime.now(timezone.utc)
        workflow.cancelled_by = actor_id
        workflow.cancellation_reason = reason
        workflow.last_action_by = actor_id
        workflow.last_action_notes = f"Onboarding cancelled: {reason}"
        await db.flush()
        await db.refresh(workflow)

        enrichment = await _build_enrichment(db, [workflow])
        return _to_enriched_dict(workflow, enrichment)

    @staticmethod
    async def get_workflow(db: AsyncSession, *, workflow_id: str) -> dict | None:
        wf = await _get_by_id(db, workflow_id)
        if not wf:
            return None
        enrichment = await _build_enrichment(db, [wf])
        return _to_enriched_dict(wf, enrichment)

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
        # Backfill the step-1 timestamp on legacy rows that pre-date this guard,
        # otherwise the tracker shows "Visit Requested" as pending forever.
        if not existing.visit_requested_at:
            existing.visit_requested_at = datetime.now(timezone.utc)
        return existing

    workflow = PropertyOnboardingWorkflow(
        property_id=property_id,
        tenant_id=tenant_id,
        owner_id=owner_id,
        state=OnboardingWorkflowState.VISIT_REQUESTED,
        visit_requested_at=datetime.now(timezone.utc),
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


async def _build_enrichment(db: AsyncSession, workflows: list) -> dict:
    """
    Batch-load names/managers/advance-payments for a set of workflows.

    Returns:
        {
            "properties":         {property_id: property_name},
            "users":              {user_id: user_name},
            "managers":           {property_id: {"id": manager_id, "name": manager_name}},
            "advance_payments":   {agreement_id: {id, status, screenshot_url, amount, label, rejection_reason}},
        }
    """
    property_ids = {wf.property_id for wf in workflows if wf.property_id}
    user_ids = set()
    for wf in workflows:
        if wf.tenant_id:
            user_ids.add(wf.tenant_id)
        if wf.owner_id:
            user_ids.add(wf.owner_id)

    properties: dict[str, str] = {}
    if property_ids:
        rows = (await db.execute(
            select(Property.id, Property.name).where(Property.id.in_(property_ids))
        )).all()
        properties = {pid: name for pid, name in rows}

    # Primary manager per property (fall back to any manager if no PRIMARY exists)
    managers: dict[str, dict] = {}
    if property_ids:
        pm_rows = (await db.execute(
            select(PropertyManager.property_id, PropertyManager.manager_id, PropertyManager.role)
            .where(PropertyManager.property_id.in_(property_ids))
            .order_by(PropertyManager.assigned_at.asc())
        )).all()
        for pid, mid, role in pm_rows:
            existing = managers.get(pid)
            if not existing or (existing.get("_role") != PropertyManagerRole.PRIMARY.value and role == PropertyManagerRole.PRIMARY):
                managers[pid] = {"id": mid, "_role": role.value if hasattr(role, "value") else role}
                user_ids.add(mid)

    users: dict[str, str] = {}
    if user_ids:
        u_rows = (await db.execute(
            select(User.id, User.name).where(User.id.in_(user_ids))
        )).all()
        users = {uid: name for uid, name in u_rows}

    # Attach manager names + drop the helper _role key
    for pid, m in managers.items():
        m["name"] = users.get(m["id"])
        m.pop("_role", None)

    # Resolve the advance/security-deposit payment linked to each workflow's
    # agreement so the manager can review + approve it directly from the
    # onboarding screen (instead of having to hop to ManagerPaymentReview).
    advance_payments: dict[str, dict] = {}
    agreement_ids = {wf.agreement_id for wf in workflows if wf.agreement_id}
    if agreement_ids:
        from app.models.agreement import Agreement
        from app.models.payment import Payment

        ag_rows = (await db.execute(
            select(Agreement.id, Agreement.deposit_payment_id)
            .where(Agreement.id.in_(agreement_ids))
        )).all()
        payment_to_agreement = {pid: aid for aid, pid in ag_rows if pid}

        if payment_to_agreement:
            pay_rows = (await db.execute(
                select(Payment).where(Payment.id.in_(list(payment_to_agreement.keys())))
            )).scalars().all()
            for p in pay_rows:
                aid = payment_to_agreement.get(p.id)
                if aid:
                    advance_payments[aid] = {
                        "id": p.id,
                        "status": p.status.value,
                        "screenshot_url": p.screenshot_url,
                        "amount": p.amount,
                        "label": p.label,
                        "rejection_reason": p.rejection_reason,
                    }

    return {
        "properties": properties,
        "users": users,
        "managers": managers,
        "advance_payments": advance_payments,
    }


def _to_enriched_dict(workflow: PropertyOnboardingWorkflow, enrichment: dict) -> dict:
    base = _to_dict(workflow)
    base["property_name"] = enrichment["properties"].get(workflow.property_id)
    base["tenant_name"] = enrichment["users"].get(workflow.tenant_id)
    base["owner_name"] = enrichment["users"].get(workflow.owner_id)
    pm = enrichment["managers"].get(workflow.property_id) or {}
    base["primary_manager_id"] = pm.get("id")
    base["primary_manager_name"] = pm.get("name")
    base["advance_payment"] = enrichment.get("advance_payments", {}).get(workflow.agreement_id)
    return base


def _to_dict(workflow: PropertyOnboardingWorkflow) -> dict:
    return {
        "id": workflow.id,
        "state": workflow.state.api_value,
        "property_id": workflow.property_id,
        "tenant_id": workflow.tenant_id,
        "owner_id": workflow.owner_id,
        "manager_id": workflow.manager_id,
        "agreement_id": workflow.agreement_id,
        "visit_request_id": workflow.visit_request_id,
        "visit_requested_at": workflow.visit_requested_at.isoformat() if workflow.visit_requested_at else None,
        "visit_scheduled_at": workflow.visit_scheduled_at.isoformat() if workflow.visit_scheduled_at else None,
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
        "police_verification_status": workflow.police_verification_status.api_value,
        "police_verification_reviewed_by": workflow.police_verification_reviewed_by,
        "police_verification_rejection_reason": workflow.police_verification_rejection_reason,
        "original_agreement_doc_url": workflow.original_agreement_doc_url,
        "original_agreement_status": workflow.original_agreement_status.api_value,
        "original_agreement_reviewed_by": workflow.original_agreement_reviewed_by,
        "original_agreement_rejection_reason": workflow.original_agreement_rejection_reason,
        "last_action_by": workflow.last_action_by,
        "last_action_notes": workflow.last_action_notes,
        "cancelled_at": workflow.cancelled_at.isoformat() if workflow.cancelled_at else None,
        "cancelled_by": workflow.cancelled_by,
        "cancellation_reason": workflow.cancellation_reason,
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
