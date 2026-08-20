# secure-rag — Onboarding & Role-Based Access (retro-spec)

Date: 2026-08-19
Status: **Approved by user (review in chat, 2026-08-19)** (retro-spec: documents
work already shipped, written to restore the spec↔code anchor)

## One-liner

Multi-user, role-based access (`admin` / `staff` / `field`) with invite
onboarding and role-scoped retrieval, added to the previously single-user
`secure-rag`.

## Problem

The original spec scoped `secure-rag` as single-user localhost — multi-user/RBAC
was an explicit non-goal. The project goal changed: an NGO field office needs
the *whole team* onboarded ("no one left behind"), with IT support as admin and
scoped access for everyone else.

## Goal

Invite → claim → active onboarding; three roles; every route and every retrieval
scoped to role; a first-run checklist for non-technical workers.

## Non-goals (unchanged)

- TLS, rate limiting, SSO/LDAP federation (still single trusted-machine scope).
- Per-document encryption; NER-based PII (regex redaction is still the scope).

## Key decisions (verify each)

1. **Roles = `admin` / `staff` / `field`.** admin = IT support (full); staff =
   team member (scoped); field = field/patient-facing worker (scoped).
2. **Password hashing = stdlib PBKDF2-HMAC-SHA256, 200,000 iterations** — *not*
   argon2/bcrypt/scrypt. Rationale: zero new dependencies; `hashlib.scrypt` is
   unavailable in the system Python build this project runs on.
3. **Auth = per-user password + one-time invite claim; sessions stay HMAC
   cookies, now bound to `user_id`.** The bootstrap API key is retained as the
   built-in `admin` so existing installs upgrade in place.
4. **Scoped retrieval = a `roles` tag on documents (empty string = public).**
   Hybrid search filters to the user's visible document IDs *before* ranking.
5. **Invite lifecycle = one-time token, 7-day expiry; claim sets
   username + password (min 8 chars).**
6. **Onboarding = a 3-step checklist** (welcome → ask your first question →
   know who to contact).
7. **Admin UI = a document-access dropdown** (Public / Staff / Field /
   Staff+Field / Admin only) in the admin panel.

## Already implemented (files)

- `srag/store/models.py`, `srag/store/db.py` — `users` / `invites` /
  `onboarding_steps` tables + CRUD; `roles` column on `documents`;
  `visible_document_ids()`.
- `srag/api/auth.py` — PBKDF2 hashing, user sessions, `require_role()`.
- `srag/api/app.py` — users/invites/onboarding routes, role-gated pages,
  scoped `/api/query` and `/api/documents`.
- `srag/api/web/templates/{login,admin,onboarding}.html`.
- `tests/test_users.py`, `tests/test_pages.py`.
