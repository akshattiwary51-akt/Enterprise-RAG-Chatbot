# Enterprise RAG Chatbot (role-based access)

Internal chatbot that answers questions from company PDFs, Word, Excel/CSV, text and Markdown files while
enforcing role-based and per-document access control **in the backend and in the retrieval pipeline**, before any
text reaches the LLM.

- Backend: Python 3.11+, Flask, SQLAlchemy/Alembic, Flask-JWT-Extended, ChromaDB (optional Pinecone), LangChain, OpenAI.
- Frontend: React + Vite + TypeScript + Tailwind.
- Docs: [ARCHITECTURE](docs/ARCHITECTURE.md) · [RBAC](docs/RBAC.md) · [API](docs/API.md) · [TESTING](docs/TESTING.md) · [DEPLOYMENT](docs/DEPLOYMENT.md) · [SECURITY](SECURITY.md)

## Quick start (local development, no paid API calls)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env            # then edit: see "Model configuration" below
export APP_ENV=development
export FLASK_APP=wsgi.py
flask db upgrade                # creates the SQLite dev DB under ../data/
flask seed-roles
ADMIN_PASSWORD='choose-a-strong-Passw0rd' flask create-admin --email you@example.com
flask run --port 5000
```

Frontend (second terminal):

```bash
cd frontend
npm install
npm run dev                     # http://localhost:5173, proxies /api to http://localhost:5000
```

### Model configuration

| Goal | Settings in `backend/.env` |
|---|---|
| Real answers | `EMBEDDINGS_PROVIDER=openai`, `LLM_PROVIDER=openai`, `OPENAI_API_KEY=...` |
| Offline UI/dev only | `EMBEDDINGS_PROVIDER=fake`, `LLM_PROVIDER=fake` (toy models; **never** in production; rejected when `APP_ENV=production`) |

Without a key the app starts, `/api/ready` reports models as not configured, and chat/ingestion return an explicit
"OPENAI_API_KEY is missing" error. Changing `EMBEDDING_MODEL` after indexing is refused at startup (the index pins
its embedding model); re-index instead.

### Optional demo data (development only)

```bash
DEMO_PASSWORD='Demo-Passw0rd-123' flask seed-demo   # 6 synthetic users, 5 synthetic documents
```
Demo users: `admin|hr|finance|eng|junior|employee@demo.local`. All data is invented and contains
`CANARY-*` strings used to prove nothing leaks across roles. Refuses to run when `APP_ENV=production`.

## Docker Compose

```bash
cp .env.example .env            # fill in POSTGRES_PASSWORD, SECRET_KEY, JWT_SECRET_KEY, OPENAI_API_KEY
docker compose up --build       # UI on http://localhost:8080
docker compose exec backend flask create-admin --email you@example.com   # prompts for the password
```
No default credentials exist; compose refuses to start if a required secret is missing, and there is no public
sign-up -- accounts are created by an administrator. See [DEPLOYMENT](docs/DEPLOYMENT.md) for production notes.

## Tests

```bash
scripts/run_tests.sh            # main suite + legacy suite (separate pytest processes)
python backend/eval/run_eval.py # access-control + retrieval evaluation
```
Tests use fake embeddings and a fake chat model (no network, no cost). Details and the latest recorded results:
[TESTING](docs/TESTING.md).

## API usage

```bash
TOKEN=$(curl -s localhost:5000/api/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"hr@demo.local","password":"Demo-Passw0rd-123"}' | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s localhost:5000/api/chat -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message":"What is the grievance escalation code?"}'
```
Interactive OpenAPI docs: `http://localhost:5000/apidocs/`. Reference: [API](docs/API.md).

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Startup error `Invalid configuration: ...` | A required setting is missing/short; the message names the setting, never its value. |
| 503 `ai_unavailable` on chat | Model key missing or provider down. Nothing was searched or exposed; retry. |
| Document stuck in `FAILED` | Read `error_message` in Documents; fix cause and use **Reindex** (idempotent). |
| `EmbeddingConfigMismatch` at startup | `EMBEDDING_MODEL` changed since indexing. Restore it or re-index into a fresh Chroma dir. |
| Everything answers "couldn't find sufficient information" | The user has no document grants (deny by default), docs are not `READY`, or `SIMILARITY_THRESHOLD` is too high. |
| 429 on login | Rate limit (`LOGIN_RATE_LIMIT`). |
| Duplicate upload rejected (409) | Same file already uploaded by you (checksum match). |

## Status: what is and isn't verified

Implemented and exercised by automated tests: authentication, token revocation, RBAC, document ACLs, ingestion for all
formats, permission-filtered retrieval with database revalidation, citation verification, conversation ownership,
audit logging, metrics. Both backend suites pass on SQLite **and** PostgreSQL 16; the frontend builds and a real-Chromium
end-to-end script (`scripts/e2e_ui.py`) passes against the live stack. **Not verified:** real OpenAI calls (tests use fake
models, so retrieval *quality* is unmeasured), the Pinecone adapter, and the Docker image builds. See
[TESTING](docs/TESTING.md) for exact results and gaps.
