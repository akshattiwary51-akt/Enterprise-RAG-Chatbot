"""Helpers for the earlier test suite (tests/legacy). Self-contained on purpose: it must not import
`conftest`, because pytest registers several conftest modules under that one name."""
from __future__ import annotations

import io

from app.core.security import hash_password
from app.extensions import db
from app.models import Role, User

PASSWORD = "Str0ng-Passw0rd!"
# Synthetic canaries (distinct, invented). Seeing one in a response for a user who may not read the
# document that holds it means access control is broken.
CANARY_HR = "CANARY-HR-LEGACY-5T2W"
CANARY_ENG = "CANARY-ENG-LEGACY-8N6R"
CANARY_FIN = "CANARY-FIN-LEGACY-3C9V"


def make_user(email, role_names, active=True, department=None, full_name=None) -> User:
    roles = Role.query.filter(Role.name.in_(role_names)).all()
    user = User(email=email, full_name=full_name or email.split("@")[0].title(),
                password_hash=hash_password(PASSWORD), is_active=active, roles=roles, department=department)
    db.session.add(user)
    db.session.commit()
    return user


def login(client, email, password=PASSWORD) -> dict:
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def upload(client, headers, filename, data, roles=(), classification="internal", user_grants=(), **extra):
    form = {"file": (io.BytesIO(data), filename), "classification": classification}
    if roles:
        form["allowed_roles"] = list(roles)
    if user_grants:
        form["user_grants"] = list(user_grants)
    form.update(extra)
    return client.post("/api/documents/upload", data=form, headers=headers, content_type="multipart/form-data")


def chat(client, token, message, conversation_id=None):
    body = {"message": message}
    if conversation_id:
        body["conversation_id"] = conversation_id
    return client.post("/api/chat", json=body, headers=auth(token))


LEGACY_DOCS = {
    # first sentence carries the answer: the fake LLM quotes the first sentence of the top passage
    "hr": ("hr_salaries.txt", ["hr"], "highly_confidential",
           f"The senior engineer salary band tops out at 185000 per year. Compensation records are confidential. "
           f"Reference marker {CANARY_HR}."),
    "hr2": ("hr_exec_compensation.txt", ["hr"], "confidential",
            f"The confidential salary for the VP of Sales is 240000 per year. Reference marker {CANARY_HR}."),
    "eng": ("eng_architecture.md", ["engineering"], "internal",
            f"The payment service handles retries with an event queue using exponential backoff. "
            f"Releases follow a blue green deployment. Reference marker {CANARY_ENG}."),
    "fin": ("finance_budget.txt", ["finance"], "confidential",
            f"The marketing budget for next year is 1.2 million. The CFO holds a forecast review every April. "
            f"Reference marker {CANARY_FIN}."),
}
