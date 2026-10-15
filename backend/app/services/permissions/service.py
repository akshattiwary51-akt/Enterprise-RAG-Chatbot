"""Authoritative document-access decisions.

Every read path (retrieval revalidation, download, preview, citations, chat
history) goes through `acl_clause` so there is exactly one definition of
"who can see a document". Access is deny-by-default:

    active user
    AND ( one of the user's roles is granted on the document
          OR the user has an individual grant )
    AND ( the document has no department restriction
          OR the user's department equals it )

Capability permissions (e.g. admin) deliberately do NOT widen this.
"""
from __future__ import annotations

from sqlalchemy import and_, exists, false, or_, select

from ...extensions import db
from ...models import (
    CLASSIFICATIONS,
    Document,
    DocumentRoleGrant,
    DocumentUserGrant,
    DocStatus,
    Role,
    User,
)
from .policy import LOW_TRUST_ROLES_FOR_HIGHLY_CONFIDENTIAL


def acl_clause(user: User):
    """SQLAlchemy boolean clause selecting Document rows this user may read."""
    if user is None or not user.is_active:
        return false()
    role_ids = [r.id for r in user.roles]
    via_role = (
        exists().where(
            and_(DocumentRoleGrant.document_id == Document.id, DocumentRoleGrant.role_id.in_(role_ids))
        )
        if role_ids
        else false()
    )
    via_user = exists().where(
        and_(DocumentUserGrant.document_id == Document.id, DocumentUserGrant.user_id == user.id)
    )
    # NB: `column == None` would compile to IS NULL for users without a department,
    # which would wrongly match unrestricted documents only -- handle explicitly.
    if user.department:
        dept_ok = or_(Document.department.is_(None), Document.department == user.department)
    else:
        dept_ok = Document.department.is_(None)
    return and_(or_(via_role, via_user), dept_ok)


def authorized_document_ids(user: User) -> list[str]:
    """IDs of READY documents whose content this user may currently retrieve."""
    stmt = select(Document.id).where(Document.status == DocStatus.READY.value, acl_clause(user))
    return [row[0] for row in db.session.execute(stmt)]


def get_readable_document(user: User, document_id: str, require_ready: bool = True) -> Document | None:
    """Return the document if the user may read it, else None (caller must not distinguish
    'missing' from 'forbidden' in its response)."""
    stmt = select(Document).where(Document.id == document_id, acl_clause(user))
    if require_ready:
        stmt = stmt.where(Document.status == DocStatus.READY.value)
    return db.session.execute(stmt).scalar_one_or_none()


def readable_ids_among(user: User, document_ids: list[str]) -> set[str]:
    """Subset of `document_ids` that is READY and readable by the user right now."""
    if not document_ids:
        return set()
    stmt = select(Document.id).where(
        Document.id.in_(set(document_ids)), Document.status == DocStatus.READY.value, acl_clause(user)
    )
    return {row[0] for row in db.session.execute(stmt)}


# ---- ACL assignment validation ----------------------------------------------
class AclValidationError(ValueError):
    pass


def resolve_acl_assignment(
    actor: User,
    role_names: list[str],
    user_ids: list[str],
    classification: str,
) -> tuple[list[Role], list[User]]:
    """Validate a requested ACL server-side and return the concrete Role/User rows."""
    if classification not in CLASSIFICATIONS:
        raise AclValidationError(f"classification must be one of: {', '.join(CLASSIFICATIONS)}")

    role_names = sorted(set(role_names))
    roles = Role.query.filter(Role.name.in_(role_names)).all() if role_names else []
    missing = set(role_names) - {r.name for r in roles}
    if missing:
        raise AclValidationError("Unknown role(s) in allowed_roles")

    users = User.query.filter(User.id.in_(set(user_ids))).all() if user_ids else []
    if len(users) != len(set(user_ids)):
        raise AclValidationError("Unknown user(s) in user_grants")
    if any(not u.is_active for u in users):
        raise AclValidationError("Cannot grant access to a deactivated user")

    if classification == "highly_confidential" and any(
        r.name in LOW_TRUST_ROLES_FOR_HIGHLY_CONFIDENTIAL for r in roles
    ):
        raise AclValidationError("Highly confidential documents cannot be granted to the 'employee' role")

    # Non-admin uploaders may only share with roles they hold themselves (prevents lateral escalation).
    if not actor.has_permission("documents.manage_permissions"):
        own = {r.id for r in actor.roles}
        if any(r.id not in own for r in roles):
            raise AclValidationError("You can only grant access to roles you belong to")
        if users:
            raise AclValidationError("Only administrators can grant access to individual users")
    return roles, users
