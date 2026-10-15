"""Response shaping. Kept in one place so we never accidentally serialise internals
(password hashes, storage paths, checksums of other users' files...)."""
from __future__ import annotations

from ..models import Document, User


def user_dict(user: User, include_permissions: bool = False) -> dict:
    out = {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "roles": user.role_names,
        "department": user.department,
        "is_active": user.is_active,
        "must_change_password": user.must_change_password,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }
    if include_permissions:
        out["permissions"] = sorted(user.permission_names)
    return out


def document_dict(doc: Document, include_acl: bool = False, can_read: bool | None = None) -> dict:
    out = {
        "id": doc.id,
        "filename": doc.original_filename,
        "file_type": doc.file_type,
        "size_bytes": doc.size_bytes,
        "classification": doc.classification,
        "owner_team": doc.owner_team,
        "department": doc.department,
        "status": doc.status,
        "error_message": doc.error_message,
        "chunk_count": doc.chunk_count,
        "uploaded_by": doc.uploader.email if doc.uploader else None,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }
    if can_read is not None:
        out["can_read"] = can_read
    if include_acl:
        out["allowed_roles"] = sorted(g.role.name for g in doc.role_grants)
        out["user_grants"] = sorted(
            ({"id": g.user_id, "email": g.user.email} for g in doc.user_grants), key=lambda x: x["email"]
        )
    return out


def paginate(query, page: int, per_page: int, mapper) -> dict:
    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    return {
        "items": [mapper(i) for i in items],
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": (total + per_page - 1) // per_page,
    }
