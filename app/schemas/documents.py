from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

DocumentStatus = Literal["uploaded", "processing", "ready", "failed"]


class KnowledgeDocument(BaseModel):
    id: UUID
    organization_id: UUID
    filename: str
    storage_path: str
    mime_type: str
    size_bytes: int
    status: DocumentStatus
    processing_error: str | None = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    chunk_count: int = 0
