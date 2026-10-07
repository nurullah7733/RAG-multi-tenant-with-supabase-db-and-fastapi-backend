"""Unit tests for the isolated OpenAI embedding provider (Phase 2.7)."""

import asyncio

import httpx
import pytest
from pydantic import SecretStr

from app.services.embeddings import (
    EmbeddingError,
    OpenAIEmbeddingProvider,
    vector_literal,
)

DIMENSIONS = 1536


def provider(handler) -> OpenAIEmbeddingProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAIEmbeddingProvider(
        client,
        api_key=SecretStr("test-only-key"),
        model="text-embedding-3-small",
        dimensions=DIMENSIONS,
    )


def embedding_payload(count: int, dimensions: int = DIMENSIONS) -> dict:
    return {
        "data": [
            {"index": index, "embedding": [0.5] * dimensions}
            for index in range(count)
        ]
    }


def test_embed_many_batches_requests_and_returns_ordered_vectors():
    seen_sizes = []

    def counting_handler(request: httpx.Request) -> httpx.Response:
        import json

        payload = json.loads(request.content)
        seen_sizes.append(len(payload["input"]))
        assert payload["model"] == "text-embedding-3-small"
        assert payload["dimensions"] == DIMENSIONS
        assert request.headers["authorization"] == "Bearer test-only-key"
        return httpx.Response(200, json=embedding_payload(len(payload["input"])))

    vectors = asyncio.run(
        provider(counting_handler).embed_many([f"chunk {i}" for i in range(40)])
    )
    assert seen_sizes == [32, 8]
    assert len(vectors) == 40
    assert all(len(vector) == DIMENSIONS for vector in vectors)


def test_empty_input_makes_no_request():
    def failing(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    assert asyncio.run(provider(failing).embed_many([])) == []


def test_empty_text_is_rejected_without_request():
    def failing(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    with pytest.raises(EmbeddingError):
        asyncio.run(provider(failing).embed_many(["ok", "   "]))


def test_provider_rejection_maps_to_safe_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "secret detail"}})

    with pytest.raises(EmbeddingError) as error:
        asyncio.run(provider(handler).embed_many(["text"]))
    assert "secret" not in str(error.value)


def test_provider_outage_maps_to_safe_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(EmbeddingError):
        asyncio.run(provider(handler).embed_many(["text"]))


def test_unexpected_dimensions_are_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=embedding_payload(1, dimensions=8))

    with pytest.raises(EmbeddingError):
        asyncio.run(provider(handler).embed_many(["text"]))


def test_malformed_provider_payload_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 1}]})

    with pytest.raises(EmbeddingError):
        asyncio.run(provider(handler).embed_many(["text"]))


def test_vector_literal_is_pgvector_compatible():
    literal = vector_literal([0.5, 1.0, -0.25])
    assert literal == "[0.5,1,-0.25]"
    assert literal.startswith("[") and literal.endswith("]")
