"""DEVELOPMENT ONLY: create demo users and index the synthetic sample documents.

* Refuses to run when APP_ENV=production.
* Generates a random password per demo user and prints it ONCE; nothing is hard-coded or committed.
Run from backend/:  python ../scripts/seed_demo.py
"""
import io, os, secrets, string, sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
from app import create_app  # noqa: E402

if os.environ.get("APP_ENV", "development") == "production":
    sys.exit("Refusing to seed demo data in production.")

from app.core.security import hash_password  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Document, Role, User  # noqa: E402
from app.services.ingestion import pipeline  # noqa: E402
from app.services.permissions.policy import seed_roles_and_permissions  # noqa: E402

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample"
DOCS = {"hr_compensation_policy.md": ["hr"], "hr_employee_handbook.md": ["hr", "finance", "engineering", "employee"],
        "eng_architecture.md": ["engineering"], "finance_budget_2027.csv": ["finance"]}
USERS = [("admin", "admin"), ("hr", "hr"), ("finance", "finance"), ("engineering", "engineering"), ("employee", "employee")]


def rand_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(16)) + "aA1"


app = create_app()
with app.app_context():
    seed_roles_and_permissions()
    created = {}
    for local, role in USERS:
        email = f"{local}@demo.local"
        if User.query.filter_by(email=email).first():
            continue
        pw = rand_password()
        db.session.add(User(email=email, full_name=f"Demo {local.title()}", password_hash=hash_password(pw),
                            roles=[Role.query.filter_by(name=role).one()]))
        created[email] = pw
    db.session.commit()
    admin = User.query.filter_by(email="admin@demo.local").one()
    for name, roles in DOCS.items():
        data = (SAMPLE / name).read_bytes()
        if Document.query.filter_by(checksum_sha256=pipeline.sha256_hex(data), uploaded_by=admin.id).first():
            continue
        from app.services.ingestion.validation import validate_upload
        from app.models import DocumentRoleGrant
        display, ftype = validate_upload(name, data, app.config["MAX_CONTENT_LENGTH"], app.config["MAX_ZIP_UNCOMPRESSED_MB"])
        doc = Document(original_filename=display, storage_name=pipeline.store_file(data, ftype), file_type=ftype,
                       size_bytes=len(data), checksum_sha256=pipeline.sha256_hex(data), uploaded_by=admin.id,
                       classification="internal", status="PENDING")
        db.session.add(doc); db.session.flush()
        for r in Role.query.filter(Role.name.in_(roles)).all():
            doc.role_grants.append(DocumentRoleGrant(document_id=doc.id, role_id=r.id, granted_by=admin.id))
        db.session.commit()
        print(f"{name}: {pipeline.ingest_document(doc.id).status}")
    if created:
        print("\nDemo credentials (shown once; store them somewhere safe):")
        for e, p in created.items():
            print(f"  {e}  {p}")
