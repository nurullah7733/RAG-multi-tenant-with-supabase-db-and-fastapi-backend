import logging
import math
from dataclasses import dataclass
from typing import Annotated, Protocol
from uuid import UUID

from app.core.auth import AuthenticatedUser, get_current_user
from app.core.config import Settings, settings
from app.core.errors import ProviderUnavailableError
from app.integrations.supabase import SupabaseGateway, get_gateway
from app.repositories.organizations import OrganizationRepository
from app.services.embeddings import (
    EmbeddingError,
    EmbeddingProvider,
    get_embedding_provider,
)
from app.services.security import (
    ContentSecurityService,
    get_security_service,
)
from fastapi import Depends
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# Minimum similarity threshold for considering a chunk relevant
_MIN_SIMILARITY = 0.15
# Maximum number of chunks to retrieve
_MAX_MATCH_COUNT = 10


class RetrievalError(ProviderUnavailableError):
    """RAG retrieval failed."""

    code = "retrieval_unavailable"
    message = "The knowledge retrieval service is temporarily unavailable."


class EmptyQueryError(Exception):
    """Query cannot be empty for retrieval."""


@dataclass(frozen=True)
class RetrievedChunk:
    """A single retrieved knowledge chunk with metadata."""

    id: str
    document_id: str
    chunk_index: int
    content: str
    page_number: int | None
    similarity: float


@dataclass(frozen=True)
class RetrievalResult:
    """Result of a semantic retrieval operation."""

    query: str
    organization_id: str
    chunks: tuple[RetrievedChunk, ...]
    embedding_provider: str
    embedding_model: str


class RetrievalProvider(Protocol):
    """Abstraction for the retrieval backend."""

    async def retrieve(
        self, query: str, organization_id: str, limit: int = _MAX_MATCH_COUNT
    ) -> RetrievalResult: ...


class RAGRequest(BaseModel):
    """Request model for RAG retrieval."""

    model_config = {"frozen": True, "extra": "forbid", "hide_input_in_errors": True}

    query: str = Field(min_length=1, max_length=10000)
    organization_id: str = Field(
        pattern=r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$"
    )
    limit: int = Field(default=10, ge=1, le=100)

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Query must not be blank")
        return value


class RAGService:
    """Orchestrates embedding generation and vector retrieval for RAG."""

    def __init__(
        self,
        gateway: SupabaseGateway,
        embedding_provider: EmbeddingProvider,
        security_service: ContentSecurityService,
        settings: Settings,
        user: AuthenticatedUser,
    ) -> None:
        self.user = user
        self.gateway = gateway
        self.embedding_provider = embedding_provider
        self.security_service = security_service
        self._settings = settings

    async def retrieve(
        self, query: str, organization_id: str, limit: int = _MAX_MATCH_COUNT
    ) -> RetrievalResult:
        """
        Retrieve organization-scoped knowledge chunks relevant to the query.

        Pipeline:
        1. Generate query embedding
        2. Search pgvector with organization filter
        3. Apply relevance threshold
        4. Return ranked chunks with metadata
        """
        organization_id = str(UUID(organization_id))
        if not query.strip():
            raise EmptyQueryError("Query must not be empty")

        if limit < 1 or limit > 100:
            raise ValueError("Limit must be between 1 and 100")

        # Authorize before embedding or accessing tenant knowledge, including internal callers.
        await OrganizationRepository(self.gateway, self.user).membership(
            UUID(organization_id)
        )

        # Validate query for prompt injection attempts
        self.security_service.validate_user_input(query)

        # Step 1: Generate query embedding
        try:
            query_embedding = await self.embedding_provider.embed_many([query])
        except (EmbeddingError, ValueError, TypeError) as e:
            logger.info("embedding_generation_failed: %s", type(e).__name__)
            raise RetrievalError() from None

        if not query_embedding or len(query_embedding) != 1:
            raise RetrievalError()

        embedding_vector = query_embedding[0]
        if (
            len(embedding_vector) != self._settings.embedding_dimensions
            or any(
                not isinstance(v, (int, float))
                or isinstance(v, bool)
                or not math.isfinite(v)
                for v in embedding_vector
            )
            or not any(embedding_vector)
        ):
            raise RetrievalError()

        # Step 2: Call the database function for organization-scoped vector search
        try:
            result = await self.gateway.request(
                "POST",
                "/rest/v1/rpc/match_knowledge_chunks",
                self.user.token,
                body={
                    "p_organization_id": organization_id,
                    "p_query_embedding": embedding_vector,
                    "p_match_count": limit,
                    "p_minimum_similarity": _MIN_SIMILARITY,
                    "p_embedding_provider": self.embedding_provider.name,
                    "p_embedding_model": self.embedding_provider.model,
                },
            )
        except (ProviderUnavailableError, ValueError, TypeError, RuntimeError) as e:
            logger.info("vector_search_failed: %s", type(e).__name__)
            raise RetrievalError() from None

        # Fail closed on malformed or cross-tenant RPC results.
        if not isinstance(result, list):
            raise RetrievalError()
        chunks: list[RetrievedChunk] = []
        for row in result:
            try:
                if row["organization_id"] != organization_id:
                    raise ValueError
                similarity = float(row["similarity"])
                if (
                    not math.isfinite(similarity)
                    or not _MIN_SIMILARITY <= similarity <= 1.000001
                ):
                    raise ValueError
                if not isinstance(row["content"], str) or not row["content"].strip():
                    raise ValueError
                chunks.append(
                    RetrievedChunk(
                        id=str(UUID(row["id"])),
                        document_id=str(UUID(row["document_id"])),
                        chunk_index=int(row["chunk_index"]),
                        content=row["content"],
                        page_number=row["page_number"],
                        similarity=similarity,
                    )
                )
            except (ValueError, TypeError, KeyError, AttributeError):
                raise RetrievalError() from None
        chunks.sort(key=lambda chunk: (-chunk.similarity, chunk.id))
        chunks = chunks[:limit]

        return RetrievalResult(
            query=query,
            organization_id=organization_id,
            chunks=tuple(chunks),
            embedding_provider=self.embedding_provider.name,
            embedding_model=self.embedding_provider.model,
        )

    async def build_context(
        self, retrieval_result: RetrievalResult, max_context_length: int = 4000
    ) -> str:
        """
        Build a grounded context string from retrieved chunks.

        Returns a formatted context suitable for inclusion in an LLM prompt.
        Preserves source information for attribution.
        """
        if not retrieval_result.chunks:
            return ""

        parts = []
        total_length = 0

        for chunk in retrieval_result.chunks:
            # Format: content (source: document_id, page: page_number)
            source_info = f"(source: {chunk.document_id}"
            if chunk.page_number is not None:
                source_info += f", page: {chunk.page_number})"
            else:
                source_info += ")"

            formatted_chunk = f"{chunk.content}\n{source_info}"

            if total_length + len(formatted_chunk) > max_context_length:
                break

            parts.append(formatted_chunk)
            total_length += len(formatted_chunk)

        return "\n\n---\n\n".join(parts)


async def get_rag_service(
    gateway: Annotated[SupabaseGateway, Depends(get_gateway)],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    embedding_provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
    security_service: Annotated[ContentSecurityService, Depends(get_security_service)],
) -> RAGService:
    """Factory function for RAG service with dependencies."""
    return RAGService(gateway, embedding_provider, security_service, settings, user)
