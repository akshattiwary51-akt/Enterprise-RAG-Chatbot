# Security

## Controls implemented

- **Authorization before the LLM** (see ARCHITECTURE): ACL-filtered vector search + database revalidation + prompt built from authorised passages only.
- **Authentication:** Argon2id hashes; signed expiring JWTs with issuer/audience; access/refresh type separation; refresh rotation; logout revocation list; per-user token version invalidates all tokens on deactivation, password change/reset; roles never taken from the client or from token claims.
- **No self-registration;** accounts are admin-created. Initial admin is an explicit CLI step (`flask create-admin`); no default credentials anywhere; startup refuses weak/placeholder/identical secrets and never prints their values.
- **Input handling:** strict request schemas (unknown fields rejected), request-size limits, upload allowlist with magic-byte, zip-bomb and macro checks, server-generated storage names, 0600 file permissions, files stored outside any served directory, downloads as attachments.
- **Web:** CORS allowlist (no wildcard in production), login/chat/upload rate limits, security headers, `Cache-Control: no-store` on API responses, uniform error bodies without stack traces, correlation ids.
- **Prompt-injection posture:** retrieved text is wrapped as untrusted data, forged passage tags are neutralised, system prompt forbids obeying passages, citations are verified server-side, ungrounded answers are refused. Treated as defence in depth -- tests assume a worst-case model that obeys injected text and prove it can only echo authorised context.
- **Audit:** separate `audit` logger/file plus `audit_events` table: logins, user/role changes, uploads, ACL changes, denied access, admin actions. Details are scrubbed of prompts/contexts/tokens/passwords; unknown login emails are stored hashed.
- **Retention:** `CHAT_RETENTION_DAYS` (default 90) and `AUDIT_RETENTION_DAYS` (default 365) applied by `flask purge-expired` or `POST /api/admin/maintenance/purge`. Schedule it (cron/Kubernetes CronJob).

## Known limits and assumptions

- Application-level audit logs cannot defend against a compromised host or database administrator; ship them to write-once storage.
- Content sent to OpenAI leaves your network; confirm that fits your data policy (or use a private/compatible endpoint via `OPENAI_BASE_URL`).
- Refresh token lives in `sessionStorage` (XSS-exposed); prefer an httpOnly cookie for high-assurance deployments.
- Rate limiting and metrics are per-process by default; use a shared limiter store behind multiple workers.
- Fake embedding/LLM providers exist for tests and are rejected in production.
- No malware scanning of uploads and no OCR; documents are parsed in-process by pypdf/python-docx/openpyxl.
- **Dependency scan (run 2026-10-15):** `npm audit --omit=dev` found 0 vulnerabilities. `pip-audit` reported 5 entries (4 distinct CVEs: 2026-45829, -45830, -45831, -45833) on
  `chromadb 1.5.9` with **no fixed version available**. All five
  concern Chroma's *HTTP server* (REST endpoints, server-side auth providers, remote-code model loading). This app uses the
  embedded `PersistentClient` and never starts or exposes that server, so it is not reachable here. **Do not** deploy Chroma in
  server mode or expose a Chroma port without upgrading to a fixed release; re-run `pip-audit` regularly and bump when a fix ships.

Report vulnerabilities privately to your security contact; do not open public issues containing exploit details.
