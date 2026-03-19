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
from app.models.user import OnboardingStatus, Role, User, UserStatus
from app.schemas.user import user_to_response


class UserService:
    """Handles user profile operations."""

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
        specialization: str | None = None,
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

        if specialization is not None and user.active_role == Role.PROVIDER:
            user.specialization = specialization

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

        The new role must be in the user's roles list.
        """
        if new_role not in user.roles:
            raise BadRequestError(
                f"You don't have the '{new_role}' role. "
                f"Your roles are: {', '.join(user.roles)}"
            )

        user.active_role = Role(new_role)
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
            query = query.where(User.active_role == Role(role))
        if status:
            query = query.where(User.status == UserStatus(status))
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

        user.status = UserStatus(new_status)
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
            roles=[Role.OWNER.value],
            active_role=Role.OWNER,
            status=UserStatus.VERIFIED,
            onboarding_status=OnboardingStatus.CREATED,
            must_reset_password=True,
            invited_by_admin_id=admin_id,
            invited_at=datetime.now(timezone.utc),
            enrolled_at=None,
        )
        db.add(user)
        await db.flush()

        payload = user_to_response(user)
        payload["temporary_password"] = temp_password
        return payload
