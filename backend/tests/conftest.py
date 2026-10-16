"""Shared fixtures. No network, no paid model calls: deterministic fake embeddings, a fake chat model
that records every prompt it receives, and an isolated persistent Chroma store per test."""
from __future__ import annotations

import io
import os

os.environ["APP_ENV"] = "testing"

import pytest

from app import create_app
from app.core.security import hash_password
from app.demo import (
    BUDGET_ROWS, ENG_ARCH_MD, HANDBOOK_TXT, HR_POLICY_MD, SALARY_ROWS, make_csv, make_xlsx,
)
from app.extensions import db
from app.models import Role, User
from app.services.permissions.policy import seed_roles_and_permissions

PASSWORD = "Str0ng-Test-Passw0rd"

USERS = {
    "admin": ("admin@test.local", ["admin"], None),
    "hr": ("hr@test.local", ["hr"], None),
    "finance": ("finance@test.local", ["finance"], None),
    "eng": ("eng@test.local", ["engineering"], None),
    "junior": ("junior@test.local", ["engineering"], None),
    "employee": ("employee@test.local", ["employee"], None),
}


def make_app(tmp_path, **overrides):
    # Default: throw-away SQLite file per test. Set TEST_DATABASE_URL (e.g. a disposable PostgreSQL database)
    # to run the identical suite against another engine; the schema is dropped and recreated for every app.
    pg_url = os.environ.get("TEST_DATABASE_URL")
    cfg = {
        "SECRET_KEY": "s" * 40, "JWT_SECRET_KEY": "j" * 40,
        "SQLALCHEMY_DATABASE_URI": pg_url or f"sqlite:///{tmp_path}/test.db",
        "EMBEDDINGS_PROVIDER": "fake", "LLM_PROVIDER": "fake",
        "UPLOAD_DIR": str(tmp_path / "uploads"), "CHROMA_DIR": str(tmp_path / "chroma"),
        "AUDIT_LOG_FILE": None, "RATELIMIT_ENABLED": False,
        "TOP_K": 4, "SIMILARITY_THRESHOLD": 0.2,
    }
    cfg.update(overrides)
    app = create_app(cfg)
    with app.app_context():
        if pg_url:
            db.drop_all()
        db.create_all()
        seed_roles_and_permissions()
    return app


@pytest.fixture()
def app(tmp_path):
    app = make_app(tmp_path)
    ctx = app.app_context()
    ctx.push()
    yield app
    db.session.remove()
    ctx.pop()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def services(app):
    return app.extensions["services"]


@pytest.fixture()
def llm(services):
    return services.llm


@pytest.fixture()
def users(app):
    roles = {r.name: r for r in Role.query.all()}
    out = {}
    for key, (email, role_names, dept) in USERS.items():
        u = User(email=email, full_name=key.title(), password_hash=hash_password(PASSWORD),
                 roles=[roles[r] for r in role_names], department=dept)
        db.session.add(u)
        out[key] = u
    db.session.commit()
    return out


def login(client, email, password=PASSWORD):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


@pytest.fixture()
def auth(client, users):
    """auth('hr') -> Authorization headers for that seeded user."""
    cache: dict[str, dict] = {}

    def _auth(key: str) -> dict:
        if key not in cache:
            tokens = login(client, USERS[key][0])
            cache[key] = {"Authorization": f"Bearer {tokens['access_token']}"}
        return cache[key]

    return _auth


def upload(client, headers, filename, data, roles=(), classification="internal", user_grants=(), **extra):
    form = {"file": (io.BytesIO(data), filename), "classification": classification}
    if roles:
        form["allowed_roles"] = list(roles)
    if user_grants:
        form["user_grants"] = list(user_grants)
    form.update(extra)
    return client.post("/api/documents/upload", data=form, headers=headers, content_type="multipart/form-data")


@pytest.fixture()
def corpus(client, auth):
    """Five synthetic documents with distinct role grants; returns {name: document_id}."""
    h = auth("admin")
    spec = [
        ("hr_policy", "hr_leave_policy.md", HR_POLICY_MD.encode(), ["hr"], "confidential"),
        ("salaries", "hr_salaries.xlsx", make_xlsx(SALARY_ROWS, "Salaries"), ["hr"], "highly_confidential"),
        ("eng", "engineering_architecture.md", ENG_ARCH_MD.encode(), ["engineering"], "internal"),
        ("budget", "finance_budget.csv", make_csv(BUDGET_ROWS), ["finance"], "confidential"),
        ("handbook", "company_handbook.txt", HANDBOOK_TXT.encode(), ["hr", "finance", "engineering", "employee"],
         "internal"),
    ]
    ids = {}
    for key, fname, data, roles, cls in spec:
        r = upload(client, h, fname, data, roles, cls)
        assert r.status_code == 201, r.get_json()
        assert r.get_json()["status"] == "READY", r.get_json()
        ids[key] = r.get_json()["id"]
    return ids


def ask(client, headers, message, conversation_id=None):
    body = {"message": message}
    if conversation_id:
        body["conversation_id"] = conversation_id
    return client.post("/api/chat", json=body, headers=headers)


def all_prompt_text(llm) -> str:
    return "\n".join(c["system"] + "\n" + c["user"] for c in llm.calls)
