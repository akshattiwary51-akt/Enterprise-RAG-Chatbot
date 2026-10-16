"""Durable database-backed queue for document ingestion."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import timedelta

from flask import Flask
from sqlalchemy import or_, update

from ...extensions import db
from ...models import DocStatus, Document, IngestionJob, utcnow
from .pipeline import ingest_document

log = logging.getLogger("app.ingestion.worker")

QUEUED = "QUEUED"
PROCESSING = "PROCESSING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"


class IngestionAlreadyRunning(RuntimeError):
    """The document already has an ingestion job claimed by a worker."""


def enqueue_ingestion(document: Document) -> IngestionJob:
    """Create or reset the durable job; caller commits with the document transaction."""
    job = db.session.get(IngestionJob, document.id)
    if job is not None and job.status == PROCESSING:
        raise IngestionAlreadyRunning(document.id)

    document.status = DocStatus.PENDING.value
    document.error_message = None
    if job is None:
        job = IngestionJob(document_id=document.id)
        db.session.add(job)
    else:
        job.status = QUEUED
        job.attempt_count = 0
        job.locked_at = None
        job.owner_token = None
        job.last_error = None
        job.updated_at = utcnow()
    return job


def _stale_clause(stale_before):
    return (IngestionJob.status == PROCESSING) & (IngestionJob.locked_at < stale_before)


def _expire_exhausted_jobs(stale_before, max_attempts: int) -> None:
    exhausted_ids = db.session.query(IngestionJob.document_id).filter(
        _stale_clause(stale_before),
        IngestionJob.attempt_count >= max_attempts,
    ).all()
    for (document_id,) in exhausted_ids:
        result = db.session.execute(
            update(IngestionJob)
            .where(
                IngestionJob.document_id == document_id,
                _stale_clause(stale_before),
                IngestionJob.attempt_count >= max_attempts,
            )
            .values(
                status=FAILED,
                owner_token=None,
                last_error="Worker lease expired after maximum attempts",
                updated_at=utcnow(),
            )
        )
        if result.rowcount:
            db.session.execute(
                update(Document)
                .where(Document.id == document_id, Document.status == DocStatus.PROCESSING.value)
                .values(status=DocStatus.FAILED.value, error_message="Worker lease expired after maximum attempts")
            )
    if exhausted_ids:
        db.session.commit()


def _claim_next_job(
    lease_seconds: int,
    max_attempts: int,
    document_id: str | None = None,
) -> tuple[str, str] | None:
    now = utcnow()
    stale_before = now - timedelta(seconds=lease_seconds)
    _expire_exhausted_jobs(stale_before, max_attempts)
    available = or_(
        IngestionJob.status == QUEUED,
        _stale_clause(stale_before) & (IngestionJob.attempt_count < max_attempts),
    )
    query = db.session.query(IngestionJob.document_id).filter(available)
    if document_id is not None:
        query = query.filter(IngestionJob.document_id == document_id)
    candidate = (
        query
        .order_by(IngestionJob.created_at, IngestionJob.document_id)
        .first()
    )
    if candidate is None:
        return None

    document_id = candidate[0]
    owner_token = str(uuid.uuid4())
    result = db.session.execute(
        update(IngestionJob)
        .where(IngestionJob.document_id == document_id, available)
        .values(
            status=PROCESSING,
            attempt_count=IngestionJob.attempt_count + 1,
            locked_at=now,
            owner_token=owner_token,
            updated_at=now,
        )
    )
    db.session.commit()
    return (document_id, owner_token) if result.rowcount == 1 else None


def _heartbeat(app: Flask, document_id: str, owner_token: str, stop: threading.Event, interval: float) -> None:
    while not stop.wait(interval):
        with app.app_context():
            try:
                db.session.execute(
                    update(IngestionJob)
                    .where(
                        IngestionJob.document_id == document_id,
                        IngestionJob.status == PROCESSING,
                        IngestionJob.owner_token == owner_token,
                    )
                    .values(locked_at=utcnow(), updated_at=utcnow())
                )
                db.session.commit()
            except Exception:
                db.session.rollback()
                log.exception("Could not renew ingestion lease document=%s", document_id)


def run_worker_once(app: Flask, document_id: str | None = None) -> bool:
    """Process one queued job; return False when there was no claimable work."""
    lease_seconds = app.config["INGESTION_LEASE_SECONDS"]
    max_attempts = app.config["INGESTION_MAX_ATTEMPTS"]
    with app.app_context():
        claim = _claim_next_job(lease_seconds, max_attempts, document_id)
    if claim is None:
        return False

    document_id, owner_token = claim
    stop = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat,
        args=(app, document_id, owner_token, stop, max(0.1, lease_seconds / 3)),
        daemon=True,
    )
    heartbeat.start()
    try:
        with app.app_context():
            doc = db.session.get(Document, document_id)
            if doc is None:
                job = db.session.get(IngestionJob, document_id)
                if job is not None and job.owner_token == owner_token:
                    job.status = FAILED
                    job.last_error = "Document no longer exists"
                    job.owner_token = None
                    job.locked_at = None
                    db.session.commit()
                return True
            if doc.status == DocStatus.READY.value:
                result = doc
            else:
                result = ingest_document(document_id, recover_processing=True)
            job = db.session.get(IngestionJob, document_id)
            if job is None or job.owner_token != owner_token:
                return True
            job.status = SUCCEEDED if result and result.status == DocStatus.READY.value else FAILED
            job.last_error = result.error_message if result and result.status == DocStatus.FAILED.value else None
            job.owner_token = None
            job.locked_at = None
            job.updated_at = utcnow()
            db.session.commit()
            log.info("Ingestion job completed document=%s status=%s", document_id, job.status)
    except Exception:
        with app.app_context():
            job = db.session.get(IngestionJob, document_id)
            if job is not None and job.owner_token == owner_token:
                doc = db.session.get(Document, document_id)
                if doc is not None and doc.status != DocStatus.DELETING.value:
                    doc.status = DocStatus.FAILED.value
                    doc.error_message = "Ingestion worker failed; retry the job"
                job.status = FAILED
                job.last_error = "Worker error; see server log"
                job.owner_token = None
                job.locked_at = None
                job.updated_at = utcnow()
                db.session.commit()
        log.exception("Ingestion worker failed document=%s", document_id)
    finally:
        stop.set()
        heartbeat.join(timeout=2)
    return True


def run_worker(app: Flask, poll_seconds: float) -> None:
    """Poll continuously until interrupted."""
    while True:
        if not run_worker_once(app):
            time.sleep(poll_seconds)
