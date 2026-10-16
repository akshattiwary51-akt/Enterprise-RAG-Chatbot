from __future__ import annotations

from flask import Blueprint, current_app, g, jsonify, request, send_file
from sqlalchemy import or_

from ..auth.decorators import login_required, permission_required
from ..core.errors import ApiError, bad_request, conflict, not_found
from ..extensions import db, limiter
from ..models import Document, DocumentRoleGrant, DocumentUserGrant, DocStatus, ParentPassage
from ..schemas import PageSchema, UpdatePermissionsSchema, UploadFormSchema, load_json
from ..services.audit import service as audit
from ..services.ingestion import pipeline
from ..services.ingestion.queue import IngestionAlreadyRunning, enqueue_ingestion, run_worker_once
from ..services.ingestion.validation import DOWNLOAD_MIME, UploadRejected, validate_upload
from ..services.permissions import service as perms
from ..services.rag.service import locator_label
from .serializers import document_dict, paginate

bp = Blueprint("documents", __name__, url_prefix="/api/documents")

_UPLOAD_FIELDS = {"allowed_roles", "user_grants", "classification", "owner_team", "department"}


def _is_inventory_admin(user) -> bool:
    return user.has_permission("documents.manage_permissions")


def _deny_read(doc_id: str, action: str):
    """Audit a denied/unknown content access and answer with a uniform 404
    (no distinction between 'missing' and 'forbidden')."""
    audit.record("document.access", result="denied", actor=g.current_user, target_type="document",
                 target_id=doc_id, details={"action": action})
    return not_found("Document not found")


def _apply_acl(doc: Document, roles, users, actor) -> None:
    doc.role_grants.clear()
    doc.user_grants.clear()
    db.session.flush()
    for r in roles:
        doc.role_grants.append(DocumentRoleGrant(document_id=doc.id, role_id=r.id, granted_by=actor.id))
    for u in users:
        doc.user_grants.append(DocumentUserGrant(document_id=doc.id, user_id=u.id, granted_by=actor.id))


def _form_to_dict() -> dict:
    unknown = set(request.form.keys()) - _UPLOAD_FIELDS
    if unknown:
        raise bad_request("Unknown form field(s)", code="validation_error", details={"fields": sorted(unknown)})
    out: dict = {}
    for key in ("allowed_roles", "user_grants"):
        values: list[str] = []
        for raw in request.form.getlist(key):
            values.extend(v.strip() for v in raw.split(",") if v.strip())
        out[key] = values
    for key in ("classification", "owner_team", "department"):
        if key in request.form and request.form[key] != "":
            out[key] = request.form[key]
    return out


@bp.post("/upload")
@permission_required("documents.upload")
@limiter.limit(lambda: current_app.config["UPLOAD_RATE_LIMIT"])
def upload():
    """Upload and index a document (multipart/form-data: file, allowed_roles, classification, ...).
    ---
    tags: [Documents]
    consumes: [multipart/form-data]
    responses: {202: {description: Stored and queued}, 400: {description: Rejected}, 409: {description: Duplicate}, 413: {description: Too large}}
    """
    user = g.current_user
    file = request.files.get("file")
    if file is None:
        raise bad_request("A 'file' part is required", code="missing_file")
    meta = UploadFormSchema().load(_form_to_dict())
    try:
        roles, users = perms.resolve_acl_assignment(user, meta["allowed_roles"], meta["user_grants"],
                                                    meta["classification"])
    except perms.AclValidationError as exc:
        raise bad_request(str(exc), code="invalid_acl")

    data = file.stream.read(current_app.config["MAX_CONTENT_LENGTH"] + 1)
    try:
        name, ftype = validate_upload(file.filename, data, current_app.config["MAX_CONTENT_LENGTH"],
                                      current_app.config["MAX_ZIP_UNCOMPRESSED_MB"])
    except UploadRejected as exc:
        audit.record("document.upload", result="failure", actor=user, details={"reason": str(exc)[:150]})
        raise bad_request(str(exc), code="upload_rejected")

    checksum = pipeline.sha256_hex(data)
    if Document.query.filter_by(checksum_sha256=checksum, uploaded_by=user.id).first():
        raise conflict("You have already uploaded an identical file", code="duplicate_document")

    storage_name = pipeline.store_file(data, ftype)
    doc = Document(
        original_filename=name, storage_name=storage_name, file_type=ftype, size_bytes=len(data),
        checksum_sha256=checksum, uploaded_by=user.id, owner_team=meta["owner_team"] or None,
        department=meta["department"] or None, classification=meta["classification"],
        status=DocStatus.PENDING.value,
    )
    db.session.add(doc)
    db.session.flush()
    _apply_acl(doc, roles, users, user)
    enqueue_ingestion(doc)
    db.session.commit()
    audit.record("document.upload", actor=user, target_type="document", target_id=doc.id,
                 details={"type": ftype, "size": len(data), "classification": doc.classification,
                          "roles": [r.name for r in roles]})

    if current_app.config["INGESTION_INLINE"]:
        run_worker_once(current_app._get_current_object(), doc.id)
        db.session.refresh(doc)
        return jsonify(document_dict(doc, include_acl=True)), 201
    return jsonify(document_dict(doc, include_acl=True)), 202


@bp.get("")
@login_required
def list_documents():
    """List documents: admins see the inventory; others see documents they may read plus their own uploads.
    ---
    tags: [Documents]
    responses: {200: {description: Paginated documents}}
    """
    user = g.current_user
    args = PageSchema().load(request.args)
    if _is_inventory_admin(user):
        query = Document.query.order_by(Document.created_at.desc())
        readable = set(perms.authorized_document_ids(user))
        return jsonify(paginate(query, args["page"], args["per_page"],
                                lambda d: document_dict(d, include_acl=True, can_read=d.id in readable)))
    query = Document.query.filter(
        or_(
            (Document.status == DocStatus.READY.value) & perms.acl_clause(user),
            Document.uploaded_by == user.id,
        )
    ).order_by(Document.created_at.desc())
    return jsonify(paginate(query, args["page"], args["per_page"], lambda d: document_dict(d)))


def _visible_metadata_doc(user, document_id: str) -> Document | None:
    doc = db.session.get(Document, document_id)
    if doc is None:
        return None
    if _is_inventory_admin(user) or doc.uploaded_by == user.id:
        return doc
    return doc if perms.get_readable_document(user, document_id) else None


@bp.get("/<document_id>")
@login_required
def get_document(document_id: str):
    """Document metadata (admin: inventory incl. ACL; others: only if readable or own upload).
    ---
    tags: [Documents]
    responses: {200: {description: Document}, 404: {description: Not found}}
    """
    doc = _visible_metadata_doc(g.current_user, document_id)
    if doc is None:
        raise _deny_read(document_id, "metadata")
    is_admin = _is_inventory_admin(g.current_user)
    return jsonify(document_dict(doc, include_acl=is_admin or doc.uploaded_by == g.current_user.id))


@bp.get("/<document_id>/status")
@login_required
def document_status(document_id: str):
    """Processing status of a document.
    ---
    tags: [Documents]
    responses: {200: {description: Status}, 404: {description: Not found}}
    """
    doc = _visible_metadata_doc(g.current_user, document_id)
    if doc is None:
        raise _deny_read(document_id, "status")
    return jsonify({"id": doc.id, "status": doc.status, "error_message": doc.error_message,
                    "chunk_count": doc.chunk_count})


@bp.get("/<document_id>/download")
@login_required
def download(document_id: str):
    """Download the original file. Requires current ACL access to the content itself.
    ---
    tags: [Documents]
    responses: {200: {description: File}, 404: {description: Not found}}
    """
    doc = perms.get_readable_document(g.current_user, document_id)
    if doc is None:
        raise _deny_read(document_id, "download")
    audit.record("document.download", actor=g.current_user, target_type="document", target_id=doc.id)
    path = pipeline.upload_path(doc.storage_name)
    if not path.exists():
        raise not_found("Document not found")
    return send_file(path, mimetype=DOWNLOAD_MIME[doc.file_type], as_attachment=True,
                     download_name=doc.original_filename)


@bp.get("/<document_id>/preview")
@login_required
def preview(document_id: str):
    """Text preview of a cited passage (or the document start). Same ACL as download.
    ---
    tags: [Documents]
    parameters: [{name: passage_id, in: query, type: string, required: false}]
    responses: {200: {description: Preview}, 404: {description: Not found}}
    """
    return _passage_response(document_id, request.args.get("passage_id"), "preview")


@bp.get("/<document_id>/passages/<passage_id>")
@login_required
def passage(document_id: str, passage_id: str):
    """Text of one cited passage. Same ACL as download; this is what the UI's citation modal calls.
    ---
    tags: [Documents]
    responses: {200: {description: Passage}, 404: {description: Not found / not permitted}}
    """
    return _passage_response(document_id, passage_id, "passage")


def _passage_response(document_id: str, passage_id: str | None, action: str):
    doc = perms.get_readable_document(g.current_user, document_id)
    if doc is None:
        raise _deny_read(document_id, action)
    q = ParentPassage.query.filter_by(document_id=doc.id, index_version=doc.index_version)
    p = q.filter_by(id=passage_id).first() if passage_id else q.order_by(ParentPassage.ordinal).first()
    if p is None and not passage_id:
        raise not_found("Preview not available")
    if p is None:  # unknown / stale passage id: same answer as "no access"
        raise _deny_read(document_id, action)
    return jsonify({"document_id": doc.id, "filename": doc.original_filename,
                    "classification": doc.classification, "location": locator_label(p.locator),
                    "text": p.text[:6000]})


@bp.patch("/<document_id>/permissions")
@permission_required("documents.manage_permissions")
def update_permissions(document_id: str):
    """Replace a document's ACL/classification. Effective immediately for retrieval.
    ---
    tags: [Documents]
    responses: {200: {description: Updated}, 400: {description: Invalid ACL}, 404: {description: Not found}}
    """
    doc = db.session.get(Document, document_id)
    if doc is None:
        raise not_found("Document not found")
    data = load_json(UpdatePermissionsSchema(), request.get_json(silent=True))
    try:
        roles, users = perms.resolve_acl_assignment(g.current_user, data["allowed_roles"], data["user_grants"],
                                                    data["classification"])
    except perms.AclValidationError as exc:
        raise bad_request(str(exc), code="invalid_acl")
    before = {"roles": sorted(g.role.name for g in doc.role_grants),
              "users": sorted(g.user_id for g in doc.user_grants), "classification": doc.classification}
    _apply_acl(doc, roles, users, g.current_user)
    doc.classification = data["classification"]
    doc.department = data["department"] or None
    doc.owner_team = data["owner_team"] or None
    db.session.commit()
    audit.record("document.permissions_change", actor=g.current_user, target_type="document", target_id=doc.id,
                 details={"before_roles": before["roles"], "after_roles": [r.name for r in roles],
                          "before_users": before["users"], "after_users": [u.id for u in users],
                          "classification": doc.classification})
    return jsonify(document_dict(doc, include_acl=True))


@bp.post("/<document_id>/reindex")
@permission_required("documents.manage_permissions")
def reindex(document_id: str):
    """Queue a document for re-indexing (idempotent).
    ---
    tags: [Documents]
    responses: {202: {description: Queued}, 404: {description: Not found}, 409: {description: Already processing}}
    """
    doc = db.session.get(Document, document_id)
    if doc is None:
        raise not_found("Document not found")
    try:
        enqueue_ingestion(doc)
    except IngestionAlreadyRunning:
        raise conflict("Document ingestion is already in progress", code="ingestion_in_progress")
    db.session.commit()
    audit.record("document.reindex", actor=g.current_user, target_type="document", target_id=document_id,
                 details={"status": "queued"})
    if current_app.config["INGESTION_INLINE"]:
        run_worker_once(current_app._get_current_object(), document_id)
        db.session.refresh(doc)
        return jsonify(document_dict(doc, include_acl=True))
    return jsonify(document_dict(doc, include_acl=True)), 202


@bp.delete("/<document_id>")
@permission_required("documents.manage_permissions")
def delete(document_id: str):
    """Delete a document, its indexed chunks and its stored file.
    ---
    tags: [Documents]
    responses: {200: {description: Deleted}, 404: {description: Not found}, 503: {description: Index cleanup failed; retry}}
    """
    doc = db.session.get(Document, document_id)
    if doc is None:
        raise not_found("Document not found")
    if doc.status == DocStatus.PROCESSING.value or (
        doc.ingestion_job is not None and doc.ingestion_job.status == "PROCESSING"
    ):
        raise conflict("Document ingestion is in progress", code="ingestion_in_progress")
    try:
        pipeline.delete_document(doc)
    except pipeline.DeletionFailed:
        audit.record("document.delete", result="failure", actor=g.current_user, target_type="document",
                     target_id=document_id)
        raise ApiError(503, "deletion_incomplete",
                       "The document is blocked from retrieval but cleanup is incomplete; retry the deletion")
    audit.record("document.delete", actor=g.current_user, target_type="document", target_id=document_id)
    return jsonify({"status": "deleted", "id": document_id})
