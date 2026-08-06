# tests/test_parsers.py
import pytest
from srag.ingestion.parsers import parse_file, parse_url

def test_parse_markdown(tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("# Setup\n\nInstall nginx with apt.")
    text, title = parse_file(f)
    assert "nginx" in text
    assert title == "notes"

def test_parse_txt(tmp_path):
    f = tmp_path / "runbook.txt"
    f.write_text("Step 1: restart service\nStep 2: check logs")
    text, title = parse_file(f)
    assert "restart service" in text
    assert title == "runbook"

def test_parse_json(tmp_path):
    f = tmp_path / "config.json"
    f.write_text('{"port": 8080, "host": "localhost"}')
    text, title = parse_file(f)
    assert '"port"' in text
    assert title == "config"

def test_parse_yaml(tmp_path):
    f = tmp_path / "docker-compose.yaml"
    f.write_text("version: '3'\nservices:\n  web:\n    image: nginx")
    text, title = parse_file(f)
    assert "nginx" in text

def test_parse_unsupported_binary_raises(tmp_path):
    f = tmp_path / "binary.bin"
    f.write_bytes(bytes(range(256)))
    with pytest.raises(ValueError, match="Unsupported or binary file"):
        parse_file(f)

def test_parse_url_mocked(mocker):
    import httpx
    mock_get = mocker.patch.object(httpx, "get")
    mock_get.return_value.text = "<html><title>My Runbook</title><body><p>Run nginx</p></body></html>"
    mock_get.return_value.raise_for_status = lambda: None
    text, title = parse_url("http://example.local/runbook")
    assert "nginx" in text.lower() or "nginx" in text
    assert title == "My Runbook"
