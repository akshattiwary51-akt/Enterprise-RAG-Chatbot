from __future__ import annotations

from datetime import timedelta

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import func

from ..auth.decorators import permission_required
from ..core.config import model_config_problems
from ..core.metrics import metrics
from ..services.audit import service as audit
from ..services.maintenance import purge_expired
from ..extensions import db
from ..models import AuditEvent, Document, User, utcnow
from ..schemas import AuditQuerySchema
from .serializers import paginate

bp = Blueprint("admin", __name__, url_prefix="/api/admin")


def _audit_dict(e: AuditEvent) -> dict:
    return {"id": e.id, "created_at": e.created_at.isoformat(), "event_type": e.event_type, "result": e.result,
            "actor": e.actor_label or e.actor_user_id, "target_type": e.target_type, "target_id": e.target_id,
            "correlation_id": e.correlation_id, "ip_address": e.ip_address, "details": e.details}


@bp.get("/audit-logs")
@permission_required("audit.view")
def audit_logs():
    """Security audit events (admin only).
    ---
    tags: [Admin]
    responses: {200: {description: Paginated audit events}, 403: {description: Forbidden}}
    """
    args = AuditQuerySchema().load(request.args)
    q = AuditEvent.query.order_by(AuditEvent.created_at.desc())
    if args["event_type"]:
        q = q.filter(AuditEvent.event_type == args["event_type"])
    if args["result"]:
        q = q.filter(AuditEvent.result == args["result"])
    return jsonify(paginate(q, args["page"], args["per_page"], _audit_dict))


@bp.get("/metrics")
@permission_required("audit.view")
def admin_metrics():
    """Operational metrics, document/index health and a failed-access summary (admin only).
    ---
    tags: [Admin]
    responses: {200: {description: Metrics}, 403: {description: Forbidden}}
    """
    since = utcnow() - timedelta(hours=24)
    docs_by_status = dict(db.session.query(Document.status, func.count(Document.id)).group_by(Document.status).all())
    denied = (
        db.session.query(AuditEvent.event_type, func.count(AuditEvent.id))
        .filter(AuditEvent.created_at >= since, AuditEvent.result.in_(["denied", "failure"]))
        .group_by(AuditEvent.event_type)
        .all()
    )
    # Response shape is the contract consumed by frontend/src/types AdminMetrics.
    return jsonify({
        "documents_by_status": docs_by_status,
        "users": {"active": User.query.filter_by(is_active=True).count(), "total": User.query.count()},
        "failed_access_last_24h": {k: v for k, v in denied},
        "model_config_problems": model_config_problems(current_app.config),
        "runtime": metrics.snapshot(),
    })


@bp.post("/maintenance/purge")
@permission_required("users.manage")
def maintenance_purge():
    """Apply the configured retention policy now (conversations, audit events, expired token records).
    ---
    tags: [Admin]
    responses: {200: {description: Counts of purged rows}, 403: {description: Forbidden}}
    """
    result = purge_expired()
    audit.record("admin.purge", actor=g.current_user, details=result)
    return jsonify(result)
