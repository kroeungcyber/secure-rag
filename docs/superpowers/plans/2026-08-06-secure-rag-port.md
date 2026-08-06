# secure-rag — CSO RAG Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new standalone repo at `/Users/bila/secure-rag` that ports itkb's proven stack verbatim (renamed `itkb` → `srag`, CLI `kb` → `srag`), repositions the domain layer for civil society organizations (real public NGO/policy PDFs, CSO governance defaults), and ships a README with the ad's keyword surface.

**Architecture:** Mechanical port of `/Users/bila/itkb` (at commit `7e9a79f`) — copy `itkb/` + `tests/` + Docker/config files, systematic rename, then a domain layer (config defaults, PROGRAM.md, PDF samples, README). Verification at the same bar that caught 3 Docker bugs in itkb: health stack + real-Ollama integration tests + full Docker boot.

**Tech Stack:** Python 3.11, FastAPI, SQLite (vec0 + FTS5), Ollama, Docker Compose profiles, pytest + pytest-mock.

**Reference spec:** `docs/superpowers/specs/2026-08-06-secure-rag-design.md` (in this repo)

**Source of truth for the port:** `/Users/bila/itkb` — copy FROM there; do not re-type itkb source into this plan.

## Global Constraints

- Package rename: `itkb` → `srag` everywhere (module paths, imports, config dir `.itkb`→`.srag`, DB `kb.sqlite`→`srag.sqlite`, app title).
- CLI rename: binary `kb` → `srag` in pyproject `[project.scripts]`, Dockerfile CMD, and compose `command:` entries. Typer command names (`serve`, `add`, `query`, ...) unchanged — tests invoke them via CliRunner on the `app` object.
- No behavior regression: every ported itkb test must pass, renamed.
- Config defaults for CSO: chat `llama3.2:3b`, embed `nomic-embed-text`; `redact_pii`/`audit_enabled`/`web_fallback` true; `commands_enabled` false.
- Health gates at the end: `flake8 srag tests`, `mypy srag` (via `/opt/homebrew/opt/python@3.11/bin/python3.11 -m mypy`), `python3 -m pytest -q`.
- Run pytest as `python3 -m pytest` from the repo root. Tests must never touch real `~/.srag` (use the existing `cfg_mod.CONFIG_DIR/CONFIG_FILE` monkeypatch pattern).
- Repo-local git identity: commits in this repo may need `-c user.name=... -c user.email=...` unless git config is set (the spec commit used `secure-rag` / `dev@secure-rag.local`).

---

### Task 1: Scaffold — mechanical port + rename

**Files:** copies of all itkb source/tests/config; `pyproject.toml` renamed; no domain work yet.

- [ ] **Step 1: Set repo-local git identity (once)**

```bash
git config user.name "secure-rag"
git config user.email "dev@secure-rag.local"
```

- [ ] **Step 2: Copy the source tree**

```bash
SRC=/Users/bila/itkb
cp -R "$SRC/itkb" /Users/bila/secure-rag/itkb
cp -R "$SRC/tests" /Users/bila/secure-rag/tests
cp "$SRC/pyproject.toml" "$SRC/Dockerfile" "$SRC/docker-compose.yml" "$SRC/.dockerignore" "$SRC/.gitignore" "$SRC/PROGRAM.md" /Users/bila/secure-rag/
```

Verify: `ls /Users/bila/secure-rag/itkb/api/app.py` exists.

- [ ] **Step 3: Rename package directory**

```bash
mv /Users/bila/secure-rag/itkb /Users/bila/secure-rag/srag
```

- [ ] **Step 4: Rename identifiers across all files (except samples/docs to be rewritten later)**

Run from `/Users/bila/secure-rag`:

```bash
# 4a. global package/name rename (handles itkb.*, .itkb, "itkb Web UI", etc.)
grep -rl 'itkb' srag tests pyproject.toml Dockerfile docker-compose.yml .dockerignore .gitignore PROGRAM.md 2>/dev/null \
  | xargs sed -i '' 's/itkb/srag/g'

# 4b. DB filename
sed -i '' 's/kb\.sqlite/srag\.sqlite/g' srag/config.py tests/*.py

# 4c. CLI binary in pyproject scripts
sed -i '' 's/^kb = /srag = /' pyproject.toml

# 4d. CLI binary in Dockerfile + compose (Dockerfile CMD + compose command arrays)
sed -i '' 's/kb serve/srag serve/g; s/kb add/srag add/g' Dockerfile docker-compose.yml
```

- [ ] **Step 5: Verify the rename is complete**

Run: `grep -rn 'itkb' srag tests pyproject.toml Dockerfile docker-compose.yml 2>/dev/null || echo "NO STRAY itkb"`
Expected: `NO STRAY itkb`
Run: `grep -rn '\[project.scripts\]' -A1 pyproject.toml`
Expected: `srag = "srag.cli:app"`

- [ ] **Step 6: Sanity import**

Run: `python3 -c "import sys; sys.path.insert(0,'.'); import srag.cli, srag.api.app; print('imports OK')"`
Expected: `imports OK`

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "port: rename itkb to srag, scaffold CSO repo from itkb@7e9a79f"
```

---

### Task 2: Port tests to green

**Files:** all `tests/` (renamed in Task 1); `srag/` fixes only where the rename broke something.

- [ ] **Step 1: Run the suite**

Run: `python3 -m pytest -q`
Expected: mostly passing; expect failures from rename-dependent tests. Record which fail and why (likely: `tests/test_docker_config.py` — compose command now `srag add`, and the registered-command check; anything referencing `itkb` that Task 1 Step 4 missed; config-path fixtures).

- [ ] **Step 2: Fix `tests/test_docker_config.py` for the CLI rename**

The test asserts compose `command[0] == "kb"`. Change to `"srag"`, and `_registered_kb_commands()` should resolve via the srag CLI (`from srag.cli import app`). Also `test_wheel_packages_web_assets` checks `itkb.api.web` — update the path to `srag.api.web` (already renamed by Task 1 Step 4a if it greps the source dirs — verify the test still references the right path and the `srag/api/web` files exist).

- [ ] **Step 3: Fix any remaining rename fallout**

Read each failing test; the fix is always a mechanical rename (`itkb`→`srag`, `kb.sqlite`→`srag.sqlite`) or a path fixture. Do NOT change test semantics.

- [ ] **Step 4: Full suite green**

Run: `python3 -m pytest -q`
Expected: all pass (count ≈ itkb's 136, minus any corpus-dependent adjustments — report the count).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "test: port and green the itkb suite under srag naming"
```

---

### Task 3: Domain layer — config defaults, PROGRAM.md, app identity

**Files:** `srag/config.py`, `PROGRAM.md`, `srag/api/app.py` (title), `pyproject.toml` (description), `README.md` (delete; rewritten in Task 5).

- [ ] **Step 1: CSO config defaults**

In `srag/config.py`, change the `Config` dataclass defaults:
- `model: str = "llama3.2:3b"` (was `llama3.1:8b`)
- `embed_model: str = "nomic-embed-text"` (unchanged, verify)
- `db_path` default: `str(Path.home() / ".srag" / "srag.sqlite")` (Task 1 Step 4a already renamed `.itkb`→`.srag` and `kb.sqlite`→`srag.sqlite` — verify)
- `CONFIG_DIR`/`CONFIG_FILE` point at `~/.srag` (verify rename)

- [ ] **Step 2: CSO PROGRAM.md**

Rewrite `PROGRAM.md` governance defaults:
- Models: `chat: llama3.2:3b`, `embed: nomic-embed-text`
- Path whitelist: `- /samples/`, `- /Users/<your_user>/csodocs/` (placeholder replaced with a sensible default like `/samples/` plus the repo dir)
- URL whitelist: `- https://www.un.org/`, `- https://moeys.gov.kh/`
- `commands_enabled: false`
- Query behavior `top_k: 5`, `trusted: false`

- [ ] **Step 3: App identity**

In `srag/api/app.py`: `FastAPI(title="secure-rag Web UI")` (verify the rename produced this; adjust wording if it reads oddly).
In `pyproject.toml`: `name = "secure-rag"`, `description = "RAG document Q&A service for civil society organizations — offline-capable, denied-by-default, fully containerized."`

- [ ] **Step 4: Remove any stale itkb docs**

Task 1 did NOT copy `README.md`/`CLAUDE.md`/`AGENTS.md`, but if any exist (e.g. a future copy), remove them — this repo must not inherit itkb's README or agent instructions:

```bash
rm -f /Users/bila/secure-rag/README.md /Users/bila/secure-rag/CLAUDE.md /Users/bila/secure-rag/AGENTS.md
```

- [ ] **Step 5: Run suite**

Run: `python3 -m pytest -q` — must stay green.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: CSO domain defaults — models, PROGRAM.md governance, app identity"
```

---

### Task 4: Sample corpus — real public NGO/policy PDFs

**Files:** `samples/` (new).

- [ ] **Step 1: Download public PDFs**

From `/Users/bila/secure-rag`, create `samples/` and download 3-4 small public policy/NGO PDFs, e.g.:

```bash
mkdir -p samples
curl -L --max-time 60 -o samples/un-sdg-policy-brief.pdf "https://www.un.org/sustainabledevelopment/wp-content/uploads/2017/12/SDG-policy-brief...pdf" 2>&1 || echo "download 1 failed"
curl -L --max-time 60 -o samples/moeys-education-policy.pdf "https://moeys.gov.kh/...pdf" 2>&1 || echo "download 2 failed"
```

Reality check: UN/MoEYS PDF URLs move. Fetch a real, working public PDF and record the actual URL used in the commit message / samples/README. Acceptable sources: a UN SDG/policy brief, a UNDP/MoEYS public document, or any clearly-public NGO policy PDF that is small (< 5MB). VERIFY each download is a real PDF:
`file samples/*.pdf` → `PDF document`.

- [ ] **Step 2: Fallback if downloads fail**

If no genuine PDF can be fetched, write 2-3 policy-themed markdown documents in `samples/` (e.g. `un-sdg-education.md`, `moeys-policy.md`) describing public NGO/education policy content, and note in the commit that the corpus is markdown-fallback (pypdf exercised only in tests, not the demo).

- [ ] **Step 3: Verify ingest whitelist**

`/samples/` is already whitelisted in PROGRAM.md (Task 3). Run:
`python3 -c "from srag.program import ingest_path_allowed; print(ingest_path_allowed('/samples/un-sdg-policy-brief.pdf'))"`
Expected: `True`

- [ ] **Step 4: Commit**

```bash
git add samples/
git commit -m "feat: public NGO/policy sample corpus (samples/)"
```

---

### Task 5: README — one-liner, diagram, quickstart, trade-offs

**Files:** `README.md` (rewrite).

- [ ] **Step 1: Write the README**

Rewrite `README.md` with:
- Title + one-liner: "RAG document Q&A service for civil society organizations — offline-capable, denied-by-default, fully containerized."
- Quick Start (`srag add`, `srag query`, `srag serve`)
- Run with Docker: profiles (`host-ollama`, `bundled`, `demo-data`) — copy itkb's working commands but with `srag`/CSO framing; note both port-8000 services are mutually exclusive; config precedence; `down -v` reset
- Architecture: Mermaid diagram (port itkb's, relabel secure-rag/srag)
- Central Control — PROGRAM.md (CSO governance: models, whitelists, commands off)
- **Design decisions & trade-offs** section that explicitly names the rejected stack:
  - "Why not LangChain's RecursiveCharacterTextSplitter" → custom heading-aware chunker (breadcrumb context, fence-aware)
  - "Why not sentence-transformers" → Ollama embeddings (no torch, ~300MB image, offline)
  - "Why not Chroma" → SQLite vec0 + FTS5 hybrid with RRF (no extra service, single-file, stronger hybrid retrieval over vector-only)
  - "Why local models" (llama3.2:3b / nomic-embed-text) → offline, no API key, privacy
  - Chunk-size choice (512 / 64 overlap)
  - Latency observations (qualitative, honest)
  - Security posture (denied-by-default auth, PII, audit) + the same non-goals as itkb (no TLS/RBAC/rate-limiting — single-user scope)
- Commands, Web UI, Files (`.srag/` dirs), Tests

Match itkb's README structure (source of truth: `/Users/bila/itkb/README.md`) but write CSO-appropriate copy. Do NOT copy itkb's IT-runbook sample content verbatim.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: CSO README — one-liner, diagram, quickstart, design decisions"
```

---

### Task 6: Docker — port, build, boot-verify

**Files:** `Dockerfile`, `docker-compose.yml` (already renamed in Task 1; verify references), `.dockerignore`.

- [ ] **Step 1: Verify rename in Docker files**

Run: `grep -n 'srag' Dockerfile docker-compose.yml | head`
Expected: `CMD ["srag", "serve", ...]`; compose `command: ["srag", "add", "/samples"]`; healthcheck on `/health`.

- [ ] **Step 2: Build (requires a Docker engine; use colima if present)**

```bash
colima start --memory 8 --cpu 4 2>&1 | tail -1 || true
docker build -t srag-demo .
```
Expected: build succeeds (multi-stage; pysqlite3 compiles in the builder stage). If the docker CLI is unavailable, report DONE_WITH_CONCERNS and defer to Task 7's manual step.

- [ ] **Step 3: Default profile boot**

```bash
docker compose --profile host-ollama up -d --build
curl -s http://localhost:8000/health   # {"status":"ok"}
docker compose --profile host-ollama down
```
Expected: `/health` 200; auth-gated API (401 without key, 200 with the printed key).

- [ ] **Step 4: Bundled demo boot + real query**

```bash
docker compose --profile bundled --profile demo-data up -d --build
# wait for ollama-init "models ready" and ingest-samples to ingest the PDFs
docker compose --profile bundled --profile demo-data logs ollama-init ingest-samples
KEY=$(docker compose --profile bundled logs api-demo 2>&1 | grep -oE '^api-demo-1  \| [A-Za-z0-9_-]{43}' | head -1 | awk '{print $3}')
curl -s -N -X POST http://localhost:8000/api/query -H "Content-Type: application/json" -H "X-API-Key: $KEY" \
  -d '{"question":"summarize the key policy points from the UN document"}' --max-time 180
docker compose --profile bundled --profile demo-data down
```
Expected: a grounded answer citing `samples/*.pdf`. (If the PDF corpus failed to download in Task 4, the query still works against the markdown samples.)

- [ ] **Step 5: Commit (if Task 6 changed Docker files beyond the Task 1 rename)**

```bash
git add -A
git commit -m "feat: verify Docker build and bundled demo boot for secure-rag"
```

---

### Task 7: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Health stack**

Run:
```bash
flake8 srag tests
/opt/homebrew/opt/python@3.11/bin/python3.11 -m mypy srag
python3 -m pytest -q
```
Expected: flake8 no output; mypy success; full suite green (report count).

- [ ] **Step 2: Real-Ollama integration tests**

Run: `python3 -m pytest tests/test_integration.py -v`
Expected: chat + embedding round-trips pass against the project's models (llama3.2:3b / nomic-embed-text); skipped cleanly if Ollama is unreachable.

- [ ] **Step 3: Repo final state**

Run: `git log --oneline; git status --short`
Expected: linear port history (7 commits), clean tree.
