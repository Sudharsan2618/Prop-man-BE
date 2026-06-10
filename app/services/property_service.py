"""
LuxeLife API — Property service.

Business logic for property CRUD, search, and filtering.
All database operations use the async session passed in.
"""

from datetime import datetime, timezone

from sqlalchemy import delete as sa_delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models import generate_cuid
from app.models.property import (
    Furnishing,
    Occupancy,
    Property,
    PropertyManager,
    PropertyManagerRole,
    PropertyOccupancyHistory,
    PropertyStatus,
    PropertyType,
)
from app.models.user import Role, User
from app.schemas.property import property_to_response
from app.services.property_scope import (
    get_managed_property_ids,
    is_property_manager as _is_property_manager,
)


class PropertyService:
    """Handles property operations."""

    @staticmethod
    async def create(
        db: AsyncSession,
        creator: User,
        **data,
    ) -> dict:
        """Create a new property listing.

        Accepts optional owner_id and manager_ids for assignment during creation.
        If caller is MANAGER and no manager_ids given, auto-assigns caller as PRIMARY.
        """
        owner_id = data.get("owner_id") or creator.id
        manager_ids: list[str] | None = data.get("manager_ids")

        status = PropertyStatus(data.get("status", "draft"))
        rent = data.get("rent")
        # Rent range mirrors the single rent for the SA flow; 0 when still a draft
        # with no rent entered yet (DB columns are NOT NULL).
        rent_for_range = rent if rent is not None else 0

        prop = Property(
            id=generate_cuid(),
            name=data["name"],
            unit=data["unit"],
            address=data["address"],
            city=data["city"],
            state=data["state"],
            pincode=data["pincode"],
            type=PropertyType(data["type"]),
            bhk=data["bhk"],
            sqft=data.get("sqft"),
            furnishing=Furnishing(data["furnishing"]),
            floor=data.get("floor"),
            total_floors=data.get("total_floors"),
            facing=data.get("facing"),
            rent=rent,
            security_deposit=data.get("security_deposit"),
            maintenance_charges=data.get("maintenance_charges"),
            description=data.get("description"),
            images=data.get("images", []),
            premium=data.get("premium", False),
            amenities=data.get("amenities", []),
            status=status,
            onboarding_step=1,
            min_rent=rent_for_range,
            max_rent=rent_for_range,
            final_rent=rent,
            owner_id=owner_id,
            created_by=creator.id,
        )
        db.add(prop)
        await db.flush()

        # Auto-assign manager(s)
        if manager_ids:
            for mid in manager_ids:
                db.add(PropertyManager(
                    property_id=prop.id,
                    manager_id=mid,
                    role=PropertyManagerRole.PRIMARY,
                    assigned_by=creator.id,
                ))
        elif creator.active_role == Role.MANAGER:
            db.add(PropertyManager(
                property_id=prop.id,
                manager_id=creator.id,
                role=PropertyManagerRole.PRIMARY,
                assigned_by=creator.id,
            ))

        await db.flush()
        await db.refresh(prop)
        return property_to_response(prop)

    @staticmethod
    async def get_by_id(db: AsyncSession, property_id: str) -> dict:
        """Get a property by ID."""
        result = await db.execute(
            select(Property).where(Property.id == property_id)
        )
        prop = result.scalar_one_or_none()
        if not prop:
            raise NotFoundError("Property")
        return property_to_response(prop)

    @staticmethod
    async def get_enriched_detail(db: AsyncSession, property_id: str) -> dict:
        """Get property with owner, tenant, managers, and occupancy history."""
        result = await db.execute(
            select(Property)
            .options(
                selectinload(Property.owner),
                selectinload(Property.tenant),
                selectinload(Property.property_managers).selectinload(PropertyManager.manager),
                selectinload(Property.occupancy_history).selectinload(PropertyOccupancyHistory.tenant),
            )
            .where(Property.id == property_id)
        )
        prop = result.scalar_one_or_none()
        if not prop:
            raise NotFoundError("Property")

        base = property_to_response(prop)
        base["owner"] = {
            "id": prop.owner.id,
            "name": prop.owner.name,
            "email": prop.owner.email,
            "phone": prop.owner.phone,
            "status": prop.owner.status.api_value,
        } if prop.owner else None

        base["tenant_detail"] = {
            "id": prop.tenant.id,
            "name": prop.tenant.name,
            "email": prop.tenant.email,
            "phone": prop.tenant.phone,
            "status": prop.tenant.status.api_value,
        } if prop.tenant else None

        base["managers"] = [
            {
                "id": pm.manager.id,
                "name": pm.manager.name,
                "email": pm.manager.email,
                "role": pm.role.value,
                "assigned_at": pm.assigned_at.isoformat() if pm.assigned_at else None,
            }
            for pm in prop.property_managers
            if pm.manager
        ]

        base["occupancy_history"] = [
            {
                "id": oh.id,
                "tenant_id": oh.tenant_id,
                "tenant_name": oh.tenant.name if oh.tenant else None,
                "status": oh.status.value,
                "start_date": oh.start_date.isoformat() if oh.start_date else None,
                "end_date": oh.end_date.isoformat() if oh.end_date else None,
                "notes": oh.notes,
            }
            for oh in sorted(prop.occupancy_history, key=lambda x: x.start_date, reverse=True)
        ]

        return base

    # ── Manager Assignment ──

    @staticmethod
    async def assign_managers(
        db: AsyncSession,
        property_id: str,
        manager_ids: list[str],
        role: str,
        assigned_by: str,
    ) -> list[dict]:
        """Assign one or more managers to a property."""
        prop = (await db.execute(
            select(Property).where(Property.id == property_id)
        )).scalar_one_or_none()
        if not prop:
            raise NotFoundError("Property")

        pm_role = PropertyManagerRole(role)
        added = []
        for mid in manager_ids:
            existing = (await db.execute(
                select(PropertyManager).where(
                    PropertyManager.property_id == property_id,
                    PropertyManager.manager_id == mid,
                )
            )).scalar_one_or_none()
            if existing:
                continue
            pm = PropertyManager(
                property_id=property_id,
                manager_id=mid,
                role=pm_role,
                assigned_by=assigned_by,
            )
            db.add(pm)
            added.append(mid)

        await db.flush()
        return added

    @staticmethod
    async def remove_manager(
        db: AsyncSession, property_id: str, manager_id: str
    ) -> dict:
        """Remove a manager from a property."""
        result = await db.execute(
            select(PropertyManager).where(
                PropertyManager.property_id == property_id,
                PropertyManager.manager_id == manager_id,
            )
        )
        pm = result.scalar_one_or_none()
        if not pm:
            raise NotFoundError("PropertyManager")
        await db.delete(pm)
        await db.flush()
        return {"message": "Manager removed from property"}

    # ── Tenant Assignment with Occupancy History ──

    @staticmethod
    async def assign_tenant(
        db: AsyncSession,
        property_id: str,
        tenant_id: str,
        *,
        lease_start: datetime | None = None,
        lease_end: datetime | None = None,
        agreement_id: str | None = None,
        notes: str | None = None,
        actor_id: str | None = None,
    ) -> dict:
        """Assign a tenant to a property and record occupancy history."""
        prop = (await db.execute(
            select(Property).where(Property.id == property_id)
        )).scalar_one_or_none()
        if not prop:
            raise NotFoundError("Property")
        if prop.tenant_id:
            raise BadRequestError("Property already has a tenant. Remove the current tenant first.")

        prop.tenant_id = tenant_id
        prop.occupancy = Occupancy.OCCUPIED
        if lease_start:
            prop.lease_start = lease_start
        if lease_end:
            prop.lease_end = lease_end

        db.add(PropertyOccupancyHistory(
            id=generate_cuid(),
            property_id=property_id,
            tenant_id=tenant_id,
            status=Occupancy.OCCUPIED,
            start_date=lease_start or datetime.now(timezone.utc),
            agreement_id=agreement_id,
            notes=notes,
        ))

        await db.flush()

        tenant = await db.get(User, tenant_id)
        from app.services.notification_service import NotificationService
        await NotificationService.notify_property_stakeholders(
            db,
            property_id=property_id,
            type="tenant_onboarded",
            title="Tenant Onboarded",
            body=f"{tenant.name if tenant else 'A tenant'} has been onboarded to {prop.name}.",
            actor_id=actor_id,
            icon="person_add",
            data={"property_id": property_id, "tenant_id": tenant_id},
        )
        return property_to_response(prop)

    @staticmethod
    async def remove_tenant(
        db: AsyncSession,
        property_id: str,
        *,
        notes: str | None = None,
        actor_id: str | None = None,
    ) -> dict:
        """Remove the current tenant and close the occupancy history record."""
        prop = (await db.execute(
            select(Property).where(Property.id == property_id)
        )).scalar_one_or_none()
        if not prop:
            raise NotFoundError("Property")
        if not prop.tenant_id:
            raise BadRequestError("Property has no tenant to remove.")

        old_tenant_id = prop.tenant_id
        now = datetime.now(timezone.utc)

        # Close the open occupancy record
        open_record = (await db.execute(
            select(PropertyOccupancyHistory).where(
                PropertyOccupancyHistory.property_id == property_id,
                PropertyOccupancyHistory.tenant_id == old_tenant_id,
                PropertyOccupancyHistory.end_date.is_(None),
            )
        )).scalar_one_or_none()
        if open_record:
            open_record.end_date = now
            if notes:
                open_record.notes = (open_record.notes or "") + f" | Removed: {notes}"

        prop.tenant_id = None
        prop.occupancy = Occupancy.VACANT
        prop.lease_start = None
        prop.lease_end = None

        await db.flush()

        old_tenant = await db.get(User, old_tenant_id) if old_tenant_id else None
        from app.services.notification_service import NotificationService
        await NotificationService.notify_property_stakeholders(
            db,
            property_id=property_id,
            type="tenant_vacated",
            title="Tenant Vacated",
            body=f"{old_tenant.name if old_tenant else 'The tenant'} has vacated {prop.name}.",
            actor_id=actor_id,
            icon="logout",
            data={"property_id": property_id, "tenant_id": old_tenant_id},
        )
        return property_to_response(prop)

    # ── Occupancy History ──

    @staticmethod
    async def get_occupancy_history(db: AsyncSession, property_id: str) -> list[dict]:
        """Get the full occupancy timeline for a property."""
        result = await db.execute(
            select(PropertyOccupancyHistory)
            .options(selectinload(PropertyOccupancyHistory.tenant))
            .where(PropertyOccupancyHistory.property_id == property_id)
            .order_by(PropertyOccupancyHistory.start_date.desc())
        )
        return [
            {
                "id": oh.id,
                "tenant_id": oh.tenant_id,
                "tenant_name": oh.tenant.name if oh.tenant else None,
                "status": oh.status.value,
                "start_date": oh.start_date.isoformat() if oh.start_date else None,
                "end_date": oh.end_date.isoformat() if oh.end_date else None,
                "agreement_id": oh.agreement_id,
                "notes": oh.notes,
            }
            for oh in result.scalars().all()
        ]

    @staticmethod
    async def update(
        db: AsyncSession,
        property_id: str,
        user: User,
        **data,
    ) -> dict:
        """Update a property. Only the owner, assigned manager, or super_admin can update."""
        result = await db.execute(
            select(Property).where(Property.id == property_id)
        )
        prop = result.scalar_one_or_none()
        if not prop:
            raise NotFoundError("Property")

        # Permission check
        if prop.owner_id != user.id:
            role = user.active_role.api_value
            if role == "super_admin":
                pass  # super-admin always allowed
            elif role == "manager":
                if not await _is_property_manager(db, property_id, user.id):
                    raise ForbiddenError("You can only update properties you manage")
            else:
                raise ForbiddenError("You can only update your own properties")

        # Apply partial scalar updates (only fields explicitly provided).
        updatable = [
            "name", "unit", "address", "city", "state", "pincode", "bhk",
            "sqft", "floor", "total_floors", "facing",
            "rent", "security_deposit", "maintenance_charges",
            "description", "premium", "amenities", "images",
            "lease_start", "lease_end",
        ]
        for field in updatable:
            if field in data and data[field] is not None:
                setattr(prop, field, data[field])

        if data.get("type") is not None:
            prop.type = PropertyType(data["type"])
        if data.get("furnishing") is not None:
            prop.furnishing = Furnishing(data["furnishing"])
        if data.get("occupancy") is not None:
            prop.occupancy = Occupancy(data["occupancy"])
        if data.get("owner_id") is not None:
            prop.owner_id = data["owner_id"]

        # Keep the rent range in sync with the single rent for the SA flow.
        if data.get("rent") is not None:
            prop.min_rent = data["rent"]
            prop.max_rent = data["rent"]
            prop.final_rent = data["rent"]

        # ── Status transition (publish / archive) ──
        if data.get("status") is not None:
            new_status = PropertyStatus(data["status"])
            if new_status == PropertyStatus.ACTIVE:
                missing = [
                    f for f in (
                        "sqft", "floor", "total_floors",
                        "rent", "security_deposit", "maintenance_charges",
                    )
                    if getattr(prop, f) is None
                ]
                if missing:
                    raise BadRequestError(
                        "Cannot publish: complete these fields first → " + ", ".join(missing)
                    )
            prop.status = new_status

        # ── Manager reassignment (full replace when provided) ──
        manager_ids = data.get("manager_ids")
        if manager_ids is not None:
            await db.execute(
                sa_delete(PropertyManager).where(PropertyManager.property_id == prop.id)
            )
            for mid in manager_ids:
                db.add(PropertyManager(
                    property_id=prop.id,
                    manager_id=mid,
                    role=PropertyManagerRole.PRIMARY,
                    assigned_by=user.id,
                ))

        await db.flush()
        await db.refresh(prop)
        return property_to_response(prop)

    @staticmethod
    async def delete(
        db: AsyncSession, property_id: str, user: User
    ) -> dict:
        """Soft-delete a property. Only the owner or admin can delete."""
        result = await db.execute(
            select(Property).where(Property.id == property_id)
        )
        prop = result.scalar_one_or_none()
        if not prop:
            raise NotFoundError("Property")

        if prop.owner_id != user.id:
            role = user.active_role.api_value
            if role == "super_admin":
                pass
            elif role == "manager":
                if not await _is_property_manager(db, property_id, user.id):
                    raise ForbiddenError("You can only delete properties you manage")
            else:
                raise ForbiddenError("You can only delete your own properties")

        await db.delete(prop)
        await db.flush()
        return {"message": "Property deleted successfully"}

    @staticmethod
    async def search(
        db: AsyncSession,
        *,
        page: int = 1,
        limit: int = 20,
        search: str | None = None,
        city: str | None = None,
        type: str | None = None,
        furnishing: str | None = None,
        occupancy: str | None = None,
        min_rent: int | None = None,
        max_rent: int | None = None,
        bhk: str | None = None,
        premium: bool | None = None,
        status: str | None = None,
        owner_id: str | None = None,
        tenant_id: str | None = None,
        restrict_to_property_ids: list[str] | None = None,
        sort: str = "-created_at",
    ) -> tuple[list[dict], int]:
        """
        Search and filter properties with pagination.

        Supports text search across name, address, city, and description,
        plus a `status` filter (draft / active / archived).

        ``restrict_to_property_ids`` is used by the route to limit a manager's
        view to properties they're assigned to. An empty list means the user
        has no accessible properties and the query short-circuits to empty.
        """
        if restrict_to_property_ids is not None and len(restrict_to_property_ids) == 0:
            return [], 0

        query = select(Property)
        if restrict_to_property_ids is not None:
            query = query.where(Property.id.in_(restrict_to_property_ids))

        # ── Filters ──
        if city:
            query = query.where(Property.city.ilike(f"%{city}%"))
        if type:
            query = query.where(Property.type == PropertyType(type))
        if furnishing:
            query = query.where(Property.furnishing == Furnishing(furnishing))
        if occupancy:
            query = query.where(Property.occupancy == Occupancy(occupancy))
        if status:
            query = query.where(Property.status == PropertyStatus(status))
        if min_rent is not None:
            query = query.where(Property.rent >= min_rent)
        if max_rent is not None:
            query = query.where(Property.rent <= max_rent)
        if bhk:
            query = query.where(Property.bhk.ilike(f"%{bhk}%"))
        if premium is not None:
            query = query.where(Property.premium == premium)
        if owner_id:
            query = query.where(Property.owner_id == owner_id)
        if tenant_id:
            query = query.where(Property.tenant_id == tenant_id)

        # ── Text Search ──
        if search:
            pattern = f"%{search}%"
            query = query.where(
                or_(
                    Property.name.ilike(pattern),
                    Property.address.ilike(pattern),
                    Property.city.ilike(pattern),
                    Property.description.ilike(pattern),
                )
            )

        # ── Count ──
        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        # ── Sort ──
        if sort.startswith("-"):
            col = getattr(Property, sort[1:], Property.created_at)
            query = query.order_by(col.desc())
        else:
            col = getattr(Property, sort, Property.created_at)
            query = query.order_by(col.asc())

        # ── Paginate ──
        query = query.offset((page - 1) * limit).limit(limit)

        result = await db.execute(query)
        properties = result.scalars().all()

        return [property_to_response(p) for p in properties], total

    @staticmethod
    async def get_owner_properties(
        db: AsyncSession, owner_id: str
    ) -> list[dict]:
        """Get all properties owned by a user."""
        from sqlalchemy.orm import selectinload
        result = await db.execute(
            select(Property)
            .options(selectinload(Property.tenant))
            .where(Property.owner_id == owner_id)
            .order_by(Property.created_at.desc())
        )
        props = []
        for p in result.scalars().all():
            d = property_to_response(p)
            d["tenant_name"] = p.tenant.name if p.tenant else None
            props.append(d)
        return props

    @staticmethod
    async def get_tenant_property(
        db: AsyncSession, tenant_id: str
    ) -> list[dict]:
        """Get properties where the user is a tenant."""
        result = await db.execute(
            select(Property)
            .where(Property.tenant_id == tenant_id)
            .order_by(Property.created_at.desc())
        )
        return [property_to_response(p) for p in result.scalars().all()]
