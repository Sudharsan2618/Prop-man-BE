"""
LuxeLife API — Shared FastAPI dependencies.

Contains the get_current_user dependency that extracts and validates
the JWT token from the Authorization header.
"""

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import DetachedInstanceError
import structlog
from time import monotonic

from app.core.exceptions import UnauthorizedError
from app.core.security import decode_access_token
from app.database import get_db
from app.models.user import User, UserStatus
from app.redis import redis_client

# Attributes the auth path and downstream handlers read off the cached User.
# We force-load these before caching so the detached instance can answer them
# from __dict__ even after its loading session is gone.
_CACHED_USER_ATTRS = (
    "id", "email", "phone", "password_hash", "name", "initials", "avatar",
    "location", "active_role", "status", "kyc_progress", "onboarding_status",
    "must_reset_password", "invited_by", "invited_at", "enrolled_at",
    "fcm_token", "last_login_at", "created_at", "updated_at",
)


def _user_cached_attrs_intact(user: User) -> bool:
    """Quick liveness check — does reading user.status raise DetachedInstanceError?"""
    try:
        _ = user.status
        return True
    except (DetachedInstanceError, InvalidRequestError):
        return False

# HTTP Bearer scheme — extracts token from "Authorization: Bearer <token>"
bearer_scheme = HTTPBearer(auto_error=False)
logger = structlog.get_logger()
AUTH_USER_CACHE_TTL_SECONDS = 30
AUTH_USER_CACHE_MAX_ITEMS = 1000
_auth_user_cache: dict[str, tuple[float, User]] = {}


def _read_cached_user(user_id: str) -> User | None:
    cached = _auth_user_cache.get(user_id)
    if not cached:
        return None

    expires_at, user = cached
    if expires_at <= monotonic():
        _auth_user_cache.pop(user_id, None)
        return None
    return user


def _write_cached_user(user: User) -> None:
    # Force-materialize every attribute the auth path will read after the
    # loading session is gone. Without this, the cached User can raise
    # DetachedInstanceError on attribute access in a later request.
    for attr in _CACHED_USER_ATTRS:
        try:
            getattr(user, attr)
        except Exception:
            # If we can't load it now, skip — we'd rather cache a partial
            # snapshot than crash the request that's writing the cache.
            pass

    if len(_auth_user_cache) >= AUTH_USER_CACHE_MAX_ITEMS:
        now = monotonic()
        expired_keys = [k for k, (exp, _) in _auth_user_cache.items() if exp <= now]
        for key in expired_keys:
            _auth_user_cache.pop(key, None)

        if len(_auth_user_cache) >= AUTH_USER_CACHE_MAX_ITEMS:
            _auth_user_cache.pop(next(iter(_auth_user_cache)))

    _auth_user_cache[user.id] = (monotonic() + AUTH_USER_CACHE_TTL_SECONDS, user)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Dependency that authenticates the current request.

    1. Extracts the Bearer token from the Authorization header.
    2. Decodes the JWT and validates its signature + expiry.
    3. Checks if the token has been blacklisted (logout).
    4. Loads the user from the database.
    5. Returns the User ORM object.

    Raises UnauthorizedError if any step fails.
    """
    if credentials is None:
        raise UnauthorizedError("Missing authorization header")

    token = credentials.credentials

    # Decode JWT
    try:
        payload = decode_access_token(token)
    except JWTError:
        raise UnauthorizedError("Invalid or expired token")

    user_id: str | None = payload.get("sub")
    jti: str | None = payload.get("jti")

    if not user_id:
        raise UnauthorizedError("Invalid token payload")

    # Check if token is blacklisted (user logged out)
    if jti:
        try:
            is_blacklisted = await redis_client.get(f"blacklist:{jti}")
            if is_blacklisted:
                raise UnauthorizedError("Token has been revoked")
        except UnauthorizedError:
            raise
        except Exception as e:
            # Redis is optional in degraded mode; do not fail authenticated requests.
            logger.warning("Redis blacklist check skipped", error=str(e))

    use_cached_user = request.method in {"GET", "HEAD", "OPTIONS"}
    user = _read_cached_user(user_id) if use_cached_user else None

    # If the cached user is detached and its attrs would raise
    # DetachedInstanceError, drop it and re-fetch from the DB.
    if user is not None and not _user_cached_attrs_intact(user):
        _auth_user_cache.pop(user_id, None)
        user = None

    # Load user from DB if cache miss (or non-read request)
    if user is None:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

    if user is None:
        raise UnauthorizedError("User not found")

    if user.status == UserStatus.SUSPENDED:
        raise UnauthorizedError("Account has been suspended")

    if user.must_reset_password:
        allowed_paths = {
            "/api/v1/auth/set-password-first-login",
            "/api/v1/auth/logout",
        }
        if request.url.path not in allowed_paths:
            raise UnauthorizedError(
                "Password reset required. Complete first-login password reset to continue."
            )

    if use_cached_user:
        _write_cached_user(user)

    return user
