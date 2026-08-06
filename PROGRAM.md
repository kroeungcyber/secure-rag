# PROGRAM.md — secure-rag Central Control File

> **Every operation below requires your explicit approval.**
> This file is the single source of truth for secure-rag's behavior.
> Edit it directly and the agent enforces it on the next query.

---

## 1. Models

```yaml
chat: llama3.2:3b            # LLM for ReAct agent loop
embed: nomic-embed-text      # embedding model for vector search
```

## 2. Path Whitelist — Ingest

> Only files under these directories can be ingested.
> Any path outside this list is rejected.

```yaml
ingest_paths:
  - /samples/
  - /Users/bila/secure-rag/
```

## 3. URL Whitelist — Ingest

> Only URLs matching these prefixes can be scraped.

```yaml
ingest_urls:
  - https://www.un.org/
  - https://moeys.gov.kh/
```

## 4. Command Execution

> **OFF by default.** Set to `true` only after reviewing what the agent
> can run. Even when ON, dangerous patterns are always blocked.

```yaml
commands_enabled: false
```

**Always blocked (cannot be overridden):**
- `rm -rf`, `dd if=`, `mkfs`, `shutdown`, `reboot`, fork bombs

**When commands_enabled is `true`:**
- The agent PROPOSES a command before running it
- You must type `y` to approve each command
- All commands and output logged to `~/.srag/srag.sqlite` → `command_history`

## 5. Query Behavior

```yaml
top_k: 5                        # max chunks returned per search
max_iterations: 5               # max ReAct loop iterations
trusted: false                  # if true, commands run without prompt
```

## 6. Web UI

```yaml
host: 127.0.0.1                 # local only
port: 8000
```

## 7. Database

```yaml
path: ~/.srag/srag.sqlite       # single-file, fully local
```

---

## Change Log

Edit below when you approve changes:

| Date | What | Approved |
|------|------|----------|
| 2026-08-06 | CSO port: models llama3.2:3b / nomic-embed-text; ingest_paths restricted to /samples/ and the secure-rag repo; ingest_urls restricted to UN and MoEYS; commands OFF; top_k 5, trusted false | ✓ |
