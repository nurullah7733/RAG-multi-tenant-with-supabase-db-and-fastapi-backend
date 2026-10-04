import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.core.authorization import require_membership
from app.repositories.organizations import OrganizationRepository, get_repository
from app.schemas.organizations import (
    Membership,
    Organization,
    OrganizationAccess,
    OrganizationCreate,
)

logger = logging.getLogger(__name__)


def private_response(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(
    prefix="/organizations",
    tags=["organizations"],
    dependencies=[Depends(private_response)],
)
Repository = Annotated[OrganizationRepository, Depends(get_repository)]
Member = Annotated[Membership, Depends(require_membership)]
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0)]


@router.post("", response_model=Organization, status_code=201)
async def create_organization(
    payload: OrganizationCreate, repository: Repository
) -> Organization:
    organization = await repository.create(payload)
    logger.info("organization_created organization_id=%s", organization.id)
    return organization


@router.get("", response_model=list[OrganizationAccess])
async def list_organizations(
    repository: Repository,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> list[OrganizationAccess]:
    return await repository.list_for_user(limit, offset)


@router.get("/{organization_id}", response_model=Organization)
async def get_organization(
    organization_id: UUID, member: Member, repository: Repository
) -> Organization:
    return await repository.get(member.organization_id)


@router.get("/{organization_id}/members", response_model=list[Membership])
async def list_members(
    organization_id: UUID,
    member: Member,
    repository: Repository,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> list[Membership]:
    return await repository.members(member.organization_id, limit, offset)
