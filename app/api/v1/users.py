"""
LuxeLife API — User routes.

Handles user profile and admin user management.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_roles
from app.core.responses import paginated_response, success_response
from app.database import get_db
from app.dependencies import get_current_user
from app.models.onboarding_workflow import OnboardingWorkflowState, PropertyOnboardingWorkflow
from app.models.job import Job
from app.models.property import Property, PropertyManager, PropertyManagerRole
from app.models.user import Role, User
from app.schemas.user import (
    AdminCreateUserRequest,
    InviteOwnerRequest,
    UserUpdateRequest,
    user_to_response,
)
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["Users"])


# ── Current User ──

@router.get("/me")
async def get_me(current_user: User = Depends(get_current_user)):
    """Get the current authenticated user's profile."""
    return success_response(user_to_response(current_user))


@router.patch("/me")
async def update_me(
    body: UserUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Update the current user's profile.

    Only provided (non-null) fields are updated.
    """
    result = await UserService.update_profile(
        db,
        current_user,
        name=body.name,
        location=body.location,
        avatar=body.avatar,
        fcm_token=body.fcm_token,
    )
    return success_response(result)


@router.post("/create", status_code=201)
async def create_user(
    body: AdminCreateUserRequest,
    admin: User = Depends(require_roles("super_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Super-admin operation to create users with explicit role assignments."""
    result = await UserService.create_user(
        db,
        creator_id=admin.id,
        name=body.name,
        email=str(body.email),
        phone=body.phone,
        password=body.password,
        active_role=body.active_role,
        roles=body.roles,
        status=body.status,
    )

    # If creating a manager with property_ids, assign them
    if body.property_ids and body.active_role == "manager" and result.get("id"):
        for pid in body.property_ids:
            existing = (await db.execute(
                select(PropertyManager).where(
                    PropertyManager.property_id == pid,
                    PropertyManager.manager_id == result["id"],
                )
            )).scalar_one_or_none()
            if not existing:
                db.add(PropertyManager(
                    property_id=pid,
                    manager_id=result["id"],
                    role=PropertyManagerRole.PRIMARY,
                    assigned_by=admin.id,
                ))
        await db.flush()

    return success_response(result)


# ── Admin: User Management ──

@router.post("/invite-owner")
async def invite_owner(
    body: InviteOwnerRequest,
    admin: User = Depends(require_roles("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Invite a new owner by email and return a one-time temporary password."""
    result = await UserService.invite_owner(
        db,
        admin_id=admin.id,
        name=body.name,
        email=str(body.email),
    )
    return success_response(result)


@router.get("/admin-stats")
async def admin_stats(
    _admin: User = Depends(require_roles("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Admin dashboard counts: users, properties, and pending onboarding actions."""
    user_count = (await db.execute(select(func.count()).select_from(User))).scalar() or 0
    property_count = (await db.execute(select(func.count()).select_from(Property))).scalar() or 0
    pending_actions_count = (
        await db.execute(
            select(func.count()).select_from(PropertyOnboardingWorkflow).where(
                PropertyOnboardingWorkflow.state != OnboardingWorkflowState.TENANT_ACTIVATED
            )
        )
    ).scalar() or 0

    return success_response(
        {
            "user_count": user_count,
            "property_count": property_count,
            "pending_actions_count": pending_actions_count,
        }
    )

@router.get("")
async def list_users(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    role: str | None = Query(None),
    status: str | None = Query(None),
    search: str | None = Query(None),
    sort: str = Query("-created_at"),
    _admin: User = Depends(require_roles("admin")),
    db: AsyncSession = Depends(get_db),
):
    """
    List all users with filtering, search, and pagination.

    **Admin only.**
    """
    items, total = await UserService.list_users(
        db,
        page=page,
        limit=limit,
        role=role,
        status=status,
        search=search,
        sort=sort,
    )
    return paginated_response(items, total, page, limit)


@router.get("/{user_id}")
async def get_user(
    user_id: str,
    _admin: User = Depends(require_roles("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Get any user by ID. **Admin only.**"""
    user = await UserService.get_by_id(db, user_id)
    return success_response(user_to_response(user))


@router.patch("/{user_id}/status")
async def update_user_status(
    user_id: str,
    new_status: str = Query(..., pattern=r"^(pending|awaiting_review|verified|suspended)$"),
    _admin: User = Depends(require_roles("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Update a user's account status. **Admin only.**"""
    result = await UserService.update_status(db, user_id, new_status=new_status)
    return success_response(result)


@router.get("/by-role/{role}")
async def get_users_by_role(
    role: str,
    search: str | None = Query(None),
    _user: User = Depends(require_roles("admin", "manager", "super_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Lightweight endpoint returning users filtered by role for dropdowns."""
    parsed_role = Role.from_api(role)
    query = select(User).where(User.active_role == parsed_role)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            User.name.ilike(pattern) | User.email.ilike(pattern)
        )
    query = query.order_by(User.name.asc()).limit(100)
    result = await db.execute(query)
    users = result.scalars().all()
    return success_response([
        {"id": u.id, "name": u.name, "email": u.email, "phone": u.phone, "status": u.status.api_value}
        for u in users
    ])


@router.get("/{user_id}/managed-summary")
async def get_user_managed_summary(
    user_id: str,
    _super_admin: User = Depends(require_roles("super_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Return high-level ownership/management context for a selected user."""
    user = await UserService.get_by_id(db, user_id)

    owned_properties = (
        await db.execute(
            select(Property)
            .where(Property.owner_id == user_id)
            .order_by(Property.created_at.desc())
            .limit(50)
        )
    ).scalars().all()

    tenant_properties = (
        await db.execute(
            select(Property)
            .where(Property.tenant_id == user_id)
            .order_by(Property.created_at.desc())
            .limit(50)
        )
    ).scalars().all()

    # Properties managed by this user (via property_managers M:N table)
    managed_properties_result = await db.execute(
        select(Property)
        .join(PropertyManager, PropertyManager.property_id == Property.id)
        .where(PropertyManager.manager_id == user_id)
        .order_by(Property.created_at.desc())
        .limit(50)
    )
    managed_properties = managed_properties_result.scalars().all()

    provider_jobs = (
        await db.execute(
            select(Job)
            .where(Job.provider_id == user_id)
            .order_by(Job.created_at.desc())
            .limit(50)
        )
    ).scalars().all()

    invited_users = (
        await db.execute(
            select(User)
            .where(User.invited_by == user_id)
            .order_by(User.created_at.desc())
            .limit(50)
        )
    ).scalars().all()

    return success_response(
        {
            "user": user_to_response(user),
            "counts": {
                "owned_properties": len(owned_properties),
                "tenant_properties": len(tenant_properties),
                "managed_properties": len(managed_properties),
                "provider_jobs": len(provider_jobs),
                "invited_users": len(invited_users),
            },
            "owned_properties": [
                {
                    "id": property_item.id,
                    "name": property_item.name,
                    "city": property_item.city,
                    "occupancy": property_item.occupancy.value,
                    "rent": property_item.rent,
                }
                for property_item in owned_properties
            ],
            "tenant_properties": [
                {
                    "id": property_item.id,
                    "name": property_item.name,
                    "city": property_item.city,
                    "occupancy": property_item.occupancy.value,
                    "rent": property_item.rent,
                }
                for property_item in tenant_properties
            ],
            "managed_properties": [
                {
                    "id": property_item.id,
                    "name": property_item.name,
                    "city": property_item.city,
                    "occupancy": property_item.occupancy.value,
                    "rent": property_item.rent,
                }
                for property_item in managed_properties
            ],
            "provider_jobs": [
                {
                    "id": job.id,
                    "service_type": job.service_type,
                    "status": job.status.value,
                    "property_id": job.property_id,
                    "created_at": job.created_at,
                }
                for job in provider_jobs
            ],
            "invited_users": [
                {
                    "id": invited.id,
                    "name": invited.name,
                    "email": invited.email,
                    "active_role": invited.active_role.api_value,
                    "status": invited.status.api_value,
                    "created_at": invited.created_at,
                }
                for invited in invited_users
            ],
        }
    )
