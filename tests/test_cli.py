from typer.testing import CliRunner
from srag.cli import app

runner = CliRunner()


def test_serve_default_host(mocker):
    mock_run = mocker.patch("uvicorn.run")
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0
    assert mock_run.call_args.kwargs["host"] == "127.0.0.1"


def test_serve_host_flag(mocker):
    mock_run = mocker.patch("uvicorn.run")
    result = runner.invoke(app, ["serve", "--host", "0.0.0.0"])
    assert result.exit_code == 0
    assert mock_run.call_args.kwargs["host"] == "0.0.0.0"


def test_config_set_bool_coercion(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    import srag.config as cfg_mod
    cfg_mod.CONFIG_DIR = tmp_path / ".srag"
    cfg_mod.CONFIG_FILE = cfg_mod.CONFIG_DIR / "config.toml"

    runner.invoke(app, ["config", "set", "redact_pii", "false"])
    assert cfg_mod.load_config().redact_pii is False

    runner.invoke(app, ["config", "set", "audit_enabled", "true"])
    assert cfg_mod.load_config().audit_enabled is True
