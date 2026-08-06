# tests/test_program.py
from srag.program import ingest_path_allowed, ingest_url_allowed

PROGRAM_TEXT = """
## Path Whitelist

```yaml
ingest_paths:
  - /tmp/srag-notes/
```

## URL Whitelist

```yaml
ingest_urls:
  - https://wiki.
```
"""


def test_ingest_path_allowed_inside_whitelist(mocker):
    mocker.patch("srag.program._read_program", return_value=PROGRAM_TEXT)
    assert ingest_path_allowed("/tmp/srag-notes/runbook.md") is True


def test_ingest_path_allowed_nested_inside_whitelist(mocker):
    mocker.patch("srag.program._read_program", return_value=PROGRAM_TEXT)
    assert ingest_path_allowed("/tmp/srag-notes/sub/dir/runbook.md") is True


def test_ingest_path_blocked_outside_whitelist(mocker):
    mocker.patch("srag.program._read_program", return_value=PROGRAM_TEXT)
    assert ingest_path_allowed("/etc/shadow") is False


def test_ingest_path_traversal_outside_whitelist_is_blocked(mocker):
    mocker.patch("srag.program._read_program", return_value=PROGRAM_TEXT)
    # Raw string starts with the whitelisted prefix, but ".." resolves
    # outside of it -- must be blocked, not allowed by a naive startswith.
    assert ingest_path_allowed("/tmp/srag-notes/../outside.txt") is False


def test_ingest_url_allowed_prefix_match(mocker):
    mocker.patch("srag.program._read_program", return_value=PROGRAM_TEXT)
    assert ingest_url_allowed("https://wiki.example.com/page") is True
    assert ingest_url_allowed("https://evil.example.com/page") is False


def test_program_path_env_override(tmp_path, monkeypatch):
    custom = tmp_path / "CUSTOM_PROGRAM.md"
    custom.write_text(
        "## 5. Command Execution\n\n```yaml\ncommands_enabled: true\n```\n"
    )
    monkeypatch.setenv("SRAG_PROGRAM_PATH", str(custom))
    from srag.program import commands_enabled
    assert commands_enabled() is True
