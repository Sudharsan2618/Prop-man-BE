"""
LuxeLife API — Property scope helpers.

Single source of truth for resolving:
  - Which properties a manager has been assigned to (PRIMARY or SECONDARY).
  - Whether a specific user is a manager of a given property.
  - The notification stakeholder set for a property: owner + assigned
    managers, with super-admin fallback when no managers are assigned.

Every service that needs to scope by manager assignment or fan notifications
out to "the right people" for a property should call these — do not run ad-hoc
queries against ``property_managers`` or ``User.active_role == MANAGER``.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.property import Property, PropertyManager
from app.models.user import Role, User


async def get_managed_property_ids(db: AsyncSession, manager_id: str) -> list[str]:
    """Property IDs the given user is assigned to manage."""
    result = await db.execute(
        select(PropertyManager.property_id).where(PropertyManager.manager_id == manager_id)
    )
    return [row[0] for row in result.all()]


async def is_property_manager(db: AsyncSession, property_id: str, user_id: str) -> bool:
    """Whether the user is an assigned manager of the property."""
    result = await db.execute(
        select(PropertyManager.manager_id)
        .where(PropertyManager.property_id == property_id)
        .where(PropertyManager.manager_id == user_id)
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def get_assigned_manager_ids(db: AsyncSession, property_id: str) -> list[str]:
    """Manager IDs currently assigned to the property (any role)."""
    result = await db.execute(
        select(PropertyManager.manager_id).where(PropertyManager.property_id == property_id)
    )
    return [row[0] for row in result.all()]


async def get_super_admin_ids(db: AsyncSession) -> list[str]:
    """All super-admin user IDs (used as fallback recipients)."""
    result = await db.execute(select(User.id).where(User.active_role == Role.SUPER_ADMIN))
    return [row[0] for row in result.all()]


async def get_property_stakeholders(
    db: AsyncSession,
    property_id: str,
    *,
    include_owner: bool = True,
    include_super_admin_fallback: bool = True,
) -> dict:
    """
    Resolve the notification recipient set for a property.

    Returns a dict::

        {
            "owner_id": str | None,
            "manager_ids": list[str],          # assigned managers (may be empty)
            "fallback_ids": list[str],         # super-admins, populated only when
                                               # manager_ids is empty and the
                                               # caller opted into fallback
        }

    Use ``recipient_ids = {owner_id} ∪ manager_ids ∪ fallback_ids`` and drop the
    actor's own id so they don't notify themselves.
    """
    prop = await db.get(Property, property_id)
    owner_id = prop.owner_id if (prop and include_owner) else None

    manager_ids = await get_assigned_manager_ids(db, property_id)
    fallback_ids: list[str] = []
    if include_super_admin_fallback and not manager_ids:
        fallback_ids = await get_super_admin_ids(db)

    return {"owner_id": owner_id, "manager_ids": manager_ids, "fallback_ids": fallback_ids}
