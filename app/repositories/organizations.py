from typing import Annotated, TypeVar
from uuid import UUID

from fastapi import Depends
from pydantic import TypeAdapter, ValidationError

from app.core.auth import AuthenticatedUser, get_current_user
from app.core.errors import ProviderUnavailableError, ResourceNotFoundError
from app.integrations.supabase import SupabaseGateway, get_gateway
from app.schemas.organizations import (
    Membership,
    Organization,
    OrganizationAccess,
    OrganizationCreate,
)

T = TypeVar("T")


def parse_response(adapter: TypeAdapter[T], data: object) -> T:
    try:
        return adapter.validate_python(data)
    except ValidationError:
        raise ProviderUnavailableError() from None


class OrganizationRepository:
    def __init__(self, gateway: SupabaseGateway, user: AuthenticatedUser) -> None:
        self.gateway = gateway
        self.user = user

    async def create(self, payload: OrganizationCreate) -> Organization:
        # The RPC derives owner from auth.uid(), never from the request body.
        result = await self.gateway.request(
            "POST",
            "/rest/v1/rpc/create_organization",
            self.user.token,
            body={
                "organization_name": payload.name,
                **{
                    key: value
                    for key, value in payload.model_dump(exclude_unset=True).items()
                    if key != "name" and value is not None
                },
            },
        )
        return parse_response(TypeAdapter(Organization), result)

    async def list_for_user(self, limit: int, offset: int) -> list[OrganizationAccess]:
        result = await self.gateway.request(
            "GET",
            "/rest/v1/organization_members",
            self.user.token,
            params={
                "select": "role,organization:organizations(id,name,created_at,organization_type,country_code,timezone,default_language,currency_code)",
                "user_id": f"eq.{self.user.user_id}",
                "order": "created_at.asc,organization_id.asc",
                "limit": limit,
                "offset": offset,
            },
        )
        return parse_response(TypeAdapter(list[OrganizationAccess]), result)

    async def membership(self, organization_id: UUID) -> Membership:
        result = await self.gateway.request(
            "GET",
            "/rest/v1/organization_members",
            self.user.token,
            params={
                "select": "organization_id,user_id,role,created_at",
                "organization_id": f"eq.{organization_id}",
                "user_id": f"eq.{self.user.user_id}",
                "limit": 1,
            },
        )
        rows = parse_response(TypeAdapter(list[Membership]), result)
        if not rows:
            # Same response for missing org and another tenant's org.
            raise ResourceNotFoundError()
        member = rows[0]
        if (
            member.organization_id != organization_id
            or member.user_id != self.user.user_id
        ):
            raise ResourceNotFoundError()
        return member

    async def get(self, organization_id: UUID) -> Organization:
        result = await self.gateway.request(
            "GET",
            "/rest/v1/organizations",
            self.user.token,
            params={
                "select": "id,name,created_at,organization_type,country_code,timezone,default_language,currency_code",
                "id": f"eq.{organization_id}",
                "limit": 1,
            },
        )
        rows = parse_response(TypeAdapter(list[Organization]), result)
        if not rows or rows[0].id != organization_id:
            raise ResourceNotFoundError()
        return rows[0]

    async def members(
        self, organization_id: UUID, limit: int, offset: int
    ) -> list[Membership]:
        result = await self.gateway.request(
            "GET",
            "/rest/v1/organization_members",
            self.user.token,
            params={
                "select": "organization_id,user_id,role,created_at",
                "organization_id": f"eq.{organization_id}",
                "order": "created_at.asc,user_id.asc",
                "limit": limit,
                "offset": offset,
            },
        )
        rows = parse_response(TypeAdapter(list[Membership]), result)
        if any(row.organization_id != organization_id for row in rows):
            raise ResourceNotFoundError()
        return rows


def get_repository(
    gateway: Annotated[SupabaseGateway, Depends(get_gateway)],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> OrganizationRepository:
    return OrganizationRepository(gateway, user)
