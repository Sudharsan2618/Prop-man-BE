"""
LuxeLife API — User schemas.

Request/response models for user endpoints.
"""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserResponse(BaseModel):
    """Public user profile returned by API."""

    id: str
    name: str
    email: str
    phone: str | None = None
    initials: str
    avatar: str | None = None
    location: str | None = None
    roles: list[str]
    active_role: str
    status: str
    kyc_progress: int
    onboarding_status: str
    must_reset_password: bool
    invited_by: str | None = None
    invited_at: datetime | None = None
    enrolled_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class UserUpdateRequest(BaseModel):
    """Fields a user can update on their own profile."""

    name: str | None = Field(None, min_length=2, max_length=100)
    location: str | None = Field(None, max_length=255)
    avatar: str | None = Field(None, max_length=512)
    fcm_token: str | None = Field(None, max_length=512)

    # Provider-specific
    specialization: str | None = Field(None, max_length=255)


class InviteOwnerRequest(BaseModel):
    """Admin request payload to invite a new owner user."""

    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr


class SwitchRoleRequest(BaseModel):
    """Switch the user's active role."""

    role: str = Field(
        ...,
        pattern=r"^(tenant|owner|service_provider|provider|manager|admin|super_admin)$",
        description="Role to switch to (must be in user's roles list)",
    )


class AdminCreateUserRequest(BaseModel):
    """Super admin payload to create a user with explicit role assignment."""

    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    phone: str | None = Field(None, pattern=r"^\+\d{10,15}$")
    password: str | None = Field(None, min_length=8, max_length=128)
    active_role: str = Field(
        default="tenant",
        pattern=r"^(tenant|owner|service_provider|provider|manager|admin|super_admin)$",
    )
    roles: list[str] | None = Field(
        default=None,
        description="If omitted, [active_role] will be used",
    )
    status: str = Field(
        default="verified",
        pattern=r"^(pending|awaiting_review|verified|suspended)$",
    )
    property_ids: list[str] | None = Field(
        default=None,
        description="Property IDs to assign when creating a manager",
    )


class UserListParams(BaseModel):
    """Query parameters for admin user listing."""

    page: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=100)
    role: str | None = None
    status: str | None = None
    search: str | None = None
    sort: str = "-created_at"


def user_to_response(user) -> dict:
    """
    Convert a User ORM object to a response dict.

    Handles enum serialization and optional role-specific fields.
    """
    return UserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        phone=user.phone,
        initials=user.initials,
        avatar=user.avatar,
        location=user.location,
        roles=[user.active_role.api_value],
        active_role=user.active_role.api_value,
        status=user.status.api_value,
        kyc_progress=user.kyc_progress,
        onboarding_status=user.onboarding_status.api_value,
        must_reset_password=user.must_reset_password,
        invited_by=user.invited_by,
        invited_at=user.invited_at,
        enrolled_at=user.enrolled_at,
        created_at=user.created_at,
    ).model_dump()
