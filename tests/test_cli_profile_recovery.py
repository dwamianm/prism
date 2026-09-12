"""CLI recovery stays on the named pack and preserves owner boundaries."""

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from prme import MemoryEngine
from prme import cli
from prme.types import Scope
from tests import test_knowledge_profile_scopes as fixtures
from tests.test_profile_work import prepare_only

config = fixtures.config
user = fixtures.user


@pytest.mark.parametrize("command", ["profile-jobs", "process-profiles", "resume-profile", "discard-profile", "collect-profile-staging"])
def test_every_profile_command_requires_owner(command):
    args = [command, "memory.duckdb"]
    if command in {"resume-profile", "discard-profile"}:
        args.append(str(uuid4()))
    for extra in ([], ["--user-id", "  "]):
        with pytest.raises(SystemExit) as error:
            cli.build_parser().parse_args(args + extra)
        assert error.value.code == 2


@pytest.mark.parametrize("options", [["--limit", "0"], ["--limit", "1001"], ["--budget-ms", "nan"],
                                    ["--budget-ms", "inf"], ["--budget-ms", "-1"], ["--scope", "unknown"]])
def test_invalid_batch_input_rejected_before_engine_open(options):
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(["process-profiles", "memory.duckdb", "--user-id", "u", *options])
    assert error.value.code == 2


def test_profile_uuid_validation():
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(["resume-profile", "memory.duckdb", "invalid", "--user-id", "u"])
    assert error.value.code == 2


async def test_explicit_local_cli_path_cannot_be_redirected_by_database_environment(tmp_path, monkeypatch):
    path = tmp_path / "memory.duckdb"
    path.touch()
    monkeypatch.setenv("PRME_DATABASE_URL", "postgresql://unintended.invalid/other")
    observed = []
    async def create(config):
        observed.append(config)
        return object()
    monkeypatch.setattr(MemoryEngine, "create", create)
    await cli._create_engine(str(path))
    assert observed[0].database_url is None
    assert observed[0].db_path == str(path)


async def test_cli_profiles_restart_scope_recovery_and_cleanup(config, user, monkeypatch, capsys):
    async with MemoryEngine.open(config) as engine:
        await fixtures.seed(engine, user)
        await fixtures.seed(engine, user + "-foreign")
        project = await prepare_only(engine, user, monkeypatch)
        personal = await prepare_only(engine, user, monkeypatch, scope=Scope.PERSONAL)
        foreign = await prepare_only(engine, user + "-foreign", monkeypatch)
    opened = []
    async def open_engine(_):
        engine = await MemoryEngine.create(config)
        engine._vector_index._provider.embed = AsyncMock(side_effect=AssertionError("No embedding during recovery"))
        engine._pipeline._extraction_provider.extract = AsyncMock(side_effect=AssertionError("No extraction during recovery"))
        engine.close = AsyncMock(wraps=engine.close)
        opened.append(engine)
        return engine
    monkeypatch.setattr(cli, "_create_engine", open_engine)
    # Mirrors main()'s diagnostic destination for direct async handler calls.
    import structlog
    import sys
    previous = structlog.get_config()
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    async def command(name, *arguments, owner=user, expected_exit=None):
        args = cli.build_parser().parse_args([name, config.db_path, *arguments,
                                             "--user-id", owner, "--format", "json"])
        capsys.readouterr()
        if expected_exit is None:
            await args.func(args)
        else:
            with pytest.raises(SystemExit) as error:
                await args.func(args)
            assert error.value.code == expected_exit
        opened[-1].close.assert_awaited_once()
        return json.loads(capsys.readouterr().out)
    try:
        rows = await command("profile-jobs", "--scope", "project")
        assert [r["plan_id"] for r in rows] == [str(project.node.id)]
        assert await command("resume-profile", str(foreign.node.id), expected_exit=1) == {
            "profile_id": None, "resumed": False}
        assert await command("resume-profile", str(uuid4()), expected_exit=1) == {
            "profile_id": None, "resumed": False}
        assert await command("process-profiles", "--scope", "project") == {
            "processed": 1, "failed": 0, "pending": 0, "errors": {}}
        assert (await command("resume-profile", str(project.node.id)))["resumed"] is True
        assert (await command("discard-profile", str(project.node.id), expected_exit=1))["discarded"] is False
        for _ in range(2):
            assert (await command("discard-profile", str(personal.node.id)))["discarded"] is True
        result = await command("collect-profile-staging", "--scope", "personal")
        assert result["collected"] == 1 and result["failed"] == result["remaining"] == 0
        assert (await command("collect-profile-staging", "--scope", "personal"))["collected"] == 0
        assert [r["plan_id"] for r in await command("profile-jobs", owner=user + "-foreign")] == [str(foreign.node.id)]
    finally:
        structlog.configure(**previous)


@pytest.mark.parametrize("action,result", [
    ("process-profiles", {"processed": 0, "failed": 1, "pending": 1, "errors": {"id": "storage"}}),
    ("collect-profile-staging", {"collected": 0, "failed": 0, "remaining": 1, "errors": {}, "blocked_reason": "incomplete_registry"}),
])
async def test_batch_failure_and_blocked_collection_report_json_then_nonzero_exit(action, result, monkeypatch, capsys):
    engine = AsyncMock()
    engine.process_profiles.return_value = result
    engine.collect_profile_staging.return_value = result
    monkeypatch.setattr(cli, "_create_engine", AsyncMock(return_value=engine))
    args = cli.build_parser().parse_args([action, "memory.duckdb", "--user-id", "u", "--format", "json"])
    with pytest.raises(SystemExit) as error:
        await args.func(args)
    assert error.value.code == 1
    assert json.loads(capsys.readouterr().out) == result
    engine.close.assert_awaited_once()


def test_main_routes_library_diagnostics_away_from_json(monkeypatch, capsys):
    import argparse
    import structlog
    previous = structlog.get_config()
    async def handler(_):
        structlog.get_logger("cli-json-test").warning("authored diagnostic")
        print(json.dumps({"success": True}))
    parser = argparse.ArgumentParser()
    parser.set_defaults(command="authored", func=handler)
    monkeypatch.setattr(cli, "build_parser", lambda: parser)
    monkeypatch.setattr("sys.argv", ["prme"])
    try:
        cli.main()
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"success": True}
        assert "authored diagnostic" in captured.err
    finally:
        structlog.configure(**previous)
