from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

OrganizationRole = Literal["owner", "admin", "staff"]


class OrganizationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    organization_type: str | None = Field(default=None, min_length=1, max_length=100)
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    default_language: str = Field(
        default="en", max_length=35, pattern=r"^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$"
    )
    currency_code: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")

    @field_validator("country_code", "currency_code", mode="before")
    @classmethod
    def normalize_code(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("Use an IANA timezone identifier.") from None
        return value


class Organization(BaseModel):
    id: UUID
    name: str
    created_at: datetime
    organization_type: str | None = None
    country_code: str | None = None
    timezone: str | None = None
    default_language: str = "en"
    currency_code: str | None = None


class Membership(BaseModel):
    organization_id: UUID
    user_id: UUID
    role: OrganizationRole
    created_at: datetime


class OrganizationAccess(BaseModel):
    organization: Organization
    role: OrganizationRole
