# Deployment

## Docker Compose (reference deployment)

Services: `db` (PostgreSQL 16, healthcheck), `backend` (Flask + gunicorn, runs migrations and role seeding on start),
`frontend` (nginx serving the SPA and proxying `/api`, adding security headers). Embedded ChromaDB, uploads and the audit
log live on the `appdata` volume; the database on `pgdata`.

1. `cp .env.example .env` and fill in `POSTGRES_PASSWORD`, `SECRET_KEY`, `JWT_SECRET_KEY` (different, ≥32 chars), `OPENAI_API_KEY`.
   Set `CORS_ORIGINS` to the public https origin.
2. `docker compose up --build -d`
3. Bootstrap the first administrator (one time, no default account exists):
   `docker compose exec backend flask create-admin --email you@example.com`
4. Put a TLS-terminating reverse proxy in front of port 8080 (the app sets HSTS in production).
5. Schedule retention: `docker compose exec backend flask purge-expired` (daily).

> **Verified vs not:** Compose syntax and secret enforcement were validated, and the application (migrations, seeding, API, both test
> suites, browser E2E) was run against a local PostgreSQL 16 (UTF8). The **Docker images themselves were not built** (no Docker
> daemon in the build environment), so run steps 1-5 in staging first.

## Production configuration notes

- **PostgreSQL must be a UTF8 database.** Non-UTF8 (e.g. `SQL_ASCII`) databases fail on any non-ASCII text; `/api/ready` returns 503 with `database: unsupported_encoding` so this is caught before users hit it. `CREATE DATABASE ragapp ENCODING 'UTF8' TEMPLATE template0;`
- `APP_ENV=production` enforces: PostgreSQL URL, non-placeholder distinct secrets, explicit CORS origins, no fake providers.
- **Scaling:** the image runs one gunicorn worker (8 threads) because embedded Chroma and the in-memory rate limiter/metrics are per-process. To scale out: run Chroma as a separately secured service or use Pinecone, set `RATELIMIT_STORAGE_URI=redis://...`, and collect metrics centrally. Never expose a Chroma server port (see SECURITY.md advisories).
- **Backups:** back up PostgreSQL *and* the `appdata` volume (original files). The vector index can be rebuilt with `flask reindex-all` (reset the Chroma directory first if the embedding model changed).
- **Changing the embedding model** requires a fresh index; startup refuses a mismatch so queries can never silently run against incompatible vectors.
- Ingestion is synchronous; keep nginx/gunicorn timeouts (300 s) in step with expected document sizes.
- Logs: application logs go to stdout; the audit log is a separate rotating file (`AUDIT_LOG_FILE`) plus the `audit_events` table. Ship both to protected storage.

## Optional: Pinecone

Set `VECTOR_BACKEND=pinecone`, `PINECONE_API_KEY`, `PINECONE_INDEX` (serverless, cosine, dimension = your embedding size, 1536 for
`text-embedding-3-small`), optionally `PINECONE_NAMESPACE`. The adapter applies the same permitted-document filter and
embedding-model pin. **It has not been tested against a live index.** Before trusting it: upload documents with different roles,
confirm a user without access gets no results, confirm deletion removes vectors (the adapter cannot count chunks, so verify in the console).

## Updating

`git pull && docker compose up --build -d` applies new migrations automatically on backend start.
