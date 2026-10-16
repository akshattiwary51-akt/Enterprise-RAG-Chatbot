# Testing

All automated tests run offline: deterministic fake embeddings (hashed bag-of-words, stopwords dropped), a fake chat model that
**records every prompt it receives**, and a real, isolated, per-test ChromaDB + SQLite. No paid API is called.

```bash
scripts/run_tests.sh              # both suites (SQLite)
cd backend && python -m pytest tests -q                         # main suite
cd backend && python -m pytest tests/legacy -o addopts="" -q    # legacy suite (own conftest; cannot share a process)
python backend/eval/run_eval.py                                 # evaluation harness

# same suites against PostgreSQL (use a throw-away UTF8 database; the schema is dropped/recreated per test):
TEST_DATABASE_URL=postgresql+psycopg2://user@host:5432/ragtest scripts/run_tests.sh

# browser end-to-end (backend with `flask seed-demo` data + `npm run dev` running; pip install playwright):
DEMO_PASSWORD=... python scripts/e2e_ui.py
```

## Commands actually run and results (2026-10-15)

| Command | Result |
|---|---|
| `pytest tests` (main suite) | **114 passed**, 0 failed (≈2m24s) |
| `pytest tests/legacy -o addopts=""` | **72 passed**, 0 failed (≈1m44s) |
| `TEST_DATABASE_URL=<PostgreSQL 16, UTF8> pytest tests` | **114 passed** (≈2m22s) |
| `TEST_DATABASE_URL=<PostgreSQL 16, UTF8> pytest tests/legacy` | **72 passed** (≈1m43s) |
| `scripts/e2e_ui.py` (real Chromium, Vite dev server → gunicorn → PostgreSQL 16, seeded demo data, fake models) | **17/17 checks passed** |
| `python eval/run_eval.py` | access control 4/4 passed; retrieval 6/6 answered, cited and covered (fake embedder) |
| `npm run build` (`tsc -b` + `vite build`) | type-check clean, production build succeeded |
| `flask db upgrade` on fresh SQLite **and** PostgreSQL 16; `flask db check` on PostgreSQL | both migrations applied; no model/migration drift |
| live `gunicorn` smoke (SQLite) (login → chat as HR → junior dev download of HR salaries → `/ready`, `/apispec.json`) | HR answer cited the HR document; download denied with 404; ready; spec served |
| `npm audit --omit=dev` / `pip-audit` | 0 / 4 distinct CVEs, all chromadb *server*-mode, no fix (see SECURITY.md) |
| `docker compose config` | syntax valid; refuses to start without required secrets |

## What the main suite covers

- **Auth:** login, bad credentials (indistinguishable), expired/forged/unsigned/wrong-audience tokens, refresh rotation and token-type separation, logout revocation, deactivation, password change invalidating all sessions, rate limiting, security headers, client-supplied roles/ids/filters rejected, JWT extra claims ignored, error bodies leak nothing, config validation never echoes secrets.
- **RBAC / ACL:** admin APIs blocked for non-admins and audited, user management rules, last-admin protection, download/preview/metadata denied uniformly (404), admin sees inventory but not content, listings hide unauthorised documents, upload/ACL validation, department restriction, audit logs free of secrets.
- **Ingestion / reliability:** every format reaches READY, provenance preserved (PDF pages, DOCX headings, sheet rows), headers kept with every spreadsheet value, formulas not evaluated, empty/unsupported/mismatched/oversized/zip-bomb/macro/corrupt files rejected or FAILED safely, duplicates, partial-write cleanup, idempotent retry, vector/embedding outages, exclusive claim, delete and failed-delete behaviour.
- **RAG security (canary secrets):** each role retrieves only its own documents; across every role and a battery of questions no unauthorised canary appears in a response **or in any prompt sent to the model**; naming a restricted document is indistinguishable from a non-existent one; prompt injection from the user and from inside an authorised document (with an obedient fake model); passage-tag breakout; ACL, user-grant, role and deletion changes alter retrievability immediately; a vector store that ignores the filter is still neutralised by revalidation; stale index version / missing passages fail closed; fabricated/missing citations refused; outages return 503 with no model call; logs, audit rows and metrics contain no canaries.
- **Chat history:** cross-user access denied (404), answers citing documents that lost access are withheld.

## Known gaps

- Fake embeddings only prove the pipeline and access control. **Retrieval quality with real embeddings is unmeasured**; re-run `eval/run_eval.py` with `EMBEDDINGS_PROVIDER=openai` and expand `eval/dataset.json` with your own corpus.
- Not tested: real OpenAI calls, Pinecone, Docker image builds, load/concurrency beyond the claim test, accessibility beyond Escape/focus handling, browsers other than Chromium. The browser script is a smoke/E2E check, not a component test suite.
- The fake embedder can produce incidental low-similarity matches from hash collisions; tests assert security properties (no forbidden content reached the model) rather than relying on "no answer" for loosely related questions, except where the threshold is raised on purpose.

## Findings from the PostgreSQL and browser runs (fixed)

- **Non-UTF8 database:** a PostgreSQL database created as `SQL_ASCII` (e.g. `initdb` under the C locale) made `POST /api/chat` return 500 as soon as a question was long enough to get a "…" in the conversation title; the same would hit any non-ASCII name or document text. `/api/ready` now reports `database: unsupported_encoding` (HTTP 503) for such a database. The official `postgres` Docker image is UTF8 by default.
- **Citation dialog and Escape:** the shared `Modal` only handled Escape when focus was already inside it, so keyboard users could not dismiss it. It now listens on the document, moves focus into the dialog, and restores focus to the opener.
