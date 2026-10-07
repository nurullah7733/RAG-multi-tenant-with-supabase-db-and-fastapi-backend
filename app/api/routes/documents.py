import logging
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from app.core.authorization import require_membership
from app.core.config import settings
from app.core.errors import UploadValidationError
from app.repositories.documents import DocumentRepository, get_document_repository
from app.schemas.documents import KnowledgeDocument
from app.schemas.organizations import Membership
from app.services.chunking import chunk_pages
from app.services.documents import (
    InvalidDocumentError,
    ensure_document_storage_path,
    validate_upload,
)
from app.services.embeddings import (
    EmbeddingError,
    EmbeddingProvider,
    get_embedding_provider,
)
from app.services.extraction import TextExtractionError, extract_text
from app.services.upload_body import read_document_upload

logger = logging.getLogger(__name__)


def private_response(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(
    prefix="/organizations/{organization_id}/documents",
    tags=["documents"],
    dependencies=[Depends(private_response)],
)
Repository = Annotated[DocumentRepository, Depends(get_document_repository)]
Member = Annotated[Membership, Depends(require_membership)]
Embeddings = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0)]


async def _mark_failed(
    repository: DocumentRepository,
    organization_id: UUID,
    document: KnowledgeDocument,
    message: str,
) -> None:
    """Record the failure state; a lost status write must not pass silently."""
    try:
        await repository.update_processing_state(
            organization_id, document.id, "failed", message
        )
    except Exception:
        logger.error(
            "document_failure_state_lost organization_id=%s document_id=%s",
            organization_id,
            document.id,
        )
        raise
    logger.info(
        "document_processing_failed organization_id=%s document_id=%s",
        organization_id,
        document.id,
    )


async def _index_document(
    repository: DocumentRepository,
    provider: EmbeddingProvider,
    organization_id: UUID,
    document: KnowledgeDocument,
    extension: str,
    content: bytes,
) -> KnowledgeDocument:
    """Extract → clean → chunk → embed → store; every failure state explicit."""
    await repository.update_processing_state(
        organization_id, document.id, "processing", None
    )
    try:
        pages = await run_in_threadpool(extract_text, extension, content)
        logger.info(
            "document_extracted organization_id=%s document_id=%s pages=%s characters=%s",
            organization_id,
            document.id,
            len(pages),
            sum(len(page.text) for page in pages),
        )
        chunks = await run_in_threadpool(
            chunk_pages,
            pages,
            settings.document_chunk_size,
            settings.document_chunk_overlap,
            settings.document_min_chunk_chars,
        )
        if not chunks:
            raise TextExtractionError("The document contains no chunkable text.")
        vectors = await provider.embed_many([chunk.content for chunk in chunks])
    except (TextExtractionError, EmbeddingError) as error:
        await _mark_failed(repository, organization_id, document, str(error))
        return document.model_copy(
            update={"status": "failed", "processing_error": str(error)}
        )
    logger.info(
        "document_chunked organization_id=%s document_id=%s chunks=%s",
        organization_id,
        document.id,
        len(chunks),
    )
    try:
        await repository.replace_chunks(
            organization_id,
            document.id,
            chunks,
            vectors,
            provider.name,
            provider.model,
        )
    except Exception:
        await _mark_failed(
            repository,
            organization_id,
            document,
            "Storing the processed knowledge chunks failed.",
        )
        raise
    await repository.update_processing_state(
        organization_id, document.id, "ready", None
    )
    logger.info(
        "document_indexed organization_id=%s document_id=%s chunks=%s",
        organization_id,
        document.id,
        len(chunks),
    )
    return document.model_copy(
        update={"status": "ready", "chunk_count": len(chunks)}
    )


@router.post(
    "",
    response_model=KnowledgeDocument,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {"file": {"type": "string", "format": "binary"}},
                    }
                }
            },
        }
    },
)
async def upload_document(
    organization_id: UUID,
    member: Member,
    repository: Repository,
    provider: Embeddings,
    request: Request,
) -> KnowledgeDocument:
    file = await read_document_upload(
        request, settings.max_document_size_mb * 1024 * 1024
    )
    content = file.content
    try:
        document_format = await run_in_threadpool(
            validate_upload,
            file.filename or "",
            file.content_type or "",
            content,
            settings.max_document_size_mb * 1024 * 1024,
        )
    except InvalidDocumentError as error:
        raise UploadValidationError(str(error)) from None
    document = await repository.create(
        organization_id=member.organization_id,
        document_id=uuid4(),
        filename=file.filename.strip(),
        extension=document_format.extension,
        mime_type=document_format.mime_type,
        size_bytes=len(content),
        content=content,
    )
    logger.info(
        "document_uploaded organization_id=%s document_id=%s size_bytes=%s",
        document.organization_id,
        document.id,
        document.size_bytes,
    )
    return await _index_document(
        repository, provider, member.organization_id, document,
        document_format.extension, content,
    )


@router.post("/{document_id}/reprocess", response_model=KnowledgeDocument)
async def reprocess_document(
    organization_id: UUID,
    document_id: UUID,
    member: Member,
    repository: Repository,
    provider: Embeddings,
) -> KnowledgeDocument:
    document = await repository.get(member.organization_id, document_id)
    # The stored path is re-derived from the verified row, never from input.
    ensure_document_storage_path(document)
    content = await repository.storage_download(document.storage_path)
    try:
        document_format = await run_in_threadpool(
            validate_upload,
            document.filename,
            document.mime_type,
            content,
            settings.max_document_size_mb * 1024 * 1024,
        )
    except InvalidDocumentError as error:
        await _mark_failed(repository, member.organization_id, document, str(error))
        return document.model_copy(
            update={"status": "failed", "processing_error": str(error)}
        )
    return await _index_document(
        repository, provider, member.organization_id, document,
        document_format.extension, content,
    )


@router.get("", response_model=list[KnowledgeDocument])
async def list_documents(
    organization_id: UUID,
    member: Member,
    repository: Repository,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> list[KnowledgeDocument]:
    return await repository.list_for_organization(member.organization_id, limit, offset)


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    organization_id: UUID,
    document_id: UUID,
    member: Member,
    repository: Repository,
) -> Response:
    await repository.delete(member.organization_id, document_id)
    logger.info(
        "document_deleted organization_id=%s document_id=%s",
        organization_id,
        document_id,
    )
    return Response(status_code=204)
