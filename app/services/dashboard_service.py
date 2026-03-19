"""LuxeLife API — Aggregated dashboard service."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job, JobStatus
from app.models.notification import Notification
from app.models.onboarding_workflow import OnboardingWorkflowState, PropertyOnboardingWorkflow
from app.models.payment import Payment, PaymentStatus, PaymentType
from app.models.property import Property
from app.models.user import User
from app.schemas.job import job_to_response
from app.schemas.property import property_to_response
from app.services.onboarding_workflow_service import OnboardingWorkflowService


class DashboardService:
    """Builds role-specific dashboard payloads in one backend call."""

    @staticmethod
    async def get_admin_dashboard(db: AsyncSession, *, admin_id: str) -> dict:
        user_count = (await db.execute(select(func.count(User.id)))).scalar() or 0
        property_count = (await db.execute(select(func.count(Property.id)))).scalar() or 0
        pending_actions_count = (
            await db.execute(
                select(func.count(PropertyOnboardingWorkflow.id)).where(
                    PropertyOnboardingWorkflow.state != OnboardingWorkflowState.TENANT_ACTIVATED
                )
            )
        ).scalar() or 0

        pending_statuses = [
            PaymentStatus.PENDING,
            PaymentStatus.OVERDUE,
            PaymentStatus.AWAITING_VERIFICATION,
        ]
        pending_invoice_count = (
            await db.execute(
                select(func.count(Payment.id)).where(Payment.status.in_(pending_statuses))
            )
        ).scalar() or 0
        pending_invoices_inr = (
            await db.execute(
                select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status.in_(pending_statuses))
            )
        ).scalar() or 0

        settled_amount_inr = (
            await db.execute(
                select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == PaymentStatus.PAID)
            )
        ).scalar() or 0

        notifications_query = (
            select(Notification)
            .where(Notification.user_id == admin_id)
            .order_by(Notification.created_at.desc())
            .limit(5)
        )
        notifications = (await db.execute(notifications_query)).scalars().all()

        # Keep financial amount scale compatible with existing UI rendering (/100 display).
        financials = {
            "escrowedFunds": int(settled_amount_inr) * 100,
            "escrowedTrend": "0%",
            "pendingInvoices": int(pending_invoices_inr) * 100,
            "pendingInvoiceCount": pending_invoice_count,
            "pendingInvoiceTrend": "0%",
            "rentSplits": [],
        }

        recent_activity = [
            {
                "id": n.id,
                "icon": n.icon or "notifications",
                "iconBg": "rgba(19,200,236,0.15)" if n.type == "payment" else "rgba(212,168,67,0.15)",
                "iconColor": "#13C8EC" if n.type == "payment" else "#D4A843",
                "title": n.title or n.type or "Activity",
                "subtitle": n.body or "",
                "badge": n.type or "",
                "timestamp": _format_time_ago(n.created_at),
            }
            for n in notifications
        ]

        return {
            "stats": {
                "user_count": user_count,
                "property_count": property_count,
                "pending_actions_count": pending_actions_count,
            },
            "financials": financials,
            "recent_activity": recent_activity,
        }

    @staticmethod
    async def get_owner_dashboard(db: AsyncSession, *, owner_id: str) -> dict:
        properties_query = (
            select(Property)
            .where(Property.owner_id == owner_id)
            .order_by(Property.created_at.desc())
        )
        properties = (await db.execute(properties_query)).scalars().all()

        workflows = await OnboardingWorkflowService.list_workflows(db, owner_id=owner_id)

        payment_rows = (
            await db.execute(
                select(Payment.amount, Payment.created_at)
                .where(
                    Payment.owner_id == owner_id,
                    Payment.status == PaymentStatus.PAID,
                    Payment.type == PaymentType.RENT,
                )
            )
        ).all()

        total_revenue = int(sum(row.amount or 0 for row in payment_rows))
        commission_rate = 10
        commission = int(total_revenue * commission_rate / 100)
        net_payout = total_revenue - commission
        tds_rate = 10
        tds_deducted = int(net_payout * tds_rate / 100)

        monthly_totals: dict[str, int] = {}
        for row in payment_rows:
            month_key = row.created_at.strftime("%b") if row.created_at else "N/A"
            monthly_totals[month_key] = monthly_totals.get(month_key, 0) + int(row.amount or 0)

        monthly_trend = [
            {"month": month, "gross": amount}
            for month, amount in list(monthly_totals.items())[-6:]
        ]

        return {
            "properties": [property_to_response(p) for p in properties],
            "workflows": workflows,
            "earnings": {
                "total_revenue": total_revenue,
                "commission": commission,
                "commission_rate": commission_rate,
                "net_payout": net_payout,
                "tds_deducted": tds_deducted,
                "tds_fy_total": tds_deducted,
                "tds_rate": tds_rate,
                "monthly_trend": monthly_trend,
            },
        }

    @staticmethod
    async def get_provider_dashboard(db: AsyncSession, *, provider_id: str) -> dict:
        jobs_query = (
            select(Job)
            .where(Job.provider_id == provider_id)
            .order_by(Job.created_at.desc())
            .limit(30)
        )
        jobs = (await db.execute(jobs_query)).scalars().all()

        active_jobs = [j for j in jobs if j.status == JobStatus.ACTIVE]
        scheduled_jobs = [j for j in jobs if j.status == JobStatus.SCHEDULED]
        completed_jobs = [j for j in jobs if j.status == JobStatus.COMPLETED]

        total_completed_amount = int(sum((j.actual_cost or 0) for j in completed_jobs))

        stats = {
            "activeJobs": len(active_jobs),
            "scheduledJobs": len(scheduled_jobs),
            "completedJobs": len(completed_jobs),
            "nextPayoutAmount": total_completed_amount * 0.1,
            "nextPayoutDate": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
            "weeklyTarget": 75,
            "earningsThisWeek": total_completed_amount * 0.3,
            "earningsThisMonth": total_completed_amount * 0.6,
            "earningsLifetime": total_completed_amount,
            "weeklyBreakdown": [
                {"day": "Mon", "amount": 0},
                {"day": "Tue", "amount": 0},
                {"day": "Wed", "amount": 0},
                {"day": "Thu", "amount": 0},
                {"day": "Fri", "amount": 0},
                {"day": "Sat", "amount": 0},
                {"day": "Sun", "amount": 0},
            ],
        }

        return {
            "jobs": [job_to_response(job) for job in jobs],
            "stats": stats,
        }


def _format_time_ago(value: datetime | None) -> str:
    if not value:
        return ""
    now = datetime.now(UTC)
    source = value if value.tzinfo else value.replace(tzinfo=UTC)
    diff = int((now - source).total_seconds())
    if diff < 60:
        return "just now"
    if diff < 3600:
        return f"{diff // 60}m ago"
    if diff < 86400:
        return f"{diff // 3600}h ago"
    return f"{diff // 86400}d ago"
