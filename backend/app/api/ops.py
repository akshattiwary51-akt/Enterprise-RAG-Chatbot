from __future__ import annotations

from datetime import timedelta

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import func, text

from ..auth.decorators import permission_required
from ..core.config import model_config_problems
from ..core.metrics import metrics
from ..extensions import db
from ..models import AuditEvent, Document, DocStatus, User, utcnow
from ..schemas import AuditQuerySchema
from ..services.container import get_services
from .serializers import paginate

bp = Blueprint("ops", __name__, url_prefix="/api")


@bp.get("/health")
def health():
    """Liveness probe (no dependencies checked).
    ---
    tags: [Operations]
    responses: {200: {description: Alive}}
    """
    return jsonify({"status": "ok"})


@bp.get("/ready")
def ready():
    """Readiness probe: database and vector store reachability. Returns no detail beyond component status.
    ---
    tags: [Operations]
    responses: {200: {description: Ready}, 503: {description: Not ready}}
    """
    checks = {}
    try:
        db.session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        db.session.rollback()
        checks["database"] = "unavailable"
    try:
        get_services().vector_store.ping()
        checks["vector_store"] = "ok"
    except Exception:
        checks["vector_store"] = "unavailable"
    checks["model_config"] = "ok" if not model_config_problems(current_app.config) else "not_configured"
    healthy = checks["database"] == "ok" and checks["vector_store"] == "ok"
    return jsonify({"status": "ready" if healthy else "not_ready", "checks": checks}), (200 if healthy else 503)


@bp.get("/admin/audit-logs")
@permission_required("audit.view")
def audit_logs():
    """Audit events (admin). Filter by event_type / result.
    ---
    tags: [Admin]
    responses: {200: {description: Paginated events}, 403: {description: Forbidden}}
    """
    args = AuditQuerySchema().load(request.args)
    q = AuditEvent.query.order_by(AuditEvent.created_at.desc())
    if args["event_type"]:
        q = q.filter(AuditEvent.event_type == args["event_type"])
    if args["result"]:
        q = q.filter(AuditEvent.result == args["result"])
    return jsonify(paginate(q, args["page"], args["per_page"], lambda e: {
        "id": e.id, "created_at": e.created_at.isoformat(), "event_type": e.event_type, "result": e.result,
        "actor": e.actor_label, "actor_user_id": e.actor_user_id, "target_type": e.target_type,
        "target_id": e.target_id, "correlation_id": e.correlation_id, "details": e.details,
    }))


@bp.get("/admin/metrics")
@permission_required("audit.view")
def admin_metrics():
    """Operational metrics, indexing health and 24h failed-access summary (admin).
    ---
    tags: [Admin]
    responses: {200: {description: Metrics}, 403: {description: Forbidden}}
    """
    since = utcnow() - timedelta(hours=24)
    status_counts = dict(db.session.query(Document.status, func.count(Document.id)).group_by(Document.status).all())
    denied = (
        db.session.query(AuditEvent.event_type, func.count(AuditEvent.id))
        .filter(AuditEvent.created_at >= since, AuditEvent.result.in_(["denied", "failure"]))
        .group_by(AuditEvent.event_type).all()
    )
    return jsonify({
        "process": metrics.snapshot(),
        "documents": {"by_status": status_counts, "total": sum(status_counts.values())},
        "users": {"total": User.query.count(), "active": User.query.filter_by(is_active=True).count()},
        "failed_access_24h": {k: v for k, v in denied},
        "model_config_problems": model_config_problems(current_app.config),
    })
