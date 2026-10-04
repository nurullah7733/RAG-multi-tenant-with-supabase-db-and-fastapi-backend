from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

OrganizationRole = Literal["owner", "admin", "staff"]


class OrganizationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)


class Organization(BaseModel):
    id: UUID
    name: str
    created_at: datetime


class Membership(BaseModel):
    organization_id: UUID
    user_id: UUID
    role: OrganizationRole
    created_at: datetime


class OrganizationAccess(BaseModel):
    organization: Organization
    role: OrganizationRole
