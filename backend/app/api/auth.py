from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, current_app, g, jsonify, request
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    current_user,
    decode_token,
    get_jwt,
    jwt_required,
    verify_jwt_in_request,
)
from flask_jwt_extended.exceptions import JWTExtendedException
from jwt import PyJWTError

from ..auth.decorators import login_required
from ..core.errors import bad_request, unauthorized
from ..core.metrics import metrics
from ..core.security import hash_password, password_needs_rehash, validate_password_strength, verify_password
from ..extensions import db, limiter
from ..models import RevokedToken, User, utcnow
from ..schemas import ChangePasswordSchema, LoginSchema, RefreshLogoutSchema, load_json
from ..services.audit import service as audit
from .serializers import user_dict

bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def _issue_tokens(user: User) -> dict:
    ident = str(user.id)
    claims = {"tv": user.token_version}
    return {
        "access_token": create_access_token(identity=ident, additional_claims=claims),
        "refresh_token": create_refresh_token(identity=ident, additional_claims=claims),
        "token_type": "Bearer",
        "expires_in": int(current_app.config["JWT_ACCESS_TOKEN_EXPIRES"].total_seconds()),
    }


def revoke_jti(jti: str, token_type: str, user_id: str | None, exp: int) -> None:
    if RevokedToken.query.filter_by(jti=jti).first():
        return
    db.session.add(
        RevokedToken(jti=jti, token_type=token_type, user_id=user_id,
                     expires_at=datetime.fromtimestamp(exp, tz=timezone.utc))
    )


@bp.post("/login")
@limiter.limit(lambda: current_app.config["LOGIN_RATE_LIMIT"])
def login():
    """Authenticate with email + password.
    ---
    tags: [Auth]
    requestBody: {required: true}
    responses: {200: {description: Token pair}, 400: {description: Validation error}, 401: {description: Invalid credentials}, 429: {description: Rate limited}}
    """
    data = load_json(LoginSchema(), request.get_json(silent=True))
    email = data["email"].strip().lower()
    user = User.query.filter_by(email=email).first()
    ok = verify_password(user.password_hash if user else None, data["password"])
    if not ok or not user or not user.is_active:
        metrics.inc("auth_failures_total")
        audit.record(
            "auth.login", result="failure", actor=None, actor_label=audit.hash_identifier(email),
            details={"reason": "inactive" if (user and ok and not user.is_active) else "bad_credentials"},
        )
        raise unauthorized("Invalid email or password", code="invalid_credentials")

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(data["password"])
    user.last_login_at = utcnow()
    db.session.commit()
    audit.record("auth.login", actor=user, target_type="user", target_id=user.id)
    return jsonify({**_issue_tokens(user), "user": user_dict(user), })


@bp.post("/refresh")
@jwt_required(refresh=True)
def refresh():
    """Exchange a refresh token for a new pair (rotation: the old refresh token is revoked).
    ---
    tags: [Auth]
    responses: {200: {description: New token pair}, 401: {description: Invalid refresh token}}
    """
    user = current_user
    if user is None or not user.is_active:
        raise unauthorized("Account is not active", code="account_disabled")
    claims = get_jwt()
    revoke_jti(claims["jti"], "refresh", user.id, claims["exp"])
    db.session.commit()
    audit.record("auth.refresh", actor=user, target_type="user", target_id=user.id)
    return jsonify(_issue_tokens(user))


@bp.post("/logout")
@login_required
def logout():
    """Revoke the presented access token (and the refresh token if supplied).
    ---
    tags: [Auth]
    responses: {200: {description: Logged out}}
    """
    data = load_json(RefreshLogoutSchema(), request.get_json(silent=True) or {})
    claims = get_jwt()
    revoke_jti(claims["jti"], "access", g.current_user.id, claims["exp"])
    refresh_token = data.get("refresh_token")
    if refresh_token:
        try:
            decoded = decode_token(refresh_token)
            # only revoke refresh tokens that belong to the caller
            if decoded.get("type") == "refresh" and str(decoded.get("sub")) == g.current_user.id:
                revoke_jti(decoded["jti"], "refresh", g.current_user.id, decoded["exp"])
        except (PyJWTError, JWTExtendedException):
            pass
    db.session.commit()
    audit.record("auth.logout", actor=g.current_user, target_type="user", target_id=g.current_user.id)
    return jsonify({"status": "logged_out"})


@bp.get("/me")
@login_required
def me():
    """Current user profile, roles and capability permissions.
    ---
    tags: [Auth]
    responses: {200: {description: Profile}, 401: {description: Not authenticated}}
    """
    return jsonify(user_dict(g.current_user, include_permissions=True))


@bp.post("/change-password")
@login_required
def change_password():
    """Change own password. Revokes the current access token.
    ---
    tags: [Auth]
    responses: {200: {description: Changed}, 400: {description: Weak password}, 401: {description: Wrong current password}}
    """
    data = load_json(ChangePasswordSchema(), request.get_json(silent=True))
    user = g.current_user
    if not verify_password(user.password_hash, data["current_password"]):
        audit.record("auth.change_password", result="failure", actor=user, target_type="user", target_id=user.id)
        raise unauthorized("Current password is incorrect", code="invalid_credentials")
    problem = validate_password_strength(data["new_password"], current_app.config["MIN_PASSWORD_LENGTH"])
    if problem:
        raise bad_request(problem, code="weak_password")
    if data["new_password"] == data["current_password"]:
        raise bad_request("New password must differ from the current password", code="weak_password")
    user.password_hash = hash_password(data["new_password"])
    user.must_change_password = False
    # Invalidate every token issued so far (all sessions, access and refresh).
    user.token_version += 1
    claims = get_jwt()
    revoke_jti(claims["jti"], "access", user.id, claims["exp"])  # current token dies immediately
    db.session.commit()
    audit.record("auth.change_password", actor=user, target_type="user", target_id=user.id)
    return jsonify({"status": "password_changed", "relogin_required": True})
