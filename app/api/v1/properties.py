"""
LuxeLife API — Property routes.

Handles property browsing, CRUD, owner/tenant views.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_roles
from app.core.responses import paginated_response, success_response
from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.property import (
    AssignManagersRequest,
    AssignTenantRequest,
    PropertyCreate,
    PropertyUpdate,
    RemoveTenantRequest,
)
from app.services.property_scope import (
    get_managed_property_ids,
    is_property_manager,
)
from app.services.property_service import PropertyService

router = APIRouter(prefix="/properties", tags=["Properties"])


# ── Browse & Search ──

@router.get("")
async def search_properties(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, description="Search name, address, city"),
    city: str | None = Query(None),
    type: str | None = Query(None),
    furnishing: str | None = Query(None),
    occupancy: str | None = Query(None),
    min_rent: int | None = Query(None, ge=0),
    max_rent: int | None = Query(None),
    bhk: str | None = Query(None),
    premium: bool | None = Query(None),
    status: str | None = Query(None, pattern=r"^(draft|active|archived)$"),
    sort: str = Query("-created_at"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Browse and search properties with filters.

    Supports filtering by city, type, furnishing, occupancy, rent range, BHK,
    premium, and listing status (draft / active / archived).

    Scope by role: managers see only properties they're assigned to. Tenants,
    owners, and super-admin see the full catalogue.
    """
    restrict_to_property_ids: list[str] | None = None
    role = user.active_role.api_value
    if role == "manager":
        restrict_to_property_ids = await get_managed_property_ids(db, user.id)
    elif role == "tenant":
        # Tenants browse the marketplace — only published, vacant listings.
        # Force these regardless of what the client sent, so the discovery
        # screen can never accidentally surface occupied or draft homes.
        status = "active"
        occupancy = "vacant"

    items, total = await PropertyService.search(
        db,
        page=page,
        limit=limit,
        search=search,
        city=city,
        type=type,
        furnishing=furnishing,
        occupancy=occupancy,
        min_rent=min_rent,
        max_rent=max_rent,
        bhk=bhk,
        premium=premium,
        status=status,
        restrict_to_property_ids=restrict_to_property_ids,
        sort=sort,
    )
    return paginated_response(items, total, page, limit)


@router.get("/owner/me")
async def get_my_properties(
    user: User = Depends(require_roles("owner", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Get all properties owned by the current user."""
    items = await PropertyService.get_owner_properties(db, user.id)
    return success_response(items)


@router.get("/tenant/me")
async def get_my_rented_properties(
    user: User = Depends(require_roles("tenant")),
    db: AsyncSession = Depends(get_db),
):
    """Get properties where the current user is a tenant."""
    items = await PropertyService.get_tenant_property(db, user.id)
    return success_response(items)


# ── CRUD ──

@router.get("/{property_id}")
async def get_property(
    property_id: str,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get property details by ID."""
    result = await PropertyService.get_by_id(db, property_id)
    return success_response(result)


@router.post("", status_code=201)
async def create_property(
    body: PropertyCreate,
    user: User = Depends(require_roles("owner", "admin", "manager", "super_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new property listing. **Owner/Admin/Manager/SuperAdmin.**"""
    result = await PropertyService.create(db, user, **body.model_dump())
    return success_response(result)


@router.patch("/{property_id}")
async def update_property(
    property_id: str,
    body: PropertyUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a property. Only the owner or admin can update."""
    data = body.model_dump(exclude_none=True)
    result = await PropertyService.update(db, property_id, user, **data)
    return success_response(result)


@router.delete("/{property_id}")
async def delete_property(
    property_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a property. Only the owner or admin can delete."""
    result = await PropertyService.delete(db, property_id, user)
    return success_response(result)


# ── Enriched Detail ──

@router.get("/{property_id}/details")
async def get_property_details(
    property_id: str,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get enriched property details including owner, tenant, managers, and occupancy history."""
    result = await PropertyService.get_enriched_detail(db, property_id)
    return success_response(result)


# ── Manager Assignment ──

async def _require_property_authority(db: AsyncSession, property_id: str, user: User) -> None:
    """Allow super_admin everywhere; managers only if assigned; reject others."""
    role = user.active_role.api_value
    if role == "super_admin":
        return
    if role in {"manager", "admin"}:
        if await is_property_manager(db, property_id, user.id):
            return
        from fastapi import HTTPException
        raise HTTPException(403, "You can only act on properties you manage")
    from fastapi import HTTPException
    raise HTTPException(403, "Insufficient permissions for this property")


@router.post("/{property_id}/managers", status_code=201)
async def assign_managers(
    property_id: str,
    body: AssignManagersRequest,
    user: User = Depends(require_roles("admin", "manager", "super_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Assign managers to a property. Super-admin or an already-assigned manager."""
    await _require_property_authority(db, property_id, user)
    added = await PropertyService.assign_managers(
        db, property_id, body.manager_ids, body.role, user.id
    )
    return success_response({"added": added})


@router.delete("/{property_id}/managers/{manager_id}")
async def remove_manager(
    property_id: str,
    manager_id: str,
    user: User = Depends(require_roles("admin", "manager", "super_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Remove a manager from a property."""
    await _require_property_authority(db, property_id, user)
    result = await PropertyService.remove_manager(db, property_id, manager_id)
    return success_response(result)


# ── Tenant Assignment ──

@router.post("/{property_id}/assign-tenant", status_code=201)
async def assign_tenant(
    property_id: str,
    body: AssignTenantRequest,
    user: User = Depends(require_roles("admin", "manager", "super_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Assign a tenant to a property and create an occupancy history record."""
    await _require_property_authority(db, property_id, user)
    result = await PropertyService.assign_tenant(
        db, property_id, body.tenant_id,
        lease_start=body.lease_start,
        lease_end=body.lease_end,
        agreement_id=body.agreement_id,
        notes=body.notes,
        actor_id=user.id,
    )
    return success_response(result)


@router.post("/{property_id}/remove-tenant")
async def remove_tenant(
    property_id: str,
    body: RemoveTenantRequest,
    user: User = Depends(require_roles("admin", "manager", "super_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Remove the current tenant and close the occupancy record."""
    await _require_property_authority(db, property_id, user)
    result = await PropertyService.remove_tenant(db, property_id, notes=body.notes, actor_id=user.id)
    return success_response(result)


# ── Occupancy History ──

@router.get("/{property_id}/occupancy-history")
async def get_occupancy_history(
    property_id: str,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the occupancy history timeline for a property."""
    result = await PropertyService.get_occupancy_history(db, property_id)
    return success_response(result)
