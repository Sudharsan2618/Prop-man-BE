"""
LuxeLife API — RBAC schemas.

Request/response models for super-admin RBAC APIs.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class RoleCreateRequest(BaseModel):
    name: str = Field(..., pattern=r"^(tenant|owner|service_provider|provider|manager|admin|super_admin)$")
    description: str | None = Field(None, max_length=255)


class RoleUpdateRequest(BaseModel):
    description: str | None = Field(None, max_length=255)


class PermissionCreateRequest(BaseModel):
    code: str = Field(..., min_length=3, max_length=100)
    description: str | None = Field(None, max_length=255)
    entity: str = Field(..., min_length=2, max_length=50)
    action: str = Field(..., min_length=2, max_length=20)


class PermissionUpdateRequest(BaseModel):
    code: str | None = Field(None, min_length=3, max_length=100)
    description: str | None = Field(None, max_length=255)
    entity: str | None = Field(None, min_length=2, max_length=50)
    action: str | None = Field(None, min_length=2, max_length=20)


class RolePermissionsUpdateRequest(BaseModel):
    permission_ids: list[int] = Field(default_factory=list)


class UserRolesUpdateRequest(BaseModel):
    role_ids: list[int] = Field(default_factory=list)


class PermissionResponse(BaseModel):
    id: int
    code: str
    description: str | None = None
    entity: str
    action: str
    created_at: datetime


class RoleResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    created_at: datetime
    permission_ids: list[int] = Field(default_factory=list)


class RoleMatrixRow(BaseModel):
    role_id: int
    role_name: str
    permissions: dict[str, bool]


class RolePermissionMatrixResponse(BaseModel):
    roles: list[RoleResponse]
    permissions: list[PermissionResponse]
    matrix: list[RoleMatrixRow]
