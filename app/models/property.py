"""
LuxeLife API — Property model.

Represents a residential property listed on the platform.
Supports different property types, furnishing levels, and occupancy states.
Amenities stored as JSONB for flexible querying.
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
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base, TimestampMixin, generate_cuid


class PropertyType(str, enum.Enum):
    APARTMENT = "apartment"
    VILLA = "villa"
    INDEPENDENT_HOUSE = "independent_house"
    PENTHOUSE = "penthouse"


class Furnishing(str, enum.Enum):
    FULLY_FURNISHED = "fully_furnished"
    SEMI_FURNISHED = "semi_furnished"
    UNFURNISHED = "unfurnished"


class Occupancy(str, enum.Enum):
    OCCUPIED = "occupied"
    VACANT = "vacant"


class PropertyStatus(str, enum.Enum):
    """
    Listing lifecycle. DRAFT = work-in-progress (incomplete fields allowed),
    ACTIVE = published & visible, ARCHIVED = retired.

    DB enum `property_status_enum` stores member names (DRAFT/ACTIVE/ARCHIVED);
    the lowercase `.value` is the API representation.
    """
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class PropertyManagerRole(str, enum.Enum):
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"


class Property(Base, TimestampMixin):
    """A residential property listed on the LuxeLife platform."""

    __tablename__ = "properties"

    # ── Identity ──
    id: Mapped[str] = mapped_column(
        String(30), primary_key=True, default=generate_cuid
    )
    name: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(50))

    # ── Location ──
    address: Mapped[str] = mapped_column(String(500))
    city: Mapped[str] = mapped_column(String(100), index=True)
    state: Mapped[str] = mapped_column(String(100))
    pincode: Mapped[str] = mapped_column(String(10))

    # ── Details ──
    type: Mapped[PropertyType] = mapped_column(
        Enum(PropertyType, name="property_type_enum", create_constraint=True)
    )
    bhk: Mapped[str] = mapped_column(String(20))
    # Spec/financial fields are nullable: a DRAFT listing fills these in on
    # later wizard steps, so they are NULL until the property reaches ACTIVE.
    sqft: Mapped[int | None] = mapped_column(Integer, nullable=True)
    furnishing: Mapped[Furnishing] = mapped_column(
        Enum(Furnishing, name="furnishing_enum", create_constraint=True)
    )
    floor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_floors: Mapped[int | None] = mapped_column(Integer, nullable=True)
    facing: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ── Financials (stored in INR) — nullable for DRAFT listings ──
    rent: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    security_deposit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    maintenance_charges: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Description & Media ──
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    images: Mapped[list] = mapped_column(ARRAY(String), default=list)

    # ── Status ──
    occupancy: Mapped[Occupancy] = mapped_column(
        Enum(Occupancy, name="occupancy_enum", create_constraint=True),
        default=Occupancy.VACANT,
        index=True,
    )
    premium: Mapped[bool] = mapped_column(Boolean, default=False)

    # ── Listing lifecycle (DRAFT → ACTIVE → ARCHIVED) ──
    status: Mapped[PropertyStatus] = mapped_column(
        Enum(PropertyStatus, name="property_status_enum", create_constraint=True),
        default=PropertyStatus.DRAFT,
        nullable=False,
        index=True,
    )
    onboarding_step: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Rent range from the listing wizard. For SA single-rent flow these mirror `rent`.
    min_rent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_rent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    final_rent: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Amenities (JSONB for flexible storage) ──
    amenities: Mapped[list] = mapped_column(JSONB, default=list)

    # ── Lease Info ──
    lease_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Foreign Keys ──
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # ── Relationships ──
    owner = relationship(
        "User", foreign_keys=[owner_id], backref="owned_properties"
    )
    tenant = relationship(
        "User", foreign_keys=[tenant_id], backref="tenant_properties"
    )
    creator = relationship(
        "User", foreign_keys=[created_by]
    )
    property_managers = relationship(
        "PropertyManager", back_populates="property", cascade="all, delete-orphan"
    )
    occupancy_history = relationship(
        "PropertyOccupancyHistory", back_populates="property", cascade="all, delete-orphan"
    )
    payments = relationship("Payment", back_populates="property")
    jobs = relationship("Job", back_populates="property")

    def __repr__(self) -> str:
        return f"<Property id={self.id} name={self.name} city={self.city}>"


class PropertyManager(Base):
    """M:N bridge between properties and manager users."""

    __tablename__ = "property_managers"

    property_id: Mapped[str] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), primary_key=True
    )
    manager_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[PropertyManagerRole] = mapped_column(
        Enum(PropertyManagerRole, name="property_manager_role_enum", create_constraint=True),
        default=PropertyManagerRole.PRIMARY,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    assigned_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    property = relationship("Property", back_populates="property_managers")
    manager = relationship("User", foreign_keys=[manager_id])

    def __repr__(self) -> str:
        return f"<PropertyManager property={self.property_id} manager={self.manager_id} role={self.role.value}>"


class PropertyOccupancyHistory(Base):
    """Tracks occupancy timeline for a property."""

    __tablename__ = "property_occupancy_history"

    id: Mapped[str] = mapped_column(
        String(30), primary_key=True, default=generate_cuid
    )
    property_id: Mapped[str] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[Occupancy] = mapped_column(
        Enum(Occupancy, name="occupancy_enum", create_constraint=True),
        nullable=False,
    )
    start_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    end_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    agreement_id: Mapped[str | None] = mapped_column(
        ForeignKey("agreements.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )

    property = relationship("Property", back_populates="occupancy_history")
    tenant = relationship("User", foreign_keys=[tenant_id])

    def __repr__(self) -> str:
        return f"<PropertyOccupancyHistory id={self.id} property={self.property_id} status={self.status.value}>"
