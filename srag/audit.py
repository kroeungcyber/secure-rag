# srag/audit.py
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path


def audit_log_path(db_path: str) -> Path:
    return Path(db_path).parent / "audit.jsonl"


def log_event(db_path: str, event: str, **fields) -> None:
    """Append one JSON line to audit.jsonl. Best-effort: never raises."""
    try:
        from srag.config import load_config

        if not load_config().audit_enabled:
            return
        record = {"event": event, "ts": datetime.now(timezone.utc).isoformat()}
        record.update(fields)
        with open(audit_log_path(db_path), "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass
