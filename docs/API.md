# API reference

Base path `/api`. Live OpenAPI/Swagger UI at `/apidocs/` (spec at `/apispec.json`). All endpoints except
`/health`, `/ready` and `/auth/login` need `Authorization: Bearer <access token>` (`/auth/refresh` needs the refresh token).
Errors share one shape: `{"error": {"code", "message", "correlation_id", "details?"}}`; every response carries `X-Request-ID`.
Request bodies reject unknown fields (400), so clients cannot inject roles, user ids or vector filters.

| Method & path | Who | Notes |
|---|---|---|
| `POST /auth/login` | anyone | rate limited; returns `access_token`, `refresh_token`, `user` |
| `POST /auth/refresh` | refresh token | rotates: old refresh token is revoked |
| `POST /auth/logout` | user | revokes access (and optionally supplied refresh) token |
| `GET /auth/me` | user | profile, roles, capability permissions |
| `POST /auth/change-password` | user | invalidates all existing tokens |
| `GET /roles` | user | role names/descriptions |
| `GET/POST /users`, `PATCH /users/{id}`, `PATCH /users/{id}/roles` | `users.manage` | invitation-style: admin sets an initial password, user must change it |
| `POST /documents/upload` | `documents.upload` | multipart: `file`, `allowed_roles`, `user_grants`, `classification`, `owner_team`, `department` |
| `GET /documents`, `GET /documents/{id}`, `GET /documents/{id}/status` | user | admins see the inventory; others only readable docs + own uploads |
| `GET /documents/{id}/download` | ACL | original file, attachment only |
| `GET /documents/{id}/passages/{passage_id}`, `GET /documents/{id}/preview` | ACL | cited text; same rule as download |
| `PATCH /documents/{id}/permissions`, `POST /documents/{id}/reindex`, `DELETE /documents/{id}` | `documents.manage_permissions` | |
| `POST /chat` | user | body `{message, conversation_id?}` → `{conversation_id, message{content, citations[], no_answer, redacted}}` |
| `GET /conversations`, `GET/DELETE /conversations/{id}` | owner only | other users' ids return 404 |
| `GET /health`, `GET /ready` | public | readiness reports database, vector store, model configuration |
| `GET /admin/audit-logs`, `GET /admin/metrics` | `audit.view` | pagination via `page`, `per_page` (≤100) |
| `POST /admin/maintenance/purge` | `users.manage` | applies retention policy |

Citation object: `{id, document_id, filename, passage_id, location}`; a citation to a document the caller can no longer
read is never returned.
