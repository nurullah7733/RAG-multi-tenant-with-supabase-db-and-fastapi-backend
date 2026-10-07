

import logging
from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException
from pydantic import SecretStr

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
        body: object | None = None,
        extra_headers: dict[str, str] | None = None,
        content: bytes | None = None,
    ) -> object:
        key = settings.supabase_publishable_key.get_secret_value()
        if not key:
            raise ProviderUnavailableError(
                "Supabase public-key configuration is required."
            )
        headers: dict[str, str] = {
            "apikey": key,
            "Authorization": f"Bearer {token.get_secret_value()}",
            "Accept": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        try:
            if content is not None:
                response = await self.client.request(
                    method,
                    settings.supabase_url + path,
                    headers=headers,
                    params=params,
                    content=content,
                )
            else:
                response = await self.client.request(
                    method,
                    settings.supabase_url + path,
                    headers=headers,
                    params=params,
                    json=body,
                )
        except httpx.RequestError:
            raise ProviderUnavailableError() from None
        if response.status_code == 401 or (
            path == "/auth/v1/user" and response.status_code == 403
        ):
            raise unauthorized()
        if response.status_code == 404 and method == "DELETE":
            # Deleting an already-removed storage object is idempotent.
            return None
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
        if response.status_code == 204:
            return None
        # PostgREST INSERT with return=minimal succeeds with 201 and no body.
        # Do not attempt JSON decoding for an explicitly requested empty reply.
        preferences = {
            value.strip().lower()
            for value in headers.get("Prefer", "").split(",")
        }
        if not response.content and "return=minimal" in preferences:
            return None
        try:
            return response.json()
        except ValueError:
            if method == "DELETE":
                return None
            raise ProviderUnavailableError() from None

    async def storage_upload(
        self,
        object_path: str,
        content: bytes,
        mime_type: str,
        token: SecretStr,
    ) -> None:
        await self.request(
            "POST",
            f"/storage/v1/object/{settings.knowledge_bucket_name}/{object_path}",
            token,
            content=content,
            extra_headers={"Content-Type": mime_type},
        )

    async def storage_delete(self, object_path: str, token: SecretStr) -> None:
        await self.request(
            "DELETE",
            f"/storage/v1/object/{settings.knowledge_bucket_name}/{object_path}",
            token,
        )

    async def storage_download(self, object_path: str, token: SecretStr) -> bytes:
        """Raw object bytes; Storage GET is binary, so bypass JSON parsing."""
        key = settings.supabase_publishable_key.get_secret_value()
        if not key:
            raise ProviderUnavailableError(
                "Supabase public-key configuration is required."
            )
        headers = {
            "apikey": key,
            "Authorization": f"Bearer {token.get_secret_value()}",
        }
        url = f"{settings.supabase_url}/storage/v1/object/{settings.knowledge_bucket_name}/{object_path}"
        try:
            response = await self.client.get(url, headers=headers)
        except httpx.RequestError:
            raise ProviderUnavailableError() from None
        if response.status_code == 401:
            raise unauthorized()
        if not response.is_success:
            logger.error(
                "Supabase storage download failed -> %s", response.status_code
            )
            raise ProviderUnavailableError() from None
        return response.content


def get_gateway(
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> SupabaseGateway:
    return SupabaseGateway(client)
