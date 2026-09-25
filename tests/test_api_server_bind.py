"""The HTTP API runner refuses unauthenticated network binds before it starts (#120)."""

from __future__ import annotations

import logging
import re
import socket
import sys
from pathlib import Path

import httpx
import pytest
import uvicorn

import prme.api.app
from prme.api import __main__ as api_main
from prme.api.server import (
    UnauthenticatedBindError,
    _check_bind,
    _is_loopback_host,
    run_server,
)
from prme.config import APIConfig, PRMEConfig

LOOPBACK = ["127.0.0.1", "127.0.0.2", "127.255.255.254", "::1", "0:0:0:0:0:0:0:1", "::ffff:127.0.0.1"]
EXTERNAL = ["0.0.0.0", "::", "", "192.168.1.10", "10.0.0.5", "2001:db8::1", "::ffff:10.0.0.5", "::ffff:0.0.0.0"]
# Names go through a fake resolver so no test depends on the machine's hosts file or DNS.
RESOLVES = {
    "localhost": ["::1", "127.0.0.1"],
    "LOCALHOST": ["::1", "127.0.0.1"],
    "loopback.test": ["127.0.0.1"],
    "lan.test": ["192.168.1.10"],
}
LOOPBACK_NAMES = ["localhost", "LOCALHOST"]
# Only localhost is resolved: any other name counts as a network address, even
# one that resolves to loopback now, because the server resolves it again later.
EXTERNAL_NAMES = ["loopback.test", "lan.test", "missing.test", "[::1]", "localhost."]
CREDENTIALS = [
    pytest.param({"api_key": "operator-token"}, id="global-key"),
    pytest.param({"user_keys": {"alice": "alice-token", "bob": "bob-token"}}, id="per-user-keys"),
]
SERVER_LOGGER = "prme.api.server"
REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_ambient_credentials(monkeypatch, tmp_path):
    """Keep credentials from the shell or a local .env out of every test."""
    monkeypatch.chdir(tmp_path)
    for name in ("PRME_API_API_KEY", "PRME_API_USER_KEYS", "PRME_API__API_KEY", "PRME_API__USER_KEYS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def resolver(monkeypatch):
    """Answer name lookups from ``RESOLVES`` and record them."""
    table = dict(RESOLVES)
    lookups = []

    def getaddrinfo(host, port, *args, **kwargs):
        lookups.append((host, kwargs))
        if host not in table:
            raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided, or not known")
        return [
            (socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))
            for address in table[host]
        ]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return table, lookups


@pytest.fixture
def launch(monkeypatch, resolver):
    """Record app creation and the uvicorn listener instead of starting either."""
    calls = {"create_app": [], "run": []}

    def create_app(config):
        calls["create_app"].append(config)
        return "app-sentinel"

    def run(app, **kwargs):
        calls["run"].append((app, kwargs))

    monkeypatch.setattr(prme.api.app, "create_app", create_app)
    monkeypatch.setattr(uvicorn, "run", run)
    return calls


def config(**api):
    return PRMEConfig(api=APIConfig(**api))


def server_warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.name == SERVER_LOGGER and r.levelno >= logging.WARNING]


# ---------------------------------------------------------------------------
# Loopback detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", LOOPBACK)
def test_loopback_addresses(host, resolver):
    assert _is_loopback_host(host)
    assert resolver[1] == []  # literal addresses are never resolved


@pytest.mark.parametrize("host", EXTERNAL)
def test_wildcard_and_network_addresses_are_not_loopback(host, resolver):
    assert not _is_loopback_host(host)
    assert resolver[1] == []


@pytest.mark.parametrize("host", LOOPBACK_NAMES)
def test_localhost_counts_when_it_resolves_only_to_loopback(host, resolver):
    assert _is_loopback_host(host)
    assert resolver[1] == [(host, {"type": socket.SOCK_STREAM, "flags": socket.AI_PASSIVE})]


@pytest.mark.parametrize("addresses", [["127.0.0.1", "192.168.1.10"], ["10.0.0.5"], []])
def test_localhost_with_any_network_address_or_none_is_not_loopback(addresses, resolver):
    resolver[0]["localhost"] = addresses
    assert not _is_loopback_host("localhost")


def test_unresolvable_localhost_is_not_loopback(resolver):
    del resolver[0]["localhost"]
    assert not _is_loopback_host("localhost")


@pytest.mark.parametrize("host", EXTERNAL_NAMES)
def test_other_names_are_network_addresses_without_a_lookup(host, resolver):
    assert not _is_loopback_host(host)
    assert resolver[1] == []


# ---------------------------------------------------------------------------
# run_server
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", LOOPBACK + LOOPBACK_NAMES)
def test_loopback_starts_without_credentials_and_without_warning(host, launch, caplog):
    cfg = config()
    run_server(host=host, port=8123, config=cfg)
    assert launch["create_app"] == [cfg]
    assert launch["run"] == [("app-sentinel", {"host": host, "port": 8123, "log_level": "info"})]
    assert server_warnings(caplog) == []


@pytest.mark.parametrize("host", EXTERNAL + EXTERNAL_NAMES)
def test_network_bind_without_credentials_is_refused_before_listener(host, launch):
    with pytest.raises(UnauthenticatedBindError, match="Refusing to start the PRME API") as refused:
        run_server(host=host, config=config())
    assert isinstance(refused.value, ValueError)
    message = str(refused.value)
    assert repr(host) in message
    assert "PRME_API_USER_KEYS" in message and "PRME_API_API_KEY" in message
    assert "--allow-unauthenticated-external-bind" in message
    # Neither the app (and so the engine) nor the listener was created.
    assert launch == {"create_app": [], "run": []}


@pytest.mark.parametrize("credentials", CREDENTIALS)
@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "lan.test"])
def test_network_bind_with_credentials_starts_with_warning(host, credentials, launch, caplog):
    run_server(host=host, config=config(**credentials))
    assert [kwargs["host"] for _, kwargs in launch["run"]] == [host]
    warnings = server_warnings(caplog)
    assert len(warnings) == 1 and "bearer-token auth is enabled" in warnings[0]
    assert "operator-token" not in warnings[0] and "alice-token" not in warnings[0]


@pytest.mark.parametrize("credentials", CREDENTIALS)
def test_credentials_on_loopback_start_quietly(credentials, launch, caplog):
    run_server(host="127.0.0.1", config=config(**credentials))
    assert len(launch["run"]) == 1
    assert server_warnings(caplog) == []


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "lan.test"])
def test_override_starts_unauthenticated_network_bind_with_warning(host, launch, caplog):
    run_server(host=host, config=config(), allow_unauthenticated_external_bind=True)
    assert [kwargs["host"] for _, kwargs in launch["run"]] == [host]
    warnings = server_warnings(caplog)
    assert len(warnings) == 1 and warnings[0].startswith("UNAUTHENTICATED EXTERNAL BIND")


def test_override_changes_nothing_on_loopback(launch, caplog):
    run_server(host="127.0.0.1", config=config(), allow_unauthenticated_external_bind=True)
    assert len(launch["run"]) == 1
    assert server_warnings(caplog) == []


@pytest.mark.parametrize("credentials", CREDENTIALS)
def test_override_with_credentials_keeps_the_authenticated_warning(credentials, launch, caplog):
    run_server(host="0.0.0.0", config=config(**credentials), allow_unauthenticated_external_bind=True)
    warnings = server_warnings(caplog)
    assert len(warnings) == 1 and "bearer-token auth is enabled" in warnings[0]


def test_override_is_keyword_only():
    with pytest.raises(TypeError):
        run_server("0.0.0.0", 8000, config(), "info", True)  # type: ignore[misc]


def test_configuration_from_the_environment_is_checked(launch, monkeypatch):
    with pytest.raises(UnauthenticatedBindError):
        run_server(host="0.0.0.0")
    assert launch == {"create_app": [], "run": []}

    monkeypatch.setenv("PRME_API_USER_KEYS", '{"alice": "alice-token"}')
    run_server(host="0.0.0.0")
    assert len(launch["run"]) == 1
    assert launch["create_app"][0].api.user_keys["alice"].get_secret_value() == "alice-token"


@pytest.mark.parametrize("credentials", [pytest.param({}, id="none"), *CREDENTIALS])
async def test_bind_check_and_router_agree_on_when_auth_is_enabled(credentials):
    """A network bind is allowed exactly when the router demands a bearer token."""
    cfg = config(**credentials)
    try:
        _check_bind("0.0.0.0", cfg, allow_unauthenticated_external_bind=False)
        bind_allowed = True
    except UnauthenticatedBindError:
        bind_allowed = False

    # The engine is never started here, so a request that passes auth gets 503.
    app = prme.api.app.create_app(cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/v1/stats")
    assert response.status_code == (401 if bind_allowed else 503)
    assert cfg.api.auth_enabled is bind_allowed


# ---------------------------------------------------------------------------
# python -m prme.api
# ---------------------------------------------------------------------------


def run_cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["python -m prme.api", *argv])
    api_main.main()


def test_cli_defaults_to_loopback(launch, monkeypatch):
    run_cli(monkeypatch)
    assert launch["run"] == [("app-sentinel", {"host": "127.0.0.1", "port": 8000, "log_level": "info"})]


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "lan.test"])
def test_cli_refuses_unauthenticated_network_bind(host, launch, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exited:
        run_cli(monkeypatch, "--host", host)
    assert exited.value.code == 2
    assert f"Refusing to start the PRME API on {host!r}" in capsys.readouterr().err
    assert launch == {"create_app": [], "run": []}


def test_cli_override_flag_allows_unauthenticated_network_bind(launch, monkeypatch, caplog):
    run_cli(monkeypatch, "--host", "0.0.0.0", "--port", "9001", "--allow-unauthenticated-external-bind")
    assert launch["run"] == [("app-sentinel", {"host": "0.0.0.0", "port": 9001, "log_level": "info"})]
    assert server_warnings(caplog)[0].startswith("UNAUTHENTICATED EXTERNAL BIND")


@pytest.mark.parametrize("abbreviation", ["--a", "--allow", "--allow-unauthenticated"])
def test_cli_override_flag_cannot_be_abbreviated(abbreviation, launch, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exited:
        run_cli(monkeypatch, "--host", "0.0.0.0", abbreviation)
    assert exited.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err
    assert launch == {"create_app": [], "run": []}


@pytest.mark.parametrize("variable,value", [
    ("PRME_API_API_KEY", "operator-token"),
    ("PRME_API_USER_KEYS", '{"alice": "alice-token"}'),
])
def test_cli_network_bind_with_credentials(variable, value, launch, monkeypatch):
    monkeypatch.setenv(variable, value)
    run_cli(monkeypatch, "--host", "0.0.0.0")
    assert [kwargs["host"] for _, kwargs in launch["run"]] == ["0.0.0.0"]


# ---------------------------------------------------------------------------
# Development PostgreSQL
# ---------------------------------------------------------------------------


def test_compose_publishes_the_test_database_on_loopback_only():
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    blocks = re.findall(r"^\s*ports:\s*\n((?:\s*-.*\n?)+)", compose, re.MULTILINE)
    published = [re.sub(r'^\s*-\s*|["\'\s]', "", line) for block in blocks for line in block.splitlines()]
    assert published == ["127.0.0.1:5432:5432"]
