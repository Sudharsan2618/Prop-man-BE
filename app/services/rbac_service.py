"""
LuxeLife API — RBAC service.

Business logic for managing roles, permissions, and user-role mappings.
"""

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.rbac import Permission, RBACRole, RolePermission, UserRole
from app.models.user import Role, User


BUILTIN_ROLES = {
    Role.TENANT,
    Role.OWNER,
    Role.SERVICE_PROVIDER,
    Role.MANAGER,
    Role.SUPER_ADMIN,
}


class RBACService:
    """Service layer for RBAC CRUD and assignments."""

    @staticmethod
    async def list_roles(db: AsyncSession) -> list[dict]:
        roles = (await db.execute(select(RBACRole).order_by(RBACRole.id.asc()))).scalars().all()
        role_ids = [r.id for r in roles]

        permission_map: dict[int, list[int]] = {rid: [] for rid in role_ids}
        if role_ids:
            role_permission_rows = (
                await db.execute(
                    select(RolePermission.role_id, RolePermission.permission_id).where(
                        RolePermission.role_id.in_(role_ids)
                    )
                )
            ).all()
            for role_id, permission_id in role_permission_rows:
                permission_map[role_id].append(permission_id)

        return [
            {
                "id": role.id,
                "name": role.name.api_value,
                "description": role.description,
                "created_at": role.created_at,
                "permission_ids": sorted(permission_map.get(role.id, [])),
            }
            for role in roles
        ]

    @staticmethod
    async def create_role(db: AsyncSession, *, name: str, description: str | None) -> dict:
        try:
            role_name = Role.from_api(name)
        except ValueError as exc:
            raise BadRequestError(f"Unsupported role: {name}") from exc

        existing = await db.execute(select(RBACRole).where(RBACRole.name == role_name))
        if existing.scalar_one_or_none():
            raise ConflictError("Role already exists")

        role = RBACRole(name=role_name, description=description)
        db.add(role)
        await db.flush()

        return {
            "id": role.id,
            "name": role.name.api_value,
            "description": role.description,
            "created_at": role.created_at,
            "permission_ids": [],
        }

    @staticmethod
    async def update_role(
        db: AsyncSession,
        *,
        role_id: int,
        description: str | None,
    ) -> dict:
        role = (await db.execute(select(RBACRole).where(RBACRole.id == role_id))).scalar_one_or_none()
        if not role:
            raise NotFoundError("Role")

        role.description = description
        await db.flush()

        permission_ids = (
            await db.execute(
                select(RolePermission.permission_id).where(RolePermission.role_id == role.id)
            )
        ).scalars().all()

        return {
            "id": role.id,
            "name": role.name.api_value,
            "description": role.description,
            "created_at": role.created_at,
            "permission_ids": sorted(permission_ids),
        }

    @staticmethod
    async def delete_role(db: AsyncSession, *, role_id: int) -> dict:
        role = (await db.execute(select(RBACRole).where(RBACRole.id == role_id))).scalar_one_or_none()
        if not role:
            raise NotFoundError("Role")

        if role.name in BUILTIN_ROLES:
            raise BadRequestError("Built-in roles cannot be deleted")

        await db.delete(role)
        await db.flush()
        return {"message": "Role deleted successfully"}

    @staticmethod
    async def list_permissions(db: AsyncSession) -> list[dict]:
        permissions = (
            await db.execute(select(Permission).order_by(Permission.entity.asc(), Permission.code.asc()))
        ).scalars().all()
        return [
            {
                "id": permission.id,
                "code": permission.code,
                "description": permission.description,
                "entity": permission.entity,
                "action": permission.action,
                "created_at": permission.created_at,
            }
            for permission in permissions
        ]

    @staticmethod
    async def create_permission(
        db: AsyncSession,
        *,
        code: str,
        description: str | None,
        entity: str,
        action: str,
    ) -> dict:
        existing = await db.execute(select(Permission).where(Permission.code == code))
        if existing.scalar_one_or_none():
            raise ConflictError("Permission code already exists")

        permission = Permission(
            code=code,
            description=description,
            entity=entity,
            action=action,
        )
        db.add(permission)
        await db.flush()

        return {
            "id": permission.id,
            "code": permission.code,
            "description": permission.description,
            "entity": permission.entity,
            "action": permission.action,
            "created_at": permission.created_at,
        }

    @staticmethod
    async def update_permission(
        db: AsyncSession,
        *,
        permission_id: int,
        code: str | None,
        description: str | None,
        entity: str | None,
        action: str | None,
    ) -> dict:
        permission = (
            await db.execute(select(Permission).where(Permission.id == permission_id))
        ).scalar_one_or_none()
        if not permission:
            raise NotFoundError("Permission")

        if code and code != permission.code:
            dup = await db.execute(select(Permission).where(Permission.code == code))
            if dup.scalar_one_or_none():
                raise ConflictError("Permission code already exists")
            permission.code = code

        if description is not None:
            permission.description = description
        if entity is not None:
            permission.entity = entity
        if action is not None:
            permission.action = action

        await db.flush()
        return {
            "id": permission.id,
            "code": permission.code,
            "description": permission.description,
            "entity": permission.entity,
            "action": permission.action,
            "created_at": permission.created_at,
        }

    @staticmethod
    async def delete_permission(db: AsyncSession, *, permission_id: int) -> dict:
        permission = (
            await db.execute(select(Permission).where(Permission.id == permission_id))
        ).scalar_one_or_none()
        if not permission:
            raise NotFoundError("Permission")

        await db.delete(permission)
        await db.flush()
        return {"message": "Permission deleted successfully"}

    @staticmethod
    async def set_role_permissions(
        db: AsyncSession,
        *,
        role_id: int,
        permission_ids: list[int],
    ) -> dict:
        role = (await db.execute(select(RBACRole).where(RBACRole.id == role_id))).scalar_one_or_none()
        if not role:
            raise NotFoundError("Role")

        deduped_permission_ids = sorted(set(permission_ids))
        if deduped_permission_ids:
            existing_permissions = (
                await db.execute(select(Permission.id).where(Permission.id.in_(deduped_permission_ids)))
            ).scalars().all()
            if len(existing_permissions) != len(deduped_permission_ids):
                raise BadRequestError("One or more permission IDs are invalid")

        await db.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
        for permission_id in deduped_permission_ids:
            db.add(RolePermission(role_id=role_id, permission_id=permission_id))
        await db.flush()

        return {
            "id": role.id,
            "name": role.name.api_value,
            "description": role.description,
            "created_at": role.created_at,
            "permission_ids": deduped_permission_ids,
        }

    @staticmethod
    async def get_matrix(db: AsyncSession) -> dict:
        roles = await RBACService.list_roles(db)
        permissions = await RBACService.list_permissions(db)

        matrix = []
        for role in roles:
            assigned = set(role["permission_ids"])
            matrix.append(
                {
                    "role_id": role["id"],
                    "role_name": role["name"],
                    "permissions": {
                        permission["code"]: permission["id"] in assigned
                        for permission in permissions
                    },
                }
            )

        return {
            "roles": roles,
            "permissions": permissions,
            "matrix": matrix,
        }

    @staticmethod
    async def get_user_roles(db: AsyncSession, *, user_id: str) -> dict:
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if not user:
            raise NotFoundError("User")

        role_rows = (
            await db.execute(
                select(RBACRole)
                .join(UserRole, UserRole.role_id == RBACRole.id)
                .where(UserRole.user_id == user_id)
                .order_by(RBACRole.id.asc())
            )
        ).scalars().all()

        # Hydrate permission_ids per role in one query (no N+1).
        role_ids = [role.id for role in role_rows]
        permission_map: dict[int, list[int]] = {rid: [] for rid in role_ids}
        if role_ids:
            rp_rows = (
                await db.execute(
                    select(RolePermission.role_id, RolePermission.permission_id)
                    .where(RolePermission.role_id.in_(role_ids))
                )
            ).all()
            for role_id, permission_id in rp_rows:
                permission_map[role_id].append(permission_id)

        return {
            "user_id": user.id,
            "active_role": user.active_role.api_value,
            "roles": [
                {
                    "id": role.id,
                    "name": role.name.api_value,
                    "description": role.description,
                    "created_at": role.created_at,
                    "permission_ids": sorted(permission_map.get(role.id, [])),
                }
                for role in role_rows
            ],
        }

    @staticmethod
    async def get_my_permission_codes(db: AsyncSession, *, user_id: str) -> list[str]:
        """
        Return the distinct, sorted list of permission codes the user holds
        across all their assigned roles.

        Backed by `public.user_permissions_view` (DB v2.1 §12) which pre-joins
        users → user_roles → roles → role_permissions → permissions.
        One round-trip, no N+1.

        The view already filters soft-deleted users.
        """
        rows = await db.execute(
            text(
                """
                SELECT DISTINCT permission_code
                FROM   public.user_permissions_view
                WHERE  user_id = :user_id
                ORDER  BY permission_code
                """
            ).bindparams(user_id=user_id)
        )
        return [row[0] for row in rows.all()]

    @staticmethod
    async def set_user_roles(
        db: AsyncSession,
        *,
        user_id: str,
        role_ids: list[int],
        assigned_by: str,
    ) -> dict:
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if not user:
            raise NotFoundError("User")

        deduped_role_ids = sorted(set(role_ids))
        if not deduped_role_ids:
            raise BadRequestError("At least one role must be assigned")

        roles = (
            await db.execute(select(RBACRole).where(RBACRole.id.in_(deduped_role_ids)).order_by(RBACRole.id.asc()))
        ).scalars().all()
        if len(roles) != len(deduped_role_ids):
            raise BadRequestError("One or more role IDs are invalid")

        await db.execute(delete(UserRole).where(UserRole.user_id == user_id))
        for role in roles:
            db.add(UserRole(user_id=user_id, role_id=role.id, assigned_by=assigned_by))

        role_values = [role.name.value for role in roles]
        if user.active_role.value not in role_values:
            user.active_role = Role(role_values[0])

        await db.flush()

        return {
            "user_id": user.id,
            "active_role": user.active_role.api_value,
            "roles": [
                {
                    "id": role.id,
                    "name": role.name.api_value,
                    "description": role.description,
                    "created_at": role.created_at,
                }
                for role in roles
            ],
        }
