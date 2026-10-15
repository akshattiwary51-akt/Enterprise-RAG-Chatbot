import json
from datetime import timedelta

from app.extensions import db
from app.models import AuditEvent, Conversation, utcnow
from legacy_support import CANARY_HR, PASSWORD, auth, chat, login, upload


def test_employee_and_other_roles_cannot_use_admin_apis(client, tokens, users):
    for who in ("emp", "hr", "finance", "eng"):
        h = auth(tokens[who])
        assert client.get("/api/users", headers=h).status_code == 403, who
        assert client.post("/api/users", json={"email": "a@b.test", "full_name": "A", "password": "Str0ng-Passw0rd!"},
                           headers=h).status_code == 403
        assert client.patch(f"/api/users/{users['emp'].id}/roles", json={"roles": ["admin"]}, headers=h).status_code == 403
        assert client.patch(f"/api/users/{users['emp'].id}", json={"is_active": False}, headers=h).status_code == 403
        assert client.get("/api/admin/audit-logs", headers=h).status_code == 403
        assert client.get("/api/admin/metrics", headers=h).status_code == 403
        assert client.post("/api/admin/maintenance/purge", headers=h).status_code == 403


def test_privilege_escalation_via_body_or_headers_is_rejected(client, tokens, users):
    h = auth(tokens["emp"])
    assert client.patch(f"/api/users/{users['emp'].id}/roles", json={"roles": ["admin"]}, headers=h).status_code == 403
    assert client.get("/api/users", headers={**h, "X-Role": "admin", "X-User-Id": users["admin"].id}).status_code == 403
    assert client.get("/api/users?role=admin&user_id=" + users["admin"].id, headers=h).status_code == 403
    me = client.get("/api/auth/me", headers={**h, "X-Role": "admin"}).get_json()
    assert me["roles"] == ["employee"]


def test_admin_creates_user_who_must_change_password(client, tokens):
    resp = client.post("/api/users", headers=auth(tokens["admin"]), json={
        "email": "New.Person@corp.test", "full_name": "New Person", "password": "Initial-Passw0rd-X", "roles": ["finance"]})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["email"] == "new.person@corp.test" and body["roles"] == ["finance"] and body["must_change_password"]
    assert "Initial-Passw0rd-X" not in json.dumps(body) and "password_hash" not in body and "argon2" not in json.dumps(body)
    assert client.post("/api/users", headers=auth(tokens["admin"]), json={
        "email": "new.person@corp.test", "full_name": "Dup", "password": "Initial-Passw0rd-X"}).status_code == 409


def test_weak_passwords_and_unknown_roles_rejected(client, tokens):
    h = auth(tokens["admin"])
    for pw in ("short1A", "alllowercase12345", "ALLUPPERCASE12345", "NoDigitsInHereAtAll"):
        r = client.post("/api/users", headers=h, json={"email": "w@corp.test", "full_name": "W", "password": pw})
        assert r.status_code == 400, pw
    r = client.post("/api/users", headers=h, json={"email": "w@corp.test", "full_name": "W",
                                                   "password": "Str0ng-Passw0rd!", "roles": ["superuser"]})
    assert r.status_code == 400 and r.get_json()["error"]["code"] == "unknown_role"


def test_no_public_self_registration(client):
    assert client.post("/api/auth/register", json={"email": "a@b.test", "password": "Str0ng-Passw0rd!"}).status_code in (404, 405)


def test_last_admin_and_self_demotion_protected(client, tokens, users):
    h = auth(tokens["admin"])
    assert client.patch(f"/api/users/{users['admin'].id}/roles", json={"roles": ["employee"]}, headers=h).status_code == 403
    assert client.patch(f"/api/users/{users['admin'].id}", json={"is_active": False}, headers=h).status_code == 409


def test_admin_password_reset_forces_change_and_kills_sessions(client, tokens, users):
    import time

    time.sleep(1.1)
    r = client.patch(f"/api/users/{users['hr'].id}", json={"password": "Reset-Passw0rd-Now"},
                     headers=auth(tokens["admin"]))
    assert r.status_code == 200 and r.get_json()["must_change_password"]
    assert client.get("/api/auth/me", headers=auth(tokens["hr"])).status_code == 401


def test_audit_log_records_security_events_without_secrets(client, tokens, users):
    client.post("/api/auth/login", json={"email": "hr@corp.test", "password": "Wrong-Passw0rd-1"})
    client.get("/api/users", headers=auth(tokens["emp"]))  # denied
    client.patch(f"/api/users/{users['emp'].id}/roles", json={"roles": ["employee", "hr"]}, headers=auth(tokens["admin"]))
    events = client.get("/api/admin/audit-logs?per_page=100", headers=auth(tokens["admin"])).get_json()["items"]
    types = {(e["event_type"], e["result"]) for e in events}
    assert ("auth.login", "failure") in types and ("authz.denied", "denied") in types
    assert ("user.roles_change", "success") in types and ("auth.login", "success") in types
    blob = json.dumps(events)
    assert "Wrong-Passw0rd-1" not in blob and PASSWORD not in blob and "access_token" not in blob
    assert "hr@corp.test" not in json.dumps([e for e in events if e["event_type"] == "auth.login" and e["result"] == "failure"])
    assert all(e["correlation_id"] for e in events)
    failures = client.get("/api/admin/audit-logs?result=denied", headers=auth(tokens["admin"])).get_json()
    assert failures["total"] >= 1 and all(e["result"] == "denied" for e in failures["items"])


def test_chat_content_is_never_written_to_audit_logs_or_metrics(client, tokens, corpus):
    chat(client, tokens["hr"], "What is the confidential salary for the VP of Sales?")
    audit_blob = json.dumps(client.get("/api/admin/audit-logs?per_page=100", headers=auth(tokens["admin"])).get_json())
    metrics_blob = json.dumps(client.get("/api/admin/metrics", headers=auth(tokens["admin"])).get_json())
    for blob in (audit_blob, metrics_blob):
        assert CANARY_HR not in blob and "confidential salary" not in blob


def test_metrics_endpoint_reports_pipeline_counters(client, tokens, corpus):
    from app.core.metrics import metrics

    metrics.reset()
    chat(client, tokens["hr"], "What is the confidential salary for the VP of Sales?")
    chat(client, tokens["emp"], "confidential salary VP Sales")  # authorised set has no match -> no answer
    m = client.get("/api/admin/metrics", headers=auth(tokens["admin"])).get_json()
    c = m["runtime"]["counters"]
    assert c["rag_answers_total"] == 2 and c["rag_no_answer_total"] >= 1
    assert c["retrieval_candidates_authorized_total"] >= 1
    assert m["runtime"]["derived"]["no_answer_rate"] is not None
    assert m["documents_by_status"]["READY"] == 4
    assert "retrieval_seconds" in m["runtime"]["timers"]


def test_retention_purge_removes_old_data_only(client, tokens, users, app):
    old = Conversation(user_id=users["hr"].id, title="old")
    old.updated_at = utcnow() - timedelta(days=app.config["CHAT_RETENTION_DAYS"] + 5)
    fresh = Conversation(user_id=users["hr"].id, title="fresh")
    db.session.add_all([old, fresh])
    db.session.add(AuditEvent(event_type="x", result="success", created_at=utcnow() - timedelta(days=9999)))
    db.session.flush()
    old_id, fresh_id = old.id, fresh.id  # read ids BEFORE commit: touching them afterwards opens a read
    db.session.commit()                  # transaction that makes SQLite lock out the request's writer
    r = client.post("/api/admin/maintenance/purge", headers=auth(tokens["admin"])).get_json()
    assert r["conversations"] == 1 and r["audit_events"] >= 1
    assert db.session.get(Conversation, old_id) is None and db.session.get(Conversation, fresh_id) is not None


def test_pagination_is_bounded(client, tokens):
    assert client.get("/api/users?per_page=1000", headers=auth(tokens["admin"])).status_code == 400
    assert client.get("/api/users?page=0", headers=auth(tokens["admin"])).status_code == 400
    r = client.get("/api/users?per_page=2", headers=auth(tokens["admin"])).get_json()
    assert len(r["items"]) == 2 and r["total"] == 5 and r["pages"] == 3


def test_openapi_spec_is_served_and_lists_core_routes(client):
    spec = client.get("/apispec.json").get_json()
    for path in ("/api/auth/login", "/api/chat", "/api/documents/upload", "/api/admin/audit-logs", "/api/health"):
        assert path in spec["paths"], path


def test_oversized_json_and_wrong_content_type_rejected(client, tokens):
    h = auth(tokens["emp"])
    assert client.post("/api/chat", data="message=hi", headers=h).status_code == 400
    assert client.post("/api/chat", data=b"[1,2,3]", headers={**h, "Content-Type": "application/json"}).status_code == 400
