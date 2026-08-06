# tests/test_config.py
from srag.config import Config, load_config, save_config

def test_default_config_values():
    cfg = Config()
    assert cfg.model == "llama3.2:3b"
    assert cfg.embed_model == "nomic-embed-text"
    assert cfg.top_k == 5
    assert cfg.trusted is False

def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # Patch CONFIG_DIR after monkeypatching HOME
    import srag.config as cfg_mod
    cfg_mod.CONFIG_DIR = tmp_path / ".srag"
    cfg_mod.CONFIG_FILE = cfg_mod.CONFIG_DIR / "config.toml"

    original = Config(model="mistral:7b", top_k=8)
    save_config(original)
    loaded = load_config()
    assert loaded.model == "mistral:7b"
    assert loaded.top_k == 8

def test_web_fallback_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    import srag.config as cfg_mod
    cfg_mod.CONFIG_DIR = tmp_path / ".srag"
    cfg_mod.CONFIG_FILE = cfg_mod.CONFIG_DIR / "config.toml"

    assert Config().web_fallback is True          # default on
    save_config(Config(web_fallback=False))
    assert load_config().web_fallback is False    # persists

def test_env_overrides_model_and_embed(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    import srag.config as cfg_mod
    cfg_mod.CONFIG_DIR = tmp_path / ".srag"
    cfg_mod.CONFIG_FILE = cfg_mod.CONFIG_DIR / "config.toml"

    save_config(Config(model="toml-model", embed_model="toml-embed"))
    monkeypatch.setenv("SRAG_MODEL", "env-model")
    monkeypatch.setenv("SRAG_EMBED_MODEL", "env-embed")
    loaded = load_config()
    assert loaded.model == "env-model"
    assert loaded.embed_model == "env-embed"

    monkeypatch.delenv("SRAG_MODEL")
    monkeypatch.delenv("SRAG_EMBED_MODEL")
    loaded = load_config()
    assert loaded.model == "toml-model"
    assert loaded.embed_model == "toml-embed"

def test_api_key_and_session_secret_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    import srag.config as cfg_mod
    cfg_mod.CONFIG_DIR = tmp_path / ".srag"
    cfg_mod.CONFIG_FILE = cfg_mod.CONFIG_DIR / "config.toml"

    assert Config().api_key == ""
    assert Config().session_secret == ""
    save_config(Config(api_key="k123", session_secret="s456"))
    loaded = load_config()
    assert loaded.api_key == "k123"
    assert loaded.session_secret == "s456"

def test_load_config_apply_env_false_ignores_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    import srag.config as cfg_mod
    cfg_mod.CONFIG_DIR = tmp_path / ".srag"
    cfg_mod.CONFIG_FILE = cfg_mod.CONFIG_DIR / "config.toml"

    save_config(Config(model="toml-model"))
    monkeypatch.setenv("SRAG_MODEL", "env-model")
    assert load_config().model == "env-model"
    assert load_config(apply_env=False).model == "toml-model"

def test_audit_enabled_defaults_true(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    import srag.config as cfg_mod
    cfg_mod.CONFIG_DIR = tmp_path / ".srag"
    cfg_mod.CONFIG_FILE = cfg_mod.CONFIG_DIR / "config.toml"

    assert Config().audit_enabled is True
    save_config(Config(audit_enabled=False))
    assert load_config().audit_enabled is False

def test_redact_pii_defaults_true(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    import srag.config as cfg_mod
    cfg_mod.CONFIG_DIR = tmp_path / ".srag"
    cfg_mod.CONFIG_FILE = cfg_mod.CONFIG_DIR / "config.toml"

    assert Config().redact_pii is True
    save_config(Config(redact_pii=False))
    assert load_config().redact_pii is False
