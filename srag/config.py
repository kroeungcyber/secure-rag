# srag/config.py
from __future__ import annotations
import os
import tomli as tomllib
import tomli_w
from pathlib import Path
from dataclasses import dataclass, asdict, fields

CONFIG_DIR = Path.home() / ".srag"
CONFIG_FILE = CONFIG_DIR / "config.toml"

@dataclass
class Config:
    model: str = "llama3.1:8b"
    embed_model: str = "nomic-embed-text"
    db_path: str = str(Path.home() / ".srag" / "srag.sqlite")
    top_k: int = 5
    trusted: bool = False
    tavily_api_key: str = ""
    web_fallback: bool = True
    api_key: str = ""
    session_secret: str = ""
    audit_enabled: bool = True
    redact_pii: bool = True

def load_config(apply_env: bool = True) -> Config:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        save_config(Config())
    with open(CONFIG_FILE, "rb") as f:
        data = tomllib.load(f)
    valid_keys = {f.name for f in fields(Config)}
    cfg = Config(**{k: v for k, v in data.items() if k in valid_keys})
    if apply_env:
        if os.environ.get("ITKB_MODEL"):
            cfg.model = os.environ["ITKB_MODEL"]
        if os.environ.get("ITKB_EMBED_MODEL"):
            cfg.embed_model = os.environ["ITKB_EMBED_MODEL"]
    return cfg

def save_config(cfg: Config) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "wb") as f:
        tomli_w.dump(asdict(cfg), f)
