"""Authentication/authorization decorators. Identity always comes from the verified
JWT subject; roles and permissions are re-read from the database on every request."""
from __future__ import annotations

from functools import wraps

from flask import g, request
from flask_jwt_extended import current_user, verify_jwt_in_request

from ..core.errors import forbidden, unauthorized
from ..services.audit import service as audit


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()  # signature, expiry, iss/aud, token type, revocation
        user = current_user
        if user is None or not user.is_active:
            raise unauthorized("Account is not active", code="account_disabled")
        g.current_user = user
        return fn(*args, **kwargs)

    return wrapper


def permission_required(permission: str):
    """Require a capability permission, enforced server-side. Denials are audited."""

    def decorator(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            user = g.current_user
            if not user.has_permission(permission):
                audit.record(
                    "authz.denied",
                    result="denied",
                    actor=user,
                    target_type="endpoint",
                    target_id=request.endpoint,
                    details={"required_permission": permission, "method": request.method},
                )
                raise forbidden()
            return fn(*args, **kwargs)

        return wrapper

    return decorator
