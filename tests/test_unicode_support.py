"""Phase 2.4: Unicode filenames and contents survive every implemented layer."""

import json

import httpx
import pytest

from tests.conftest import DOCX_MIME, ORG_A, STAMP, TEXT_PDF, USER_A, document
from tests.test_documents import members_reply, upload, valid_docx

BENGALI = "দাম তালিকা.txt"
BENGALI_TEXT = "মাসিক ফি: ৪৯ ডলার\nসময়: সকাল ৯টা থেকে ৬টা\n"
ARABIC_PDF = "سياسة-الاشتراك.pdf"
SPANISH_DOCX = "política de devolución.docx"
EMOJI_TXT = "মেনু 🍕.txt"


@pytest.fixture
def documents_api(api):
    return api


def metadata_row(**overrides: object) -> dict:
    return {**document(ORG_A), "created_by": USER_A, **overrides}


def test_unicode_filename_and_content_survive_upload_json_and_metadata(documents_api):
    client, _requests, configure = documents_api
    inserted_bodies: list[dict] = []

    def flow(request):
        if request.url.path == "/rest/v1/organization_members":
            return members_reply()
        if request.url.path.startswith("/storage/v1/object/"):
            assert request.content.decode("utf-8") == BENGALI_TEXT
            return httpx.Response(200, json={"Key": "stored"})
        if request.url.path == "/rest/v1/knowledge_chunks":
            return httpx.Response(204)
        if request.method == "PATCH":
            return httpx.Response(204)
        inserted_bodies.append(json.loads(request.content)[0])
        return httpx.Response(
            201, json=[{**metadata_row(), **inserted_bodies[-1]}]
        )

    configure(flow)
    result = upload(client, BENGALI, "text/plain", BENGALI_TEXT.encode("utf-8"))
    assert result.status_code == 201
    assert result.headers["content-type"].startswith("application/json")
    payload = result.json()
    assert payload["filename"] == BENGALI
    assert payload["status"] == "ready"
    assert inserted_bodies[0]["filename"] == BENGALI


def test_unicode_filenames_accepted_for_every_supported_format(documents_api):
    client, _, configure = documents_api
    cases = [
        (BENGALI, "text/plain", BENGALI_TEXT.encode("utf-8")),
        (ARABIC_PDF, "application/pdf", TEXT_PDF),
        (SPANISH_DOCX, DOCX_MIME, valid_docx()),
    ]

    def flow(request):
        if request.url.path == "/rest/v1/organization_members":
            return members_reply()
        if request.method == "PATCH":
            return httpx.Response(204)
        if request.url.path == "/rest/v1/knowledge_chunks":
            return httpx.Response(204)
        if request.url.path.startswith("/storage/"):
            return httpx.Response(200, json={})
        return httpx.Response(
            201, json=[{**metadata_row(), **json.loads(request.content)[0]}]
        )

    configure(flow)
    for name, mime, data in cases:
        assert upload(client, name, mime, data).status_code == 201, name


def test_list_response_preserves_unicode_metadata(documents_api):
    client, _, configure = documents_api
    doc_id = "55000000-0000-0000-0000-000000000001"
    stored_row = metadata_row(
        id=doc_id,
        filename=EMOJI_TXT,
        mime_type="text/plain",
        storage_path=f"{ORG_A}/{doc_id}/{doc_id}.txt",
        created_at=STAMP,
    )

    def flow(request):
        if request.url.path == "/rest/v1/organization_members":
            return members_reply()
        assert request.url.path == "/rest/v1/knowledge_documents"
        return httpx.Response(200, json=[stored_row])

    configure(flow)
    result = client.get(f"/api/v1/organizations/{ORG_A}/documents")
    assert result.status_code == 200
    assert [row["filename"] for row in result.json()] == [EMOJI_TXT]


def test_unicode_processing_error_is_returned_unchanged(documents_api):
    client, _, configure = documents_api

    def flow(request):
        if request.url.path == "/rest/v1/organization_members":
            return members_reply()
        if request.url.path.startswith("/storage/"):
            return httpx.Response(200, json={})
        if request.url.path == "/rest/v1/knowledge_chunks":
            return httpx.Response(204)
        if request.method == "PATCH":
            return httpx.Response(204)
        return httpx.Response(
            201, json=[{**metadata_row(), **json.loads(request.content)[0]}]
        )

    configure(flow)
    assert (
        upload(client, BENGALI, "text/plain", BENGALI_TEXT.encode("utf-8")).status_code
        == 201
    )

    failed_row = metadata_row(
        status="failed", processing_error="সম্ভবত স্ক্যান করা PDF"
    )

    def listing(request):
        if request.url.path == "/rest/v1/organization_members":
            return members_reply()
        return httpx.Response(200, json=[failed_row])

    configure(listing)
    rows = client.get(f"/api/v1/organizations/{ORG_A}/documents").json()
    assert rows[0]["processing_error"] == "সম্ভবত স্ক্যান করা PDF"
    assert rows[0]["status"] == "failed"
