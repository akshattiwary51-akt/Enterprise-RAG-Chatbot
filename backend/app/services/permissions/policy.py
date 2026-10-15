"""Initial RBAC policy: roles, capability permissions and idempotent seeding.

Capability permissions govern *actions* (manage users, upload, ...). They never
grant access to document *content*; that is decided only by document ACL grants.
"""
from __future__ import annotations

from ...extensions import db
from ...models import Permission, Role

PERMISSIONS: dict[str, str] = {
    "users.manage": "Create users, change roles, activate/deactivate accounts",
    "documents.upload": "Upload documents",
    "documents.manage_permissions": "View the document inventory, change document ACLs and delete documents",
    "audit.view": "View security audit records and operational metrics",
}

ROLES: dict[str, dict] = {
    "admin": {
        "description": "Administrators manage users, documents and audit records",
        "permissions": ["users.manage", "documents.upload", "documents.manage_permissions", "audit.view"],
    },
    "hr": {"description": "Human Resources", "permissions": ["documents.upload"]},
    "finance": {"description": "Finance", "permissions": ["documents.upload"]},
    "engineering": {"description": "Engineering", "permissions": ["documents.upload"]},
    "employee": {"description": "General employee", "permissions": []},
}

# Roles that may never be granted access to highly confidential documents.
LOW_TRUST_ROLES_FOR_HIGHLY_CONFIDENTIAL = {"employee"}


def seed_roles_and_permissions() -> None:
    """Create missing roles/permissions. Never removes or downgrades existing grants."""
    perms: dict[str, Permission] = {p.name: p for p in Permission.query.all()}
    for name, desc in PERMISSIONS.items():
        if name not in perms:
            perms[name] = Permission(name=name, description=desc)
            db.session.add(perms[name])
    existing = {r.name: r for r in Role.query.all()}
    for name, spec in ROLES.items():
        role = existing.get(name)
        if role is None:
            role = Role(name=name, description=spec["description"])
            db.session.add(role)
            role.permissions = [perms[p] for p in spec["permissions"]]
    db.session.commit()
