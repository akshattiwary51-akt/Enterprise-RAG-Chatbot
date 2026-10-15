import io
import os
from pathlib import Path
import subprocess

import pytest

from app.demo import make_csv, make_docx, make_pdf, make_xlsx
from app.extensions import db
from app.models import Document, ParentPassage
from app.services.retrieval.vectorstore import VectorStoreUnavailable
from conftest import make_app, upload


def assert_windows_private_acl(path: Path) -> None:
    result = subprocess.run(
        ["icacls.exe", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    acl = result.stdout.casefold()
    assert "owner rights:" in acl
    assert "nt authority\\system:" in acl
    assert "builtin\\administrators:" in acl
    assert "authenticated users:" not in acl
    assert "builtin\\users:" not in acl
    assert "everyone:" not in acl


# ---- supported formats ------------------------------------------------------------------------
@pytest.mark.parametrize(
    "filename,data",
    [
        ("notes.txt", b"The quarterly offsite is planned for the second week of March."),
        ("readme.md", b"# Title\n\nSome intro text.\n\n## Details\n\nMore details about the process."),
        ("table.csv", make_csv([("Name", "Dept", "Amount"), ("Ann", "Ops", 10), ("Bo", "Ops", 20)])),
        ("book.xlsx", make_xlsx([("Name", "Amount"), ("Ann", 10), ("Bo", 20)], "Data")),
        ("memo.docx", make_docx([("Heading 1", "Policy"), ("Normal", "Employees must badge in daily."), ("Heading 2", "Remote"), ("Normal", "Remote work needs approval.")])),
        ("report.pdf", make_pdf(["First page about apples.", "Second page about oranges."])),
    ],
    ids=["notes.txt", "readme.md", "table.csv", "book.xlsx", "memo.docx", "report.pdf"],
)
def test_supported_formats_ingest_to_ready(client, auth, filename, data):
    r = upload(client, auth("admin"), filename, data, ["hr"])
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["status"] == "READY" and body["chunk_count"] >= 1, body


def test_pdf_pages_docx_headings_and_sheet_rows_are_preserved(client, auth, services):
    h = auth("admin")
    pdf = upload(client, h, "r.pdf", make_pdf(["Alpha page one.", "Bravo page two."]), ["hr"]).get_json()["id"]
    docx = upload(client, h, "m.docx", make_docx([("Heading 1", "Policy"), ("Heading 2", "Remote"), ("Normal", "Remote work needs approval.")]), ["hr"]).get_json()["id"]
    xlsx = upload(client, h, "b.xlsx", make_xlsx([("Name", "Amount"), ("Ann", 10), ("Bo", 20)], "Budget"), ["hr"]).get_json()["id"]
    loc = lambda d: [p.locator for p in ParentPassage.query.filter_by(document_id=d).order_by(ParentPassage.ordinal)]
    assert [l["page"] for l in loc(pdf)] == [1, 2]
    assert any(l.get("section") == "Policy > Remote" for l in loc(docx))
    assert loc(xlsx)[0]["sheet"] == "Budget" and loc(xlsx)[0]["rows"] == "2-3"


def test_excel_rows_keep_headers_with_values(client, auth):
    rows = [("Employee", "Department", "Salary")] + [(f"Person{i}", "Eng" if i % 2 else "HR", 1000 + i) for i in range(60)]
    doc_id = upload(client, auth("admin"), "pay.xlsx", make_xlsx(rows, "Pay"), ["hr"]).get_json()["id"]
    passages = ParentPassage.query.filter_by(document_id=doc_id).all()
    text = "\n".join(p.text for p in passages)
    assert "Sheet: Pay | Row 2 | Employee: Person0; Department: HR; Salary: 1000" in text
    for line in text.split("\n"):  # every row line is self-contained: header names travel with every value
        assert line.count("Employee:") == 1 and line.count("Salary:") == 1


def test_excel_numbers_dates_and_formulas(client, auth, app):
    from datetime import date
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Item", "Cost", "Due", "Total"])
    ws.append(["Laptop", 1299.5, date(2026, 3, 1), "=B2*2"])
    buf = io.BytesIO()
    wb.save(buf)
    doc_id = upload(client, auth("admin"), "f.xlsx", buf.getvalue(), ["hr"]).get_json()["id"]
    text = ParentPassage.query.filter_by(document_id=doc_id).first().text
    assert "Cost: 1299.5" in text and "Due: 2026-03-01" in text
    assert "=B2*2" not in text  # formulas are never evaluated or echoed; uncached values are simply absent


# ---- rejection paths --------------------------------------------------------------------------
def test_empty_and_unsupported_and_mismatched_uploads_rejected(client, auth):
    h = auth("admin")
    assert upload(client, h, "empty.txt", b"", ["hr"]).status_code == 400
    assert upload(client, h, "run.exe", b"MZ\x90\x00", ["hr"]).status_code == 400
    assert upload(client, h, "noext", b"hello", ["hr"]).status_code == 400
    assert upload(client, h, "fake.pdf", b"this is not a pdf", ["hr"]).status_code == 400
    assert upload(client, h, "fake.docx", b"not a zip", ["hr"]).status_code == 400
    assert upload(client, h, "fake.xlsx", b"PK\x03\x04garbage", ["hr"]).status_code == 400
    assert upload(client, h, "bin.txt", b"abc\x00def", ["hr"]).status_code == 400
    assert Document.query.count() == 0


def test_path_traversal_and_dangerous_filenames_rejected(client, auth):
    h = auth("admin")
    for name in ("../../etc/passwd.txt", "..\\win.txt", "a/b.txt", ".hidden.txt", "x" * 300 + ".txt"):
        assert upload(client, h, name, b"hello world", ["hr"]).status_code == 400, name


def test_storage_name_is_server_generated_and_private(client, auth, app):
    r = upload(client, auth("admin"), "Salaries 2026 (final).txt", b"payroll details here", ["hr"]).get_json()
    doc = db.session.get(Document, r["id"])
    assert "Salaries" not in doc.storage_name and doc.storage_name.endswith(".txt")
    path = Path(app.config["UPLOAD_DIR"]) / doc.storage_name
    assert path.exists()
    if os.name != "nt":
        assert (path.stat().st_mode & 0o077) == 0  # owner-only permissions
    else:
        assert_windows_private_acl(path)
    assert "storage_name" not in str(r)


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL behavior only")
def test_existing_upload_files_are_secured_on_startup(tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    existing_file = upload_dir / "previous-upload.txt"
    existing_file.write_text("private", encoding="utf-8")
    subprocess.run(
        ["icacls.exe", str(existing_file), "/grant", "*S-1-1-0:(M)"],
        capture_output=True,
        text=True,
        check=True,
    )

    make_app(tmp_path, UPLOAD_DIR=str(upload_dir))

    assert_windows_private_acl(existing_file)


def test_macro_workbook_rejected(client, auth):
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", "<workbook/>")
        z.writestr("xl/vbaProject.bin", b"macro")
    assert upload(client, auth("admin"), "m.xlsx", buf.getvalue(), ["hr"]).status_code == 400


def test_zip_bomb_rejected(client, auth, app):
    import zipfile

    app.config["MAX_ZIP_UNCOMPRESSED_MB"] = 1
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"0" * (5 * 1024 * 1024))
    assert upload(client, auth("admin"), "bomb.docx", buf.getvalue(), ["hr"]).status_code == 400


def test_oversized_upload_rejected(client, auth, app):
    app.config["MAX_CONTENT_LENGTH"] = 2048
    r = upload(client, auth("admin"), "big.txt", b"a" * 10_000, ["hr"])
    assert r.status_code == 413
    assert Document.query.count() == 0


def test_corrupt_documents_fail_safely(client, auth):
    h = auth("admin")
    r = upload(client, h, "corrupt.pdf", b"%PDF-1.4\nthis is garbage, not a pdf body\n%%EOF", ["hr"])
    assert r.status_code == 201 and r.get_json()["status"] == "FAILED" and r.get_json()["error_message"]
    blank = upload(client, h, "blank.pdf", make_pdf([""]), ["hr"]).get_json()
    assert blank["status"] == "FAILED" and "No extractable text" in blank["error_message"]
    header_only = upload(client, h, "h.csv", b"A,B,C\n", ["hr"]).get_json()
    assert header_only["status"] == "FAILED"
    ws_only = upload(client, h, "ws.txt", b"   \n\n  \n", ["hr"]).get_json()
    assert ws_only["status"] == "FAILED"
    for d in Document.query.filter_by(status="FAILED"):
        assert ParentPassage.query.filter_by(document_id=d.id).count() == 0
        assert d.chunk_count == 0


def test_duplicate_upload_by_same_user_conflicts(client, auth):
    h = auth("admin")
    assert upload(client, h, "a.txt", b"identical content for dedupe", ["hr"]).status_code == 201
    r = upload(client, h, "renamed.txt", b"identical content for dedupe", ["hr"])
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "duplicate_document"
    assert Document.query.count() == 1


# ---- lifecycle / rollback / idempotency ---------------------------------------------------------
class FailingWrites:
    """Wraps the real store; add_chunks writes half the chunks to Chroma, then fails."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def add_chunks(self, chunks, embeddings):
        half = max(1, len(chunks) // 2)
        self.inner.add_chunks(chunks[:half], embeddings[:half])
        raise VectorStoreUnavailable("simulated outage mid-write")


def test_partial_ingestion_is_cleaned_up_and_retry_is_idempotent(client, auth, services):
    real = services.vector_store
    services.vector_store = FailingWrites(real)
    text = ("Paragraph about policy. " * 80).encode()
    r = upload(client, auth("admin"), "long.txt", text, ["hr"])
    body = r.get_json()
    assert r.status_code == 201 and body["status"] == "FAILED" and "Vector database error" in body["error_message"]
    doc_id = body["id"]
    assert real.count_document_chunks(doc_id) == 0  # half-written vectors removed
    assert ParentPassage.query.filter_by(document_id=doc_id).count() == 0

    services.vector_store = real  # outage over: retry
    for _ in range(2):  # twice -> must not duplicate chunks
        r = client.post(f"/api/documents/{doc_id}/reindex", headers=auth("admin"))
        assert r.status_code == 200 and r.get_json()["status"] == "READY"
        assert real.count_document_chunks(doc_id) == r.get_json()["chunk_count"] > 0
    assert db.session.get(Document, doc_id).index_version == 2


def test_vector_store_down_at_upload_is_a_clean_failure(client, auth, services):
    class Down:
        def __getattr__(self, name):
            def boom(*a, **k):
                raise VectorStoreUnavailable("down")
            return boom

    services.vector_store = Down()
    r = upload(client, auth("admin"), "n.txt", b"some content to index here", ["hr"])
    assert r.status_code == 201 and r.get_json()["status"] == "FAILED"


def test_embedding_outage_marks_failed_not_ready(client, auth, services):
    from app.services.retrieval.embeddings import EmbeddingUnavailable

    def boom(texts):
        raise EmbeddingUnavailable("Embedding service error (Timeout)")

    services.embedder.embed_documents = boom
    r = upload(client, auth("admin"), "n.txt", b"some content to index here", ["hr"]).get_json()
    assert r["status"] == "FAILED" and "Embedding" in r["error_message"]


def test_concurrent_claim_is_exclusive(client, auth, app):
    from app.services.ingestion import pipeline

    doc_id = upload(client, auth("admin"), "a.txt", b"hello hello hello world", ["hr"]).get_json()["id"]
    doc = db.session.get(Document, doc_id)
    doc.status = "PROCESSING"  # another worker holds the claim
    db.session.commit()
    pipeline.ingest_document(doc_id)
    assert db.session.get(Document, doc_id).status == "PROCESSING"  # second worker backed off


def test_delete_removes_vectors_passages_file_and_row(client, auth, services, app):
    h = auth("admin")
    d = upload(client, h, "gone.txt", b"ephemeral content about unicorns", ["hr"]).get_json()
    storage = db.session.get(Document, d["id"]).storage_name
    assert services.vector_store.count_document_chunks(d["id"]) > 0
    assert client.delete(f"/api/documents/{d['id']}", headers=h).status_code == 200
    assert services.vector_store.count_document_chunks(d["id"]) == 0
    assert ParentPassage.query.filter_by(document_id=d["id"]).count() == 0
    assert db.session.get(Document, d["id"]) is None
    assert not (Path(app.config["UPLOAD_DIR"]) / storage).exists()
    assert client.get(f"/api/documents/{d['id']}", headers=h).status_code == 404


def test_failed_delete_blocks_retrieval_and_is_retryable(client, auth, services, corpus):
    h = auth("admin")
    real = services.vector_store

    class DeleteFails:
        def __getattr__(self, name):
            return getattr(real, name)

        def delete_document(self, document_id):
            raise VectorStoreUnavailable("down")

    services.vector_store = DeleteFails()
    r = client.delete(f"/api/documents/{corpus['hr_policy']}", headers=h)
    assert r.status_code == 503 and r.get_json()["error"]["code"] == "deletion_incomplete"
    assert db.session.get(Document, corpus["hr_policy"]).status == "DELETING"
    services.vector_store = real
    assert client.delete(f"/api/documents/{corpus['hr_policy']}", headers=h).status_code == 200  # retry works


def test_chunk_size_configuration_is_respected(client, auth, app):
    app.config.update(CHUNK_SIZE=200, CHUNK_OVERLAP=20, PARENT_CHUNK_SIZE=400)
    text = ("Sentence number one about topics. " * 60).encode()
    d = upload(client, auth("admin"), "c.txt", text, ["hr"]).get_json()
    assert d["chunk_count"] >= 8
