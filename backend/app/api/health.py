from __future__ import annotations

from flask import Blueprint, current_app, jsonify
from sqlalchemy import text

from ..core.config import model_config_problems
from ..extensions import db
from ..services.container import get_services

bp = Blueprint("health", __name__, url_prefix="/api")


@bp.get("/health")
def health():
    """Liveness probe.
    ---
    tags: [Operations]
    responses: {200: {description: Process is up}}
    """
    return jsonify({"status": "ok"})


@bp.get("/ready")
def ready():
    """Readiness probe: database, vector store and model configuration. No details beyond component status.
    ---
    tags: [Operations]
    responses: {200: {description: Ready}, 503: {description: Not ready}}
    """
    checks: dict[str, str] = {}
    try:
        db.session.execute(text("SELECT 1"))
        checks["database"] = "ok"
        if db.engine.dialect.name == "postgresql":
            # A non-UTF8 database (e.g. SQL_ASCII from a C-locale initdb) makes every non-ASCII value
            # (names, currency symbols, accented document text) fail at write time. Surface it here.
            encoding = db.session.execute(text("SHOW server_encoding")).scalar()
            if str(encoding).upper() != "UTF8":
                checks["database"] = "unsupported_encoding"
    except Exception:
        db.session.rollback()
        checks["database"] = "unavailable"
    try:
        get_services().vector_store.ping()
        checks["vector_store"] = "ok"
    except Exception:
        checks["vector_store"] = "unavailable"
    checks["models"] = "ok" if not model_config_problems(current_app.config) else "not_configured"
    ok = all(v == "ok" for v in checks.values())
    return jsonify({"status": "ready" if ok else "not_ready", "checks": checks}), (200 if ok else 503)
