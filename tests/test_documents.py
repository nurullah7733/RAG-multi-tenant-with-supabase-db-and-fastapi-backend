import json
from io import BytesIO
from zipfile import ZipFile

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.services.documents import (
    InvalidDocumentError,
    build_storage_path,
    validate_upload,
)
from tests.conftest import (
    BASE,
    DOC_ID,
    DOCX_MIME,
    ORG_A,
    ORG_B,
    PDF,
    STAMP,
    TEXT_PDF,
    USER_A,
    document,
    member_row,
    members_reply,
    storage_object_path,
    valid_docx,
)
from tests.test_processing import build_pdf


def upload(
    client: TestClient,
    name="Knowledge.pdf",
    mime="application/pdf",
    data=PDF,
):
    return client.post(BASE, files={"file": (name, data, mime)})


def test_upload_stores_object_with_tenant_scoped_path_and_inserts_row(api):
    client, requests, configure = api
    patches = []

    def create(request):
        if request.url.path == "/rest/v1/organization_members":
            return members_reply()
        if request.url.path.startswith("/storage/v1/object/"):
            path = storage_object_path(request)
            assert path.startswith(f"{ORG_A}/")
            assert path.endswith(".pdf")
            assert request.content == TEXT_PDF
            assert request.headers["content-type"] == "application/pdf"
            return httpx.Response(200, json={"Key": path})
        if request.url.path == "/rest/v1/knowledge_chunks":
            assert request.headers["prefer"] == "return=minimal"
            if request.method == "DELETE":
                assert request.url.params["organization_id"] == f"eq.{ORG_A}"
                return httpx.Response(204)
            body = json.loads(request.content)
            assert body[0]["organization_id"] == ORG_A
            assert body[0]["chunk_index"] == 0
            assert body[0]["content"].strip()
            assert body[0]["embedding"].startswith("[")
            assert len(body[0]["embedding"].split(",")) == 1536
            assert body[0]["embedding_provider"] == "openai"
            # PostgREST INSERT + return=minimal: 201 with an empty body.
            return httpx.Response(201)
        if request.method == "PATCH":
            assert request.headers["prefer"] == "return=minimal"
            assert request.url.params["organization_id"] == f"eq.{ORG_A}"
            patches.append(json.loads(request.content))
            return httpx.Response(204)
        assert request.url.path == "/rest/v1/knowledge_documents"
        assert request.headers["prefer"] == "return=representation"
        body = json.loads(request.content)[0]
        assert body["organization_id"] == ORG_A
        assert body["status"] == "uploaded"
        assert body["filename"] == "Knowledge.pdf"
        row = {**document(), **body}
        return httpx.Response(201, json=[row])

    configure(create)
    result = upload(client, data=TEXT_PDF)
    assert result.status_code == 201
    assert result.json()["organization_id"] == ORG_A
    assert result.json()["status"] == "ready"
    assert result.json()["chunk_count"] == 1
    assert [patch["status"] for patch in patches] == ["processing", "ready"]
    assert len(requests) == 8


def test_upload_metadata_failure_cleans_up_storage_object(api):
    client, requests, configure = api

    def create(request):
        if request.url.path == "/rest/v1/organization_members":
            return members_reply()
        if request.url.path.startswith("/storage/v1/object/"):
            return httpx.Response(200, json={})
        return httpx.Response(400, json={"code": "42501"})

    configure(create)
    assert upload(client).status_code == 503
    deletes = [r for r in requests if r.method == "DELETE"]
    assert len(deletes) == 1
    assert deletes[0].url.path.endswith(".pdf")


@pytest.mark.parametrize(
    "name,mime,data",
    [
        ("malware.pdf", "application/pdf", b"MZ\x90\x00 fake executable"),
        ("report.docx", "application/pdf", b"PK\x03\x04"),
        ("notes.txt", "application/msword", b"hello"),
        ("archive.zip", "application/pdf", b"PK\x03\x04"),
        ("noext", "application/pdf", b"%PDF-1.4"),
        ("empty.pdf", "application/pdf", b""),
        ("a" * 256 + ".pdf", "application/pdf", b"%PDF-1.4"),
        ("../evil.pdf", "application/pdf", b"%PDF-1.4"),
    ],
)
def test_invalid_uploads_never_reach_storage_or_database(api, name, mime, data):
    client, requests, configure = api

    def rejected(request):
        assert request.url.path in (
            "/auth/v1/user",
            "/rest/v1/organization_members",
        )
        if request.url.path == "/rest/v1/organization_members":
            return members_reply()
        return httpx.Response(500, text="storage or database must never be called")

    configure(rejected)
    result = upload(client, name=name, mime=mime, data=data)
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "invalid_document"
    assert all(
        r.url.path == "/auth/v1/user" or r.url.path == "/rest/v1/organization_members"
        for r in requests
    )


def test_oversized_upload_rejected_before_storage_or_metadata(api, monkeypatch):
    client, _requests, configure = api
    monkeypatch.setattr(settings, "max_document_size_mb", 0)
    configure(
        lambda r: (
            members_reply()
            if r.url.path == "/rest/v1/organization_members"
            else httpx.Response(500, text="must not be called")
        )
    )
    result = upload(client, data=b"%PDF-1.4 small")
    assert result.status_code == 413


def test_list_is_scoped_to_organization_and_paginated(api):
    client, _, configure = api

    def listing(request):
        if request.url.path == "/rest/v1/organization_members":
            return members_reply()
        assert request.url.path == "/rest/v1/knowledge_documents"
        assert request.url.params["organization_id"] == f"eq.{ORG_A}"
        assert request.url.params["limit"] == "2"
        assert request.url.params["offset"] == "1"
        return httpx.Response(200, json=[document()])

    configure(listing)
    result = client.get(BASE + "?limit=2&offset=1")
    assert result.status_code == 200
    assert result.json()[0]["id"] == DOC_ID


def test_non_member_cannot_list_or_upload(api):
    client, requests, configure = api

    def no_membership(request):
        assert request.url.path == "/rest/v1/organization_members"
        return httpx.Response(200, json=[])

    configure(no_membership)
    assert upload(client).status_code == 404
    assert client.get(BASE).status_code == 404
    assert all(
        r.url.path == "/rest/v1/organization_members" or r.url.path == "/auth/v1/user"
        for r in requests
    )


def test_delete_removes_storage_object_then_row(api):
    client, requests, configure = api

    def flow(request):
        if request.url.path == "/rest/v1/organization_members":
            return httpx.Response(
                200,
                json=[
                    {
                        "organization_id": ORG_A,
                        "user_id": USER_A,
                        "role": "owner",
                        "created_at": STAMP,
                    }
                ],
            )
        if request.method == "GET":
            assert request.url.params["organization_id"] == f"eq.{ORG_A}"
            return httpx.Response(200, json=[document()])
        if request.url.path.startswith("/storage/v1/object/"):
            assert request.url.path.endswith(f"{DOC_ID}/{DOC_ID}.pdf")
            return httpx.Response(200)
        assert request.method == "DELETE"
        return httpx.Response(204)

    configure(flow)
    result = client.delete(f"{BASE}/{DOC_ID}")
    assert result.status_code == 204
    assert any(
        r.method == "DELETE" and r.url.path.startswith("/storage/v1/object/")
        for r in requests
    )


def test_delete_of_document_from_other_organization_is_404(api):
    client, requests, configure = api

    def flow(request):
        if request.url.path == "/rest/v1/organization_members":
            return httpx.Response(
                200,
                json=[
                    {
                        "organization_id": ORG_A,
                        "user_id": USER_A,
                        "role": "owner",
                        "created_at": STAMP,
                    }
                ],
            )
        if request.method == "GET":
            return httpx.Response(200, json=[document(ORG_B)])
        return httpx.Response(204)

    configure(flow)
    assert client.delete(f"{BASE}/{DOC_ID}").status_code == 404
    assert not any(r.url.path.startswith("/storage/") for r in requests)


def test_missing_document_is_404(api):
    client, _, configure = api

    def flow(request):
        if request.url.path == "/rest/v1/organization_members":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[])

    configure(flow)
    assert client.delete(f"{BASE}/{DOC_ID}").status_code == 404


def test_provider_failure_during_upload_is_fail_closed(api):
    client, _, configure = api

    def fail(request):
        raise httpx.ConnectError("private provider detail", request=request)

    configure(fail)
    assert upload(client).status_code == 503


def test_unauthenticated_requests_never_reach_provider(api):
    client, requests, _ = api
    client.headers.pop("Authorization")
    assert client.get(BASE).status_code == 401
    assert requests == []


def test_validate_rejects_control_characters_and_path_traversal():
    for name in ["x\x00.pdf", "..\\evil.pdf", "a/b.pdf", "", " "]:
        with pytest.raises(InvalidDocumentError):
            validate_upload(name, "application/pdf", b"%PDF-1.4", 1024)


def test_validate_accepts_supported_formats_and_returns_format():
    fmt = validate_upload("doc.docx", DOCX_MIME, valid_docx(), 4096)
    assert fmt.extension == "docx"
    fmt = validate_upload("notes.txt", "text/plain", "héllo".encode(), 1024)
    assert fmt.extension == "txt"


def test_storage_path_is_tenant_scoped():
    path = build_storage_path(ORG_A, DOC_ID, "pdf")
    assert path.startswith(f"{ORG_A}/{DOC_ID}/")
    assert path.endswith(".pdf")


@pytest.mark.parametrize(
    "name,mime,data",
    [
        ("fake.docx", DOCX_MIME, b"PK\x03\x04"),
        ("binary.txt", "text/plain", b"MZ\x00\x01\xff"),
        ("control.txt", "text/plain", b"hello\x00world"),
        ("fake.pdf", "application/pdf", b"%PDF-1.4 no real structure"),
        ("hidden\u202e.pdf", "application/pdf", PDF),
        ("file.pdf.exe", "application/pdf", PDF),
    ],
)
def test_spoofed_upload_content_is_rejected(api, name, mime, data):
    client, requests, configure = api
    configure(lambda _: members_reply())
    assert upload(client, name, mime, data).status_code == 422
    assert not any(r.url.path.startswith("/storage/") for r in requests)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "utf-32"])
def test_unicode_text_encodings_are_validated_without_modifying_bytes(encoding):
    content = "বাংলা हिन्दी العربية hello\n".encode(encoding)
    assert (
        validate_upload("নথি.txt", "text/plain; charset=utf-8", content, 4096).extension
        == "txt"
    )


def test_docx_rejects_generic_zip_traversal_macro_and_entity_xml():
    for name, content in [
        ("../escape", "text"),
        ("word/vbaProject.bin", "macro"),
        ("word/document.xml", '<!DOCTYPE doc [<!ENTITY x "hello">]><doc>&x;</doc>'),
    ]:
        output = BytesIO()
        with ZipFile(BytesIO(valid_docx())) as source, ZipFile(output, "w") as target:
            for entry in source.infolist():
                if entry.filename != name:
                    target.writestr(entry.filename, source.read(entry))
            target.writestr(name, content)
        with pytest.raises(InvalidDocumentError):
            validate_upload("file.docx", DOCX_MIME, output.getvalue(), 10000)
    output = BytesIO()
    with ZipFile(output, "w") as target:
        target.writestr("test.txt", "not a DOCX")
    with pytest.raises(InvalidDocumentError):
        validate_upload("file.docx", DOCX_MIME, output.getvalue(), 10000)


def test_docx_rejects_compression_bomb_and_bad_crc():
    from zipfile import ZIP_DEFLATED

    output = BytesIO()
    with (
        ZipFile(BytesIO(valid_docx())) as source,
        ZipFile(output, "w", compression=ZIP_DEFLATED) as target,
    ):
        for entry in source.infolist():
            target.writestr(entry.filename, source.read(entry))
        target.writestr("word/media/bomb.bin", b"a" * 1000000)
    with pytest.raises(InvalidDocumentError):
        validate_upload("file.docx", DOCX_MIME, output.getvalue(), 10000)
    broken = valid_docx().replace(b"<w:body/>", b"<w:bady/>")
    with pytest.raises(InvalidDocumentError):
        validate_upload("file.docx", DOCX_MIME, broken, 10000)


@pytest.mark.parametrize("extra", ["another_file", "organization_id"])
def test_upload_rejects_extra_multipart_parts(api, extra):
    client, requests, configure = api
    configure(lambda _: members_reply())
    files = [("file", ("file.pdf", PDF, "application/pdf"))]
    files.append(
        (
            extra,
            ("extra.txt", b"text", "text/plain")
            if extra == "another_file"
            else (None, ORG_B),
        )
    )
    assert client.post(BASE, files=files).status_code == 422
    assert len(requests) == 2


def test_unauthenticated_and_nonmember_uploads_do_not_read_body(api):
    client, requests, configure = api
    client.headers.pop("Authorization")
    assert (
        client.post(
            BASE,
            content=b"bad multipart",
            headers={"Content-Type": "multipart/form-data"},
        ).status_code
        == 401
    )
    assert requests == []
    client.headers["Authorization"] = "Bearer user-access-token"
    configure(lambda _: httpx.Response(200, json=[]))
    assert (
        client.post(
            BASE,
            content=b"bad multipart",
            headers={"Content-Type": "multipart/form-data"},
        ).status_code
        == 404
    )


def test_upload_size_limit_exact_and_overflow(api, monkeypatch):
    client, _requests, configure = api
    monkeypatch.setattr(settings, "max_document_size_mb", 1)
    stored = []

    def flow(request):
        if request.url.path == "/rest/v1/organization_members":
            return members_reply()
        if request.url.path.startswith("/storage/"):
            stored.append(request.content)
            return httpx.Response(200, json={})
        if request.url.path == "/rest/v1/knowledge_chunks":
            return httpx.Response(204)
        if request.method == "PATCH":
            return httpx.Response(204)
        body = json.loads(request.content)[0]
        return httpx.Response(201, json=[{**document(), **body}])

    configure(flow)
    assert (
        upload(client, "file.txt", "text/plain", b"a" * (1024 * 1024)).status_code
        == 201
    )
    assert (
        upload(client, "file.txt", "text/plain", b"a" * (1024 * 1024 + 1)).status_code
        == 413
    )
    assert len(stored) == 1


def test_forged_storage_path_cannot_delete_another_tenants_file(api):
    client, requests, configure = api

    def flow(request):
        if request.url.path == "/rest/v1/organization_members":
            return members_reply()
        return httpx.Response(
            200, json=[{**document(), "storage_path": f"{ORG_B}/{DOC_ID}/{DOC_ID}.pdf"}]
        )

    configure(flow)
    assert client.delete(f"{BASE}/{DOC_ID}").status_code == 404
    assert not any(r.url.path.startswith("/storage/") for r in requests)


def test_upload_openapi_keeps_swagger_file_picker(api):
    client, _, _ = api
    operation = client.get("/openapi.json").json()["paths"][
        "/api/v1/organizations/{organization_id}/documents"
    ]["post"]
    assert (
        operation["requestBody"]["content"]["multipart/form-data"]["schema"][
            "properties"
        ]["file"]["format"]
        == "binary"
    )


def test_configuration_cannot_select_public_bucket_or_exceed_storage_ceiling():
    from pydantic import ValidationError

    from app.core.config import Settings

    for options in [
        {"knowledge_bucket_name": "public-files"},
        {"max_document_size_mb": 0},
        {"max_document_size_mb": 21},
    ]:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, openai_api_key="k", **options)


def test_upload_membership_is_rechecked_after_revocation(api):
    client, requests, configure = api
    allowed = True
    def flow(request):
        if request.url.path == "/rest/v1/organization_members":
            return members_reply() if allowed else httpx.Response(200, json=[])
        if request.url.path.startswith("/storage/"):
            return httpx.Response(200, json={})
        if request.method == "PATCH":
            return httpx.Response(204)
        body = json.loads(request.content)[0]
        return httpx.Response(201, json=[{**document(), **body}])
    configure(flow)
    assert upload(client).status_code == 201
    allowed = False
    previous = len(requests)
    assert upload(client).status_code == 404
    assert not any(r.url.path.startswith("/storage/") for r in requests[previous:])


@pytest.mark.parametrize("role", ["owner", "admin", "staff"])
def test_existing_member_roles_keep_upload_access(api, role):
    client, _, configure = api
    def flow(request):
        if request.url.path == "/rest/v1/organization_members":
            return httpx.Response(200, json=[{**member_row(), "role": role}])
        if request.url.path.startswith("/storage/"):
            return httpx.Response(200, json={})
        if request.url.path == "/rest/v1/knowledge_chunks":
            return httpx.Response(204)
        if request.method == "PATCH":
            return httpx.Response(204)
        body = json.loads(request.content)[0]
        return httpx.Response(201, json=[{**document(), **body}])
    configure(flow)
    assert upload(client, "notes.txt", "text/plain", "বাংলা hello".encode()).status_code == 201


def test_upload_of_image_only_pdf_records_safe_failure(api):
    client, requests, configure = api
    patches = []

    def flow(request):
        if request.url.path == "/rest/v1/organization_members":
            return members_reply()
        if request.url.path.startswith("/storage/"):
            return httpx.Response(200, json={})
        if request.method == "PATCH":
            patches.append(json.loads(request.content))
            return httpx.Response(204)
        assert request.method == "POST"
        return httpx.Response(201, json=[{**document(), **json.loads(request.content)[0]}])

    configure(flow)
    result = upload(client, data=build_pdf("BT ET", "0 0 m"))
    assert result.status_code == 201
    assert result.json()["status"] == "failed"
    assert "scanned" in result.json()["processing_error"]
    assert [patch["status"] for patch in patches] == ["processing", "failed"]
    assert "scanned" in patches[-1]["processing_error"]
    # Keep the stored file and row: nothing is deleted on extraction failure.
    assert not any(request.method == "DELETE" for request in requests)


def test_upload_of_unparsable_pdf_records_safe_failure(api):
    client, _, configure = api

    def flow(request):
        if request.url.path == "/rest/v1/organization_members":
            return members_reply()
        if request.url.path.startswith("/storage/"):
            return httpx.Response(200, json={})
        if request.method == "PATCH":
            payload = json.loads(request.content)
            if payload["status"] == "failed":
                assert "damaged" in payload["processing_error"]
            return httpx.Response(204)
        assert request.method == "POST"
        return httpx.Response(201, json=[{**document(), **json.loads(request.content)[0]}])

    configure(flow)
    result = upload(client, data=PDF)
    assert result.status_code == 201
    assert result.json()["status"] == "failed"
    assert "damaged" in result.json()["processing_error"]


def test_extraction_status_write_failure_returns_503(api):
    client, _, configure = api

    def flow(request):
        if request.url.path == "/rest/v1/organization_members":
            return members_reply()
        if request.url.path.startswith("/storage/"):
            return httpx.Response(200, json={})
        if request.method == "PATCH":
            return httpx.Response(400, json={"code": "42501"})
        return httpx.Response(201, json=[{**document(), **json.loads(request.content)[0]}])

    configure(flow)
    assert upload(client, data=TEXT_PDF).status_code == 503


def test_list_response_includes_chunk_count(api):
    client, _, configure = api
    row = {
        **document(),
        "knowledge_chunks": [{"count": 5}],
    }

    def flow(request):
        if request.url.path == "/rest/v1/organization_members":
            return members_reply()
        assert request.url.path == "/rest/v1/knowledge_documents"
        return httpx.Response(200, json=[row])

    configure(flow)
    result = client.get(BASE)
    assert result.status_code == 200
    assert result.json()[0]["chunk_count"] == 5


def test_reprocess_repipelines_document_and_marks_ready(api):
    client, _requests, configure = api
    patches = []

    def flow(request):
        if request.url.path == "/rest/v1/organization_members":
            return members_reply()
        if (
            request.url.path == "/rest/v1/knowledge_documents"
            and request.method == "GET"
        ):
            assert request.url.params["id"] == f"eq.{DOC_ID}"
            return httpx.Response(
                200,
                json=[{**document(), "status": "failed", "processing_error": "old"}],
            )
        if request.url.path.startswith("/storage/v1/object/"):
            assert request.method == "GET"
            return httpx.Response(200, content=TEXT_PDF)
        if request.url.path == "/rest/v1/knowledge_chunks":
            return httpx.Response(204)
        if request.method == "PATCH":
            patches.append(json.loads(request.content))
            return httpx.Response(204)
        raise AssertionError(f"unexpected {request.method} {request.url.path}")

    configure(flow)
    result = client.post(f"{BASE}/{DOC_ID}/reprocess")
    assert result.status_code == 200
    assert result.json()["status"] == "ready"
    assert result.json()["chunk_count"] == 1
    assert [patch["status"] for patch in patches] == ["processing", "ready"]


def test_reprocess_of_damaged_stored_file_marks_failed(api):
    client, _, configure = api

    def flow(request):
        if request.url.path == "/rest/v1/organization_members":
            return members_reply()
        if request.url.path == "/rest/v1/knowledge_documents":
            return httpx.Response(200, json=[document()])
        if request.url.path.startswith("/storage/v1/object/"):
            return httpx.Response(200, content=b"corrupted-not-a-pdf")
        if request.method == "PATCH":
            payload = json.loads(request.content)
            if payload["status"] == "failed":
                assert payload["processing_error"]
            return httpx.Response(204)
        raise AssertionError(f"unexpected {request.method} {request.url.path}")

    configure(flow)
    result = client.post(f"{BASE}/{DOC_ID}/reprocess")
    assert result.status_code == 200
    assert result.json()["status"] == "failed"
    assert result.json()["processing_error"]


def test_reprocess_of_forged_storage_path_is_blocked(api):
    client, requests, configure = api

    def flow(request):
        if request.url.path == "/rest/v1/organization_members":
            return members_reply()
        if request.url.path == "/rest/v1/knowledge_documents":
            return httpx.Response(
                200,
                json=[{**document(), "storage_path": f"{ORG_B}/{DOC_ID}/{DOC_ID}.pdf"}],
            )
        raise AssertionError("storage must never be reached for a forged path")

    configure(flow)
    assert client.post(f"{BASE}/{DOC_ID}/reprocess").status_code == 404
    assert not any(r.url.path.startswith("/storage/") for r in requests)


def test_reprocess_of_other_organizations_document_is_404(api):
    client, _, configure = api

    def flow(request):
        if request.url.path == "/rest/v1/organization_members":
            return members_reply()
        return httpx.Response(200, json=[])

    configure(flow)
    assert client.post(f"{BASE}/{DOC_ID}/reprocess").status_code == 404
