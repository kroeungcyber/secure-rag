# srag/program.py
"""Read and enforce PROGRAM.md — the central control file."""
from __future__ import annotations
import os
import re
from pathlib import Path

PROGRAM_PATH = Path(__file__).parent.parent / "PROGRAM.md"


def _read_program() -> str:
    path = Path(os.environ.get("SRAG_PROGRAM_PATH", PROGRAM_PATH))
    return path.read_text()


def _parse_yaml_block(text: str, heading: str) -> dict:
    """Extract a YAML-like block under a markdown heading."""
    pattern = rf"## \d*\.?\s*{re.escape(heading)}.*?\n```yaml\n(.*?)\n```"
    m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if not m:
        return {}
    out = {}
    for line in m.group(1).strip().split("\n"):
        line = line.strip()
        if ":" in line and not line.startswith("#"):
            key, _, val = line.partition(":")
            val = val.split("#", 1)[0].strip().strip('"').strip("'")
            out[key.strip()] = val
    return out


def _parse_list(text: str, heading: str) -> list[str]:
    """Extract a YAML list block under a markdown heading."""
    pattern = rf"## \d*\.?\s*{re.escape(heading)}.*?\n```yaml\n(.*?)\n```"
    m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if not m:
        return []
    items = []
    for line in m.group(1).strip().split("\n"):
        stripped = line.strip().lstrip("- ").split("#", 1)[0].strip().strip('"').strip("'")
        # A bare `key:` line (e.g. `ingest_paths:`) is a section header, not an item.
        if stripped and not stripped.startswith("#") and not stripped.endswith(":"):
            items.append(stripped)
    return items


# ── Public API ────────────────────────────────────────────────────────

def commands_enabled() -> bool:
    text = _read_program()
    block = _parse_yaml_block(text, "Command Execution")
    return block.get("commands_enabled", "false").lower() == "true"


def ingest_path_allowed(path: str) -> bool:
    whitelist = _parse_list(_read_program(), "Path Whitelist")
    if not whitelist:
        return True  # no whitelist = allow all
    # Resolve both sides (expand ~, collapse ..) before comparing, otherwise
    # a path like "<whitelisted>/../../../etc/shadow" passes a raw prefix
    # check while actually escaping the whitelisted directory.
    resolved = str(Path(path).expanduser().resolve())
    for w in whitelist:
        w_resolved = str(Path(w).expanduser().resolve())
        if resolved == w_resolved or resolved.startswith(w_resolved.rstrip("/") + "/"):
            return True
    return False


def ingest_url_allowed(url: str) -> bool:
    whitelist = _parse_list(_read_program(), "URL Whitelist")
    if not whitelist:
        return True
    return any(url.startswith(w) for w in whitelist)


def get_models() -> dict:
    text = _read_program()
    return _parse_yaml_block(text, "Models")


def get_query_settings() -> dict:
    text = _read_program()
    return _parse_yaml_block(text, "Query Behavior")


def get_web_settings() -> dict:
    text = _read_program()
    return _parse_yaml_block(text, "Web UI")


def reload_config_from_program():
    """Sync config.toml from PROGRAM.md models."""
    from srag.config import load_config, save_config
    cfg = load_config()
    models = get_models()
    if models.get("chat"):
        cfg.model = models["chat"]
    if models.get("embed"):
        cfg.embed_model = models["embed"]
    settings = get_query_settings()
    if settings.get("top_k"):
        cfg.top_k = int(settings["top_k"])
    if settings.get("trusted"):
        cfg.trusted = settings["trusted"].lower() == "true"
    save_config(cfg)
    return cfg
