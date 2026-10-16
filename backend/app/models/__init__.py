"""Relational schema. PostgreSQL is authoritative for identity and ACLs."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Index, UniqueConstraint, event
from sqlalchemy.engine import Engine

from ..extensions import db


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@event.listens_for(Engine, "connect")
def _sqlite_fk_pragma(dbapi_connection, _):  # pragma: no cover - trivial
    # SQLite ignores foreign keys unless asked; keep dev/test behaviour aligned with PostgreSQL.
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


class DocStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"
    DELETING = "DELETING"


CLASSIFICATIONS = ("public", "internal", "confidential", "highly_confidential")

# ---- association tables -----------------------------------------------------
user_roles = db.Table(
    "user_roles",
    db.Column("user_id", db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    db.Column("role_id", db.String(36), db.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)

role_permissions = db.Table(
    "role_permissions",
    db.Column("role_id", db.String(36), db.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    db.Column("permission_id", db.String(36), db.ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class Permission(db.Model):
    __tablename__ = "permissions"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    name = db.Column(db.String(64), unique=True, nullable=False)
    description = db.Column(db.String(255), nullable=False, default="")


class Role(db.Model):
    __tablename__ = "roles"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    name = db.Column(db.String(64), unique=True, nullable=False)
    description = db.Column(db.String(255), nullable=False, default="")
    permissions = db.relationship("Permission", secondary=role_permissions, lazy="selectin")


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(200), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    department = db.Column(db.String(64), nullable=True)
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    last_login_at = db.Column(db.DateTime(timezone=True), nullable=True)
    # Embedded in every token as claim `tv`; bumping it invalidates all outstanding tokens at once
    # (password change / admin reset). An integer, so there is no same-second timing window.
    token_version = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    roles = db.relationship("Role", secondary=user_roles, lazy="selectin")

    @property
    def role_names(self) -> list[str]:
        return sorted(r.name for r in self.roles)

    @property
    def permission_names(self) -> set[str]:
        return {p.name for r in self.roles for p in r.permissions}

    def has_permission(self, name: str) -> bool:
        return self.is_active and name in self.permission_names


class DocumentRoleGrant(db.Model):
    __tablename__ = "document_role_grants"
    document_id = db.Column(db.String(36), db.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    role_id = db.Column(db.String(36), db.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    granted_by = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    granted_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    role = db.relationship("Role", lazy="joined")


class DocumentUserGrant(db.Model):
    __tablename__ = "document_user_grants"
    document_id = db.Column(db.String(36), db.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    granted_by = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    granted_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    user = db.relationship("User", foreign_keys=[user_id], lazy="joined")


class Document(db.Model):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("checksum_sha256", "uploaded_by", name="uq_document_checksum_uploader"),
        Index("ix_documents_status", "status"),
    )
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    original_filename = db.Column(db.String(255), nullable=False)
    storage_name = db.Column(db.String(128), nullable=False, unique=True)
    file_type = db.Column(db.String(16), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False)
    checksum_sha256 = db.Column(db.String(64), nullable=False)
    uploaded_by = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    owner_team = db.Column(db.String(64), nullable=True)
    department = db.Column(db.String(64), nullable=True)  # optional restriction: user.department must match
    classification = db.Column(db.String(32), nullable=False, default="internal")
    status = db.Column(db.String(16), nullable=False, default=DocStatus.PENDING.value)
    error_message = db.Column(db.String(500), nullable=True)
    chunk_count = db.Column(db.Integer, nullable=False, default=0)
    index_version = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    uploader = db.relationship("User", foreign_keys=[uploaded_by], lazy="joined")
    role_grants = db.relationship("DocumentRoleGrant", cascade="all, delete-orphan", lazy="selectin")
    user_grants = db.relationship("DocumentUserGrant", cascade="all, delete-orphan", lazy="selectin",
                                  foreign_keys="DocumentUserGrant.document_id")
    passages = db.relationship("ParentPassage", cascade="all, delete-orphan", lazy="select")


class ParentPassage(db.Model):
    """Larger text window behind each embedded child chunk (parent-document retrieval)."""

    __tablename__ = "parent_passages"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    document_id = db.Column(db.String(36), db.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    index_version = db.Column(db.Integer, nullable=False)
    ordinal = db.Column(db.Integer, nullable=False)
    locator = db.Column(db.JSON, nullable=False, default=dict)
    text = db.Column(db.Text, nullable=False)


class Conversation(db.Model):
    __tablename__ = "conversations"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False, default="New conversation")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    messages = db.relationship("Message", cascade="all, delete-orphan", order_by="Message.created_at, Message.seq",
                               lazy="select")


class Message(db.Model):
    __tablename__ = "messages"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    seq = db.Column(db.Integer, nullable=False, default=0)
    conversation_id = db.Column(db.String(36), db.ForeignKey("conversations.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    role = db.Column(db.String(16), nullable=False)  # user | assistant
    content = db.Column(db.Text, nullable=False)
    # [{"id","document_id","passage_id","locator"}] -- ids only; titles are resolved at read time
    citations = db.Column(db.JSON, nullable=False, default=list)
    no_answer = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class AuditEvent(db.Model):
    __tablename__ = "audit_events"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    actor_user_id = db.Column(db.String(36), nullable=True)  # no FK: events outlive users
    actor_label = db.Column(db.String(128), nullable=True)
    event_type = db.Column(db.String(64), nullable=False, index=True)
    target_type = db.Column(db.String(32), nullable=True)
    target_id = db.Column(db.String(64), nullable=True)
    result = db.Column(db.String(16), nullable=False)  # success | failure | denied
    correlation_id = db.Column(db.String(64), nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    details = db.Column(db.JSON, nullable=False, default=dict)


class RevokedToken(db.Model):
    __tablename__ = "revoked_tokens"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    jti = db.Column(db.String(64), unique=True, nullable=False, index=True)
    token_type = db.Column(db.String(16), nullable=False)
    user_id = db.Column(db.String(36), nullable=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
