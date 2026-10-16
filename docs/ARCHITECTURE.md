# Architecture

```
React SPA ──/api──▶ Flask (auth, RBAC, ACL) ──▶ SQL DB   (users, roles, documents, ACL grants, chats, audit)
                         │                  └─▶ Chroma  (child-chunk vectors, filtered by document_id)
                         └─▶ OpenAI (embeddings, chat) -- only ever receives authorised passages
```

## Core principle

The access boundary is enforced **before** the model sees text. The relational database is the single source of truth
for "who may read which document"; the vector store holds only `document_id`, version and location metadata -- never
permissions that could go stale.

## Query path (`services/rag/service.py`)

1. Identity comes only from the verified JWT subject; roles are re-read from the DB on every request.
2. `authorized_document_ids(user)`: READY documents the user may read (role grant OR individual grant, AND department
   restriction if set). Deny by default. An empty set means no search at all.
3. Question is embedded; Chroma is queried with `where document_id $in <authorised ids>` (filtering happens inside
   the store; an empty `$in` is never sent because Chroma rejects it -- we short-circuit instead).
4. Similarity threshold and top-k are applied.
5. **Revalidation:** every hit is re-checked against the DB (still readable, still READY, `index_version` matches).
   Failures are dropped and counted. This is defence in depth: a bug or stale state in the vector layer cannot leak
   text (tested with a deliberately unfiltered store).
6. Parent passages (larger windows) are loaded only for revalidated hits; tabular documents additionally get exact
   computed aggregates (sum/mean/min/max/count) from a fixed operation set -- no user-supplied SQL/code/formulas.
7. The prompt is built only from these passages, wrapped as untrusted data; the system prompt tells the model to
   ignore instructions inside them. This is a secondary control.
8. The answer's `[S#]` citations are verified against the passages actually supplied; unknown ids are stripped, and an
   answer with no valid citation is replaced by the neutral "couldn't find sufficient information" message.
9. Messages store citation ids only. On every read, citations are re-authorised; if any cited document is no longer
   readable the whole assistant answer is withheld (it may paraphrase protected content).

Failures (vector store, embeddings, model) return 503 and never fall back to unfiltered retrieval.

## Ingestion (`services/ingestion/`)

Validate (extension allowlist, magic bytes, size, zip-bomb and macro checks, filename rules) → store under a random
name with 0600 permissions outside any served directory → checksum dedupe → parse (PDF pages, DOCX headings, sheet+row
provenance) → parent/child chunking → embeddings → write passages + vectors → commit `READY` last.
Lifecycle `PENDING → PROCESSING → READY | FAILED | DELETING`. The claim into `PROCESSING` is an atomic
compare-and-set; chunk ids are deterministic; failures remove partial vectors and passages. Deletion first sets
`DELETING` (unretrievable), then removes vectors, passages, file, row; if vector cleanup fails the document stays
blocked and the delete can be retried.

Spreadsheets: each row becomes `Sheet | Row n | Header: value; ...` so headers travel with values; rows are never
split across chunks; formulas are never evaluated (cached values only); macro workbooks are rejected; hidden sheets
are skipped.

## Trade-offs and limitations

- **Synchronous ingestion** keeps the system simple and failure handling obvious, but long uploads block a request
  thread. A task queue (Celery/RQ) is the next step for large corpora.
- **Embedded Chroma + in-memory rate limiter/metrics are per-process.** The container runs one gunicorn worker with
  threads. To scale out, move to Chroma server or Pinecone, and use a shared limiter store (Redis).
- **Pinecone adapter is implemented but untested.**
- No streaming responses; no OCR for scanned PDFs (they fail with a clear message).
- No server-side answer cache exists, so there are no cache keys to scope; if one is added it must key on
  principal + effective ACL version + document `index_version` and be invalidated on ACL/role/index changes.
- Refresh token is kept in `sessionStorage` by the SPA; an httpOnly SameSite cookie is the stronger production option.
- Audit logs are application-level: they do not protect against a compromised database admin or host.
