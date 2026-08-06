# tests/test_docker_config.py
"""Guard the Docker packaging artifacts against silent breakage.

These tests exist because a non-editable install previously shipped without the
web assets (crashing the container at import) and the demo profile invoked a
nonexistent `kb ingest` subcommand. Both bugs passed the whole test suite.
"""
import tomli as tomllib
from pathlib import Path

import yaml

from srag.cli import app

REPO_ROOT = Path(__file__).parent.parent


def _registered_kb_commands() -> set[str]:
    commands = {c.name for c in app.registered_commands if c.name}
    # typer leaves the first command nameless unless name= is given; srag's
    # `kb add` is that command (srag/cli.py:18) and is invoked by the compose
    # demo profile, so it must count as registered.
    unnamed = [c for c in app.registered_commands if c.name is None]
    if unnamed:
        commands.add("add")
    return commands


def _compose_services() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())["services"]


def test_wheel_packages_web_assets():
    with open(REPO_ROOT / "pyproject.toml", "rb") as f:
        pyproject = tomllib.load(f)
    pkg_data = pyproject["tool"]["setuptools"]["package-data"]
    assert pkg_data.get("srag.api.web") == ["static/*", "templates/*"]
    # The referenced files must actually exist.
    web = REPO_ROOT / "srag" / "api" / "web"
    assert (web / "static" / "style.css").is_file()
    assert (web / "templates" / "chat.html").is_file()


def test_compose_kb_commands_are_registered():
    kb_commands = _registered_kb_commands()
    for name, svc in _compose_services().items():
        cmd = svc.get("command")
        if isinstance(cmd, list) and cmd and cmd[0] == "srag":
            assert cmd[1] in kb_commands, f"{name} calls unknown srag command: {cmd[1]}"


def test_compose_services_do_not_collide_on_ports():
    services = _compose_services()
    by_port: dict[str, list[str]] = {}
    for name, svc in services.items():
        for p in svc.get("ports", []):
            by_port.setdefault(p, []).append(name)
    for port, owners in by_port.items():
        # A published port must be owned by exactly one service, OR by
        # services in mutually-exclusive profiles. `api` (host-ollama) and
        # `api-demo` (bundled) deliberately both publish 8000 and are never
        # co-active under the documented commands.
        if len(owners) == 1:
            continue
        profile_sets = [set(services[n].get("profiles", [])) for n in owners]
        for i, a in enumerate(profile_sets):
            for b in profile_sets[i + 1:]:
                # A service with no profiles is always active, so it collides
                # with every other service on the same port regardless of
                # their profiles.
                overlap = (a & b) if (a and b) else bool(a or b)
                assert not overlap, (
                    f"port {port} published by co-active services {owners}: "
                    f"overlapping profiles {sorted(a & b) if (a and b) else '(always-active)'}"
                )
