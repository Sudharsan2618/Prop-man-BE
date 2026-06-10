"""
LuxeLife API — RBAC routes.

Super Admin APIs for full role-permission-user assignment management.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_roles
from app.core.responses import success_response
from app.database import get_db
from app.models.user import User
from app.schemas.rbac import (
    PermissionCreateRequest,
    PermissionUpdateRequest,
    RoleCreateRequest,
    RolePermissionsUpdateRequest,
    RoleUpdateRequest,
    UserRolesUpdateRequest,
)
from app.services.rbac_service import RBACService

router = APIRouter(prefix="/rbac", tags=["RBAC"])


@router.get("/roles")
async def list_roles(
    _super_admin: User = Depends(require_roles("super_admin")),
    db: AsyncSession = Depends(get_db),
):
    return success_response(await RBACService.list_roles(db))


@router.post("/roles", status_code=201)
async def create_role(
    body: RoleCreateRequest,
    _super_admin: User = Depends(require_roles("super_admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await RBACService.create_role(
        db,
        name=body.name,
        description=body.description,
    )
    return success_response(result)


@router.patch("/roles/{role_id}")
async def update_role(
    role_id: int,
    body: RoleUpdateRequest,
    _super_admin: User = Depends(require_roles("super_admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await RBACService.update_role(
        db,
        role_id=role_id,
        description=body.description,
    )
    return success_response(result)


@router.delete("/roles/{role_id}")
async def delete_role(
    role_id: int,
    _super_admin: User = Depends(require_roles("super_admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await RBACService.delete_role(db, role_id=role_id)
    return success_response(result)


@router.put("/roles/{role_id}/permissions")
async def set_role_permissions(
    role_id: int,
    body: RolePermissionsUpdateRequest,
    _super_admin: User = Depends(require_roles("super_admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await RBACService.set_role_permissions(
        db,
        role_id=role_id,
        permission_ids=body.permission_ids,
    )
    return success_response(result)


@router.get("/permissions")
async def list_permissions(
    _super_admin: User = Depends(require_roles("super_admin")),
    db: AsyncSession = Depends(get_db),
):
    return success_response(await RBACService.list_permissions(db))


@router.post("/permissions", status_code=201)
async def create_permission(
    body: PermissionCreateRequest,
    _super_admin: User = Depends(require_roles("super_admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await RBACService.create_permission(
        db,
        code=body.code,
        description=body.description,
        entity=body.entity,
        action=body.action,
    )
    return success_response(result)


@router.patch("/permissions/{permission_id}")
async def update_permission(
    permission_id: int,
    body: PermissionUpdateRequest,
    _super_admin: User = Depends(require_roles("super_admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await RBACService.update_permission(
        db,
        permission_id=permission_id,
        code=body.code,
        description=body.description,
        entity=body.entity,
        action=body.action,
    )
    return success_response(result)


@router.delete("/permissions/{permission_id}")
async def delete_permission(
    permission_id: int,
    _super_admin: User = Depends(require_roles("super_admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await RBACService.delete_permission(db, permission_id=permission_id)
    return success_response(result)


@router.get("/matrix")
async def get_permission_matrix(
    _super_admin: User = Depends(require_roles("super_admin")),
    db: AsyncSession = Depends(get_db),
):
    return success_response(await RBACService.get_matrix(db))


@router.get("/users/{user_id}/roles")
async def get_user_roles(
    user_id: str,
    _super_admin: User = Depends(require_roles("super_admin")),
    db: AsyncSession = Depends(get_db),
):
    return success_response(await RBACService.get_user_roles(db, user_id=user_id))


@router.put("/users/{user_id}/roles")
async def set_user_roles(
    user_id: str,
    body: UserRolesUpdateRequest,
    super_admin: User = Depends(require_roles("super_admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await RBACService.set_user_roles(
        db,
        user_id=user_id,
        role_ids=body.role_ids,
        assigned_by=super_admin.id,
    )
    return success_response(result)
