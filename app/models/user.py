"""
LuxeLife API — User model.

Supports multi-role users (a single person can be both a tenant and an owner).
The active_role field tracks which role they are currently operating as.
"""

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base, TimestampMixin, generate_cuid


class Role(str, enum.Enum):
    """User roles in the system."""
    SUPER_ADMIN = "SUPER_ADMIN"
    MANAGER = "MANAGER"
    OWNER = "OWNER"
    TENANT = "TENANT"
    SERVICE_PROVIDER = "SERVICE_PROVIDER"

    # Backward-compatible aliases for legacy code paths.
    PROVIDER = "SERVICE_PROVIDER"
    ADMIN = "MANAGER"

    @classmethod
    def from_api(cls, value: str) -> "Role":
        normalized = value.strip().replace("-", "_").upper()
        if normalized == "PROVIDER":
            normalized = "SERVICE_PROVIDER"
        if normalized == "ADMIN":
            normalized = "MANAGER"
        return cls(normalized)

    @property
    def api_value(self) -> str:
        return self.value.lower()


class UserStatus(str, enum.Enum):
    """KYC / account verification status."""
    PENDING = "PENDING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    VERIFIED = "VERIFIED"
    SUSPENDED = "SUSPENDED"

    @classmethod
    def from_api(cls, value: str) -> "UserStatus":
        return cls(value.strip().upper())

    @property
    def api_value(self) -> str:
        return self.value.lower()


class OnboardingStatus(str, enum.Enum):
    """Owner/NRI onboarding lifecycle status."""
    CREATED = "CREATED"
    ENROLLED = "ENROLLED"

    @classmethod
    def from_api(cls, value: str) -> "OnboardingStatus":
        return cls(value.strip().upper())

    @property
    def api_value(self) -> str:
        return self.value.lower()


class User(Base, TimestampMixin):
    """Core user entity — shared across all roles."""

    __tablename__ = "users"

    # ── Identity ──
    id: Mapped[str] = mapped_column(
        String(30), primary_key=True, default=generate_cuid
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, index=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(100))
    initials: Mapped[str] = mapped_column(String(4))
    avatar: Mapped[str | None] = mapped_column(String(512), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Role & Status ──
    active_role: Mapped[Role] = mapped_column(
        Enum(Role, name="role_enum", create_constraint=True),
        default=Role.TENANT,
    )
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, name="user_status_enum", create_constraint=True),
        default=UserStatus.PENDING,
        index=True,
    )
    kyc_progress: Mapped[int] = mapped_column(Integer, default=0)

    # ── Owner/NRI onboarding ──
    onboarding_status: Mapped[OnboardingStatus] = mapped_column(
        Enum(OnboardingStatus, name="onboarding_status_enum", create_constraint=True),
        default=OnboardingStatus.ENROLLED,
        index=True,
    )
    must_reset_password: Mapped[bool] = mapped_column(Boolean, default=False)
    invited_by: Mapped[str | None] = mapped_column(
        "invited_by_admin_id",
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Device & Auth ──
    fcm_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Relationships (defined in future models) ──
    # owned_properties, tenant_properties, kyc_documents, bank_accounts,
    # notifications, etc. will be added as those models are created.

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email} role={self.active_role.value}>"
