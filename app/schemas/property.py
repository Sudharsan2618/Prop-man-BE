"""
LuxeLife API — Property schemas.

Request/response models for property endpoints.
"""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

# Fields a listing must have before it can be published (status=active).
_PUBLISH_REQUIRED = (
    "sqft", "floor", "total_floors", "rent", "security_deposit", "maintenance_charges",
)


class PropertyCreate(BaseModel):
    """
    Create a property listing.

    `status` controls completeness rules:
      - 'draft' (default): spec/financial fields are OPTIONAL — the wizard can
        save a partially-filled listing and finish it later.
      - 'active' (publish): all spec/financial fields are REQUIRED and positive;
        a model validator enforces this so you can never publish a half-listing.
    """

    name: str = Field(..., min_length=2, max_length=200, examples=["Serenity Heights"])
    unit: str = Field(..., max_length=50, examples=["Apt 4B"])
    address: str = Field(..., max_length=500, examples=["123 Marine Drive"])
    city: str = Field(..., max_length=100, examples=["Mumbai"])
    state: str = Field(..., max_length=100, examples=["Maharashtra"])
    pincode: str = Field(..., pattern=r"^\d{6}$", examples=["400001"])
    type: str = Field(..., pattern=r"^(apartment|villa|independent_house|penthouse)$")
    bhk: str = Field(..., max_length=20, examples=["3 BHK"])
    sqft: int | None = Field(None, gt=0, examples=[1200])
    furnishing: str = Field(
        ..., pattern=r"^(fully_furnished|semi_furnished|unfurnished)$"
    )
    floor: int | None = Field(None, ge=0, examples=[4])
    total_floors: int | None = Field(None, gt=0, examples=[12])
    facing: str | None = Field(None, max_length=50, examples=["East"])
    rent: int | None = Field(None, gt=0, description="Monthly rent in INR", examples=[35000])
    security_deposit: int | None = Field(None, ge=0, examples=[10500000])
    maintenance_charges: int | None = Field(None, ge=0, examples=[300000])
    description: str | None = Field(None, max_length=2000)
    premium: bool = False
    status: str = Field(
        default="draft",
        pattern=r"^(draft|active)$",
        description="'draft' saves incomplete; 'active' publishes (requires full data)",
    )
    amenities: list[str] = Field(default=[], examples=[["parking", "gym", "pool"]])
    images: list[str] = Field(default=[], description="GCS URLs for property images")
    owner_id: str | None = Field(None, description="Owner user ID (optional, assign later)")
    manager_ids: list[str] | None = Field(None, description="Manager user IDs (optional, assign later)")

    @model_validator(mode="after")
    def _require_full_data_when_publishing(self) -> "PropertyCreate":
        if self.status == "active":
            missing = [f for f in _PUBLISH_REQUIRED if getattr(self, f) is None]
            if missing:
                raise ValueError(
                    "Cannot publish: these fields are required → " + ", ".join(missing)
                )
        return self


class PropertyUpdate(BaseModel):
    """
    Partial update for a property — also the publish/unpublish path via `status`.

    Publishing a draft (status → 'active') requires the property to already hold
    all spec/financial fields; the service layer validates completeness against
    the persisted row so a draft can't be published while still incomplete.
    """

    name: str | None = Field(None, min_length=2, max_length=200)
    unit: str | None = Field(None, max_length=50)
    address: str | None = Field(None, max_length=500)
    city: str | None = Field(None, max_length=100)
    state: str | None = Field(None, max_length=100)
    pincode: str | None = Field(None, pattern=r"^\d{6}$")
    type: str | None = Field(None, pattern=r"^(apartment|villa|independent_house|penthouse)$")
    bhk: str | None = Field(None, max_length=20)
    sqft: int | None = Field(None, gt=0)
    floor: int | None = Field(None, ge=0)
    total_floors: int | None = Field(None, gt=0)
    facing: str | None = Field(None, max_length=50)
    rent: int | None = Field(None, gt=0)
    security_deposit: int | None = Field(None, ge=0)
    maintenance_charges: int | None = Field(None, ge=0)
    description: str | None = None
    furnishing: str | None = Field(None, pattern=r"^(fully_furnished|semi_furnished|unfurnished)$")
    amenities: list[str] | None = None
    images: list[str] | None = None
    premium: bool | None = None
    status: str | None = Field(None, pattern=r"^(draft|active|archived)$")
    occupancy: str | None = Field(None, pattern=r"^(occupied|vacant)$")
    tenant_id: str | None = None
    owner_id: str | None = None
    manager_ids: list[str] | None = None
    lease_start: datetime | None = None
    lease_end: datetime | None = None


class AssignManagersRequest(BaseModel):
    """Assign managers to a property."""
    manager_ids: list[str] = Field(..., min_length=1)
    role: str = Field(default="PRIMARY", pattern=r"^(PRIMARY|SECONDARY)$")


class AssignTenantRequest(BaseModel):
    """Assign a tenant to a property."""
    tenant_id: str
    lease_start: datetime | None = None
    lease_end: datetime | None = None
    agreement_id: str | None = None
    notes: str | None = None


class RemoveTenantRequest(BaseModel):
    """Remove tenant from a property."""
    notes: str | None = None


class PropertyResponse(BaseModel):
    """
    Property detail returned by API.

    Spec/financial fields (sqft, floor, total_floors, rent, security_deposit,
    maintenance_charges) are nullable: DRAFT listings created via the
    multi-step wizard fill these in on later steps, so an early-stage draft
    legitimately has them as NULL. They are guaranteed non-null only once the
    property reaches ACTIVE status.
    """

    id: str
    name: str
    unit: str
    address: str
    city: str
    state: str
    pincode: str
    type: str
    bhk: str
    sqft: int | None = None
    furnishing: str
    floor: int | None = None
    total_floors: int | None = None
    facing: str | None = None
    rent: int | None = None
    security_deposit: int | None = None
    maintenance_charges: int | None = None
    min_rent: int | None = None
    max_rent: int | None = None
    description: str | None = None
    images: list[str] = []
    occupancy: str
    premium: bool
    status: str = "draft"
    amenities: list = []
    lease_start: datetime | None = None
    lease_end: datetime | None = None
    owner_id: str
    tenant_id: str | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


def property_to_response(prop) -> dict:
    """Convert a Property ORM object to a response dict."""
    effective_occupancy = "occupied" if prop.tenant_id else prop.occupancy.value
    return PropertyResponse(
        id=prop.id,
        name=prop.name,
        unit=prop.unit,
        address=prop.address,
        city=prop.city,
        state=prop.state,
        pincode=prop.pincode,
        type=prop.type.value,
        bhk=prop.bhk,
        sqft=prop.sqft,
        furnishing=prop.furnishing.value,
        floor=prop.floor,
        total_floors=prop.total_floors,
        facing=prop.facing,
        rent=prop.rent,
        security_deposit=prop.security_deposit,
        maintenance_charges=prop.maintenance_charges,
        min_rent=getattr(prop, "min_rent", None),
        max_rent=getattr(prop, "max_rent", None),
        description=prop.description,
        images=prop.images or [],
        occupancy=effective_occupancy,
        premium=prop.premium,
        status=prop.status.value if getattr(prop, "status", None) else "draft",
        amenities=prop.amenities or [],
        lease_start=prop.lease_start,
        lease_end=prop.lease_end,
        owner_id=prop.owner_id,
        tenant_id=prop.tenant_id,
        created_by=prop.created_by,
        created_at=prop.created_at,
        updated_at=prop.updated_at,
    ).model_dump()
