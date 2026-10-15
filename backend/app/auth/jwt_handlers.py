"""Wire Flask-JWT-Extended callbacks: user lookup, revocation and uniform 401 bodies."""
from __future__ import annotations

from ..core.errors import error_response
from ..extensions import db, jwt
from ..models import RevokedToken, User


def register_jwt_handlers(app) -> None:
    @jwt.user_lookup_loader
    def _load_user(_header, payload):
        return db.session.get(User, payload["sub"])

    @jwt.token_in_blocklist_loader
    def _is_revoked(_header, payload) -> bool:
        if RevokedToken.query.filter_by(jti=payload["jti"]).first() is not None:
            return True
        user = db.session.get(User, payload.get("sub"))
        if user is None:
            return True
        return payload.get("tv", 0) != user.token_version

    @jwt.expired_token_loader
    def _expired(_h, _p):
        return error_response(401, "token_expired", "Session expired; please sign in again")

    @jwt.invalid_token_loader
    def _invalid(_reason):
        return error_response(401, "invalid_token", "Invalid authentication token")

    @jwt.unauthorized_loader
    def _missing(_reason):
        return error_response(401, "unauthorized", "Authentication required")

    @jwt.revoked_token_loader
    def _revoked(_h, _p):
        return error_response(401, "token_revoked", "Session is no longer valid; please sign in again")

    @jwt.user_lookup_error_loader
    def _lookup_error(_h, _p):
        return error_response(401, "invalid_token", "Invalid authentication token")

    @jwt.needs_fresh_token_loader
    def _fresh(_h, _p):
        return error_response(401, "fresh_token_required", "Please sign in again")
