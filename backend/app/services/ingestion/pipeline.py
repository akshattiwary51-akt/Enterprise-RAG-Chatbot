"""Document lifecycle: store -> PENDING -> PROCESSING -> READY | FAILED, and secure deletion.

Invariants:
  * A document is retrievable only while status == READY *and* the chunk `index_version`
    stored in the vector index equals `documents.index_version` (checked by the RAG service).
  * Ingestion is idempotent: chunk IDs are deterministic and old chunks are removed first, and
    the PENDING/FAILED/READY -> PROCESSING transition is an atomic compare-and-set so two workers
    cannot index the same document at once.
  * Any failure removes partially indexed chunks and parent passages before reporting FAILED.
  * Logs/audit events carry ids and counts only -- never document text.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from pathlib import Path

from flask import current_app
from sqlalchemy import update

from ...core.metrics import metrics
from ...extensions import db
from ...models import Document, DocStatus, ParentPassage
from ..audit import service as audit
from ..container import get_services
from ..retrieval.embeddings import EmbeddingUnavailable
from ..retrieval.vectorstore import EmbeddingConfigMismatch, VectorStoreUnavailable
from .chunking import build_chunks
from .parsers import IngestionError, parse_file
from .validation import ALLOWED_EXTENSIONS

log = logging.getLogger("app.ingestion")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def upload_path(storage_name: str) -> Path:
    root = Path(current_app.config["UPLOAD_DIR"]).resolve()
    path = (root / storage_name).resolve()
    if root not in path.parents:  # defence in depth; storage_name is server-generated
        raise ValueError("Unsafe storage path")
    return path


def store_file(data: bytes, file_type: str) -> str:
    """Write bytes to the private upload dir under a random name; return the storage name."""
    ext = next(e for e, t in ALLOWED_EXTENSIONS.items() if t == file_type)
    storage_name = f"{uuid.uuid4().hex}{ext}"
    root = Path(current_app.config["UPLOAD_DIR"])
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".{storage_name}.part"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, root / storage_name)
    return storage_name


def _claim_for_processing(document_id: str, recover_processing: bool = False) -> bool:
    claimable_statuses = [DocStatus.PENDING.value, DocStatus.FAILED.value, DocStatus.READY.value]
    if recover_processing:
        claimable_statuses.append(DocStatus.PROCESSING.value)
    res = db.session.execute(
        update(Document)
        .where(
            Document.id == document_id,
            Document.status.in_(claimable_statuses),
        )
        .values(status=DocStatus.PROCESSING.value, error_message=None)
    )
    db.session.commit()
    return res.rowcount == 1


def _cleanup_index(document_id: str) -> None:
    """Best-effort removal of vectors + parent passages. Never raises."""
    try:
        get_services().vector_store.delete_document(document_id)
    except Exception:
        log.exception("Vector cleanup failed for document %s", document_id)
    try:
        ParentPassage.query.filter_by(document_id=document_id).delete()
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception("Passage cleanup failed for document %s", document_id)


def _fail(doc: Document, message: str) -> Document:
    db.session.rollback()
    _cleanup_index(doc.id)
    doc = db.session.get(Document, doc.id)
    doc.status = DocStatus.FAILED.value
    doc.error_message = message[:480]
    doc.chunk_count = 0
    db.session.commit()
    metrics.inc("ingestion_failure_total")
    audit.record("document.ingest", result="failure", target_type="document", target_id=doc.id,
                 details={"reason": message[:200]})
    return doc


def ingest_document(document_id: str, recover_processing: bool = False) -> Document | None:
    """(Re-)index a document. Returns the refreshed Document (READY or FAILED)."""
    cfg = current_app.config
    started = time.perf_counter()
    if not _claim_for_processing(document_id, recover_processing):
        doc = db.session.get(Document, document_id)
        return doc  # already processing/deleting/missing: do nothing (idempotent)
    doc = db.session.get(Document, document_id)
    services = get_services()
    new_version = doc.index_version + 1

    try:
        path = upload_path(doc.storage_name)
        if not path.exists():
            raise IngestionError("Stored file is missing")
        segments = parse_file(path, doc.file_type, cfg["MAX_SPREADSHEET_ROWS"])
        parents, chunks = build_chunks(
            segments, doc.id, new_version, cfg["CHUNK_SIZE"], cfg["CHUNK_OVERLAP"], cfg["PARENT_CHUNK_SIZE"]
        )
        if not chunks:
            raise IngestionError("No indexable content was produced")

        vectors: list[list[float]] = []
        for start in range(0, len(chunks), 64):
            vectors.extend(services.embedder.embed_documents([c.text for c in chunks[start : start + 64]]))
        if len(vectors) != len(chunks):
            raise IngestionError("Embedding service returned an unexpected number of vectors")

        # Replace old index content, then write the new content.
        services.vector_store.delete_document(doc.id)
        ParentPassage.query.filter_by(document_id=doc.id).delete()
        db.session.add_all(
            ParentPassage(id=p.id, document_id=doc.id, index_version=new_version, ordinal=p.ordinal,
                          locator=p.locator, text=p.text)
            for p in parents
        )
        db.session.flush()
        services.vector_store.add_chunks(chunks, vectors)

        # READY is committed only after everything above succeeded.
        doc.index_version = new_version
        doc.chunk_count = len(chunks)
        doc.status = DocStatus.READY.value
        doc.error_message = None
        db.session.commit()
    except IngestionError as exc:
        return _fail(doc, str(exc))
    except EmbeddingUnavailable as exc:
        return _fail(doc, f"Embedding failed: {exc}")
    except EmbeddingConfigMismatch as exc:
        return _fail(doc, str(exc))
    except VectorStoreUnavailable as exc:
        return _fail(doc, f"Vector database error: {exc}")
    except Exception:
        ref = uuid.uuid4().hex[:8]
        log.exception("Unexpected ingestion error (document=%s ref=%s)", document_id, ref)
        return _fail(doc, f"Unexpected processing error (reference {ref})")

    metrics.inc("ingestion_success_total")
    metrics.observe("ingestion_seconds", time.perf_counter() - started)
    log.info("Indexed document=%s type=%s chunks=%d version=%d", doc.id, doc.file_type, len(chunks), new_version)
    audit.record("document.ingest", target_type="document", target_id=doc.id,
                 details={"chunks": len(chunks), "version": new_version})
    return doc


class DeletionFailed(RuntimeError):
    pass


def delete_document(doc: Document) -> None:
    """Block retrieval first (DELETING), remove vectors, passages and the source file, then the row.

    If vector deletion fails the document stays in DELETING -- unretrievable, retry-able -- rather
    than risking orphaned searchable chunks.
    """
    doc_id, storage_name = doc.id, doc.storage_name
    doc.status = DocStatus.DELETING.value
    db.session.commit()
    try:
        services = get_services()
        services.vector_store.delete_document(doc_id)
        remaining = None
        try:
            remaining = services.vector_store.count_document_chunks(doc_id)
        except NotImplementedError:
            pass
        if remaining:
            raise DeletionFailed("Indexed chunks could not be fully removed")
    except (VectorStoreUnavailable, DeletionFailed) as exc:
        raise DeletionFailed(str(exc)) from exc

    ParentPassage.query.filter_by(document_id=doc_id).delete()
    try:
        upload_path(storage_name).unlink(missing_ok=True)
    except OSError:
        log.exception("Could not remove source file for document %s", doc_id)
        raise DeletionFailed("Source file could not be removed")
    fresh = db.session.get(Document, doc_id)
    db.session.delete(fresh)
    db.session.commit()
