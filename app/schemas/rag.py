"""Pydantic schemas for RAG retrieval (Phase 3.2)."""

from pydantic import BaseModel, Field


class RetrievedChunk(BaseModel):
    """A single retrieved knowledge chunk with metadata."""

    model_config = {"frozen": True}

    id: str = Field(description="Unique identifier for the chunk")
    document_id: str = Field(description="ID of the source document")
    chunk_index: int = Field(description="Index of the chunk within the document")
    content: str = Field(description="Text content of the chunk")
    page_number: int | None = Field(default=None, description="Page number if available")
    similarity: float = Field(description="Similarity score between query and chunk")


class RetrievalResult(BaseModel):
    """Result of a semantic retrieval operation."""

    model_config = {"frozen": True}

    query: str = Field(description="The original query string")
    organization_id: str = Field(description="Organization ID for the retrieval")
    chunks: tuple[RetrievedChunk, ...] = Field(
        default=(), description="Retrieved knowledge chunks with metadata"
    )
    embedding_provider: str = Field(description="Name of the embedding provider used")
    embedding_model: str = Field(description="Name of the embedding model used")