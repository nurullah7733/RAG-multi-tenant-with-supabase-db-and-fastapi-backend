

from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException
from pydantic import SecretStr

import logging

from app.core.config import settings
from app.core.errors import ProviderUnavailableError

logger = logging.getLogger(__name__)


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="A valid authenticated session is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        yield client


class SupabaseGateway:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def request(
        self,
        method: str,
        path: str,
        token: SecretStr,
        *,
        params: dict[str, str | int] | None = None,
        body: dict[str, str] | None = None,
    ) -> object:
        key = settings.supabase_publishable_key.get_secret_value()
        if not key:
            raise ProviderUnavailableError(
                "Supabase public-key configuration is required."
            )
        try:
            response = await self.client.request(
                method,
                settings.supabase_url + path,
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {token.get_secret_value()}",
                    "Accept": "application/json",
                },
                params=params,
                json=body,
            )
        except httpx.RequestError:
            raise ProviderUnavailableError() from None
        if response.status_code == 401 or (
            path == "/auth/v1/user" and response.status_code == 403
        ):
            raise unauthorized()
        if not response.is_success:
            # Raw provider bodies may include SQL, identities or credentials.
            # Do not forward or log them. Only the status and error-code
            # identifier (e.g. "PGRST202", "42501") are safe to log.
            provider_code: object = None
            try:
                provider_code = response.json().get("code")
            except ValueError:
                pass
            logger.error(
                "Supabase request failed: %s %s -> %s (provider code: %s)",
                method,
                path,
                response.status_code,
                provider_code,
            )
            raise ProviderUnavailableError() from None
        try:
            return response.json()
        except ValueError:
            raise ProviderUnavailableError() from None


def get_gateway(
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> SupabaseGateway:
    return SupabaseGateway(client)
