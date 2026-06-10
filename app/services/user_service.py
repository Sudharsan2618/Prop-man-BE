"""
LuxeLife API — User service.

Business logic for user profile management, role switching, and admin user listing.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.security import generate_temporary_password, hash_password
from app.models import generate_cuid
from app.models.rbac import RBACRole, UserRole
from app.models.user import OnboardingStatus, Role, User, UserStatus
from app.schemas.user import user_to_response


class UserService:
    """Handles user profile operations."""

    @staticmethod
    async def _get_user_role_values(db: AsyncSession, user_id: str) -> list[str]:
        role_rows = (
            await db.execute(
                select(RBACRole.name)
                .join(UserRole, UserRole.role_id == RBACRole.id)
                .where(UserRole.user_id == user_id)
            )
        ).scalars().all()
        return [role.value for role in role_rows]

    @staticmethod
    async def get_by_id(db: AsyncSession, user_id: str) -> User:
        """Fetch a user by ID. Raises NotFoundError if not found."""
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise NotFoundError("User")
        return user

    @staticmethod
    async def update_profile(
        db: AsyncSession,
        user: User,
        *,
        name: str | None = None,
        location: str | None = None,
        avatar: str | None = None,
        fcm_token: str | None = None,
    ) -> dict:
        """
        Update the current user's profile.

        Only non-None fields are updated (partial update).
        """
        if name is not None:
            user.name = name
            user.initials = "".join(
                word[0].upper() for word in name.split()[:2]
            ) or name[:2].upper()

        if location is not None:
            user.location = location

        if avatar is not None:
            user.avatar = avatar

        if fcm_token is not None:
            user.fcm_token = fcm_token

        await db.flush()
        return user_to_response(user)

    @staticmethod
    async def switch_role(
        db: AsyncSession,
        user: User,
        *,
        new_role: str,
    ) -> dict:
        """
        Switch the user's active role.

        The new role must be assigned in user_roles.
        """
        parsed_role = Role.from_api(new_role)
        assigned_roles = await UserService._get_user_role_values(db, user.id)
        if parsed_role.value not in assigned_roles:
            raise BadRequestError(
                f"You don't have the '{parsed_role.api_value}' role. "
                f"Your roles are: {', '.join(r.lower() for r in assigned_roles)}"
            )

        user.active_role = parsed_role
        await db.flush()
        return user_to_response(user)

    @staticmethod
    async def list_users(
        db: AsyncSession,
        *,
        page: int = 1,
        limit: int = 20,
        role: str | None = None,
        status: str | None = None,
        search: str | None = None,
        sort: str = "-created_at",
    ) -> tuple[list[dict], int]:
        """
        List users with filtering, search, and pagination. Admin only.

        Returns (items, total_count).
        """
        query = select(User)

        # Filters
        if role:
            query = query.where(User.active_role == Role.from_api(role))
        if status:
            query = query.where(User.status == UserStatus.from_api(status))
        if search:
            pattern = f"%{search}%"
            query = query.where(
                User.name.ilike(pattern)
                | User.email.ilike(pattern)
                | User.phone.ilike(pattern)
            )

        # Count total (before pagination)
        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        # Sorting
        if sort.startswith("-"):
            col = getattr(User, sort[1:], User.created_at)
            query = query.order_by(col.desc())
        else:
            col = getattr(User, sort, User.created_at)
            query = query.order_by(col.asc())

        # Pagination
        query = query.offset((page - 1) * limit).limit(limit)

        result = await db.execute(query)
        users = result.scalars().all()

        return [user_to_response(u) for u in users], total

    @staticmethod
    async def update_status(
        db: AsyncSession,
        user_id: str,
        *,
        new_status: str,
    ) -> dict:
        """Admin: update a user's account status (approve, suspend, etc.)."""
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise NotFoundError("User")

        user.status = UserStatus.from_api(new_status)
        await db.flush()
        return user_to_response(user)

    @staticmethod
    async def invite_owner(
        db: AsyncSession,
        *,
        admin_id: str,
        name: str,
        email: str,
    ) -> dict:
        """Admin invite flow for NRI owners with temporary password."""
        existing = await db.execute(select(User).where(User.email == email))
        if existing.scalar_one_or_none():
            raise ConflictError("A user with this email already exists")

        temp_password = generate_temporary_password()
        initials = "".join(word[0].upper() for word in name.split()[:2]) or name[:2].upper()

        user = User(
            id=generate_cuid(),
            name=name,
            email=email,
            phone=None,
            password_hash=hash_password(temp_password),
            initials=initials,
            active_role=Role.OWNER,
            status=UserStatus.VERIFIED,
            onboarding_status=OnboardingStatus.CREATED,
            must_reset_password=True,
            invited_by=admin_id,
            invited_at=datetime.now(timezone.utc),
            enrolled_at=None,
        )
        db.add(user)
        await db.flush()

        owner_role = (
            await db.execute(select(RBACRole).where(RBACRole.name == Role.OWNER))
        ).scalar_one_or_none()
        if not owner_role:
            raise NotFoundError("Role OWNER")

        db.add(UserRole(user_id=user.id, role_id=owner_role.id, assigned_by=admin_id))
        await db.flush()

        payload = user_to_response(user)
        payload["temporary_password"] = temp_password
        return payload

    @staticmethod
    async def create_user(
        db: AsyncSession,
        *,
        creator_id: str,
        name: str,
        email: str,
        phone: str | None,
        password: str | None,
        active_role: str,
        roles: list[str] | None,
        status: str,
    ) -> dict:
        """Super-admin operation to create a user with explicit role assignment."""
        existing_email = await db.execute(select(User).where(User.email == email))
        if existing_email.scalar_one_or_none():
            raise ConflictError("A user with this email already exists")

        if phone:
            existing_phone = await db.execute(select(User).where(User.phone == phone))
            if existing_phone.scalar_one_or_none():
                raise ConflictError("A user with this phone already exists")

        assigned_roles = roles or [active_role]
        if not assigned_roles:
            raise BadRequestError("At least one role must be assigned")

        normalized_roles: list[Role] = []
        for role_value in assigned_roles:
            try:
                parsed_role = Role.from_api(role_value)
            except ValueError as exc:
                raise BadRequestError(f"Unsupported role: {role_value}") from exc
            normalized_roles.append(parsed_role)

        active_role_enum = Role.from_api(active_role)
        normalized_role_values = [role_item.value for role_item in normalized_roles]
        if active_role_enum.value not in normalized_role_values:
            raise BadRequestError("active_role must exist in roles list")

        temp_password = None
        final_password = password
        if not final_password:
            temp_password = generate_temporary_password()
            final_password = temp_password

        initials = "".join(word[0].upper() for word in name.split()[:2]) or name[:2].upper()

        user = User(
            id=generate_cuid(),
            name=name,
            email=email,
            phone=phone,
            password_hash=hash_password(final_password),
            initials=initials,
            active_role=active_role_enum,
            status=UserStatus.from_api(status),
            onboarding_status=OnboardingStatus.ENROLLED,
            must_reset_password=temp_password is not None,
            invited_by=creator_id,
            invited_at=datetime.now(timezone.utc),
        )
        db.add(user)
        await db.flush()

        role_rows = (
            await db.execute(select(RBACRole).where(RBACRole.name.in_(normalized_roles)))
        ).scalars().all()
        if len(role_rows) != len(set(normalized_roles)):
            raise BadRequestError("One or more roles are not present in RBAC roles table")

        for role_row in role_rows:
            db.add(UserRole(user_id=user.id, role_id=role_row.id, assigned_by=creator_id))

        await db.flush()

        payload = user_to_response(user)
        if temp_password:
            payload["temporary_password"] = temp_password
        return payload
