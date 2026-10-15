import io

from app.demo import CANARY_HR, HR_POLICY_MD
from app.extensions import db
from app.models import AuditEvent, User
from conftest import PASSWORD, USERS, ask, login, upload


# ---- capability / admin API protection -------------------------------------------------------
def test_employee_cannot_use_admin_apis(client, auth):
    h = auth("employee")
    for method, path in [("get", "/api/users"), ("get", "/api/admin/audit-logs"), ("get", "/api/admin/metrics")]:
        assert getattr(client, method)(path, headers=h).status_code == 403
    assert client.post("/api/users", json={"email": "x@y.zz", "full_name": "X", "password": PASSWORD}, headers=h).status_code == 403
    assert upload(client, h, "a.txt", b"hello world", ["employee"]).status_code == 403


def test_department_roles_cannot_use_admin_apis(client, auth):
    for who in ("hr", "finance", "eng"):
        h = auth(who)
        assert client.get("/api/users", headers=h).status_code == 403
        assert client.get("/api/admin/audit-logs", headers=h).status_code == 403
        assert client.get("/api/admin/metrics", headers=h).status_code == 403


def test_denied_admin_attempts_are_audited(client, auth):
    client.get("/api/users", headers=auth("employee"))
    events = AuditEvent.query.filter_by(event_type="authz.denied").all()
    assert events and events[0].result == "denied" and events[0].actor_label == USERS["employee"][0]


def test_admin_user_management(client, auth, users):
    h = auth("admin")
    r = client.post("/api/users", json={"email": "New.Hire@Test.Local", "full_name": "New Hire", "password": "Initial-Passw0rd!", "roles": ["employee"]}, headers=h)
    assert r.status_code == 201 and r.get_json()["email"] == "new.hire@test.local" and r.get_json()["must_change_password"]
    assert client.post("/api/users", json={"email": "new.hire@test.local", "full_name": "Dup", "password": "Initial-Passw0rd!"}, headers=h).status_code == 409
    assert client.post("/api/users", json={"email": "weak@test.local", "full_name": "W", "password": "weak"}, headers=h).status_code == 400
    assert client.post("/api/users", json={"email": "r@test.local", "full_name": "R", "password": "Initial-Passw0rd!", "roles": ["superuser"]}, headers=h).status_code == 400
    assert client.post("/api/users", json={"email": "m@test.local", "full_name": "M", "password": "Initial-Passw0rd!", "is_admin": True}, headers=h).status_code == 400
    assert any(e.event_type == "user.create" for e in AuditEvent.query.all())


def test_cannot_remove_own_admin_or_last_admin(client, auth, users):
    h = auth("admin")
    assert client.patch(f"/api/users/{users['admin'].id}/roles", json={"roles": ["employee"]}, headers=h).status_code == 403
    assert client.patch(f"/api/users/{users['admin'].id}", json={"is_active": False}, headers=h).status_code == 409


def test_role_change_audited_and_visible(client, auth, users):
    r = client.patch(f"/api/users/{users['employee'].id}/roles", json={"roles": ["hr", "employee"]}, headers=auth("admin"))
    assert r.status_code == 200 and r.get_json()["roles"] == ["employee", "hr"]
    ev = AuditEvent.query.filter_by(event_type="user.roles_change").one()
    assert ev.details["before"] == ["employee"] and ev.details["after"] == ["employee", "hr"]


# ---- document access -------------------------------------------------------------------------
def test_unauthorized_user_cannot_download_preview_or_view_metadata(client, auth, corpus):
    hr_doc = corpus["salaries"]
    for who in ("eng", "junior", "finance", "employee"):
        h = auth(who)
        for suffix in ("", "/download", "/preview", "/status"):
            r = client.get(f"/api/documents/{hr_doc}{suffix}", headers=h)
            assert r.status_code == 404, (who, suffix)
    nonexistent = client.get("/api/documents/00000000-0000-0000-0000-000000000000/download", headers=auth("eng"))
    restricted = client.get(f"/api/documents/{hr_doc}/download", headers=auth("eng"))
    assert nonexistent.get_json()["error"]["message"] == restricted.get_json()["error"]["message"]  # indistinguishable
    assert AuditEvent.query.filter_by(event_type="document.access").count() >= 4


def test_authorized_user_can_download_and_preview(client, auth, corpus):
    h = auth("hr")
    d = client.get(f"/api/documents/{corpus['hr_policy']}/download", headers=h)
    assert d.status_code == 200 and CANARY_HR.encode() in d.data
    assert "attachment" in d.headers["Content-Disposition"]
    start = client.get(f"/api/documents/{corpus['hr_policy']}/preview", headers=h)
    assert start.status_code == 200 and "Leave Policy" in start.get_json()["text"]  # first section
    from app.models import ParentPassage

    secret_passage = next(p for p in ParentPassage.query.filter_by(document_id=corpus["hr_policy"]) if CANARY_HR in p.text)
    p = client.get(f"/api/documents/{corpus['hr_policy']}/preview?passage_id={secret_passage.id}", headers=h)
    assert p.status_code == 200 and CANARY_HR in p.get_json()["text"]
    # the same passage id is useless to someone without access to the document
    assert client.get(f"/api/documents/{corpus['hr_policy']}/preview?passage_id={secret_passage.id}", headers=auth("eng")).status_code == 404


def test_admin_sees_inventory_but_not_content_without_grant(client, auth, corpus):
    h = auth("admin")
    listing = client.get("/api/documents", headers=h).get_json()
    assert listing["total"] == 5 and all("allowed_roles" in d for d in listing["items"])
    row = next(d for d in listing["items"] if d["id"] == corpus["salaries"])
    assert row["can_read"] is False
    assert client.get(f"/api/documents/{corpus['salaries']}/download", headers=h).status_code == 404
    assert client.get(f"/api/documents/{corpus['salaries']}/preview", headers=h).status_code == 404
    assert client.get(f"/api/documents/{corpus['salaries']}", headers=h).status_code == 200  # metadata only


def test_document_listing_hides_unauthorized_documents(client, auth, corpus):
    names = lambda who: {d["filename"] for d in client.get("/api/documents", headers=auth(who)).get_json()["items"]}
    assert names("employee") == {"company_handbook.txt"}
    assert names("eng") == {"company_handbook.txt", "engineering_architecture.md"}
    assert names("hr") == {"company_handbook.txt", "hr_leave_policy.md", "hr_salaries.xlsx"}
    leaked = client.get("/api/documents", headers=auth("employee")).get_data(as_text=True)
    assert "salaries" not in leaked.lower() and "allowed_roles" not in leaked


def test_non_admin_cannot_change_permissions_or_delete(client, auth, corpus):
    for who in ("hr", "eng", "employee"):
        h = auth(who)
        r = client.patch(f"/api/documents/{corpus['hr_policy']}/permissions", json={"allowed_roles": ["employee"], "classification": "internal"}, headers=h)
        assert r.status_code == 403
        assert client.delete(f"/api/documents/{corpus['hr_policy']}", headers=h).status_code == 403


def test_uploader_cannot_share_beyond_own_roles_or_to_users(client, auth, users):
    h = auth("hr")
    assert upload(client, h, "x.txt", b"some hr text here", ["finance"]).status_code == 400
    assert upload(client, h, "x.txt", b"some hr text here", ["hr"], user_grants=[users["eng"].id]).status_code == 400
    assert upload(client, h, "x.txt", b"some hr text here", ["hr"]).status_code == 201


def test_unknown_roles_users_and_classifications_rejected(client, auth, users):
    h = auth("admin")
    assert upload(client, h, "a.txt", b"hello there friend", ["nonexistent"]).status_code == 400
    assert upload(client, h, "b.txt", b"hello there friend", ["hr"], user_grants=["not-a-user-id"]).status_code == 400
    assert upload(client, h, "c.txt", b"hello there friend", ["hr"], classification="top_secret").status_code == 400
    assert upload(client, h, "d.txt", b"hello there friend", ["employee"], classification="highly_confidential").status_code == 400
    assert upload(client, h, "e.txt", b"hello there friend", ["hr"], role="admin").status_code == 400  # unknown form field


def test_permission_update_validates_and_applies(client, auth, corpus, users):
    h = auth("admin")
    url = f"/api/documents/{corpus['eng']}/permissions"
    assert client.patch(url, json={"allowed_roles": ["engineering", "bogus"], "classification": "internal"}, headers=h).status_code == 400
    assert client.patch(url, json={"allowed_roles": ["engineering"], "classification": "internal", "extra": 1}, headers=h).status_code == 400
    r = client.patch(url, json={"allowed_roles": ["engineering", "finance"], "classification": "confidential", "user_grants": [users["employee"].id]}, headers=h)
    assert r.status_code == 200
    body = r.get_json()
    assert body["allowed_roles"] == ["engineering", "finance"] and body["user_grants"][0]["email"] == USERS["employee"][0]
    ev = AuditEvent.query.filter_by(event_type="document.permissions_change").one()
    assert ev.details["before_roles"] == ["engineering"] and "finance" in ev.details["after_roles"]


def test_department_restriction_is_enforced(client, auth, users):
    users["hr"].department = "Payroll"
    users["eng"].department = "Platform"
    db.session.commit()
    h = auth("admin")
    r = upload(client, h, "payroll.txt", b"payroll processing happens on the 25th of every month", ["hr", "engineering"], department="Payroll")
    assert r.status_code == 201
    doc_id = r.get_json()["id"]
    assert client.get(f"/api/documents/{doc_id}/download", headers=auth("hr")).status_code == 200
    assert client.get(f"/api/documents/{doc_id}/download", headers=auth("eng")).status_code == 404  # role matches, department does not


def test_audit_logs_contain_no_secrets(client, auth, corpus):
    client.post("/api/auth/login", json={"email": "ghost@test.local", "password": "Sup3rSecretGuess!"})
    ask(client, auth("hr"), "What is the grievance escalation code?")
    logs = client.get("/api/admin/audit-logs?per_page=100", headers=auth("admin")).get_data(as_text=True)
    assert "Sup3rSecretGuess" not in logs and CANARY_HR not in logs and "ghost@test.local" not in logs
    assert "auth.login" in logs and "document.upload" in logs
    failures = client.get("/api/admin/audit-logs?result=failure", headers=auth("admin")).get_json()
    assert failures["total"] >= 1


def test_metrics_endpoint_shape(client, auth, corpus):
    ask(client, auth("hr"), "How many days of annual leave do employees receive?")
    m = client.get("/api/admin/metrics", headers=auth("admin")).get_json()
    assert m["documents_by_status"]["READY"] == 5
    assert m["runtime"]["counters"]["rag_answers_total"] >= 1
    assert "ingestion_failure_rate" in m["runtime"]["derived"] and "model_config_problems" in m
