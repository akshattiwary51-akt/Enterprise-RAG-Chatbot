"""Application factory."""
from __future__ import annotations

import logging
import os

from flask import Flask, request
from werkzeug.middleware.proxy_fix import ProxyFix

from .core.config import build_config, validate_config
from .core.errors import ApiError, error_response, register_error_handlers
from .core.security import register_request_hooks
from .core.storage import secure_upload_directory
from .extensions import cors, db, jwt, limiter, migrate


def create_app(env: str | dict | None = None, config_overrides: dict | None = None) -> Flask:
    """create_app() | create_app({overrides}) | create_app("testing", {overrides})"""
    if isinstance(env, dict):
        env, config_overrides = None, env
    if (env or os.environ.get("APP_ENV", "development")) != "testing":
        try:  # .env is a developer convenience; real deployments inject real environment variables
            from dotenv import load_dotenv

            load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=False)
        except ImportError:  # pragma: no cover
            pass

    cfg = build_config(env)
    if config_overrides:
        cfg.update(config_overrides)
    validate_config(cfg)  # raises ConfigError naming bad settings (never their values)

    app = Flask(__name__)
    app.config.update(cfg)
    secure_upload_directory(cfg["UPLOAD_DIR"])
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if cfg["TRUSTED_PROXY_COUNT"] > 0:
        n = cfg["TRUSTED_PROXY_COUNT"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)

    db.init_app(app)
    migrate.init_app(app, db, directory=os.path.join(os.path.dirname(__file__), "..", "migrations"))
    jwt.init_app(app)
    limiter.init_app(app)
    cors.init_app(
        app,
        resources={r"/api/*": {"origins": cfg["CORS_ORIGINS"]}},
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        supports_credentials=False,
    )

    register_request_hooks(app)
    register_error_handlers(app)

    @app.before_request
    def _limit_json_size():
        # multipart uploads are bounded by MAX_CONTENT_LENGTH; JSON bodies get a much smaller cap
        if request.mimetype == "application/json" and (request.content_length or 0) > app.config["MAX_JSON_BYTES"]:
            raise ApiError(413, "payload_too_large", "Request body is too large")

    from .auth.jwt_handlers import register_jwt_handlers
    from .services.audit.service import configure_audit_logger
    from .services.container import build_services

    register_jwt_handlers(app)
    configure_audit_logger(app)
    app.extensions["services"] = build_services(app.config)

    from .api import admin, auth, chat, documents, health, users

    for module in (health, auth, users, documents, chat, admin):
        app.register_blueprint(module.bp)

    _register_swagger(app)

    from .cli import register_cli

    register_cli(app)

    @limiter.request_filter
    def _skip_options():
        return request.method == "OPTIONS"

    @app.errorhandler(429)
    def _rate_limited(_e):
        return error_response(429, "rate_limited", "Too many requests; please slow down")

    return app


def _register_swagger(app: Flask) -> None:
    from flasgger import Swagger

    template = {
        "swagger": "2.0",
        "info": {"title": "Enterprise RAG Chatbot API", "version": "1.0.0",
                 "description": "Role-based RAG chatbot. All endpoints except /health, /ready and /auth/login "
                                "require `Authorization: Bearer <access_token>`."},
        "securityDefinitions": {"Bearer": {"type": "apiKey", "name": "Authorization", "in": "header",
                                           "description": "Format: Bearer <token>"}},
        "security": [{"Bearer": []}],
    }
    Swagger(app, template=template, config={
        "headers": [], "specs": [{"endpoint": "apispec", "route": "/apispec.json",
                                  "rule_filter": lambda r: True, "model_filter": lambda t: True}],
        "static_url_path": "/flasgger_static", "swagger_ui": True, "specs_route": "/apidocs/",
    })
