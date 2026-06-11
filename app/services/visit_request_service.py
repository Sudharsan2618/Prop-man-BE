"""
LuxeLife API — Visit Request service (DB v2.2).

Implements the propose / counter-propose / accept / reject / reschedule
flow between tenants and managers (with super-admin fallback when a
property has no assigned manager).

Notification routing is *dynamic*: the recipient is re-resolved on every
state change via ``property_scope.get_property_stakeholders``. That means
if a manager gets assigned to a property mid-negotiation, the next
notification automatically routes to them instead of the super-admin.
"""

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.responses import paginated_response, success_response
from app.models import generate_cuid
from app.models.property import Property
from app.models.user import Role, User
from app.models.visit_request import (
    VisitProposalResponse,
    VisitProposalRole,
    VisitRequest,
    VisitRequestProposal,
    VisitRequestStatus,
    VisitResult,
)
from app.services.notification_service import NotificationService
from app.services.property_scope import (
    get_assigned_manager_ids,
    get_managed_property_ids,
    get_super_admin_ids,
    is_property_manager,
)

logger = structlog.get_logger()

IST = ZoneInfo("Asia/Kolkata")


class VisitRequestService:
    """Manage property visit requests with full propose/counter negotiation."""

    # ── Create (tenant) ───────────────────────────────────────────────────

    @staticmethod
    async def create_visit_request(
        db: AsyncSession,
        *,
        property_id: str,
        tenant_id: str,
        requested_date: date,
        start_time: time,
        end_time: time,
        message: str | None = None,
    ) -> dict:
        """Tenant proposes a date+time slot for a property visit."""
        _validate_slot(requested_date, start_time, end_time)

        prop = await db.get(Property, property_id)
        if not prop:
            raise ValueError("Property not found")

        tenant = await db.get(User, tenant_id)
        tenant_name = tenant.name if tenant else "A tenant"

        # Block duplicate active requests for the same tenant+property.
        existing = (await db.execute(
            select(VisitRequest).where(
                and_(
                    VisitRequest.property_id == property_id,
                    VisitRequest.tenant_id == tenant_id,
                    VisitRequest.status.in_([
                        VisitRequestStatus.PENDING,
                        VisitRequestStatus.NEGOTIATING,
                        VisitRequestStatus.CONFIRMED,
                        VisitRequestStatus.APPOINTMENT_SCHEDULED,
                    ]),
                )
            )
        )).scalar_one_or_none()
        if existing:
            raise ValueError("You already have an active visit request for this property")

        # Counterparty: assigned manager if any, else super-admin fallback.
        counterparty = await _resolve_counterparty(db, property_id)

        visit = VisitRequest(
            id=generate_cuid(),
            property_id=property_id,
            tenant_id=tenant_id,
            status=VisitRequestStatus.NEGOTIATING,
            visit_result=VisitResult.PENDING,
            requested_date=requested_date,
            requested_start_time=start_time,
            requested_end_time=end_time,
            requested_message=message,
            pending_with=counterparty,
        )
        db.add(visit)
        await db.flush()

        proposal = VisitRequestProposal(
            id=generate_cuid(),
            visit_request_id=visit.id,
            proposed_by_user_id=tenant_id,
            proposed_by_role=VisitProposalRole.TENANT,
            proposed_date=requested_date,
            proposed_start_time=start_time,
            proposed_end_time=end_time,
            message=message,
            response=VisitProposalResponse.PENDING,
        )
        db.add(proposal)
        await db.flush()

        visit.current_proposal_id = proposal.id
        await db.flush()

        # Bootstrap the onboarding workflow row so the tracker shows step 1
        # ("Visit Requested") immediately, with the correct actor + timestamp.
        from app.services.onboarding_workflow_service import OnboardingWorkflowService
        await OnboardingWorkflowService.mark_visit_booked(
            db,
            property_id=property_id,
            tenant_id=tenant_id,
            owner_id=prop.owner_id,
            slot_id=visit.id,
            actor_id=tenant_id,
        )

        # Notify the counterparty (assigned manager or super-admin fallback)
        await _notify_counterparty(
            db,
            property_id=property_id,
            counterparty=counterparty,
            type="visit_requested",
            title="New Visit Request",
            body=f"{tenant_name} proposed a visit on {requested_date.isoformat()} "
                 f"{start_time.strftime('%H:%M')}–{end_time.strftime('%H:%M')} for {prop.name}.",
            actor_id=tenant_id,
            visit_id=visit.id,
        )

        logger.info("Visit request created", visit_id=visit.id, tenant_id=tenant_id, property_id=property_id)
        return success_response(data=await _visit_to_dict(db, visit, with_proposals=True))

    # ── Propose (counter-propose by tenant, manager, or SA) ───────────────

    @staticmethod
    async def propose(
        db: AsyncSession,
        *,
        visit_request_id: str,
        actor: User,
        proposed_date: date,
        start_time: time,
        end_time: time,
        message: str | None = None,
    ) -> dict:
        """Counter-propose a different date/time. The previous open proposal
        is marked SUPERSEDED."""
        _validate_slot(proposed_date, start_time, end_time)
        visit = await _get_visit_request(db, visit_request_id)
        if not visit:
            raise ValueError("Visit request not found")

        actor_role = await _resolve_actor_role(db, visit, actor)
        if visit.status in (VisitRequestStatus.CANCELLED, VisitRequestStatus.REJECTED, VisitRequestStatus.COMPLETED):
            raise ValueError(f"Cannot propose on a {visit.status.api_value} visit")

        # Whoever's turn it isn't may not propose
        if visit.pending_with and not _actor_matches_pending(actor_role, visit.pending_with):
            raise ValueError("It is not your turn to propose; wait for the other side to respond")

        # Supersede the current open proposal
        if visit.current_proposal_id:
            cur = await db.get(VisitRequestProposal, visit.current_proposal_id)
            if cur and cur.response == VisitProposalResponse.PENDING:
                cur.response = VisitProposalResponse.SUPERSEDED
                cur.responded_by_user_id = actor.id
                cur.responded_at = datetime.now(timezone.utc)

        proposal = VisitRequestProposal(
            id=generate_cuid(),
            visit_request_id=visit.id,
            proposed_by_user_id=actor.id,
            proposed_by_role=VisitProposalRole(actor_role),
            proposed_date=proposed_date,
            proposed_start_time=start_time,
            proposed_end_time=end_time,
            message=message,
            response=VisitProposalResponse.PENDING,
        )
        db.add(proposal)
        await db.flush()

        visit.current_proposal_id = proposal.id
        visit.status = VisitRequestStatus.NEGOTIATING
        visit.pending_with = _flip_side(actor_role, await _resolve_counterparty(db, visit.property_id))
        if actor_role in (VisitProposalRole.MANAGER.value, VisitProposalRole.SUPER_ADMIN.value):
            visit.manager_id = actor.id  # remember who's been engaging
        await db.flush()

        prop = await db.get(Property, visit.property_id)
        await _notify_after_state_change(
            db,
            visit=visit,
            actor_id=actor.id,
            type="visit_counter_proposal",
            title="New Visit Proposal",
            body=f"A new time has been proposed for {prop.name if prop else 'the property'}: "
                 f"{proposed_date.isoformat()} {start_time.strftime('%H:%M')}–{end_time.strftime('%H:%M')}.",
        )

        return success_response(data=await _visit_to_dict(db, visit, with_proposals=True))

    # ── Accept ────────────────────────────────────────────────────────────

    @staticmethod
    async def accept(
        db: AsyncSession,
        *,
        visit_request_id: str,
        actor: User,
    ) -> dict:
        """Accept the current open proposal. Both sides are now agreed."""
        visit = await _get_visit_request(db, visit_request_id)
        if not visit:
            raise ValueError("Visit request not found")
        actor_role = await _resolve_actor_role(db, visit, actor)
        if not visit.current_proposal_id:
            raise ValueError("No proposal to accept")
        if visit.pending_with and not _actor_matches_pending(actor_role, visit.pending_with):
            raise ValueError("It is not your turn to accept")

        proposal = await db.get(VisitRequestProposal, visit.current_proposal_id)
        if not proposal or proposal.response != VisitProposalResponse.PENDING:
            raise ValueError("Current proposal is not pending acceptance")

        now = datetime.now(timezone.utc)
        proposal.response = VisitProposalResponse.ACCEPTED
        proposal.responded_by_user_id = actor.id
        proposal.responded_at = now

        visit.status = VisitRequestStatus.CONFIRMED
        visit.scheduled_date = proposal.proposed_date
        visit.scheduled_start_time = proposal.proposed_start_time
        visit.scheduled_end_time = proposal.proposed_end_time
        visit.pending_with = None
        if actor_role in (VisitProposalRole.MANAGER.value, VisitProposalRole.SUPER_ADMIN.value):
            visit.manager_id = actor.id
        await db.flush()

        prop = await db.get(Property, visit.property_id)
        if prop:
            from app.services.onboarding_workflow_service import OnboardingWorkflowService
            await OnboardingWorkflowService.mark_visit_scheduled(
                db,
                property_id=visit.property_id,
                tenant_id=visit.tenant_id,
                owner_id=prop.owner_id,
                visit_request_id=visit.id,
                actor_id=actor.id,
            )

        await _notify_after_state_change(
            db,
            visit=visit,
            actor_id=actor.id,
            type="visit_confirmed",
            title="Visit Confirmed",
            body=f"Property visit is confirmed for {proposal.proposed_date.isoformat()} "
                 f"{proposal.proposed_start_time.strftime('%H:%M')}–{proposal.proposed_end_time.strftime('%H:%M')}"
                 f" at {prop.name if prop else 'the property'}.",
        )
        # Owner FYI (fan out via stakeholders is already covered above)

        logger.info("Visit confirmed", visit_id=visit.id)
        return success_response(data=await _visit_to_dict(db, visit, with_proposals=True))

    # ── Reject (close without counter-proposal) ────────────────────────────

    @staticmethod
    async def reject(
        db: AsyncSession,
        *,
        visit_request_id: str,
        actor: User,
        reason: str,
    ) -> dict:
        if not reason:
            raise ValueError("Rejection reason is required")
        visit = await _get_visit_request(db, visit_request_id)
        if not visit:
            raise ValueError("Visit request not found")
        actor_role = await _resolve_actor_role(db, visit, actor)
        if visit.pending_with and not _actor_matches_pending(actor_role, visit.pending_with):
            raise ValueError("It is not your turn to reject")

        if visit.current_proposal_id:
            proposal = await db.get(VisitRequestProposal, visit.current_proposal_id)
            if proposal and proposal.response == VisitProposalResponse.PENDING:
                proposal.response = VisitProposalResponse.REJECTED
                proposal.responded_by_user_id = actor.id
                proposal.responded_at = datetime.now(timezone.utc)

        visit.status = VisitRequestStatus.REJECTED
        visit.rejection_reason = reason
        visit.pending_with = None
        await db.flush()

        prop = await db.get(Property, visit.property_id)
        await _notify_after_state_change(
            db,
            visit=visit,
            actor_id=actor.id,
            type="visit_rejected",
            title="Visit Request Rejected",
            body=f"Visit request for {prop.name if prop else 'the property'} was rejected: {reason}",
        )

        logger.info("Visit rejected", visit_id=visit.id)
        return success_response(data=await _visit_to_dict(db, visit, with_proposals=True))

    # ── Reschedule a CONFIRMED visit (re-opens negotiation) ────────────────

    @staticmethod
    async def reschedule(
        db: AsyncSession,
        *,
        visit_request_id: str,
        actor: User,
        proposed_date: date,
        start_time: time,
        end_time: time,
        message: str | None = None,
    ) -> dict:
        """Re-open a confirmed visit with a new proposal; the other side must
        re-accept."""
        _validate_slot(proposed_date, start_time, end_time)
        visit = await _get_visit_request(db, visit_request_id)
        if not visit:
            raise ValueError("Visit request not found")
        if visit.status != VisitRequestStatus.CONFIRMED:
            raise ValueError("Only confirmed visits can be rescheduled")
        actor_role = await _resolve_actor_role(db, visit, actor)

        proposal = VisitRequestProposal(
            id=generate_cuid(),
            visit_request_id=visit.id,
            proposed_by_user_id=actor.id,
            proposed_by_role=VisitProposalRole(actor_role),
            proposed_date=proposed_date,
            proposed_start_time=start_time,
            proposed_end_time=end_time,
            message=message,
            response=VisitProposalResponse.PENDING,
        )
        db.add(proposal)
        await db.flush()

        visit.current_proposal_id = proposal.id
        visit.status = VisitRequestStatus.NEGOTIATING
        visit.pending_with = _flip_side(actor_role, await _resolve_counterparty(db, visit.property_id))
        await db.flush()

        prop = await db.get(Property, visit.property_id)
        await _notify_after_state_change(
            db,
            visit=visit,
            actor_id=actor.id,
            type="visit_reschedule",
            title="Visit Reschedule Requested",
            body=f"A reschedule has been proposed for {prop.name if prop else 'the property'}: "
                 f"{proposed_date.isoformat()} {start_time.strftime('%H:%M')}–{end_time.strftime('%H:%M')}.",
        )

        return success_response(data=await _visit_to_dict(db, visit, with_proposals=True))

    # ── Cancel ─────────────────────────────────────────────────────────────

    @staticmethod
    async def cancel(
        db: AsyncSession,
        *,
        visit_request_id: str,
        actor: User,
        reason: str | None = None,
    ) -> dict:
        visit = await _get_visit_request(db, visit_request_id)
        if not visit:
            raise ValueError("Visit request not found")
        if visit.status in (VisitRequestStatus.COMPLETED, VisitRequestStatus.CANCELLED, VisitRequestStatus.REJECTED):
            raise ValueError("Cannot cancel a finalized visit")

        actor_role = await _resolve_actor_role(db, visit, actor)
        # Tenants can always cancel their own; managers/SA only if entitled
        if actor_role == VisitProposalRole.TENANT.value and visit.tenant_id != actor.id:
            raise PermissionError("Not your visit to cancel")

        visit.status = VisitRequestStatus.CANCELLED
        visit.pending_with = None
        visit.rejection_reason = reason
        await db.flush()

        prop = await db.get(Property, visit.property_id)
        await _notify_after_state_change(
            db,
            visit=visit,
            actor_id=actor.id,
            type="visit_cancelled",
            title="Visit Cancelled",
            body=f"Visit for {prop.name if prop else 'the property'} was cancelled"
                 + (f": {reason}" if reason else ""),
        )

        return success_response(data=await _visit_to_dict(db, visit, with_proposals=True))

    # ── Complete (manager marks visit as done; agreement auto-generation) ──

    @staticmethod
    async def complete(
        db: AsyncSession,
        *,
        visit_request_id: str,
        actor: User,
        approve: bool,
        notes: str | None = None,
        rejection_reason: str | None = None,
    ) -> dict:
        """Manager marks the visit completed and approves/rejects the tenant."""
        visit = await _get_visit_request(db, visit_request_id)
        if not visit:
            raise ValueError("Visit request not found")
        if visit.status not in (VisitRequestStatus.CONFIRMED, VisitRequestStatus.APPOINTMENT_SCHEDULED):
            raise ValueError("Only confirmed visits can be completed")

        actor_role = await _resolve_actor_role(db, visit, actor)
        if actor_role not in (VisitProposalRole.MANAGER.value, VisitProposalRole.SUPER_ADMIN.value):
            raise PermissionError("Only managers or super-admin can complete visits")

        visit.status = VisitRequestStatus.COMPLETED
        visit.completed_at = datetime.now(timezone.utc)
        visit.visit_notes = notes

        agreement_data = None
        if approve:
            visit.visit_result = VisitResult.APPROVED
            from app.services.agreement_service import AgreementService
            try:
                agreement = await AgreementService.auto_generate_agreement(
                    db,
                    property_id=visit.property_id,
                    tenant_id=visit.tenant_id,
                    admin_id=actor.id,
                )
                agreement_data = {"agreement_id": agreement.id}

                from app.services.onboarding_workflow_service import OnboardingWorkflowService
                prop = await db.get(Property, visit.property_id)
                if prop:
                    await OnboardingWorkflowService.mark_visit_result(
                        db,
                        property_id=visit.property_id,
                        tenant_id=visit.tenant_id,
                        owner_id=prop.owner_id,
                        slot_id=visit_request_id,
                        actor_id=actor.id,
                        approved=True,
                    )
                    await OnboardingWorkflowService.mark_agreement_generated(
                        db,
                        property_id=visit.property_id,
                        tenant_id=visit.tenant_id,
                        owner_id=prop.owner_id,
                        agreement_id=agreement.id,
                        actor_id=actor.id,
                    )
            except ValueError as e:
                logger.warning("Could not auto-generate agreement", error=str(e))
                agreement_data = {"error": str(e)}
        else:
            if not rejection_reason:
                raise ValueError("Rejection reason is required")
            visit.visit_result = VisitResult.REJECTED
            visit.rejection_reason = rejection_reason

            prop = await db.get(Property, visit.property_id)
            if prop:
                from app.services.onboarding_workflow_service import OnboardingWorkflowService
                await OnboardingWorkflowService.mark_visit_result(
                    db,
                    property_id=visit.property_id,
                    tenant_id=visit.tenant_id,
                    owner_id=prop.owner_id,
                    slot_id=visit_request_id,
                    actor_id=actor.id,
                    approved=False,
                )

        await db.flush()

        # Notify tenant directly with the outcome
        await NotificationService.create(
            db,
            user_id=visit.tenant_id,
            type="visit_completed",
            title="Visit Result",
            body="Your visit has been approved." if approve else f"Your visit was not approved: {rejection_reason}",
            icon="check_circle" if approve else "cancel",
            data={"visit_request_id": visit.id, "approved": approve},
        )

        result = await _visit_to_dict(db, visit, with_proposals=True)
        if agreement_data:
            result["agreement"] = agreement_data
        return success_response(data=result)

    # ── Reads ──────────────────────────────────────────────────────────────

    @staticmethod
    async def get_visit_request(
        db: AsyncSession,
        *,
        visit_request_id: str,
        user: User,
    ) -> dict:
        visit = await _get_visit_request(db, visit_request_id)
        if not visit:
            raise ValueError("Visit request not found")
        await _assert_can_read(db, visit, user)
        return success_response(data=await _visit_to_dict(db, visit, with_proposals=True))

    @staticmethod
    async def list_visit_requests(
        db: AsyncSession,
        *,
        user: User,
        status: str | None = None,
        property_id: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict:
        query = select(VisitRequest)
        conditions = []
        role = user.active_role.api_value

        if role == "tenant":
            conditions.append(VisitRequest.tenant_id == user.id)
        elif role == "owner":
            owned = (await db.execute(select(Property.id).where(Property.owner_id == user.id))).scalars().all()
            conditions.append(VisitRequest.property_id.in_(owned) if owned else False)
        elif role == "manager":
            managed = await get_managed_property_ids(db, user.id)
            conditions.append(VisitRequest.property_id.in_(managed) if managed else False)
        elif role == "super_admin":
            # SA sees the fallback pool: requests on properties that have NO
            # assigned manager in property_managers.
            from app.models.property import PropertyManager
            managed_props_subq = select(PropertyManager.property_id).distinct()
            conditions.append(~VisitRequest.property_id.in_(managed_props_subq))

        if status:
            try:
                conditions.append(VisitRequest.status == VisitRequestStatus(status.upper()))
            except ValueError:
                raise ValueError(f"Invalid status: {status}")
        if property_id:
            conditions.append(VisitRequest.property_id == property_id)

        if conditions:
            query = query.where(and_(*conditions))

        query = query.order_by(VisitRequest.created_at.desc())
        count_q = select(func.count()).select_from(VisitRequest).where(and_(*conditions)) if conditions else select(func.count()).select_from(VisitRequest)
        total = (await db.execute(count_q)).scalar() or 0
        rows = (await db.execute(query.offset((page - 1) * limit).limit(limit))).scalars().all()
        items = [await _visit_to_dict(db, v, with_proposals=False) for v in rows]
        return paginated_response(items=items, total=total, page=page, limit=limit)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_slot(d: date, start: time, end: time) -> None:
    if end <= start:
        raise ValueError("End time must be after start time")
    # Require at least 15 minutes
    start_min = start.hour * 60 + start.minute
    end_min = end.hour * 60 + end.minute
    if end_min - start_min < 15:
        raise ValueError("Visit slot must be at least 15 minutes")
    if d < _ist_today():
        raise ValueError("Cannot request a visit in the past")


def _ist_today() -> date:
    return datetime.now(IST).date()


async def _resolve_counterparty(db: AsyncSession, property_id: str) -> str:
    """Returns 'MANAGER' if at least one is assigned, else 'SUPER_ADMIN'."""
    managers = await get_assigned_manager_ids(db, property_id)
    return "MANAGER" if managers else "SUPER_ADMIN"


async def _resolve_actor_role(db: AsyncSession, visit: VisitRequest, actor: User) -> str:
    """Map the authenticated user to their role in this visit's context."""
    if actor.id == visit.tenant_id:
        return VisitProposalRole.TENANT.value
    role = actor.active_role.api_value
    if role == "super_admin":
        return VisitProposalRole.SUPER_ADMIN.value
    if role == "manager":
        # Even if not yet assigned, allow if they have manager role and this is a fallback request
        return VisitProposalRole.MANAGER.value
    raise PermissionError("You are not a party to this visit request")


def _actor_matches_pending(actor_role: str, pending_with: str) -> bool:
    if actor_role == pending_with:
        return True
    # SA can act on either MANAGER or SUPER_ADMIN turns
    if actor_role == "SUPER_ADMIN" and pending_with in ("MANAGER", "SUPER_ADMIN"):
        return True
    return False


def _flip_side(current_role: str, fallback_for_other_side: str) -> str:
    """Compute who has the next turn after `current_role` acted.

    If tenant just acted → counterparty (manager if assigned, else SA).
    If manager/SA just acted → tenant.
    """
    if current_role == VisitProposalRole.TENANT.value:
        return fallback_for_other_side  # 'MANAGER' or 'SUPER_ADMIN'
    return VisitProposalRole.TENANT.value


async def _get_visit_request(db: AsyncSession, visit_request_id: str) -> VisitRequest | None:
    return await db.get(VisitRequest, visit_request_id)


async def _assert_can_read(db: AsyncSession, visit: VisitRequest, user: User) -> None:
    if user.id == visit.tenant_id:
        return
    role = user.active_role.api_value
    if role == "super_admin":
        return
    if role == "manager":
        if await is_property_manager(db, visit.property_id, user.id):
            return
        # If no managers assigned, SA-fallback applies — still let managers see it? No.
        managers = await get_assigned_manager_ids(db, visit.property_id)
        if not managers:
            # In the fallback pool — only SA can read.
            raise PermissionError("Visit is in super-admin fallback pool")
        raise PermissionError("You do not manage this property")
    if role == "owner":
        prop = await db.get(Property, visit.property_id)
        if prop and prop.owner_id == user.id:
            return
    raise PermissionError("Not allowed to read this visit request")


async def _notify_counterparty(
    db: AsyncSession,
    *,
    property_id: str,
    counterparty: str,
    type: str,
    title: str,
    body: str,
    actor_id: str | None,
    visit_id: str,
) -> None:
    """Notify only the counterparty side (not the owner — owner gets FYI on confirm/cancel)."""
    recipients: list[str] = []
    if counterparty == "MANAGER":
        recipients = await get_assigned_manager_ids(db, property_id)
    elif counterparty == "SUPER_ADMIN":
        recipients = await get_super_admin_ids(db)

    if actor_id:
        recipients = [r for r in recipients if r != actor_id]

    for user_id in recipients:
        await NotificationService.create(
            db,
            user_id=user_id,
            type=type,
            title=title,
            body=body,
            icon="event",
            action_target=f"/visit-requests/{visit_id}",
            data={"visit_request_id": visit_id, "property_id": property_id},
        )


async def _notify_after_state_change(
    db: AsyncSession,
    *,
    visit: VisitRequest,
    actor_id: str | None,
    type: str,
    title: str,
    body: str,
) -> None:
    """Notify the side now waiting, plus the tenant if it isn't them.

    On terminal states (CONFIRMED, REJECTED, CANCELLED, COMPLETED) we
    additionally FYI the property owner via the stakeholder fanout.
    """
    # Active negotiation: notify whoever's turn it is now
    if visit.pending_with == "TENANT":
        if visit.tenant_id != actor_id:
            await NotificationService.create(
                db,
                user_id=visit.tenant_id,
                type=type, title=title, body=body, icon="event",
                action_target=f"/book-visit/{visit.property_id}",
                data={"visit_request_id": visit.id, "property_id": visit.property_id},
            )
    elif visit.pending_with in ("MANAGER", "SUPER_ADMIN"):
        await _notify_counterparty(
            db,
            property_id=visit.property_id,
            counterparty=visit.pending_with,
            type=type, title=title, body=body, actor_id=actor_id,
            visit_id=visit.id,
        )

    # Terminal states: owner + assigned managers fanout
    if visit.status in (
        VisitRequestStatus.CONFIRMED,
        VisitRequestStatus.REJECTED,
        VisitRequestStatus.CANCELLED,
        VisitRequestStatus.COMPLETED,
    ):
        await NotificationService.notify_property_stakeholders(
            db,
            property_id=visit.property_id,
            type=type,
            title=title,
            body=body,
            actor_id=actor_id,
            icon="event",
            data={"visit_request_id": visit.id, "property_id": visit.property_id},
        )
        # Also notify tenant if not already notified above (terminal state)
        if visit.pending_with != "TENANT" and visit.tenant_id != actor_id:
            await NotificationService.create(
                db,
                user_id=visit.tenant_id,
                type=type, title=title, body=body, icon="event",
                action_target=f"/book-visit/{visit.property_id}",
                data={"visit_request_id": visit.id, "property_id": visit.property_id},
            )


async def _visit_to_dict(db: AsyncSession, v: VisitRequest, *, with_proposals: bool) -> dict:
    # Ensure server-default columns (created_at, updated_at) are loaded —
    # accessing them lazily on an expired object would attempt sync IO and
    # fail with MissingGreenlet inside an async session.
    await db.refresh(v)

    # Enrich with human-readable names. Cheap per-row lookups; we accept the
    # overhead vs surfacing IDs in every UI.
    prop = await db.get(Property, v.property_id) if v.property_id else None
    tenant = await db.get(User, v.tenant_id) if v.tenant_id else None
    manager = await db.get(User, v.manager_id) if v.manager_id else None

    base = {
        "id": v.id,
        "property_id": v.property_id,
        "property_name": prop.name if prop else None,
        "property_address": prop.address if prop else None,
        "tenant_id": v.tenant_id,
        "tenant_name": tenant.name if tenant else None,
        "manager_id": v.manager_id,
        "manager_name": manager.name if manager else None,
        "status": v.status.api_value,
        "visit_result": v.visit_result.api_value if v.visit_result else None,
        "pending_with": v.pending_with.lower() if v.pending_with else None,
        "current_proposal_id": v.current_proposal_id,
        "requested_date": v.requested_date.isoformat() if v.requested_date else None,
        "requested_start_time": v.requested_start_time.strftime("%H:%M") if v.requested_start_time else None,
        "requested_end_time": v.requested_end_time.strftime("%H:%M") if v.requested_end_time else None,
        "requested_message": v.requested_message,
        "scheduled_date": v.scheduled_date.isoformat() if v.scheduled_date else None,
        "scheduled_start_time": v.scheduled_start_time.strftime("%H:%M") if v.scheduled_start_time else None,
        "scheduled_end_time": v.scheduled_end_time.strftime("%H:%M") if v.scheduled_end_time else None,
        "visit_notes": v.visit_notes,
        "rejection_reason": v.rejection_reason,
        "completed_at": _fmt_dt(v.completed_at),
        "created_at": _fmt_dt(v.created_at),
        "updated_at": _fmt_dt(v.updated_at),
    }
    if with_proposals:
        rows = (await db.execute(
            select(VisitRequestProposal)
            .where(VisitRequestProposal.visit_request_id == v.id)
            .order_by(VisitRequestProposal.created_at.desc())
        )).scalars().all()
        base["proposals"] = [_proposal_to_dict(p) for p in rows]
    return base


def _proposal_to_dict(p: VisitRequestProposal) -> dict:
    return {
        "id": p.id,
        "proposed_by_user_id": p.proposed_by_user_id,
        "proposed_by_role": p.proposed_by_role.api_value,
        "proposed_date": p.proposed_date.isoformat(),
        "proposed_start_time": p.proposed_start_time.strftime("%H:%M"),
        "proposed_end_time": p.proposed_end_time.strftime("%H:%M"),
        "message": p.message,
        "response": p.response.api_value,
        "responded_by_user_id": p.responded_by_user_id,
        "responded_at": _fmt_dt(p.responded_at),
        "created_at": _fmt_dt(p.created_at),
    }


def _fmt_dt(value: datetime | None) -> str | None:
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(IST).isoformat()
