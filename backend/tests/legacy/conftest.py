"""Fixtures for the earlier suite. They override same-named fixtures in tests/conftest.py (users, corpus, auth)
for this directory only; `app`, `client`, `services` and `llm` are inherited."""
import pytest

from app.models import Role
from legacy_support import LEGACY_DOCS, auth, login, make_user, upload

LEGACY_USERS = {
    "admin": ("admin@corp.test", ["admin"]),
    "hr": ("hr@corp.test", ["hr"]),
    "finance": ("finance@corp.test", ["finance"]),
    "eng": ("eng@corp.test", ["engineering"]),
    "emp": ("emp@corp.test", ["employee"]),
}


@pytest.fixture()
def users(app):
    return {key: make_user(email, roles) for key, (email, roles) in LEGACY_USERS.items()}


@pytest.fixture()
def tokens(client, users):
    return {key: login(client, u.email)["access_token"] for key, u in users.items()}


@pytest.fixture()
def headers(tokens):
    return {key: auth(t) for key, t in tokens.items()}


@pytest.fixture(name="auth")
def auth_factory(headers):
    return lambda key: headers[key]


@pytest.fixture()
def corpus(client, headers, app):
    app.config["SIMILARITY_THRESHOLD"] = 0.3  # these tests assert "no answer" for loosely related queries
    ids = {}
    for key, (filename, roles, classification, text) in LEGACY_DOCS.items():
        r = upload(client, headers["admin"], filename, text.encode(), roles, classification)
        assert r.status_code == 201 and r.get_json()["status"] == "READY", r.get_json()
        ids[key] = r.get_json()["id"]
    return ids
