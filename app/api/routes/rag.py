"""RAG retrieval API endpoints (Phase 3.2)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.repositories.organizations import OrganizationRepository, get_repository
from app.schemas.rag import RetrievalResult as RetrievalResultSchema
from app.services.rag import (
    RAGRequest,
    RAGService,
    RetrievalResult,
    get_rag_service,
)

router = APIRouter(prefix="/rag", tags=["rag"])


def _to_schema(result: RetrievalResult) -> RetrievalResultSchema:
    """Convert internal dataclass result to API schema."""
    from app.schemas.rag import RetrievedChunk as RetrievedChunkSchema

    return RetrievalResultSchema(
        query=result.query,
        organization_id=result.organization_id,
        chunks=tuple(
            RetrievedChunkSchema(
                id=chunk.id,
                document_id=chunk.document_id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                page_number=chunk.page_number,
                similarity=chunk.similarity,
            )
            for chunk in result.chunks
        ),
        embedding_provider=result.embedding_provider,
        embedding_model=result.embedding_model,
    )


@router.post("/retrieve", response_model=RetrievalResultSchema)
async def retrieve_knowledge(
    request: RAGRequest,
    repository: Annotated[OrganizationRepository, Depends(get_repository)],
    rag_service: Annotated[RAGService, Depends(get_rag_service)],
) -> RetrievalResultSchema:

    # Verify the user belongs to the requested organization
    await repository.membership(UUID(request.organization_id))

    result = await rag_service.retrieve(
        query=request.query,
        organization_id=request.organization_id,
        limit=request.limit,
    )
    return _to_schema(result)


@router.post("/build-context", response_model=str)
async def build_context(
    request: RAGRequest,
    repository: Annotated[OrganizationRepository, Depends(get_repository)],
    rag_service: Annotated[RAGService, Depends(get_rag_service)],
) -> str:

    # Verify the user belongs to the requested organization
    await repository.membership(UUID(request.organization_id))

    retrieval_result = await rag_service.retrieve(
        query=request.query,
        organization_id=request.organization_id,
        limit=request.limit,
    )

    context = await rag_service.build_context(retrieval_result)
    return context
