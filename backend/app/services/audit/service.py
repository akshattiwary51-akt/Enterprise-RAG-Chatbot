"""Security audit trail.

Persisted to the `audit_events` table and mirrored to a dedicated `audit`
logger (separate file handler) -- never to the general application log.
Callers must pass only identifiers and short, non-sensitive details: no
passwords, tokens, prompts, retrieved text or document contents.
"""
from __future__ import annotations

import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, g, has_request_context, request

from ...extensions import db
from ...models import AuditEvent, User

audit_logger = logging.getLogger("audit")

_FORBIDDEN_DETAIL_KEYS = {"password", "token", "authorization", "api_key", "prompt", "context", "text", "content"}


def configure_audit_logger(app: Flask) -> None:
    audit_logger.setLevel(logging.INFO)
    audit_logger.propagate = False  # keep out of the general application log
    path = app.config.get("AUDIT_LOG_FILE")
    if app.config["TESTING"] or not path:
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    resolved = str(Path(path).resolve())
    if any(getattr(h, "baseFilename", None) == resolved for h in audit_logger.handlers):
        return
    handler = RotatingFileHandler(path, maxBytes=10_000_000, backupCount=10)
    handler.setFormatter(logging.Formatter("%(message)s"))
    audit_logger.addHandler(handler)


def hash_identifier(value: str) -> str:
    """Stable pseudonym for unauthenticated identifiers (e.g. unknown login emails)."""
    return "h:" + hashlib.sha256(value.strip().lower().encode()).hexdigest()[:16]


def _clean_details(details: dict | None) -> dict:
    out = {}
    for key, value in (details or {}).items():
        if key.lower() in _FORBIDDEN_DETAIL_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value if not isinstance(value, str) else value[:200]
        elif isinstance(value, (list, tuple)):
            out[key] = [str(v)[:64] for v in value][:50]
    return out


def record(
    event_type: str,
    result: str = "success",
    actor: User | None = None,
    actor_label: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict | None = None,
) -> None:
    """Write one audit event and commit it immediately (so denials persist even if the
    surrounding request later fails)."""
    event = AuditEvent(
        event_type=event_type,
        result=result,
        actor_user_id=actor.id if actor else None,
        actor_label=(actor.email if actor else actor_label),
        target_type=target_type,
        target_id=str(target_id) if target_id else None,
        correlation_id=getattr(g, "correlation_id", None) if has_request_context() else None,
        ip_address=(request.remote_addr if has_request_context() else None),
        details=_clean_details(details),
    )
    try:
        db.session.add(event)
        db.session.commit()
    except Exception:  # audit failure must not take down the request, but must be loud
        db.session.rollback()
        logging.getLogger("app.audit").exception("FAILED to persist audit event %s", event_type)
        return
    audit_logger.info(
        json.dumps(
            {
                "ts": event.created_at.isoformat() if event.created_at else None,
                "event": event_type,
                "result": result,
                "actor": event.actor_user_id or event.actor_label,
                "target": f"{target_type}:{target_id}" if target_type else None,
                "correlation_id": event.correlation_id,
                "ip": event.ip_address,
                "details": event.details,
            },
            default=str,
        )
    )
