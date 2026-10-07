from typing import Annotated, TypeVar
from uuid import UUID

from fastapi import Depends
from pydantic import TypeAdapter, ValidationError

from app.core.auth import AuthenticatedUser, get_current_user
from app.core.errors import ProviderUnavailableError, ResourceNotFoundError
from app.integrations.supabase import SupabaseGateway, get_gateway
from app.schemas.documents import KnowledgeDocument
from app.services.chunking import TextChunk
from app.services.documents import (
    build_storage_path,
    ensure_document_belongs_to_organization,
    ensure_document_storage_path,
)
from app.services.embeddings import vector_literal

T = TypeVar("T")

_COLUMNS = (
    "id,organization_id,filename,storage_path,mime_type,size_bytes,"
    "status,processing_error,created_by,created_at,updated_at,"
    "knowledge_chunks(count)"
)


def parse_response(adapter: TypeAdapter[T], data: object) -> T:
    try:
        return adapter.validate_python(data)
    except ValidationError:
        raise ProviderUnavailableError() from None


def _with_chunk_counts(rows: object) -> object:
    """Flatten PostgREST embed counts into chunk_count fields."""
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                embedded = row.pop("knowledge_chunks", None)
                row["chunk_count"] = (
                    embedded[0].get("count", 0)
                    if isinstance(embedded, list) and embedded
                    else 0
                )
    return rows


class DocumentRepository:
    def __init__(self, gateway: SupabaseGateway, user: AuthenticatedUser) -> None:
        self.gateway = gateway
        self.user = user

    async def create(
        self,
        organization_id: UUID,
        document_id: UUID,
        filename: str,
        extension: str,
        mime_type: str,
        size_bytes: int,
        content: bytes,
    ) -> KnowledgeDocument:
        storage_path = build_storage_path(organization_id, document_id, extension)
        # Storage first: the row only exists once the object is persisted.
        await self.gateway.storage_upload(
            storage_path, content, mime_type, self.user.token
        )
        try:
            result = await self.gateway.request(
                "POST",
                "/rest/v1/knowledge_documents",
                self.user.token,
                body=[
                    {
                        "id": str(document_id),
                        "organization_id": str(organization_id),
                        "filename": filename,
                        "storage_path": storage_path,
                        "mime_type": mime_type,
                        "size_bytes": size_bytes,
                        "status": "uploaded",
                        "created_by": str(self.user.user_id),
                    }
                ],
                extra_headers={"Prefer": "return=representation"},
            )
        except Exception:
            # Do not leave an unreachable object behind on metadata failure.
            await self.gateway.storage_delete(storage_path, self.user.token)
            raise
        rows = parse_response(
            TypeAdapter(list[KnowledgeDocument]), _with_chunk_counts(result)
        )
        if len(rows) != 1 or rows[0].id != document_id:
            raise ProviderUnavailableError()
        ensure_document_belongs_to_organization(rows[0], organization_id)
        ensure_document_storage_path(rows[0])
        return rows[0]

    async def list_for_organization(
        self, organization_id: UUID, limit: int, offset: int
    ) -> list[KnowledgeDocument]:
        result = await self.gateway.request(
            "GET",
            "/rest/v1/knowledge_documents",
            self.user.token,
            params={
                "select": _COLUMNS,
                "organization_id": f"eq.{organization_id}",
                "order": "created_at.desc,id.asc",
                "limit": limit,
                "offset": offset,
            },
        )
        rows = parse_response(
            TypeAdapter(list[KnowledgeDocument]), _with_chunk_counts(result)
        )
        for row in rows:
            ensure_document_belongs_to_organization(row, organization_id)
            ensure_document_storage_path(row)
        return rows

    async def get(self, organization_id: UUID, document_id: UUID) -> KnowledgeDocument:
        result = await self.gateway.request(
            "GET",
            "/rest/v1/knowledge_documents",
            self.user.token,
            params={
                "select": _COLUMNS,
                "organization_id": f"eq.{organization_id}",
                "id": f"eq.{document_id}",
                "limit": 1,
            },
        )
        rows = parse_response(
            TypeAdapter(list[KnowledgeDocument]), _with_chunk_counts(result)
        )
        if not rows or rows[0].id != document_id:
            raise ResourceNotFoundError()
        ensure_document_belongs_to_organization(rows[0], organization_id)
        ensure_document_storage_path(rows[0])
        return rows[0]

    async def update_processing_state(
        self,
        organization_id: UUID,
        document_id: UUID,
        status: str,
        processing_error: str | None,
    ) -> None:
        await self.gateway.request(
            "PATCH",
            "/rest/v1/knowledge_documents",
            self.user.token,
            params={
                "id": f"eq.{document_id}",
                "organization_id": f"eq.{organization_id}",
            },
            body={"status": status, "processing_error": processing_error},
            extra_headers={"Prefer": "return=minimal"},
        )

    async def replace_chunks(
        self,
        organization_id: UUID,
        document_id: UUID,
        chunks: list[TextChunk],
        vectors: list[list[float]],
        provider_name: str,
        model_name: str,
    ) -> None:
        """Idempotent chunk replacement: reprocessing never duplicates."""
        if len(chunks) != len(vectors) or not chunks:
            raise ProviderUnavailableError()
        await self.gateway.request(
            "DELETE",
            "/rest/v1/knowledge_chunks",
            self.user.token,
            params={
                "document_id": f"eq.{document_id}",
                "organization_id": f"eq.{organization_id}",
            },
            extra_headers={"Prefer": "return=minimal"},
        )
        await self.gateway.request(
            "POST",
            "/rest/v1/knowledge_chunks",
            self.user.token,
            body=[
                {
                    "organization_id": str(organization_id),
                    "document_id": str(document_id),
                    "chunk_index": index,
                    "content": chunk.content,
                    "page_number": chunk.page_number,
                    "section_title": None,
                    "embedding": vector_literal(vectors[index]),
                    "embedding_provider": provider_name,
                    "embedding_model": model_name,
                }
                for index, chunk in enumerate(chunks)
            ],
            extra_headers={"Prefer": "return=minimal"},
        )

    async def storage_download(self, object_path: str) -> bytes:
        return await self.gateway.storage_download(object_path, self.user.token)

    async def delete(self, organization_id: UUID, document_id: UUID) -> None:
        document = await self.get(organization_id, document_id)
        ensure_document_belongs_to_organization(document, organization_id)
        # The storage path is re-derived from the verified row, never from input.
        await self.gateway.storage_delete(document.storage_path, self.user.token)
        await self.gateway.request(
            "DELETE",
            "/rest/v1/knowledge_documents",
            self.user.token,
            params={
                "id": f"eq.{document.id}",
                "organization_id": f"eq.{organization_id}",
            },
        )


def get_document_repository(
    gateway: Annotated[SupabaseGateway, Depends(get_gateway)],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> DocumentRepository:
    return DocumentRepository(gateway, user)
