"""Evaluation harness (deterministic: fake embeddings + fake model, isolated temp stores).

Reports two independent groups of metrics:
  * ACCESS CONTROL (must be 100%): for 'denied' cases, forbidden strings never appear in the response
    AND never appear in the context sent to the model.
  * RETRIEVAL / GROUNDING (quality, informational): for 'allowed' cases, expected document cited,
    expected facts present, citation coverage.
Usage:  python eval/run_eval.py            (exit code 1 if any access-control case fails)
NOTE: with the fake embedder these numbers only validate the harness and the pipeline; run with real
embeddings (EMBEDDINGS_PROVIDER=openai) to measure real retrieval quality.
"""
import io, json, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SAMPLE = ROOT.parent / "data" / "sample"

from app import create_app  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Role, User  # noqa: E402
from app.services.permissions.policy import seed_roles_and_permissions  # noqa: E402

PW = "Eval-Passw0rd-Local-1"


def main() -> int:
    data = json.loads((ROOT / "eval" / "dataset.json").read_text())
    tmp = Path(tempfile.mkdtemp(prefix="rag-eval-"))
    app = create_app("testing", {
        "SECRET_KEY": "eval-secret-key-long-enough-0000000001", "JWT_SECRET_KEY": "eval-jwt-key-long-enough-00000000002",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp/'eval.db'}", "EMBEDDINGS_PROVIDER": "fake", "LLM_PROVIDER": "fake",
        "CHROMA_DIR": str(tmp / "chroma"), "UPLOAD_DIR": str(tmp / "up"), "RATELIMIT_ENABLED": False,
        "SIMILARITY_THRESHOLD": 0.05})
    results = []
    with app.app_context():
        db.create_all(); seed_roles_and_permissions()
        admin = User(email="admin@eval.test", full_name="Admin", password_hash=hash_password(PW),
                     roles=[Role.query.filter_by(name="admin").one()])
        db.session.add(admin)
        for r in ("hr", "finance", "engineering", "employee"):
            db.session.add(User(email=f"{r}@eval.test", full_name=r, password_hash=hash_password(PW),
                                roles=[Role.query.filter_by(name=r).one()]))
        db.session.commit()
        c = app.test_client()
        tok = lambda e: {"Authorization": "Bearer " + c.post("/api/auth/login", json={"email": e, "password": PW}).get_json()["access_token"]}
        ah = tok("admin@eval.test")
        for name, roles in data["documents"].items():
            r = c.post("/api/documents/upload", headers=ah, content_type="multipart/form-data", data={
                "file": (io.BytesIO((SAMPLE / name).read_bytes()), name), "allowed_roles": roles, "classification": "internal"})
            assert r.status_code == 201 and r.get_json()["status"] == "READY", (name, r.get_json())
        llm = app.extensions["services"].llm
        for case in data["cases"]:
            llm.calls.clear()
            body = c.post("/api/chat", headers=tok(f"{case['role']}@eval.test"), json={"message": case["question"]}).get_json()["message"]
            ctx = "\n".join(x["user"] for x in llm.calls); text = json.dumps(body)
            if case["expect_access"] == "denied":
                leaked = [s for s in case["forbidden_strings"] if s in text or s in ctx]
                results.append({"id": case["id"], "kind": "access", "pass": not leaked, "leaked": leaked})
            else:
                cited = {x["filename"] for x in body["citations"]}
                results.append({"id": case["id"], "kind": "quality", "pass": all(s in body["content"] for s in case["must_contain"]),
                                "doc_hit": bool(cited & set(case["expected_documents"])), "cited": bool(body["citations"])})
    acc = [r for r in results if r["kind"] == "access"]; q = [r for r in results if r["kind"] == "quality"]
    print(json.dumps({"access_control": {"cases": len(acc), "passed": sum(r["pass"] for r in acc), "failures": [r for r in acc if not r["pass"]]},
                      "retrieval_quality": {"cases": len(q), "answer_correct": sum(r["pass"] for r in q),
                                            "expected_doc_cited": sum(r["doc_hit"] for r in q), "citation_coverage": sum(r["cited"] for r in q)}}, indent=2))
    return 0 if all(r["pass"] for r in acc) else 1


if __name__ == "__main__":
    sys.exit(main())
