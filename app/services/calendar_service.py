"""LuxeLife API — Admin Calendar service."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.responses import paginated_response, success_response
from app.models.admin_slot import AdminSlot, SlotStatus, VisitResult
from app.models.admin_slot_block import AdminSlotBlock
from app.models.agreement import Agreement
from app.models.notification import Notification
from app.models.property import Property
from app.models.user import Role, User

logger = structlog.get_logger()

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_SLOT_START = time(7, 0)
DEFAULT_SLOT_END = time(21, 0)
SLOT_MINUTES = 30
DEFAULT_LOOKAHEAD_DAYS = 14
VIRTUAL_SLOT_PREFIX = "virtual"


class CalendarService:
    """Admin calendar slot and block-rule management."""

    @staticmethod
    async def create_slots(
        db: AsyncSession,
        admin_id: str,
        slots: list[dict],
    ) -> dict:
        """Backward-compatible manual slot creation."""
        created = []
        for s in slots:
            slot_date = date.fromisoformat(s["date"])
            start = time.fromisoformat(s["start_time"])
            end = time.fromisoformat(s["end_time"])

            if end <= start:
                raise ValueError(f"End time must be after start time for slot on {s['date']}")
            if slot_date < _ist_today():
                raise ValueError(f"Cannot create slots in the past: {s['date']}")

            overlap = await db.execute(
                select(AdminSlot).where(
                    and_(
                        AdminSlot.admin_id == admin_id,
                        AdminSlot.slot_date == slot_date,
                        AdminSlot.status != SlotStatus.CANCELLED,
                        AdminSlot.start_time < end,
                        AdminSlot.end_time > start,
                    )
                )
            )
            if overlap.scalar_one_or_none():
                raise ValueError(f"Overlapping slot exists on {s['date']} at {s['start_time']}")

            slot = AdminSlot(
                admin_id=admin_id,
                slot_date=slot_date,
                start_time=start,
                end_time=end,
                status=SlotStatus.AVAILABLE,
            )
            db.add(slot)
            await db.flush()
            created.append(_slot_to_dict(slot))

        logger.info("Admin created slots", admin_id=admin_id, count=len(created))
        return success_response(data=created)

    @staticmethod
    async def create_block_rule(
        db: AsyncSession,
        *,
        admin_id: str,
        start_time_str: str,
        end_time_str: str,
        duration_type: str,
        duration_value: int | None = None,
        effective_from: date | None = None,
        effective_to: date | None = None,
        reason: str | None = None,
    ) -> dict:
        """Create a blocked time-window rule for an admin."""
        start = time.fromisoformat(start_time_str)
        end = time.fromisoformat(end_time_str)
        if end <= start:
            raise ValueError("End time must be after start time")
        if not _is_half_hour_aligned(start) or not _is_half_hour_aligned(end):
            raise ValueError("Only 30-minute aligned times are allowed")

        start_date = effective_from or _ist_today()
        if start_date < _ist_today():
            raise ValueError("effective_from cannot be in the past")

        normalized_duration = (duration_type or "").strip().lower()
        end_date = _resolve_block_end_date(
            duration_type=normalized_duration,
            duration_value=duration_value,
            effective_from=start_date,
            effective_to=effective_to,
        )

        block = AdminSlotBlock(
            admin_id=admin_id,
            start_time=start,
            end_time=end,
            effective_from=start_date,
            effective_to=end_date,
            timezone="Asia/Kolkata",
            reason=(reason or "").strip() or None,
        )
        db.add(block)
        await db.flush()
        logger.info("Calendar block created", admin_id=admin_id, block_id=block.id)
        return success_response(data=_block_to_dict(block))

    @staticmethod
    async def list_block_rules(
        db: AsyncSession,
        *,
        admin_id: str,
        active_only: bool = False,
        page: int = 1,
        limit: int = 100,
    ) -> dict:
        """List block rules for an admin."""
        conditions = [AdminSlotBlock.admin_id == admin_id]
        if active_only:
            today = _ist_today()
            conditions.append(
                or_(AdminSlotBlock.effective_to.is_(None), AdminSlotBlock.effective_to >= today)
            )

        query = select(AdminSlotBlock).where(and_(*conditions)).order_by(
            AdminSlotBlock.effective_from.desc(),
            AdminSlotBlock.start_time,
        )
        count_query = select(func.count()).select_from(AdminSlotBlock).where(and_(*conditions))

        total = (await db.execute(count_query)).scalar() or 0
        result = await db.execute(query.offset((page - 1) * limit).limit(limit))
        blocks = result.scalars().all()
        return paginated_response(
            items=[_block_to_dict(b) for b in blocks],
            total=total,
            page=page,
            limit=limit,
        )

    @staticmethod
    async def delete_block_rule(
        db: AsyncSession,
        *,
        admin_id: str,
        block_id: str,
    ) -> dict:
        """Delete a block rule owned by an admin."""
        result = await db.execute(select(AdminSlotBlock).where(AdminSlotBlock.id == block_id))
        block = result.scalar_one_or_none()
        if not block:
            raise ValueError("Block rule not found")
        if block.admin_id != admin_id:
            raise PermissionError("You can only delete your own block rules")

        await db.delete(block)
        await db.flush()
        logger.info("Calendar block deleted", admin_id=admin_id, block_id=block_id)
        return success_response(data={"id": block_id, "deleted": True})

    @staticmethod
    async def list_slots(
        db: AsyncSession,
        *,
        admin_id: str | None = None,
        status: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict:
        """List slots with dynamic default availability and persisted visit slots."""
        normalized_status = (status or "all").lower()
        if normalized_status not in {"all", "available", "booked", "completed", "cancelled"}:
            raise ValueError("Invalid slot status filter")

        start_date, end_date = _resolve_window(from_date, to_date)
        if not admin_id:
            return await CalendarService.get_available_slots(
                db,
                from_date=start_date,
                to_date=end_date,
                page=page,
                limit=limit,
            )

        items: list[dict] = []
        if normalized_status in {"all", "available"}:
            available_virtual = await _generate_available_virtual_slots(
                db,
                admin_ids=[admin_id],
                from_date=start_date,
                to_date=end_date,
            )
            available_real = await _fetch_real_slots(
                db,
                admin_ids=[admin_id],
                from_date=start_date,
                to_date=end_date,
                statuses=[SlotStatus.AVAILABLE],
            )
            items.extend(available_virtual)
            items.extend([_slot_to_dict(slot) for slot in available_real])

        if normalized_status in {"all", "booked", "completed", "cancelled"}:
            real_statuses = [
                SlotStatus.BOOKED,
                SlotStatus.COMPLETED,
            ] if normalized_status == "all" else [SlotStatus(normalized_status)]

            real_slots = await _fetch_real_slots(
                db,
                admin_ids=[admin_id],
                from_date=start_date,
                to_date=end_date,
                statuses=real_statuses,
            )
            items.extend([_slot_to_dict(slot) for slot in real_slots])

        items.sort(key=lambda item: (item["slot_date"], item["start_time"], item["admin_id"]))
        return _paginate_items(items=items, page=page, limit=limit)

    @staticmethod
    async def get_available_slots(
        db: AsyncSession,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
        property_id: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict:
        """List dynamically generated available slots for tenants."""
        del property_id  # reserved for future per-property constraints
        start_date, end_date = _resolve_window(from_date, to_date)
        admin_ids = await _get_active_admin_ids(db)
        virtual_items = await _generate_available_virtual_slots(
            db,
            admin_ids=admin_ids,
            from_date=start_date,
            to_date=end_date,
        )
        real_available = await _fetch_real_slots(
            db,
            admin_ids=admin_ids,
            from_date=start_date,
            to_date=end_date,
            statuses=[SlotStatus.AVAILABLE],
        )
        items = virtual_items + [_slot_to_dict(slot) for slot in real_available]
        items.sort(key=lambda item: (item["slot_date"], item["start_time"], item["admin_id"]))
        return _paginate_items(items=items, page=page, limit=limit)

    @staticmethod
    async def book_slot(
        db: AsyncSession,
        slot_id: str,
        tenant_id: str,
        property_id: str,
    ) -> dict:
        """Tenant books an available slot for a property visit."""
        slot = await _get_slot(db, slot_id)

        if not slot and _is_virtual_slot_id(slot_id):
            virtual = _parse_virtual_slot_id(slot_id)
            if _is_slot_in_past(virtual["slot_date"], virtual["start_time"]):
                raise ValueError("Cannot book slots in the past")
            if not await _is_interval_available(
                db,
                admin_id=virtual["admin_id"],
                slot_date=virtual["slot_date"],
                start_time=virtual["start_time"],
                end_time=virtual["end_time"],
            ):
                raise ValueError("This slot is no longer available")

            existing_available = await db.execute(
                select(AdminSlot).where(
                    and_(
                        AdminSlot.admin_id == virtual["admin_id"],
                        AdminSlot.slot_date == virtual["slot_date"],
                        AdminSlot.start_time == virtual["start_time"],
                        AdminSlot.end_time == virtual["end_time"],
                        AdminSlot.status == SlotStatus.AVAILABLE,
                    )
                )
            )
            slot = existing_available.scalar_one_or_none()
            if not slot:
                slot = AdminSlot(
                    admin_id=virtual["admin_id"],
                    slot_date=virtual["slot_date"],
                    start_time=virtual["start_time"],
                    end_time=virtual["end_time"],
                    status=SlotStatus.AVAILABLE,
                )
                db.add(slot)
                await db.flush()

        if not slot:
            raise ValueError("Slot not found")
        if slot.status != SlotStatus.AVAILABLE:
            raise ValueError("This slot is no longer available")

        existing = await db.execute(
            select(AdminSlot).where(
                and_(
                    AdminSlot.booked_by == tenant_id,
                    AdminSlot.property_id == property_id,
                    AdminSlot.status == SlotStatus.BOOKED,
                )
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError("You already have a visit booked for this property")

        await _mark_slot_booked(db, slot=slot, tenant_id=tenant_id, property_id=property_id)
        logger.info("Slot booked", slot_id=slot.id, tenant_id=tenant_id, property_id=property_id)
        return success_response(data=_slot_to_dict(slot))

    @staticmethod
    async def cancel_booking(
        db: AsyncSession,
        slot_id: str,
        user_id: str,
    ) -> dict:
        """Cancel a booking (either admin or tenant can cancel)."""
        slot = await _get_slot(db, slot_id)
        if not slot:
            raise ValueError("Slot not found")
        if slot.status != SlotStatus.BOOKED:
            raise ValueError("Only booked slots can be cancelled")
        if slot.booked_by != user_id and slot.admin_id != user_id:
            raise PermissionError("You do not have permission to cancel this booking")

        old_tenant_id = slot.booked_by
        slot.status = SlotStatus.AVAILABLE
        slot.booked_by = None
        slot.property_id = None
        slot.booked_at = None
        await db.flush()

        # Notify the other party
        if user_id == slot.admin_id and old_tenant_id:
            notif = Notification(
                user_id=old_tenant_id,
                type="visit_cancelled",
                title="Visit Cancelled",
                body=f"Your property visit on {slot.slot_date} has been cancelled by admin.",
                data={"slot_id": slot.id},
            )
            db.add(notif)

        logger.info("Booking cancelled", slot_id=slot_id, cancelled_by=user_id)
        return success_response(data=_slot_to_dict(slot))

    @staticmethod
    async def delete_slot(
        db: AsyncSession,
        slot_id: str,
        admin_id: str,
    ) -> dict:
        """Admin deletes/cancels a slot."""
        slot = await _get_slot(db, slot_id)
        if not slot:
            raise ValueError("Slot not found")
        if slot.admin_id != admin_id:
            raise PermissionError("You can only delete your own slots")

        if slot.status == SlotStatus.BOOKED and slot.booked_by:
            # Notify tenant
            notif = Notification(
                user_id=slot.booked_by,
                type="visit_cancelled",
                title="Visit Slot Cancelled",
                body=f"Your visit on {slot.slot_date} at {slot.start_time.strftime('%I:%M %p')} has been cancelled.",
                data={"slot_id": slot.id},
            )
            db.add(notif)

        slot.status = SlotStatus.CANCELLED
        await db.flush()
        logger.info("Slot cancelled", slot_id=slot_id, admin_id=admin_id)
        return success_response(data=_slot_to_dict(slot))

    @staticmethod
    async def complete_visit(
        db: AsyncSession,
        slot_id: str,
        admin_id: str,
        *,
        approve: bool,
        notes: str | None = None,
        rejection_reason: str | None = None,
    ) -> dict:
        """Admin marks a visit as completed and approves/rejects the tenant."""
        slot = await _get_slot(db, slot_id)
        if not slot:
            raise ValueError("Slot not found")
        if slot.status != SlotStatus.BOOKED:
            raise ValueError("Only booked slots can be completed")
        if slot.admin_id != admin_id:
            raise PermissionError("You can only complete visits for your own slots")
        if not slot.booked_by:
            raise ValueError("No tenant booked for this slot")

        slot.status = SlotStatus.COMPLETED
        slot.completed_at = datetime.now(timezone.utc)
        slot.visit_notes = notes

        agreement_data = None
        if approve:
            slot.visit_result = VisitResult.APPROVED

            # Auto-generate agreement
            from app.services.agreement_service import AgreementService
            try:
                agreement = await AgreementService.auto_generate_agreement(
                    db,
                    property_id=slot.property_id,
                    tenant_id=slot.booked_by,
                    admin_id=admin_id,
                )
                agreement_data = {"agreement_id": agreement.id}

                from app.services.onboarding_workflow_service import OnboardingWorkflowService

                await OnboardingWorkflowService.mark_visit_result(
                    db,
                    property_id=slot.property_id,
                    tenant_id=slot.booked_by,
                    owner_id=agreement.owner_id,
                    slot_id=slot.id,
                    actor_id=admin_id,
                    approved=True,
                )
                await OnboardingWorkflowService.mark_agreement_generated(
                    db,
                    property_id=slot.property_id,
                    tenant_id=slot.booked_by,
                    owner_id=agreement.owner_id,
                    agreement_id=agreement.id,
                    actor_id=admin_id,
                )
            except ValueError as e:
                logger.warning("Could not auto-generate agreement", error=str(e))
                agreement_data = {"error": str(e)}

            # Notification is already sent by AgreementService
        else:
            if not rejection_reason:
                raise ValueError("Rejection reason is required")
            slot.visit_result = VisitResult.REJECTED
            slot.rejection_reason = rejection_reason
            # Notify tenant of rejection
            notif = Notification(
                user_id=slot.booked_by,
                type="visit_rejected",
                title="Visit Update",
                body=f"Unfortunately, your visit was not approved. Reason: {rejection_reason}",
                data={"slot_id": slot.id},
            )
            db.add(notif)

            prop_result = await db.execute(select(Property).where(Property.id == slot.property_id))
            prop = prop_result.scalar_one_or_none()
            if prop:
                from app.services.onboarding_workflow_service import OnboardingWorkflowService

                await OnboardingWorkflowService.mark_visit_result(
                    db,
                    property_id=slot.property_id,
                    tenant_id=slot.booked_by,
                    owner_id=prop.owner_id,
                    slot_id=slot.id,
                    actor_id=admin_id,
                    approved=False,
                )

        await db.flush()
        result = _slot_to_dict(slot)
        if agreement_data:
            result["agreement"] = agreement_data
        logger.info(
            "Visit completed",
            slot_id=slot_id,
            result="approved" if approve else "rejected",
        )
        return success_response(data=result)

    @staticmethod
    async def list_tenant_visits(
        db: AsyncSession,
        *,
        tenant_id: str,
        page: int = 1,
        limit: int = 50,
    ) -> dict:
        """List booked/completed visits for a tenant with agreement linkage when available."""
        query = (
            select(AdminSlot)
            .where(
                and_(
                    AdminSlot.booked_by == tenant_id,
                    AdminSlot.status.in_([SlotStatus.BOOKED, SlotStatus.COMPLETED]),
                )
            )
            .order_by(AdminSlot.slot_date.desc(), AdminSlot.start_time.desc())
        )
        count_query = select(func.count()).select_from(AdminSlot).where(
            and_(
                AdminSlot.booked_by == tenant_id,
                AdminSlot.status.in_([SlotStatus.BOOKED, SlotStatus.COMPLETED]),
            )
        )

        total = (await db.execute(count_query)).scalar() or 0
        result = await db.execute(query.offset((page - 1) * limit).limit(limit))
        slots = result.scalars().all()

        items = []
        for slot in slots:
            item = _slot_to_dict(slot)
            if slot.visit_result == VisitResult.APPROVED and slot.property_id:
                agreement_result = await db.execute(
                    select(Agreement)
                    .where(
                        and_(
                            Agreement.property_id == slot.property_id,
                            Agreement.tenant_id == tenant_id,
                        )
                    )
                    .order_by(Agreement.created_at.desc())
                    .limit(1)
                )
                agreement = agreement_result.scalar_one_or_none()
                if agreement:
                    item["agreement_id"] = agreement.id
            items.append(item)

        return paginated_response(items=items, total=total, page=page, limit=limit)


async def _get_slot(db: AsyncSession, slot_id: str) -> AdminSlot | None:
    result = await db.execute(select(AdminSlot).where(AdminSlot.id == slot_id))
    return result.scalar_one_or_none()


async def _get_active_admin_ids(db: AsyncSession) -> list[str]:
    result = await db.execute(select(User.id).where(User.active_role == Role.ADMIN))
    return [row[0] for row in result.all()]


async def _fetch_real_slots(
    db: AsyncSession,
    *,
    admin_ids: list[str],
    from_date: date,
    to_date: date,
    statuses: list[SlotStatus] | None = None,
) -> list[AdminSlot]:
    if not admin_ids:
        return []

    conditions = [
        AdminSlot.admin_id.in_(admin_ids),
        AdminSlot.slot_date >= from_date,
        AdminSlot.slot_date <= to_date,
    ]
    if statuses:
        conditions.append(AdminSlot.status.in_(statuses))
    else:
        conditions.append(AdminSlot.status != SlotStatus.CANCELLED)

    result = await db.execute(
        select(AdminSlot)
        .where(and_(*conditions))
        .order_by(AdminSlot.slot_date, AdminSlot.start_time)
    )
    return result.scalars().all()


async def _fetch_blocks(
    db: AsyncSession,
    *,
    admin_ids: list[str],
    from_date: date,
    to_date: date,
) -> list[AdminSlotBlock]:
    if not admin_ids:
        return []

    result = await db.execute(
        select(AdminSlotBlock)
        .where(
            and_(
                AdminSlotBlock.admin_id.in_(admin_ids),
                AdminSlotBlock.effective_from <= to_date,
                or_(AdminSlotBlock.effective_to.is_(None), AdminSlotBlock.effective_to >= from_date),
            )
        )
        .order_by(AdminSlotBlock.effective_from, AdminSlotBlock.start_time)
    )
    return result.scalars().all()


async def _generate_available_virtual_slots(
    db: AsyncSession,
    *,
    admin_ids: list[str],
    from_date: date,
    to_date: date,
) -> list[dict]:
    if not admin_ids:
        return []

    real_slots = await _fetch_real_slots(
        db,
        admin_ids=admin_ids,
        from_date=from_date,
        to_date=to_date,
        statuses=None,
    )
    blocks = await _fetch_blocks(
        db,
        admin_ids=admin_ids,
        from_date=from_date,
        to_date=to_date,
    )

    occupied_intervals: dict[tuple[str, date], list[tuple[time, time]]] = {}
    for slot in real_slots:
        key = (slot.admin_id, slot.slot_date)
        occupied_intervals.setdefault(key, []).append((slot.start_time, slot.end_time))

    blocked_intervals: dict[tuple[str, date], list[tuple[time, time]]] = {}
    for block in blocks:
        start = max(block.effective_from, from_date)
        end = min(block.effective_to or to_date, to_date)
        cursor = start
        while cursor <= end:
            key = (block.admin_id, cursor)
            blocked_intervals.setdefault(key, []).append((block.start_time, block.end_time))
            cursor += timedelta(days=1)

    items: list[dict] = []
    day_cursor = from_date
    while day_cursor <= to_date:
        for admin_id in admin_ids:
            key = (admin_id, day_cursor)
            blocked_for_day = blocked_intervals.get(key, [])
            occupied_for_day = occupied_intervals.get(key, [])

            current = DEFAULT_SLOT_START
            while current < DEFAULT_SLOT_END:
                end = _add_minutes(current, SLOT_MINUTES)
                if _is_slot_in_past(day_cursor, current):
                    current = end
                    continue
                if _has_overlap(current, end, blocked_for_day):
                    current = end
                    continue
                if _has_overlap(current, end, occupied_for_day):
                    current = end
                    continue

                items.append(
                    {
                        "id": _build_virtual_slot_id(admin_id, day_cursor, current, end),
                        "slot_date": day_cursor.isoformat(),
                        "start_time": current.strftime("%H:%M"),
                        "end_time": end.strftime("%H:%M"),
                        "status": SlotStatus.AVAILABLE.value,
                        "visit_result": VisitResult.PENDING.value,
                        "visit_notes": None,
                        "rejection_reason": None,
                        "admin_id": admin_id,
                        "booked_by": None,
                        "property_id": None,
                        "booked_at": None,
                        "completed_at": None,
                        "created_at": None,
                        "is_virtual": True,
                        "timezone": "Asia/Kolkata",
                    }
                )
                current = end
        day_cursor += timedelta(days=1)
    return items


async def _is_interval_available(
    db: AsyncSession,
    *,
    admin_id: str,
    slot_date: date,
    start_time: time,
    end_time: time,
) -> bool:
    blocks = await _fetch_blocks(
        db,
        admin_ids=[admin_id],
        from_date=slot_date,
        to_date=slot_date,
    )
    for block in blocks:
        if _interval_overlap(start_time, end_time, block.start_time, block.end_time):
            return False

    existing = await db.execute(
        select(AdminSlot).where(
            and_(
                AdminSlot.admin_id == admin_id,
                AdminSlot.slot_date == slot_date,
                AdminSlot.status != SlotStatus.CANCELLED,
                AdminSlot.start_time < end_time,
                AdminSlot.end_time > start_time,
            )
        )
    )
    return existing.scalar_one_or_none() is None


async def _mark_slot_booked(
    db: AsyncSession,
    *,
    slot: AdminSlot,
    tenant_id: str,
    property_id: str,
) -> None:
    slot.status = SlotStatus.BOOKED
    slot.booked_by = tenant_id
    slot.property_id = property_id
    slot.booked_at = datetime.now(timezone.utc)
    await db.flush()

    notif = Notification(
        user_id=slot.admin_id,
        type="visit_booked",
        title="Property Visit Booked",
        body=f"A tenant has booked a visit on {slot.slot_date} at {slot.start_time.strftime('%I:%M %p')}",
        data={"slot_id": slot.id, "property_id": property_id},
    )
    db.add(notif)

    prop_result = await db.execute(select(Property).where(Property.id == property_id))
    prop = prop_result.scalar_one_or_none()
    if prop:
        from app.services.onboarding_workflow_service import OnboardingWorkflowService

        await OnboardingWorkflowService.mark_visit_booked(
            db,
            property_id=property_id,
            tenant_id=tenant_id,
            owner_id=prop.owner_id,
            slot_id=slot.id,
            actor_id=tenant_id,
        )


def _resolve_window(from_date: date | None, to_date: date | None) -> tuple[date, date]:
    start = from_date or _ist_today()
    end = to_date or (start + timedelta(days=DEFAULT_LOOKAHEAD_DAYS - 1))
    if end < start:
        raise ValueError("to_date must be on or after from_date")
    return start, end


def _add_minutes(t: time, minutes: int) -> time:
    dt = datetime.combine(date(2000, 1, 1), t) + timedelta(minutes=minutes)
    return dt.time()


def _is_half_hour_aligned(t: time) -> bool:
    return t.second == 0 and t.minute in {0, 30}


def _resolve_block_end_date(
    *,
    duration_type: str,
    duration_value: int | None,
    effective_from: date,
    effective_to: date | None,
) -> date | None:
    if duration_type == "forever":
        return None
    if duration_type == "days":
        if not duration_value or duration_value < 1:
            raise ValueError("duration_value must be >= 1 for day-based blocks")
        return effective_from + timedelta(days=duration_value - 1)
    if duration_type == "weeks":
        if not duration_value or duration_value < 1:
            raise ValueError("duration_value must be >= 1 for week-based blocks")
        return effective_from + timedelta(days=(duration_value * 7) - 1)
    if duration_type == "months":
        if not duration_value or duration_value < 1:
            raise ValueError("duration_value must be >= 1 for month-based blocks")
        month = effective_from.month - 1 + duration_value
        year = effective_from.year + month // 12
        month = month % 12 + 1
        last_day = _last_day_of_month(year, month)
        end_day = min(effective_from.day, last_day)
        return date(year, month, end_day) - timedelta(days=1)
    if duration_type == "custom":
        if not effective_to:
            raise ValueError("effective_to is required for custom block duration")
        if effective_to < effective_from:
            raise ValueError("effective_to cannot be before effective_from")
        return effective_to
    raise ValueError("duration_type must be one of: forever, days, weeks, months, custom")


def _last_day_of_month(year: int, month: int) -> int:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return (next_month - timedelta(days=1)).day


def _has_overlap(start: time, end: time, intervals: list[tuple[time, time]]) -> bool:
    return any(_interval_overlap(start, end, i_start, i_end) for i_start, i_end in intervals)


def _interval_overlap(start_a: time, end_a: time, start_b: time, end_b: time) -> bool:
    return start_a < end_b and end_a > start_b


def _is_slot_in_past(slot_date: date, start_time: time) -> bool:
    now_ist = datetime.now(IST)
    slot_dt = datetime.combine(slot_date, start_time, tzinfo=IST)
    return slot_dt <= now_ist


def _build_virtual_slot_id(admin_id: str, slot_date: date, start_time: time, end_time: time) -> str:
    return (
        f"{VIRTUAL_SLOT_PREFIX}:{admin_id}:{slot_date.isoformat()}:"
        f"{start_time.strftime('%H:%M')}:{end_time.strftime('%H:%M')}"
    )


def _is_virtual_slot_id(slot_id: str) -> bool:
    return slot_id.startswith(f"{VIRTUAL_SLOT_PREFIX}:")


def _parse_virtual_slot_id(slot_id: str) -> dict:
    parts = slot_id.split(":")
    if len(parts) != 7 or parts[0] != VIRTUAL_SLOT_PREFIX:
        raise ValueError("Invalid virtual slot id")
    return {
        "admin_id": parts[1],
        "slot_date": date.fromisoformat(parts[2]),
        "start_time": time.fromisoformat(f"{parts[3]}:{parts[4]}"),
        "end_time": time.fromisoformat(f"{parts[5]}:{parts[6]}"),
    }


def _paginate_items(*, items: list[dict], page: int, limit: int) -> dict:
    total = len(items)
    start = (page - 1) * limit
    end = start + limit
    return paginated_response(items=items[start:end], total=total, page=page, limit=limit)


def _ist_today() -> date:
    return datetime.now(IST).date()


def _format_dt_ist(value: datetime | None) -> str | None:
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(IST).isoformat()


def _block_to_dict(block: AdminSlotBlock) -> dict:
    return {
        "id": block.id,
        "admin_id": block.admin_id,
        "start_time": block.start_time.strftime("%H:%M"),
        "end_time": block.end_time.strftime("%H:%M"),
        "effective_from": block.effective_from.isoformat(),
        "effective_to": block.effective_to.isoformat() if block.effective_to else None,
        "is_permanent": block.effective_to is None,
        "timezone": block.timezone,
        "reason": block.reason,
        "created_at": _format_dt_ist(block.created_at),
        "updated_at": _format_dt_ist(block.updated_at),
    }


def _slot_to_dict(s: AdminSlot) -> dict:
    """Convert AdminSlot ORM object to response dict."""
    return {
        "id": s.id,
        "slot_date": s.slot_date.isoformat(),
        "start_time": s.start_time.strftime("%H:%M"),
        "end_time": s.end_time.strftime("%H:%M"),
        "status": s.status.value,
        "visit_result": s.visit_result.value if s.visit_result else None,
        "visit_notes": s.visit_notes,
        "rejection_reason": s.rejection_reason,
        "admin_id": s.admin_id,
        "booked_by": s.booked_by,
        "property_id": s.property_id,
        "booked_at": _format_dt_ist(s.booked_at),
        "completed_at": _format_dt_ist(s.completed_at),
        "created_at": _format_dt_ist(s.created_at),
        "is_virtual": False,
        "timezone": "Asia/Kolkata",
    }
