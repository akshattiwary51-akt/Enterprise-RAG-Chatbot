from __future__ import annotations

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import func

from ..auth.decorators import login_required, permission_required
from ..core.errors import bad_request, conflict, forbidden, not_found
from ..core.security import hash_password, validate_password_strength
from ..extensions import db
from ..models import Role, User, utcnow
from ..schemas import CreateUserSchema, PageSchema, UpdateRolesSchema, UpdateUserSchema, load_json
from ..services.audit import service as audit
from .serializers import paginate, user_dict

bp = Blueprint("users", __name__, url_prefix="/api")


def _active_admin_count(excluding: str | None = None) -> int:
    q = (
        db.session.query(func.count(User.id))
        .join(User.roles)
        .filter(Role.name == "admin", User.is_active.is_(True))
    )
    if excluding:
        q = q.filter(User.id != excluding)
    return q.scalar() or 0


def _resolve_roles(names: list[str]) -> list[Role]:
    names = sorted(set(names))
    roles = Role.query.filter(Role.name.in_(names)).all()
    if {r.name for r in roles} != set(names):
        raise bad_request("Unknown role", code="unknown_role")
    return roles


@bp.get("/roles")
@login_required
def list_roles():
    """List available roles (names/descriptions only).
    ---
    tags: [Users]
    responses: {200: {description: Roles}}
    """
    roles = Role.query.order_by(Role.name).all()
    return jsonify({"items": [{"name": r.name, "description": r.description} for r in roles]})


@bp.get("/users")
@permission_required("users.manage")
def list_users():
    """List users (admin).
    ---
    tags: [Users]
    responses: {200: {description: Paginated users}, 403: {description: Forbidden}}
    """
    args = PageSchema().load(request.args)
    query = User.query.order_by(User.email)
    return jsonify(paginate(query, args["page"], args["per_page"], user_dict))


@bp.post("/users")
@permission_required("users.manage")
def create_user():
    """Create a user (invitation-style: admin sets an initial password the user must change).
    ---
    tags: [Users]
    responses: {201: {description: Created}, 400: {description: Invalid}, 409: {description: Email exists}}
    """
    data = load_json(CreateUserSchema(), request.get_json(silent=True))
    email = data["email"].strip().lower()
    if User.query.filter_by(email=email).first():
        raise conflict("A user with this email already exists", code="email_exists")
    problem = validate_password_strength(data["password"], current_app.config["MIN_PASSWORD_LENGTH"])
    if problem:
        raise bad_request(problem, code="weak_password")
    user = User(
        email=email,
        full_name=data["full_name"].strip(),
        password_hash=hash_password(data["password"]),
        department=(data.get("department") or None),
        must_change_password=True,
        roles=_resolve_roles(data["roles"]),
    )
    db.session.add(user)
    db.session.commit()
    audit.record("user.create", actor=g.current_user, target_type="user", target_id=user.id,
                 details={"roles": user.role_names})
    return jsonify(user_dict(user)), 201


@bp.patch("/users/<user_id>")
@permission_required("users.manage")
def update_user(user_id: str):
    """Update profile fields, activation or reset the password (admin).
    ---
    tags: [Users]
    responses: {200: {description: Updated}, 404: {description: Not found}}
    """
    user = db.session.get(User, user_id)
    if user is None:
        raise not_found("User not found")
    data = load_json(UpdateUserSchema(), request.get_json(silent=True))
    changed = []
    if "full_name" in data:
        user.full_name = data["full_name"].strip()
        changed.append("full_name")
    if "department" in data:
        user.department = data["department"] or None
        changed.append("department")
    if "is_active" in data and data["is_active"] != user.is_active:
        if not data["is_active"] and "admin" in user.role_names and _active_admin_count(excluding=user.id) == 0:
            raise conflict("Cannot deactivate the last active administrator", code="last_admin")
        user.is_active = data["is_active"]
        changed.append("is_active")
    if "password" in data:
        problem = validate_password_strength(data["password"], current_app.config["MIN_PASSWORD_LENGTH"])
        if problem:
            raise bad_request(problem, code="weak_password")
        user.password_hash = hash_password(data["password"])
        user.must_change_password = True
        user.token_version += 1
        changed.append("password_reset")
    db.session.commit()
    audit.record("user.update", actor=g.current_user, target_type="user", target_id=user.id,
                 details={"fields": changed})
    return jsonify(user_dict(user))


@bp.patch("/users/<user_id>/roles")
@permission_required("users.manage")
def update_user_roles(user_id: str):
    """Replace a user's roles (admin). Effective immediately for retrieval.
    ---
    tags: [Users]
    responses: {200: {description: Updated}, 409: {description: Would remove last admin}}
    """
    user = db.session.get(User, user_id)
    if user is None:
        raise not_found("User not found")
    data = load_json(UpdateRolesSchema(), request.get_json(silent=True))
    roles = _resolve_roles(data["roles"])
    if user.id == g.current_user.id and "admin" not in {r.name for r in roles}:
        raise forbidden("Administrators cannot remove their own admin role", code="self_demotion")
    before = user.role_names
    if "admin" in before and "admin" not in {r.name for r in roles} and _active_admin_count(excluding=user.id) == 0:
        raise conflict("Cannot remove the last administrator", code="last_admin")
    user.roles = roles
    db.session.commit()
    audit.record("user.roles_change", actor=g.current_user, target_type="user", target_id=user.id,
                 details={"before": before, "after": user.role_names})
    return jsonify(user_dict(user))
