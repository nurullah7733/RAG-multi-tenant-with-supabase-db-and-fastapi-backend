"""RAG evaluation suite (Phase 3.7 / Step 24).

Runs the repeatable evaluation dataset through the full grounded pipeline
(route -> authorization -> tenant-scoped retrieval -> grounding -> generation).
The generation stub is extractive: it can only return a retrieved fact, so
success is judged by retrieval scoping and grounding correctness, never by
fluent output.
"""

import json
import re

import httpx
import pytest

from app.main import app
from app.services.embeddings import get_embedding_provider
from app.services.generation import GenerationResult, get_ai_service
from tests.rag_evaluation_data import (
    DOC_ORG,
    EVALUATION_CASES,
    KNOWLEDGE,
    ORG_NAMES,
    EvaluationCase,
    KnowledgeChunk,
)

GENERATE = "/api/v1/grounded-ai/generate"
STAMP = "2026-10-05T00:00:00Z"
_CONTEXT_MARKER = "Relevant information from approved documents:"


class KeywordEmbeddingProvider:
    """Deterministic provider that records query tokens for the RPC emulation."""

    name = "openai"
    model = "text-embedding-3-small"

    def __init__(self) -> None:
        self.last_tokens: frozenset[str] = frozenset()

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        self.last_tokens = frozenset(re.findall(r"[a-z0-9]+", texts[0].lower()))
        return [[0.5] * 1536]


class ExtractiveAI:
    """Stub that can only echo the first retrieved fact (grounding, not fluency)."""

    async def generate(self, request: object) -> GenerationResult:
        user = request.messages[-1].content  # type: ignore[attr-defined]
        if _CONTEXT_MARKER not in user:
            raise AssertionError("grounded prompt must contain retrieved context")
        fact = user.split(_CONTEXT_MARKER, 1)[1].strip().splitlines()[0].strip()
        return GenerationResult(
            provider="openai",
            model="evaluation-stub",
            text=fact,
            finish_reason="stop",
        )


def chunk_row(chunk: KnowledgeChunk) -> dict:
    return {
        "id": chunk.id,
        "organization_id": chunk.organization_id,
        "document_id": chunk.document_id,
        "chunk_index": 0,
        "content": chunk.content,
        "page_number": chunk.page_number,
        "similarity": 0.9,
    }


def evaluation_handler(provider: KeywordEmbeddingProvider):
    """Emulates Supabase exactly like the migration-scoped RPC would behave."""

    def respond(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.url.path.endswith("/organization_members"):
            return httpx.Response(
                200,
                json=[
                    {
                        "organization_id": params["organization_id"].removeprefix(
                            "eq."
                        ),
                        "user_id": params["user_id"].removeprefix("eq."),
                        "role": "owner",
                        "created_at": STAMP,
                    }
                ],
            )
        if request.url.path == "/rest/v1/organizations":
            org = params["id"].removeprefix("eq.")
            return httpx.Response(
                200,
                json=[
                    {
                        "id": org,
                        "name": ORG_NAMES[org],
                        "created_at": STAMP,
                        "default_language": "en",
                    }
                ],
            )
        assert request.url.path == "/rest/v1/rpc/match_knowledge_chunks"
        body = json.loads(request.content)
        requested_org = body["p_organization_id"]
        tokens = provider.last_tokens
        # The real RPC is scoped by p_organization_id; emulate that strictly.
        rows = [
            chunk_row(chunk)
            for chunk in KNOWLEDGE
            if chunk.organization_id == requested_org and tokens & set(chunk.keywords)
        ]
        return httpx.Response(200, json=rows)

    return respond


@pytest.fixture
def eval_api(api):
    client, requests, configure = api
    provider = KeywordEmbeddingProvider()
    app.dependency_overrides[get_embedding_provider] = lambda: provider
    app.dependency_overrides[get_ai_service] = lambda: ExtractiveAI()
    configure(evaluation_handler(provider))
    requests.clear()
    return client, provider, requests


@pytest.mark.parametrize(
    "case", EVALUATION_CASES, ids=lambda case: f"{case.category}:{case.slug}"
)
def test_rag_evaluation(eval_api, case: EvaluationCase):
    client, _provider, requests = eval_api
    response = client.post(
        GENERATE,
        json={"organization_id": case.organization_id, "query": case.query},
    )

    if case.category == "prompt_injection":
        assert response.status_code == 400, response.text
        assert "This request cannot be processed" in response.json()["error"]["message"]
        # Nothing beyond membership/organization lookups: no RPC, no generation.
        assert not any("/rpc/" in str(r.url) for r in requests)
        return

    assert response.status_code == 200, response.text
    body = response.json()

    # Universal tenant-isolation invariant: only the requested organization's
    # knowledge may appear in sources or in the response.
    other_facts = [
        chunk.content
        for chunk in KNOWLEDGE
        if chunk.organization_id != case.organization_id
    ]
    for fact in other_facts:
        assert fact not in body["response"]
    for source in body["sources"]:
        assert DOC_ORG[source["document_id"]] == case.organization_id
    assert body["language"] == "en"

    if case.expect_fallback:
        assert body["fallback"] is True
        assert body["response"].startswith(
            "I don't have enough information about that yet."
        )
        assert body["sources"] == []
    else:
        assert body["fallback"] is False
        for fact in case.expected_facts:
            assert fact in body["response"]
        assert [(s["document_id"], s["page_number"]) for s in body["sources"]] == [
            (document_id, page) for document_id, page in case.expected_sources
        ]


def test_evaluation_dataset_covers_all_categories():
    categories = {case.category for case in EVALUATION_CASES}
    assert categories == {
        "answerable",
        "unknown",
        "misleading",
        "cross_tenant",
        "prompt_injection",
        "source_correctness",
    }
    orgs = {case.organization_id for case in EVALUATION_CASES}
    assert orgs == set(ORG_NAMES)
