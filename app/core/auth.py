"""Verified user context; no role or organization comes from browser claims."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.integrations.supabase import SupabaseGateway, get_gateway, unauthorized

bearer = HTTPBearer(auto_error=False)


class AuthenticatedUser(BaseModel):
    model_config = ConfigDict(frozen=True)
    user_id: UUID
    token: SecretStr = Field(exclude=True, repr=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    gateway: Annotated[SupabaseGateway, Depends(get_gateway)],
) -> AuthenticatedUser:
    if credentials is None or len(credentials.credentials) > 16384:
        raise unauthorized()
    token = SecretStr(credentials.credentials)
    # Auth validates the signature, expiry and user; never decode an unverified JWT.
    result = await gateway.request("GET", "/auth/v1/user", token)
    if not isinstance(result, dict) or result.get("is_anonymous") is True:
        raise unauthorized()
    try:
        return AuthenticatedUser(user_id=result.get("id"), token=token)
    except ValidationError:
        raise unauthorized() from None
