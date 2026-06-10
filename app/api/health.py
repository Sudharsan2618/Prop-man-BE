"""
LuxeLife API — Health check endpoints.

Used by load balancers and monitoring tools to verify the service is running.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.redis import redis_client

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check():
    """
    Lightweight health check for constant polling.
    Does NOT hit the database or Redis to preserve Free Tier quotas.
    """
    return {
        "status": "healthy",
        "version": "1.0.0",
        "note": "DB checks disabled for quota reasons",
    }


@router.get("/health/ready")
async def readiness():
    """Simple readiness probe (for K8s / ECS)."""
    return {"status": "ready"}
