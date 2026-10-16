# RBAC and document access policy

## Two separate layers

1. **Capability permissions** (what actions you may perform) -- attached to roles.
2. **Document ACLs** (which content you may read) -- explicit grants on each document.

Capabilities never widen content access. An administrator can manage users and see the document *inventory*, but
receives document **content** (retrieval, download, preview, citations) only if an ACL grant covers them. Deny by default.

## Initial matrix

| Capability permission | admin | hr | finance | engineering | employee |
|---|---|---|---|---|---|
| `users.manage` (create users, roles, activate/deactivate, retention purge) | yes | no | no | no | no |
| `documents.upload` | yes | yes | yes | yes | no |
| `documents.manage_permissions` (inventory, edit ACLs, reindex, delete) | yes | no | no | no | no |
| `audit.view` (audit log, metrics) | yes | no | no | no | no |
| Query documents | only through ACL grants | same | same | same | same |

Roles are seeded by `flask seed-roles` (idempotent, never downgrades existing grants).

## Document ACL

A document is readable by an active user when:

```
(user has a role granted on the document  OR  user has an individual grant)
AND (document.department is empty  OR  document.department == user.department)
AND document.status == READY            -- for retrieval/download/preview
```

Other rules enforced server-side (`services/permissions/service.py`):

- Unknown roles/users/classifications are rejected; deactivated users cannot be granted access.
- `highly_confidential` documents cannot be granted to the `employee` role.
- Non-admin uploaders may only share with roles they hold themselves and cannot grant to individuals.
- The last active administrator cannot be deactivated or demoted; admins cannot remove their own admin role.
- Unauthorized or non-existent document ids return the same 404, and every denial is audited.

Changes take effect on the next request (no caches): role changes, ACL edits, deactivation (token version bump),
password change/reset (token version bump), deletion.

## Classification

`public | internal | confidential | highly_confidential` is metadata plus the one rule above; it is not itself a grant.
