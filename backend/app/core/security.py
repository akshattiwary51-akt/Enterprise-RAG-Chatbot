"""Password hashing, request correlation IDs and security headers."""
from __future__ import annotations

import re
import uuid

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from flask import Flask, g, request

_hasher = PasswordHasher()  # argon2id with library-default (RFC 9106 low-memory) parameters
# Verified against when the account does not exist, to equalise response timing.
_DUMMY_HASH = _hasher.hash("timing-equalisation-dummy-password")

_CORRELATION_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def validate_password_strength(password: str, min_length: int) -> str | None:
    """Return an error message, or None if acceptable."""
    if len(password) < min_length:
        return f"Password must be at least {min_length} characters"
    if len(password) > 256:
        return "Password is too long"
    if password.lower() == password or password.upper() == password:
        return "Password must mix upper and lower case letters"
    if not any(c.isdigit() for c in password):
        return "Password must contain at least one digit"
    return None


def register_request_hooks(app: Flask) -> None:
    @app.before_request
    def _correlation_id():
        incoming = request.headers.get("X-Request-ID", "")
        g.correlation_id = incoming if _CORRELATION_RE.match(incoming) else uuid.uuid4().hex

    @app.after_request
    def _security_headers(resp):
        resp.headers["X-Request-ID"] = getattr(g, "correlation_id", "")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        if request.path.startswith("/api/") and not request.path.startswith("/apidocs"):
            resp.headers["Cache-Control"] = "no-store"
            resp.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        if app.config["APP_ENV"] == "production":
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return resp
