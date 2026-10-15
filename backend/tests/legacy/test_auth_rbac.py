import time

import jwt as pyjwt
from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models import User
from legacy_support import PASSWORD, login, make_user


def test_valid_login_returns_tokens_and_profile(client, users):
    body = login(client, users["hr"].email)
    assert body["access_token"] and body["refresh_token"]
    assert body["user"]["roles"] == ["hr"]
    assert "password_hash" not in str(body)


def test_invalid_credentials_rejected_uniformly(client, users):
    bad_pw = client.post("/api/auth/login", json={"email": users["hr"].email, "password": "wrong-Password-1"})
    no_user = client.post("/api/auth/login", json={"email": "ghost@corp.test", "password": "wrong-Password-1"})
    assert bad_pw.status_code == no_user.status_code == 401
    assert bad_pw.get_json()["error"]["message"] == no_user.get_json()["error"]["message"]


def test_inactive_user_cannot_login(client, app):
    make_user("off@corp.test", ["employee"], active=False)
    r = client.post("/api/auth/login", json={"email": "off@corp.test", "password": PASSWORD})
    assert r.status_code == 401


def test_protected_endpoints_require_auth(client):
    for method, path in [("get", "/api/auth/me"), ("get", "/api/documents"), ("post", "/api/chat"),
                         ("get", "/api/conversations"), ("get", "/api/users"), ("get", "/api/admin/audit-logs"),
                         ("get", "/api/admin/metrics"), ("post", "/api/documents/upload"), ("get", "/api/roles")]:
        assert getattr(client, method)(path).status_code == 401, path


def test_garbage_and_wrongly_signed_tokens_rejected(client, app, users):
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401
    forged = pyjwt.encode({"sub": users["admin"].id, "type": "access", "jti": "x", "iat": int(time.time()),
                           "exp": int(time.time()) + 600, "iss": "enterprise-rag-chatbot",
                           "aud": "enterprise-rag-api", "fresh": False},
                          "attacker-key-attacker-key-attacker-key", algorithm="HS256")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_alg_none_token_rejected(client, users):
    import base64, json

    def b64(d): return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    token = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'sub': users['admin'].id, 'type': 'access'})}."
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_expired_token_rejected(client, app, users):
    from datetime import timedelta
    with app.app_context():
        token = create_access_token(identity=users["hr"].id, expires_delta=timedelta(seconds=-5))
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and r.get_json()["error"]["code"] == "token_expired"


def test_refresh_token_cannot_be_used_as_access_token(client, users):
    tokens = login(client, users["hr"].email)
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tokens['refresh_token']}"})
    assert r.status_code == 401


def test_logout_revokes_access_token(client, users):
    tokens = login(client, users["hr"].email)
    h = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert client.get("/api/auth/me", headers=h).status_code == 200
    assert client.post("/api/auth/logout", json={"refresh_token": tokens["refresh_token"]}, headers=h).status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401
    r = client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {tokens['refresh_token']}"})
    assert r.status_code == 401


def test_refresh_rotation_revokes_old_refresh_token(client, users):
    tokens = login(client, users["hr"].email)
    rh = {"Authorization": f"Bearer {tokens['refresh_token']}"}
    first = client.post("/api/auth/refresh", headers=rh)
    assert first.status_code == 200
    assert client.post("/api/auth/refresh", headers=rh).status_code == 401  # replay rejected
    new_access = first.get_json()["access_token"]
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_access}"}).status_code == 200


def test_deactivated_user_token_stops_working(client, auth, users):
    h = auth("hr")
    assert client.get("/api/auth/me", headers=h).status_code == 200
    r = client.patch(f"/api/users/{users['hr'].id}", json={"is_active": False}, headers=auth("admin"))
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401


def test_password_change_invalidates_existing_tokens(client, users):
    t = login(client, users["hr"].email)
    h = {"Authorization": f"Bearer {t['access_token']}"}
    time.sleep(1.1)  # token `iat` has 1s resolution
    r = client.post("/api/auth/change-password",
                    json={"current_password": PASSWORD, "new_password": "Another-Strong-Pass-42"}, headers=h)
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401
    assert client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {t['refresh_token']}"}).status_code == 401


def test_passwords_are_hashed_with_argon2(app, users):
    assert users["hr"].password_hash.startswith("$argon2id$")


def test_login_is_rate_limited(tmp_path):
    from app import create_app
    from app.extensions import limiter
    app = create_app("testing", {
        "SECRET_KEY": "s" * 40, "JWT_SECRET_KEY": "j" * 40, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path/'rl.db'}",
        "UPLOAD_DIR": str(tmp_path / "u"), "CHROMA_DIR": str(tmp_path / "c"), "AUDIT_LOG_FILE": "",
        "EMBEDDINGS_PROVIDER": "fake", "LLM_PROVIDER": "fake", "RATELIMIT_ENABLED": True,
        "LOGIN_RATE_LIMIT": "3 per minute"})
    with app.app_context():
        db.create_all()
        c = app.test_client()
        codes = [c.post("/api/auth/login", json={"email": "x@y.test", "password": "nope"}).status_code for _ in range(5)]
        assert codes[:3] == [401, 401, 401] and 429 in codes[3:]
        limiter.reset()
        db.drop_all()


# ------------------------------------------------------------------ RBAC / privilege escalation
def test_employee_cannot_use_admin_apis(client, auth):
    h = auth("emp")
    assert client.get("/api/users", headers=h).status_code == 403
    assert client.post("/api/users", json={}, headers=h).status_code == 403
    assert client.get("/api/admin/audit-logs", headers=h).status_code == 403
    assert client.get("/api/admin/metrics", headers=h).status_code == 403


def test_non_admin_roles_cannot_use_admin_apis(client, auth):
    for who in ("hr", "finance", "eng"):
        assert client.get("/api/users", headers=auth(who)).status_code == 403
        assert client.get("/api/admin/audit-logs", headers=auth(who)).status_code == 403


def test_employee_cannot_upload(client, auth):
    from legacy_support import upload
    r = upload(client, auth("emp"), "a.txt", b"hello world", roles=["employee"])
    assert r.status_code == 403


def test_client_supplied_role_or_user_id_is_rejected(client, auth, users):
    h = auth("emp")
    r = client.post("/api/chat", json={"message": "hi", "role": "admin"}, headers=h)
    assert r.status_code == 400
    r = client.post("/api/chat", json={"message": "hi", "user_id": users["admin"].id}, headers=h)
    assert r.status_code == 400
    r = client.post("/api/auth/login", json={"email": "emp@corp.test", "password": PASSWORD, "roles": ["admin"]})
    assert r.status_code == 400


def test_role_header_or_query_cannot_escalate(client, auth):
    h = {**auth("emp"), "X-Role": "admin", "X-User-Role": "admin"}
    assert client.get("/api/users?role=admin", headers=h).status_code == 403


def test_admin_can_create_user_and_change_roles(client, auth):
    r = client.post("/api/users", headers=auth("admin"),
                    json={"email": "New.Hire@corp.test", "full_name": "New Hire", "password": "Initial-Pass-12345",
                          "roles": ["employee"]})
    assert r.status_code == 201
    uid = r.get_json()["id"]
    assert r.get_json()["email"] == "new.hire@corp.test" and r.get_json()["must_change_password"] is True
    r = client.patch(f"/api/users/{uid}/roles", json={"roles": ["finance"]}, headers=auth("admin"))
    assert r.status_code == 200 and r.get_json()["roles"] == ["finance"]
    assert client.patch(f"/api/users/{uid}/roles", json={"roles": ["superuser"]}, headers=auth("admin")).status_code == 400


def test_weak_password_rejected_on_user_creation(client, auth):
    r = client.post("/api/users", headers=auth("admin"),
                    json={"email": "w@corp.test", "full_name": "W", "password": "short1A", "roles": ["employee"]})
    assert r.status_code == 400 and r.get_json()["error"]["code"] == "weak_password"


def test_cannot_remove_last_admin_or_self_demote(client, auth, users):
    r = client.patch(f"/api/users/{users['admin'].id}/roles", json={"roles": ["employee"]}, headers=auth("admin"))
    assert r.status_code == 403
    r = client.patch(f"/api/users/{users['admin'].id}", json={"is_active": False}, headers=auth("admin"))
    assert r.status_code == 409


def test_error_responses_never_leak_internals(client, auth):
    r = client.get("/api/users/does-not-exist-%27%20OR%201=1", headers=auth("emp"))
    body = r.get_data(as_text=True)
    assert "Traceback" not in body and "sqlalchemy" not in body.lower()
    assert r.get_json()["error"]["correlation_id"]


def test_security_headers_and_correlation_id(client):
    r = client.get("/api/health", headers={"X-Request-ID": "trace-12345678"})
    assert r.headers["X-Request-ID"] == "trace-12345678"
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
    bad = client.get("/api/health", headers={"X-Request-ID": "bad id; <script>"})
    assert bad.headers["X-Request-ID"] != "bad id; <script>"  # invalid ids are replaced, not echoed


def test_audit_log_records_login_failure_without_secrets(client, auth, users):
    client.post("/api/auth/login", json={"email": "hr@corp.test", "password": "Super-Secret-Typo-1"})
    r = client.get("/api/admin/audit-logs?event_type=auth.login&result=failure", headers=auth("admin"))
    assert r.status_code == 200 and r.get_json()["total"] >= 1
    assert "Super-Secret-Typo-1" not in r.get_data(as_text=True)
