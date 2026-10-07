"""Bounded, authenticated multipart handling for document uploads."""

from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Request
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.core.errors import UploadTooLargeError, UploadValidationError

# One file plus multipart headers/boundaries; not extra business fields.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


@dataclass(frozen=True)
class UploadedDocument:
    filename: str
    content_type: str
    content: bytes


async def read_document_upload(request: Request, max_bytes: int) -> UploadedDocument:
    """Call only after membership authorization; bound actual streamed bytes."""
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "multipart/form-data"
    ):
        raise UploadValidationError("Send one file as multipart/form-data.")
    request_limit = max_bytes + MULTIPART_OVERHEAD_BYTES
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
        except ValueError:
            raise UploadValidationError("Invalid Content-Length.") from None
        if length < 0:
            raise UploadValidationError("Invalid Content-Length.")
        if length > request_limit:
            raise UploadTooLargeError()

    async def limited_stream() -> AsyncIterator[bytes]:
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > request_limit:
                raise UploadTooLargeError()
            yield chunk

    # Starlette closes partial temporary files if parsing/stream reading fails.
    parser = MultiPartParser(
        request.headers, limited_stream(), max_files=1, max_fields=0
    )
    try:
        form = await parser.parse()
    except MultiPartException:
        raise UploadValidationError(
            "Send exactly one file in the 'file' field."
        ) from None
    try:
        file = form.get("file")
        if len(form) != 1 or not isinstance(file, UploadFile):
            raise UploadValidationError("Send exactly one file in the 'file' field.")
        if file.size is not None and file.size > max_bytes:
            raise UploadTooLargeError()
        content = await file.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise UploadTooLargeError()
        return UploadedDocument(file.filename or "", file.content_type or "", content)
    finally:
        await form.close()
