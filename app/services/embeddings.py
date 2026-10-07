"""Isolated embedding provider; business code never sees provider details."""

import logging
from typing import Annotated, Protocol

import httpx
from fastapi import Depends
from pydantic import SecretStr

from app.core.config import settings
from app.integrations.supabase import get_http_client

logger = logging.getLogger(__name__)

_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"
_BATCH_SIZE = 32


class EmbeddingError(Exception):
    """A safe provider failure; never carries keys, payloads or provider text."""


class EmbeddingProvider(Protocol):
    name: str
    model: str

    async def embed_many(self, texts: list[str]) -> list[list[float]]: ...


def vector_literal(values: list[float]) -> str:
    """pgvector text representation, e.g. "[0.1,0.2]"."""
    return "[" + ",".join(f"{value:.8g}" for value in values) + "]"


class OpenAIEmbeddingProvider:
    name = "openai"

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        api_key: SecretStr,
        model: str,
        dimensions: int,
    ) -> None:
        self.client = client
        self._api_key = api_key
        self.model = model
        self.dimensions = dimensions

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if any(not text.strip() for text in texts):
            raise EmbeddingError("Embeddings cannot be generated from empty text.")
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _BATCH_SIZE):
            vectors.extend(
                await self._embed_batch(texts[start : start + _BATCH_SIZE])
            )
        return vectors

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        try:
            response = await self.client.post(
                _EMBEDDINGS_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                },
                json={
                    "model": self.model,
                    "input": batch,
                    "dimensions": self.dimensions,
                    "encoding_format": "float",
                },
            )
        except httpx.RequestError:
            logger.info("embedding_provider_unreachable")
            raise EmbeddingError(
                "The embedding provider is unreachable right now."
            ) from None
        if not response.is_success:
            # Provider bodies may echo request content; log the status only.
            logger.info(
                "embedding_provider_rejected status=%s", response.status_code
            )
            raise EmbeddingError(
                "The embedding provider rejected the request."
            ) from None
        try:
            data = response.json()["data"]
        except (ValueError, KeyError, TypeError):
            raise EmbeddingError(
                "The embedding provider returned an unexpected response."
            ) from None
        if (
            not isinstance(data, list)
            or len(data) != len(batch)
            or not all(isinstance(item, dict) for item in data)
            or sorted(item.get("index", -1) for item in data)
            != list(range(len(batch)))
        ):
            raise EmbeddingError(
                "The embedding provider returned an unexpected response."
            ) from None
        vectors: list[list[float]] = []
        for item in sorted(data, key=lambda entry: entry["index"]):
            embedding = item.get("embedding")
            if (
                not isinstance(embedding, list)
                or len(embedding) != self.dimensions
                or not all(isinstance(value, (int, float)) for value in embedding)
            ):
                raise EmbeddingError(
                    "The embedding provider returned unexpected dimensions."
                )
            vectors.append([float(value) for value in embedding])
        return vectors


def get_embedding_provider(
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> EmbeddingProvider:
    return OpenAIEmbeddingProvider(
        client,
        api_key=settings.openai_api_key,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )
