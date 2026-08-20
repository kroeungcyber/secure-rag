# Onboarding & Role-Based Access — Design

> Blueprint for the M1 milestone: turn `secure-rag` from a single-user
> localhost tool into a role-based multi-user knowledge base where field and
> patient-facing workers are onboarded and **no one is left behind**.

---

## 1. Goal (MVP)

Extend `secure-rag` with:

1. **Multi-user accounts** with role-based access.
2. An **onboarding flow** — admin invites → worker claims account → role
   assigned → guided first-run → active.
3. **Scoped retrieval** — a user only sees knowledge relevant to their role.

Success = an IT-support admin can invite a field worker, the worker claims the
account, is assigned a role, and asks a question scoped to that role — entirely
offline/local, with every step audited.

---

## 2. Current state (what we're extending)

- **Auth** (`srag/api/auth.py`): a single shared API key plus an HMAC-signed
  session cookie. `require_auth` checks the key or the cookie — there is no
  notion of *which* user is asking.
- **Store** (`srag/store/db.py`): SQLite with `documents`, `chunks`,
  `chunk_embeddings` (vec0), `chunks_fts` (FTS5), `command_history`,
  `query_context`, `incident_notes`, `pending_commands`. No user or role tables.
- **API** (`srag/api/app.py`): query, ingest, documents, models, command
  confirm, notes, login, logout. Every `/api/*` route uses the same
  `require_auth`.

---

## 3. Target role model

Three roles cover the NGO reality (IT support + a team + field/patient workers):

| Capability | `admin` (IT support) | `staff` (team member) | `field` (field/patient worker) |
|---|:---:|:---:|:---:|
| Ask questions | ✅ scoped | ✅ scoped | ✅ scoped |
| See all documents | ✅ | ❌ role-tagged only | ❌ role-tagged only |
| Ingest / delete documents | ✅ | ❌ | ❌ |
| Manage users & invites | ✅ | ❌ | ❌ |
| View audit log | ✅ | ❌ | ❌ |
| Run / confirm shell commands | ✅ (if `commands_enabled`) | ❌ | ❌ |
| View incident notes | ✅ | ❌ | ❌ |

---

## 4. Data model additions

```text
users(id, username UNIQUE, password_hash, role, status, created_at, last_login)
invites(token UNIQUE, role, doc_scope, created_by, created_at, expires_at, claimed_by)
doc_access(doc_id, role)          -- or a `roles`/`topics` column on documents
onboarding_steps(user_id, step_key, completed_at)
```

Idempotent `init_db` adds these tables; existing single-user installs get a
bootstrap admin so nothing breaks on upgrade.

---

## 5. Onboarding flow

1. **Admin** creates an invite (picks a role + document scope) → one-time invite
   token, printed/shared offline.
2. **Worker** opens the app, enters the token, sets a username + password →
   account becomes `active`.
3. **First-run checklist** guides them: what this tool is, how to ask a
   question, who to contact for help — in their language.
4. Every later request is authorized against their role and document scope.

---

## 6. Auth changes

- Replace the shared-key-only model with **per-user password login**
  (stdlib PBKDF2-HMAC-SHA256, 200k iterations — no new dependency) plus
  **invite-token claim** for onboarding.
- Sessions stay HMAC-signed cookies, but now bind to a `user_id` and role
  instead of a single shared key.
- The existing bootstrap API key is retained as the **admin bootstrap
  credential** for first-run, so the "denied by default" story is preserved.

---

## 7. Retrieval scoping

- Documents carry a `roles`/`topics` tag. The hybrid RRF search (vec0 + FTS5)
  filters to the user's allowed document set *before* ranking.
- `admin` sees everything; `staff`/`field` see only role-tagged documents.

---

## 8. Field-worker considerations ("no one left behind")

- Offline-capable and low-bandwidth (consistent with the existing no-build-step,
  no-CDN UI).
- One simple question box, large text, minimal steps.
- Bilingual (Khmer / English) labels — the deployment context is Cambodia.
- Progressive enhancement: the existing single-user UI keeps working; multi-user
  is additive, not a rewrite.

---

## 9. Explicitly out of scope (unchanged from today)

- TLS, rate limiting, SSO/LDAP federation, per-field encryption. The existing
  README already documents these as deliberate single-machine scope limits.

---

## 10. Files touched

```text
srag/store/models.py   add User, Invite, OnboardingStep dataclasses
srag/store/db.py       schema + CRUD for users/invites/access/onboarding
srag/api/auth.py       password auth, role resolution, scoped require_role()
srag/api/app.py        /api/users, /api/invites, /api/onboarding routes + role deps
srag/api/web/templates login.html, admin.html, onboarding.html (new)
srag/api/web/static    minimal JS for login/admin/onboarding screens
```

## 11. Tests

- Unit (offline, always green): role matrix enforced per route, invite lifecycle
  (create → claim → active → expire), retrieval scoping excludes out-of-role
  documents.
- Integration: same skip-when-Ollama-absent pattern as the existing suite.
