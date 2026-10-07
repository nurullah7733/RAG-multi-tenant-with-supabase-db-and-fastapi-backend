"""Transport limits count bytes even without a trustworthy Content-Length."""

import asyncio

import pytest
from starlette.requests import Request

from app.core.errors import UploadTooLargeError
from app.services.upload_body import MULTIPART_OVERHEAD_BYTES, read_document_upload


@pytest.mark.parametrize("length", [None, b"1"])
def test_stream_limit_cannot_be_bypassed_by_omitted_or_false_length(length):
    chunks_read = 0

    async def receive():
        nonlocal chunks_read
        chunks_read += 1
        return {
            "type": "http.request",
            "body": b"x" * (MULTIPART_OVERHEAD_BYTES + 2),
            "more_body": True,
        }

    headers = [(b"content-type", b"multipart/form-data; boundary=test")]
    if length is not None:
        headers.append((b"content-length", length))
    request = Request({"type": "http", "headers": headers}, receive=receive)
    with pytest.raises(UploadTooLargeError):
        asyncio.run(read_document_upload(request, 1))
    assert chunks_read == 1


def test_declared_oversize_does_not_read_stream():
    async def receive():
        pytest.fail("oversized Content-Length must be rejected before read")

    request = Request(
        {
            "type": "http",
            "headers": [
                (b"content-type", b"multipart/form-data; boundary=test"),
                (b"content-length", str(MULTIPART_OVERHEAD_BYTES + 2).encode()),
            ],
        },
        receive=receive,
    )
    with pytest.raises(UploadTooLargeError):
        asyncio.run(read_document_upload(request, 1))
