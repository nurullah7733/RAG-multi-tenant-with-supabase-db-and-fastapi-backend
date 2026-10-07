"""PostgREST success response contracts for indexing writes."""

import asyncio

import httpx
import pytest
from pydantic import SecretStr

from app.core.errors import ProviderUnavailableError
from app.integrations.supabase import SupabaseGateway


@pytest.mark.parametrize(
    "method,status", [("POST", 201), ("PATCH", 204), ("DELETE", 204)]
)
def test_minimal_write_accepts_empty_success(method, status):
    async def run():
        def respond(request):
            assert request.headers["prefer"] == "return=minimal"
            return httpx.Response(status)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await SupabaseGateway(client).request(
                method,
                "/rest/v1/knowledge_chunks",
                SecretStr("test-token"),
                extra_headers={"Prefer": "return=minimal"},
            )

    assert asyncio.run(run()) is None


@pytest.mark.parametrize(
    "status,body,preferences",
    [
        (201, b"", {"Prefer": "return=representation"}),
        (201, b"invalid-json", {"Prefer": "return=minimal"}),
        (403, b"", {"Prefer": "return=minimal"}),
        (500, b"", {"Prefer": "return=minimal"}),
    ],
)
def test_minimal_support_does_not_hide_real_errors(status, body, preferences):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(status, content=body)
            )
        ) as client:
            return await SupabaseGateway(client).request(
                "POST",
                "/rest/v1/knowledge_chunks",
                SecretStr("test-token"),
                extra_headers=preferences,
            )

    with pytest.raises(ProviderUnavailableError):
        asyncio.run(run())
