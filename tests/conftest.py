"""Isolated test configuration and the shared HTTP-boundary fixture."""

import os
from collections.abc import Callable
from io import BytesIO
from zipfile import ZipFile

os.environ["DEBUG"] = "false"
os.environ["SUPABASE_URL"] = "https://supabase.example.test"
os.environ["SUPABASE_PUBLISHABLE_KEY"] = "sb_publishable_test_only"
os.environ["OPENAI_API_KEY"] = "test-embedding-key"

import httpx
import pytest
from fastapi.testclient import TestClient

from app.integrations.supabase import get_http_client
from app.main import app
from app.services.embeddings import get_embedding_provider
from tests.test_processing import build_pdf

USER_A = "10000000-0000-0000-0000-000000000001"
ORG_A = "20000000-0000-0000-0000-000000000001"
ORG_B = "20000000-0000-0000-0000-000000000002"
DOC_ID = "30000000-0000-0000-0000-000000000001"
STAMP = "2026-10-05T00:00:00Z"
BASE = f"/api/v1/organizations/{ORG_A}/documents"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\nxref\n0 1\n0000000000 65535 f \ntrailer\n<< /Root 1 0 R /Size 1 >>\nstartxref\n51\n%%EOF\n"
TEXT_PDF = build_pdf(
    "BT /F1 14 Tf 72 720 Td (Example knowledge text) Tj ET",
)


def valid_docx():
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>',
        )
    return output.getvalue()


def document(org: str = ORG_A) -> dict:
    return {
        "id": DOC_ID,
        "organization_id": org,
        "filename": "Knowledge.pdf",
        "storage_path": f"{org}/{DOC_ID}/{DOC_ID}.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 11,
        "status": "uploaded",
        "processing_error": None,
        "created_by": USER_A,
        "created_at": STAMP,
        "updated_at": STAMP,
    }


def member_row(org: str = ORG_A) -> dict:
    return {
        "organization_id": org,
        "user_id": USER_A,
        "role": "owner",
        "created_at": STAMP,
    }


def members_reply(org: str = ORG_A) -> httpx.Response:
    return httpx.Response(200, json=[member_row(org)])


def storage_object_path(request: httpx.Request) -> str:
    return request.url.path.removeprefix("/storage/v1/object/knowledge-documents/")


@pytest.fixture
def api():
    requests: list[httpx.Request] = []
    handler: Callable = lambda request: httpx.Response(200, json=[])

    class FakeEmbeddingProvider:
        name = "openai"
        model = "text-embedding-3-small"

        async def embed_many(self, texts: list[str]) -> list[list[float]]:
            return [[0.5] * 1536 for _ in texts]

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["apikey"] == "sb_publishable_test_only"
        assert request.headers["authorization"] == "Bearer user-access-token"
        if request.url.path == "/auth/v1/user":
            return httpx.Response(
                200, json={"id": USER_A, "user_metadata": {"role": "platform_admin"}}
            )
        return handler(request)

    async def client_override():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            yield client

    def configure(callback):
        nonlocal handler
        handler = callback

    app.dependency_overrides[get_http_client] = client_override
    app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider()
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer user-access-token"
        yield client, requests, configure
    app.dependency_overrides.clear()
