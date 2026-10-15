"""Retention enforcement shared by the CLI (`flask purge-expired`) and the admin API."""
from __future__ import annotations

from datetime import timedelta

from flask import current_app

from ..extensions import db
from ..models import AuditEvent, Conversation, RevokedToken, utcnow


def purge_expired() -> dict[str, int]:
    cfg = current_app.config
    now = utcnow()
    result = {
        "conversations": Conversation.query.filter(
            Conversation.updated_at < now - timedelta(days=cfg["CHAT_RETENTION_DAYS"])
        ).delete(),
        "audit_events": AuditEvent.query.filter(
            AuditEvent.created_at < now - timedelta(days=cfg["AUDIT_RETENTION_DAYS"])
        ).delete(),
        "revoked_tokens": RevokedToken.query.filter(RevokedToken.expires_at < now).delete(),
    }
    db.session.commit()
    return result
