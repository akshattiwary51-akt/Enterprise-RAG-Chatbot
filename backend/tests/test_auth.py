from datetime import timedelta

import pytest
from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models import User
from conftest import PASSWORD, USERS, login, make_app

PROTECTED = [
    ("get", "/api/auth/me"), ("get", "/api/users"), ("get", "/api/roles"), ("get", "/api/documents"),
    ("get", "/api/conversations"), ("get", "/api/admin/audit-logs"), ("get", "/api/admin/metrics"),
    ("post", "/api/chat"), ("post", "/api/documents/upload"), ("get", "/api/documents/x/download"),
]


def test_valid_login(client, users):
    body = login(client, USERS["hr"][0])
    assert body["user"]["roles"] == ["hr"] and body["token_type"] == "Bearer"
    assert "password_hash" not in str(body)


def test_invalid_credentials_are_indistinguishable(client, users):
    wrong = client.post("/api/auth/login", json={"email": USERS["hr"][0], "password": "nope-nope-Nope1"})
    unknown = client.post("/api/auth/login", json={"email": "ghost@test.local", "password": "nope-nope-Nope1"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.get_json()["error"]["message"] == unknown.get_json()["error"]["message"]


@pytest.mark.parametrize("method,path", PROTECTED)
def test_unauthenticated_requests_rejected(client, method, path):
    assert getattr(client, method)(path).status_code == 401


def test_garbage_and_foreign_tokens_rejected(client, app, users):
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401
    import jwt as pyjwt
    forged = pyjwt.encode({"sub": users["admin"].id, "type": "access", "jti": "x", "iat": 1, "exp": 9999999999,
                           "iss": "enterprise-rag-chatbot", "aud": "enterprise-rag-api", "fresh": False},
                          "attacker-secret-attacker-secret-1234", algorithm="HS256")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    unsigned = pyjwt.encode({"sub": users["admin"].id}, None, algorithm="none")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {unsigned}"}).status_code == 401


def test_expired_token_rejected(client, app, users):
    token = create_access_token(identity=users["hr"].id, expires_delta=timedelta(seconds=-5))
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and r.get_json()["error"]["code"] == "token_expired"


def test_wrong_audience_rejected(client, app, users):
    app.config["JWT_ENCODE_AUDIENCE"] = "some-other-api"
    token = create_access_token(identity=users["hr"].id)
    app.config["JWT_ENCODE_AUDIENCE"] = "enterprise-rag-api"
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_logout_revokes_access_token(client, users):
    tokens = login(client, USERS["hr"][0])
    h = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert client.get("/api/auth/me", headers=h).status_code == 200
    assert client.post("/api/auth/logout", json={"refresh_token": tokens["refresh_token"]}, headers=h).status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401
    r = client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {tokens['refresh_token']}"})
    assert r.status_code == 401  # refresh token was revoked too


def test_refresh_rotation_and_token_type_separation(client, users):
    tokens = login(client, USERS["hr"][0])
    refresh_h = {"Authorization": f"Bearer {tokens['refresh_token']}"}
    # refresh token cannot be used as an access token; access token cannot refresh
    assert client.get("/api/auth/me", headers=refresh_h).status_code == 401
    assert client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {tokens['access_token']}"}).status_code == 401
    r = client.post("/api/auth/refresh", headers=refresh_h)
    assert r.status_code == 200 and r.get_json()["access_token"]
    assert client.post("/api/auth/refresh", headers=refresh_h).status_code == 401  # rotated: single use


def test_deactivated_user_loses_access_immediately(client, auth, users):
    h = auth("hr")
    assert client.get("/api/auth/me", headers=h).status_code == 200
    r = client.patch(f"/api/users/{users['hr'].id}", json={"is_active": False}, headers=auth("admin"))
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401
    assert client.post("/api/auth/login", json={"email": USERS["hr"][0], "password": PASSWORD}).status_code == 401


def test_password_change_invalidates_all_tokens(client, users):
    t1, t2 = login(client, USERS["hr"][0]), login(client, USERS["hr"][0])
    h1 = {"Authorization": f"Bearer {t1['access_token']}"}
    r = client.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": "An0ther-Strong-Pass"}, headers=h1)
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers=h1).status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {t2['access_token']}"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": USERS["hr"][0], "password": "An0ther-Strong-Pass"}).status_code == 200


def test_weak_password_rejected_on_change(client, auth):
    r = client.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": "short"}, headers=auth("hr"))
    assert r.status_code == 400


# ---- privilege escalation through request bodies -------------------------------------------
def test_client_supplied_role_or_user_fields_are_rejected(client, auth, users):
    h = auth("employee")
    for path, body in [
        ("/api/chat", {"message": "hi", "role": "admin"}),
        ("/api/chat", {"message": "hi", "user_id": users["hr"].id}),
        ("/api/chat", {"message": "hi", "document_ids": ["x"], "filter": {"document_id": {"$ne": ""}}}),
    ]:
        r = client.post(path, json=body, headers=h)
        assert r.status_code == 400, (body, r.get_json())
    assert client.post("/api/auth/login", json={"email": "a@b.co", "password": "x", "role": "admin"}).status_code == 400


def test_employee_cannot_escalate_self(client, auth, users):
    h = auth("employee")
    assert client.patch(f"/api/users/{users['employee'].id}/roles", json={"roles": ["admin"]}, headers=h).status_code == 403
    assert client.patch(f"/api/users/{users['employee'].id}", json={"is_active": True}, headers=h).status_code == 403
    db.session.refresh(users["employee"])
    assert users["employee"].role_names == ["employee"]


def test_jwt_claims_cannot_grant_roles(client, app, users):
    """Roles are read from the DB; extra claims in a (validly signed) token are ignored."""
    token = create_access_token(identity=users["employee"].id, additional_claims={"roles": ["admin"], "role": "admin"})
    h = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/users", headers=h).status_code == 403
    assert client.get("/api/auth/me", headers=h).get_json()["roles"] == ["employee"]


def test_login_rate_limit(tmp_path):
    app = make_app(tmp_path, RATELIMIT_ENABLED=True, LOGIN_RATE_LIMIT="3 per minute")
    c = app.test_client()
    codes = [c.post("/api/auth/login", json={"email": "x@y.zz", "password": "bad"}).status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and 429 in codes[3:]
    assert c.post("/api/auth/login", json={"email": "x@y.zz", "password": "bad"}).get_json()["error"]["code"] == "rate_limited"


def test_security_headers_and_correlation_id(client):
    r = client.get("/api/health", headers={"X-Request-ID": "abc12345-trace"})
    assert r.headers["X-Request-ID"] == "abc12345-trace"
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Cache-Control"] == "no-store"
    bad = client.get("/api/health", headers={"X-Request-ID": "<script>alert(1)</script>"})
    assert "<" not in bad.headers["X-Request-ID"]


def test_errors_do_not_leak_internals(client, auth, monkeypatch):
    from app.api import users as users_api

    def boom(*a, **k):
        raise RuntimeError("SELECT * FROM secret_table password=hunter2")

    monkeypatch.setattr(users_api, "paginate", boom)
    r = client.get("/api/users", headers=auth("admin"))
    assert r.status_code == 500
    text = r.get_data(as_text=True)
    assert "hunter2" not in text and "Traceback" not in text and "correlation_id" in text


def test_oversized_json_rejected(client, auth):
    r = client.post("/api/chat", data=b'{"message": "' + b"a" * 100_000 + b'"}', content_type="application/json", headers=auth("hr"))
    assert r.status_code == 413


def test_startup_config_validation_never_echoes_secrets(tmp_path):
    from app.core.config import ConfigError

    with pytest.raises(ConfigError) as exc:
        make_app(tmp_path, SECRET_KEY="short-secret-value-xyz")
    assert "SECRET_KEY" in str(exc.value) and "short-secret-value-xyz" not in str(exc.value)
    with pytest.raises(ConfigError):
        make_app(tmp_path, JWT_SECRET_KEY="s" * 40)  # equals SECRET_KEY
