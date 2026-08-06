# secure-rag — CSO RAG Service Design Spec

Date: 2026-08-06
Status: Approved by user (design review in chat)

## One-liner

RAG document Q&A service for civil society organizations — offline-capable,
denied-by-default, fully containerized.

## Problem

A standalone portfolio repo matching the "secure-rag" brief: a RAG Q&A
service whose engineering is inherited from itkb (a proven, boot-verified
local-first RAG system), but repositioned for civil society organizations —
policy/NGO documents as the corpus, a security posture that fits an NGO's
obligations (beneficiary PII, denied-by-default access, auditability), and the
job-ad's keyword surface present in the README's trade-offs narrative.

## Goal

A new repository at `~/secure-rag` that ports itkb's stronger spec verbatim,
renames the package (`itkb` → `srag`, CLI `kb` → `srag`), swaps the domain
layer for CSOs (real public NGO/policy PDFs, CSO-flavored governance defaults),
and ships a README that delivers the one-liner, a Mermaid diagram, a
`docker compose up` quickstart, and a Design decisions & trade-offs section
that explicitly names LangChain / sentence-transformers / Chroma as
evaluated-and-rejected alternatives.

## Non-Goals

- Writing a new retrieval/agent/security stack from scratch — the point is to
  reuse itkb's proven, tested implementation.
- Adopting LangChain / sentence-transformers / Chroma literally (user chose
  "keep itkb's stack" — option 1).
- Presidio NER (regex PII is the chosen scope, same as itkb).
- Multi-user/RBAC/TLS/rate-limiting (single-user localhost/field-office scope,
  documented as non-goals in the README).
- Any itkb behavior regression — the ported test suite must stay green.

## Architecture

### Source port (unchanged behavior)

Copy from `~/itkb`, rename package `itkb` → `srag`, CLI `kb` → `srag`:

| itkb module | secure-rag module | Role |
|---|---|---|
| `itkb/store/db.py` | `srag/store/db.py` | SQLite vec0 + FTS5 BM25, RRF hybrid retrieval, docs/chunks/commands/notes tables |
| `itkb/ingestion/chunker.py` | `srag/ingestion/chunker.py` | Heading-aware chunker (512 tok / 64 overlap), fence-aware |
| `itkb/ingestion/parsers.py` | `srag/ingestion/parsers.py` | pypdf, python-docx, markdownify (URL), text |
| `itkb/ingestion/embedder.py` | `srag/ingestion/embedder.py` | Ollama embeddings |
| `itkb/ingestion/redact.py` | `srag/ingestion/redact.py` | PII redaction (emails, phones, SSN form, Luhn-validated cards) |
| `itkb/agent/loop.py` + `tools.py` + `prompts.py` | `srag/agent/…` | ReAct tool loop (search_kb, web_search w/ Tavily fallback, run_command), citations, follow-ups |
| `itkb/api/app.py` + `auth.py` | `srag/api/…` | FastAPI, /health, lifespan, docs hidden, SSE streaming, API-key + session auth |
| `itkb/audit.py` | `srag/audit.py` | JSON audit log (query/auth/ingest/delete/command) |
| `itkb/config.py` | `srag/config.py` | Config + env overrides |
| `itkb/program.py` | `srag/program.py` | PROGRAM.md governance |
| `itkb/cli.py` | `srag/cli.py` | `srag` CLI (add/query/list/delete/serve/config/reindex) |
| `tests/` | `tests/` | Ported suite, renamed imports |

Also ported: `Dockerfile` (multi-stage), `docker-compose.yml` (profiles),
`.dockerignore`, `.gitignore`, `pyproject.toml` (renamed), `PROGRAM.md`.

### Domain layer (new/adapted)

- **Package/CLI**: `srag`; app title "secure-rag"; DB file `srag.sqlite`;
  audit `audit.jsonl` (same scheme).
- **Config defaults** (config.py + PROGRAM.md): chat `llama3.2:3b`, embed
  `nomic-embed-text` (matches the spec's model choice; also the bundled demo
  models). Ingest whitelist = `/samples/` (+ user dirs). `commands_enabled:
  false`. `redact_pii: true`, `audit_enabled: true`, `web_fallback: true`.
- **`samples/`**: 3-4 real public PDFs downloaded during implementation —
  one UN/SDG policy brief and one MoEYS public document (small files, committed
  to the repo; exercises pypdf in the demo). Fallback if download fails: ship
  policy-themed markdown and document the substitution.
- **README** (rewritten, not copied): one-liner; Mermaid architecture
  diagram; `docker compose up` quickstart (profiles); **Design decisions &
  trade-offs** — chunk-size choice, why local models, latency observations
  (qualitative, honest), and an "evaluated alternatives" subsection naming
  LangChain's RecursiveCharacterTextSplitter, sentence-transformers, and
  Chroma with the concrete reasons they were rejected in favor of the SQLite
  hybrid RRF + custom chunker + Ollama stack (offline, no extra service, lean
  container, stronger hybrid retrieval).

## Data flow

1. `srag add /samples` (or web ingest) → parse PDF → PII-redact → heading-aware
   chunk → Ollama embed → SQLite hybrid store.
2. `srag query` / web chat → agent retrieves top-k (BM25+vector RRF) → grounds
   the answer with citations → streams via SSE → logs to audit.jsonl.
3. Every `/api/*` call requires an API key or session cookie (denied by
   default; key auto-generated on first boot and printed once).
4. `docker compose --profile bundled --profile demo-data up` → bundled Ollama
   pulls llama3.2:3b + nomic-embed-text → ingest-samples loads the PDFs →
   grounded answers on :8000.

## Security posture (ported unchanged)

- API-key auth denied by default; stateless HMAC sessions; key never in cookie.
- PII redaction at ingestion (true-PII only; IT-safe regexes with Luhn).
- JSON audit log of every query/auth/ingest/delete/command.
- PROGRAM.md governance; commands off by default; path/URL whitelists;
  blocked command patterns.
- `/health` open; `/docs`+`/openapi.json` hidden.

## Error handling

- Identical to itkb (ported): best-effort audit, fail-closed PII, denied-by-
  default auth, multi-stage Docker with fail-closed model pull.

## Testing

- Port all 136 itkb tests (rename imports/CLI; adapt corpus-dependent tests).
- Keep the 2 real-Ollama integration tests (skip-if-unreachable, using the
  project's configured models — now llama3.2:3b / nomic-embed-text).
- Health gates: `flake8`, `mypy`, full `pytest`.
- Docker boot verification (the standard that caught 3 bugs in itkb): build +
  default profile + bundled demo + a real query citing a sample PDF.

## Docker compatibility

Ported compose unchanged: `host-ollama` (default), `bundled`
(ollama + ollama-init via HTTP /api/pull + api-demo), `demo-data`
(ingest-samples). Healthcheck on `/health`. Multi-stage build (pysqlite3
compiler in builder stage). Verified against a real engine before delivery.
