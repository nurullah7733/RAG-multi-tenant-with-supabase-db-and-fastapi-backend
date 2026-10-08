import json

import httpx
import pytest
from app.main import app
from app.services.generation import GenerationResult, get_ai_service

from tests.conftest import DOC_ID, ORG_A, ORG_B, members_reply

BASE = "/api/v1/grounded-ai/generate"
CHUNK = "40000000-0000-0000-0000-000000000001"


@pytest.fixture
def ai():
    calls = []

    class FakeAI:
        async def generate(self, request):
            calls.append(request)
            return GenerationResult(
                provider="openai",
                model="test",
                text="Plants need light.",
                finish_reason="stop",
            )

    app.dependency_overrides[get_ai_service] = lambda: FakeAI()
    yield calls
    app.dependency_overrides.pop(get_ai_service, None)


def handler(rows, language="en"):
    def respond(request):
        if request.url.path.endswith("/organization_members"):
            return members_reply()
        if request.url.path == "/rest/v1/organizations":
            return httpx.Response(200, json=[organization_row(language)])
        assert request.url.path == "/rest/v1/rpc/match_knowledge_chunks"
        body = json.loads(request.content)
        assert body["p_organization_id"] == ORG_A
        assert body["p_embedding_model"] == "text-embedding-3-small"
        assert body["p_embedding_provider"] == "openai"
        assert len(body["p_query_embedding"]) == 1536
        assert request.headers["authorization"] == "Bearer user-access-token"
        return httpx.Response(200, json=rows)

    return respond


def chunk(org=ORG_A):
    return {
        "id": CHUNK,
        "organization_id": org,
        "document_id": DOC_ID,
        "chunk_index": 0,
        "content": "Plants need light.",
        "page_number": 1,
        "similarity": 0.9,
    }


def organization_row(language="en"):
    return {
        "id": ORG_A,
        "name": "Org A",
        "created_at": "2026-10-05T00:00:00Z",
        "default_language": language,
    }


def test_playground_pipeline(api, ai):
    client, _, configure = api
    configure(handler([chunk()]))
    response = client.post(
        BASE, json={"organization_id": ORG_A, "query": "What is said about plants?"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["response"] == "Plants need light."
    assert response.json()["sources"] == [{"document_id": DOC_ID, "page_number": 1}]
    assert len(ai) == 1
    assert "Plants need light." in ai[0].messages[1].content


def test_conversation_language_defaults_to_english(api, ai):
    client, _, configure = api
    configure(handler([chunk()]))
    response = client.post(BASE, json={"organization_id": ORG_A, "query": "plants"})
    assert response.status_code == 200, response.text
    assert response.json()["language"] == "en"
    system = ai[0].messages[0].content
    assert 'Respond in English ("en")' in system
    assert "REMEMBER: If in doubt, use the fallback message." in system
    assert system.index("REMEMBER:") < system.index("LANGUAGE:")


def test_unsupported_language_falls_back_to_english(api, ai):
    client, _, configure = api
    configure(handler([chunk()], language="fr"))
    response = client.post(BASE, json={"organization_id": ORG_A, "query": "plants"})
    assert response.status_code == 200, response.text
    assert response.json()["language"] == "en"
    assert 'Respond in English ("en")' in ai[0].messages[0].content


def test_request_body_cannot_set_language(api, ai):
    client, _, _ = api
    response = client.post(
        BASE, json={"organization_id": ORG_A, "query": "plants", "language": "fr"}
    )
    assert response.status_code == 422


def test_fallback_stays_english(api, ai):
    client, _, configure = api
    configure(handler([]))
    response = client.post(BASE, json={"organization_id": ORG_A, "query": "plants"})
    assert response.status_code == 200
    body = response.json()
    assert body["fallback"] is True
    assert body["language"] == "en"
    assert "I don't have enough information about that yet." in body["response"]


@pytest.mark.parametrize(
    "endpoint", [BASE, "/api/v1/rag/retrieve", "/api/v1/rag/build-context"]
)
def test_nonmember_never_reaches_retrieval(api, ai, endpoint):
    client, requests, configure = api
    configure(lambda request: httpx.Response(200, json=[]))
    response = client.post(endpoint, json={"organization_id": ORG_B, "query": "plants"})
    assert response.status_code == 404
    assert not any("/rpc/" in str(r.url) for r in requests)
    assert ai == []


def test_empty_knowledge_skips_generation(api, ai):
    client, _, configure = api
    configure(handler([]))
    response = client.post(BASE, json={"organization_id": ORG_A, "query": "plants"})
    assert response.status_code == 200
    assert response.json()["fallback"] is True
    assert response.json()["sources"] == []
    assert ai == []


@pytest.mark.parametrize(
    "rows",
    [
        [chunk(ORG_B)],
        {},
        [dict(chunk(), similarity=float("inf"))],
        [dict(chunk(), content="")],
    ],
)
def test_bad_rpc_response_fails_closed(api, ai, rows):
    client, _, configure = api
    # JSON does not encode infinity; use an explicit string to exercise validation.
    if isinstance(rows, list) and rows and rows[0].get("similarity") == float("inf"):
        rows[0]["similarity"] = "Infinity"
    configure(handler(rows))
    response = client.post(BASE, json={"organization_id": ORG_A, "query": "plants"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "retrieval_unavailable"
    assert ai == []


def test_invalid_org_is_validation_error(api, ai):
    client, _, _ = api
    assert (
        client.post(
            BASE, json={"organization_id": "invalid", "query": "plants"}
        ).status_code
        == 422
    )
