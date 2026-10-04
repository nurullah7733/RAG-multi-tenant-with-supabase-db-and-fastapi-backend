from typing import Annotated
from uuid import UUID

from fastapi import Depends

from app.repositories.organizations import OrganizationRepository, get_repository
from app.schemas.organizations import Membership


async def require_membership(
    organization_id: UUID,
    repository: Annotated[OrganizationRepository, Depends(get_repository)],
) -> Membership:
    """Reusable boundary for organization-scoped routes; evaluated per request."""
    return await repository.membership(organization_id)
