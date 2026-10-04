import json
from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.core.config import Settings, settings
from app.integrations.supabase import get_http_client
from app.main import app

USER_A = "10000000-0000-0000-0000-000000000001"
USER_B = "10000000-0000-0000-0000-000000000002"
ORG_A = "20000000-0000-0000-0000-000000000001"
ORG_B = "20000000-0000-0000-0000-000000000002"
STAMP = "2026-10-03T00:00:00Z"
BASE = "/api/v1/organizations"


def membership(org: str = ORG_A, user: str = USER_A, role: str = "owner") -> dict:
    return {"organization_id": org, "user_id": user, "role": role, "created_at": STAMP}


def organization(org: str = ORG_A) -> dict:
    return {"id": org, "name": "Example Organization", "created_at": STAMP}


@pytest.fixture
def api():
    requests: list[httpx.Request] = []
    handler: Callable = lambda request: httpx.Response(200, json=[])

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["apikey"] == "sb_publishable_test_only"
        assert request.headers["authorization"] == "Bearer user-access-token"
        if request.url.path == "/auth/v1/user":
            return httpx.Response(
                200, json={"id": USER_A, "user_metadata": {"role": "platform_admin"}}
            )
        return handler(request)

    async def client_override():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            yield client

    def configure(callback):
        nonlocal handler
        handler = callback

    app.dependency_overrides[get_http_client] = client_override
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer user-access-token"
        yield client, requests, configure
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "authorization", [None, "Basic invalid", "Bearer", "Bearer " + "x" * 16385]
)
def test_missing_or_malformed_auth_never_reaches_provider(api, authorization):
    client, requests, _ = api
    client.headers.pop("Authorization")
    headers = {"Authorization": authorization} if authorization else {}
    result = client.get(BASE, headers=headers)
    assert result.status_code == 401
    assert result.headers["www-authenticate"] == "Bearer"
    assert requests == []


@pytest.mark.parametrize("status", [401, 403, 500, 429])
def test_auth_provider_rejection_is_fail_closed(status):
    async def rejected_client():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(status, json={"secret": "do-not-expose"})
            )
        ) as client:
            yield client

    app.dependency_overrides[get_http_client] = rejected_client
    try:
        result = TestClient(app).get(
            BASE, headers={"Authorization": "Bearer expired-or-forged"}
        )
        assert result.status_code == (401 if status in {401, 403} else 503)
        assert "do-not-expose" not in result.text
    finally:
        app.dependency_overrides.clear()


def test_create_uses_only_validated_name_and_user_token(api):
    client, requests, configure = api

    def create(request):
        assert request.method == "POST"
        assert request.url.path == "/rest/v1/rpc/create_organization"
        assert json.loads(request.content) == {"organization_name": "বাংলা Organization"}
        return httpx.Response(200, json=organization())

    configure(create)
    result = client.post(BASE, json={"name": "  বাংলা Organization  "})
    assert result.status_code == 201
    assert result.json()["id"] == ORG_A
    assert result.headers["cache-control"] == "private, no-store"
    assert len(requests) == 2


@pytest.mark.parametrize(
    "body",
    [
        {"name": " "},
        {"name": "x" * 101},
        {},
        {"name": 42},
        {"name": "Example", "user_id": USER_B},
        {"name": "Example", "role": "owner"},
        {"name": "Example", "organization_id": ORG_B},
    ],
)
def test_create_rejects_invalid_names_and_identity_overrides(api, body):
    client, requests, _ = api
    assert client.post(BASE, json=body).status_code == 422
    assert all(request.url.path == "/auth/v1/user" for request in requests)


def test_list_is_filtered_to_authenticated_users_memberships_and_paginated(api):
    client, _, configure = api

    def listing(request):
        assert request.url.params["user_id"] == f"eq.{USER_A}"
        assert request.url.params["limit"] == "2"
        assert request.url.params["offset"] == "1"
        return httpx.Response(
            200,
            json=[
                {"organization": organization(), "role": "owner"},
                {"organization": organization(ORG_B), "role": "staff"},
            ],
        )

    configure(listing)
    result = client.get(BASE + "?limit=2&offset=1&user_id=" + USER_B)
    assert result.status_code == 200
    assert [row["role"] for row in result.json()] == ["owner", "staff"]


@pytest.mark.parametrize("suffix", ["", "/members"])
def test_changed_org_id_denied_before_any_business_data_fetch(api, suffix):
    client, requests, configure = api

    def no_membership(request):
        assert request.url.path == "/rest/v1/organization_members"
        assert request.url.params["organization_id"] == f"eq.{ORG_B}"
        assert request.url.params["user_id"] == f"eq.{USER_A}"
        return httpx.Response(200, json=[])

    configure(no_membership)
    result = client.get(BASE + f"/{ORG_B}{suffix}")
    assert result.status_code == 404
    assert len(requests) == 2


@pytest.mark.parametrize("role", ["owner", "admin", "staff"])
def test_each_member_role_can_read_own_organization(api, role):
    client, _, configure = api

    def read(request):
        if request.url.path.endswith("organization_members"):
            return httpx.Response(200, json=[membership(role=role)])
        assert request.url.params["id"] == f"eq.{ORG_A}"
        return httpx.Response(200, json=[organization()])

    configure(read)
    assert client.get(BASE + f"/{ORG_A}").status_code == 200


def test_members_list_only_queries_authorized_org(api):
    client, _, configure = api

    def read(request):
        assert request.url.params["organization_id"] == f"eq.{ORG_A}"
        if "user_id" in request.url.params:
            return httpx.Response(200, json=[membership()])
        return httpx.Response(
            200, json=[membership(), membership(user=USER_B, role="admin")]
        )

    configure(read)
    result = client.get(BASE + f"/{ORG_A}/members")
    assert result.status_code == 200
    assert len(result.json()) == 2


def test_membership_is_checked_again_after_revocation(api):
    client, _, configure = api
    configure(lambda _: httpx.Response(200, json=[membership()]))
    assert client.get(BASE + f"/{ORG_A}/members").status_code == 200
    configure(lambda _: httpx.Response(200, json=[]))
    assert client.get(BASE + f"/{ORG_A}/members").status_code == 404


@pytest.mark.parametrize("row", [membership(ORG_B), membership(user=USER_B)])
def test_unexpected_membership_identity_fails_closed(api, row):
    client, _, configure = api
    configure(lambda _: httpx.Response(200, json=[row]))
    assert client.get(BASE + f"/{ORG_A}").status_code == 404


@pytest.mark.parametrize("path", ["?limit=101", "?offset=-1", "/not-a-uuid"])
def test_invalid_pagination_and_uuid(api, path):
    client, _, _ = api
    assert client.get(BASE + path).status_code == 422


def test_provider_failures_and_malformed_payloads_are_not_exposed(api):
    client, _, configure = api
    for reply in [
        httpx.Response(500, text="private sql details"),
        httpx.Response(200, json={"wrong": "shape"}),
        httpx.Response(200, text="not json"),
    ]:
        configure(lambda _, reply=reply: reply)
        result = client.get(BASE)
        assert result.status_code == 503
        assert "private sql details" not in result.text


def test_network_failure_is_safe(api):
    client, _, configure = api

    def fail(request):
        raise httpx.ConnectError("private provider detail", request=request)

    configure(fail)
    assert client.get(BASE).status_code == 503


def test_missing_public_key_returns_503_but_health_remains_public(api, monkeypatch):
    client, _, _ = api
    monkeypatch.setattr(settings, "supabase_publishable_key", SecretStr(""))
    assert client.get(BASE).status_code == 503
    assert client.get("/health").status_code == 200


def test_settings_reject_service_role_key_and_redact_input():
    with pytest.raises(ValidationError) as captured:
        Settings(_env_file=None, supabase_publishable_key="sb_secret_never-log-me")
    assert "sb_secret_never-log-me" not in str(captured.value)


def test_no_team_mutation_or_platform_admin_routes(api):
    client, _, _ = api
    assert (
        client.post(
            BASE + f"/{ORG_A}/members", json={"user_id": USER_B, "role": "owner"}
        ).status_code
        == 405
    )
    assert client.get("/api/v1/admin").status_code == 404
