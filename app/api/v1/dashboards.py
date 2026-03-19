"""LuxeLife API — Aggregated role dashboard routes."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_roles
from app.core.responses import success_response
from app.database import get_db
from app.models.user import User
from app.services.dashboard_service import DashboardService

router = APIRouter(prefix="/dashboard", tags=["Dashboards"])


@router.get("/admin")
async def get_admin_dashboard(
    admin: User = Depends(require_roles("admin")),
    db: AsyncSession = Depends(get_db),
):
    payload = await DashboardService.get_admin_dashboard(db, admin_id=admin.id)
    return success_response(payload)


@router.get("/owner")
async def get_owner_dashboard(
    owner: User = Depends(require_roles("owner")),
    db: AsyncSession = Depends(get_db),
):
    payload = await DashboardService.get_owner_dashboard(db, owner_id=owner.id)
    return success_response(payload)


@router.get("/provider")
async def get_provider_dashboard(
    provider: User = Depends(require_roles("provider")),
    db: AsyncSession = Depends(get_db),
):
    payload = await DashboardService.get_provider_dashboard(db, provider_id=provider.id)
    return success_response(payload)
