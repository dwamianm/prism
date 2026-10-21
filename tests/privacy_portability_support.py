"""Only task-owned local artifacts and explicitly assigned disposable databases."""
from contextlib import asynccontextmanager
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4


class HeldLocalLedger:
    """Test adapter: actual independent SQLite ledger, same-task nested lease.

    No hosted implementation or synthetic erasure entries are substituted.
    The original file lock remains held; only the engine's settled methods may
    consume it from a child, never acquire a nested child guard.
    """
    def __init__(self, source):
        from contextvars import ContextVar
        self.source, self.path, self.ledger_id = source, source.path, source.ledger_id
        self._lease = ContextVar("pv326_held_local_ledger", default=None)

    @asynccontextmanager
    async def guard(self):
        import asyncio
        from prme.privacy.policy import PrivacyDenied
        current = self._lease.get()
        if current is not None and current[1]:
            if current[0] is not asyncio.current_task():
                raise PrivacyDenied("privacy_ledger_unavailable")
            yield
            return
        async with self.source.guard():
            held = [asyncio.current_task(), True]
            token = self._lease.set(held)
            try:
                yield
            finally:
                held[1] = False
                self._lease.reset(token)

    def _require_lease(self):
        from prme.privacy.policy import PrivacyDenied
        current = self._lease.get()
        if current is None or not current[1]:
            raise PrivacyDenied("privacy_ledger_unavailable")

    async def register_store(self, *args):
        self._require_lease()
        return await self.source.register_store(*args)

    async def current_head(self, *args):
        self._require_lease()
        return await self.source.current_head(*args)

    async def entries_since(self, *args):
        self._require_lease()
        return await self.source.entries_since(*args)

    async def append_intent(self, *args):
        self._require_lease()
        return await self.source.append_intent(*args)


class PgRestoreAuthority:
    """Engine test's persisted policy/control row, with real PG locking.

    This small test capability is not the hosted policy implementation. Every
    effect validates the saved policy against the current stored descriptor;
    recovery alone can reuse its exclusive same-task transaction.
    """
    def __init__(self, pool, table, ledger):
        from contextvars import ContextVar
        self.pool, self.table, self.ledger = pool, table, ledger
        self._lease = ContextVar("pv326_recovery_policy", default=None)

    async def row(self, connection, *, lock=""):
        from prme.privacy.policy import PrivacyDenied
        rows = await connection.fetch(f"SELECT * FROM {self.table}" + lock)
        if len(rows) != 1:
            raise PrivacyDenied("privacy_capability_unavailable")
        return rows[0]

    @asynccontextmanager
    async def guard(self, saved, effect):
        import asyncio
        from prme.privacy.policy import PrivacyDenied, PrivacyPolicy
        held = self._lease.get()
        if held is not None and held[2]:
            if held[0] is not asyncio.current_task():
                raise PrivacyDenied("privacy_capability_unavailable")
            row = await self.row(held[1])
            if saved.policy != PrivacyPolicy.model_validate_json(row["policy"]):
                raise PrivacyDenied("privacy_stale_policy")
            yield
            return
        async with self.ledger.guard(), self.pool.acquire() as conn, conn.transaction():
            row = await self.row(conn, lock=" FOR SHARE")
            if row["state"] != "active" or saved.policy != PrivacyPolicy.model_validate_json(row["policy"]):
                raise PrivacyDenied("privacy_stale_policy")
            yield


class PgRestoreCapability:
    """Actual external ledger then exclusive PG control lock through COMMIT."""
    def __init__(self, authority, target):
        self.authority, self.ledger, self.target = authority, authority.ledger, target
        self.artifact_hash = target.artifact_sha256
        self.fail_acknowledgement = False

    @asynccontextmanager
    async def guard(self, *, namespace_id, operation_id):
        import asyncio
        from prme.privacy.policy import PrivacyDenied
        if (namespace_id, operation_id) != (self.target.namespace_id, self.target.operation_id):
            raise PrivacyDenied("privacy_boundary_mismatch")
        if self.authority._lease.get() is not None:
            raise PrivacyDenied("privacy_capability_unavailable")
        async with self.ledger.guard(), self.authority.pool.acquire() as conn, conn.transaction():
            await conn.execute("SET LOCAL lock_timeout='5s'")
            row = await self.authority.row(conn, lock=" FOR UPDATE")
            if (row["state"], row["namespace_id"], row["operation_id"], row["artifact_hash"]) != (
                    "recovering", namespace_id, operation_id, self.artifact_hash):
                raise PrivacyDenied("privacy_boundary_mismatch")
            held = [asyncio.current_task(), conn, True]
            token = self.authority._lease.set(held)
            try:
                yield
                if await self.authority.row(conn) != row:
                    raise PrivacyDenied("privacy_stale_policy")
                if self.fail_acknowledgement:
                    self.fail_acknowledgement = False
                    raise RuntimeError("synthetic lost restore acknowledgement")
            finally:
                held[2] = False
                self.authority._lease.reset(token)


@asynccontextmanager
async def fresh_config(base, path, layout, *, restore=False):
    if layout != "postgres":
        yield base.model_copy(update={"database_url": None,
            "pack_path": str(path.with_suffix(".prme")) if layout == "pack" else None,
            "db_path": str(path / "memory.duckdb"), "vector_path": str(path / "vectors.usearch"),
            "lexical_path": str(path / "lexical_index"), "encryption_enabled": False, "encryption_key": None})
        return
    import asyncpg
    url = os.environ["PRME_TEST_RESTORE_DATABASE_URL" if restore else "PRME_TEST_DATABASE_URL"]
    parts = urlsplit(url)
    assert parts.hostname in {"127.0.0.1", "localhost"} and parts.port == 55432
    assert parts.path in {"/privacy_engine_326", "/privacy_engine_326_restore"}
    admin = await asyncpg.connect(url)
    schema = "pv326_" + uuid4().hex
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        query = [(key, value) for key, value in parse_qsl(parts.query) if key != "search_path"] + [("search_path", schema)]
        configured = urlunsplit(parts._replace(query=urlencode(query)))
        yield type(base).model_validate({**base.model_dump(), "database_url": configured})
    finally:
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


# Runs only in the two pinned isolated historical environments. No current
# package imports or dependency/key failures count as format refusal.
HISTORICAL_PROBE = r'''
import asyncio, contextlib, io, json, sys, traceback
from pathlib import Path
from prme import MemoryEngine, MemoryClient, MemoryWorkspace, PRMEConfig
import prme.storage.engine as storage

spec = json.loads(Path(sys.argv[1]).read_text())
config = PRMEConfig.model_validate(spec["config"])
provider_creations = 0
class Embedding:
    model_name = "privacy-component-embedding"
    model_version = "1"
    dimension = 384
    async def embed(self, texts):
        return [[float(i % 3) / 3 for i in range(384)] for text in texts]
    async def embed_query(self, text):
        return (await self.embed([text]))[0]
def provider(*args, **kwargs):
    global provider_creations
    provider_creations += 1
    return Embedding()
storage.create_embedding_provider = provider
captured = io.StringIO()
async def verify(engine):
    value = await engine.get_event(spec["event_id"], user_id=spec["owner"])
    assert value.content == "Historical reader usable control."
async def run():
    adapter = spec["adapter"]
    if adapter == "engine":
        async with MemoryEngine.open(config, embedding_provider=Embedding()) as engine:
            await verify(engine)
    elif adapter == "client":
        def client():
            with MemoryClient(config=config, embedding_provider=Embedding()) as current:
                assert current.get_event(spec["event_id"], user_id=spec["owner"]).content == "Historical reader usable control."
        await asyncio.to_thread(client)
    elif adapter == "cli":
        import prme.cli as cli
        from argparse import Namespace
        await cli.cmd_export(Namespace(db_path=config.pack_path or config.db_path))
        assert "Historical reader usable control." in captured.getvalue()
    elif adapter == "http":
        from prme.api.app import create_app
        app = create_app(config)
        async with app.router.lifespan_context(app):
            await verify(app.state.engine)
    elif adapter == "mcp":
        from prme.mcp.server import create_mcp_server, engine_lifespan
        server = create_mcp_server(config)
        async with engine_lifespan(server) as state:
            await verify(state["engine"])
    elif adapter == "workspace":
        workspace_config = config.model_copy(update={"namespace_id": None})
        context = (MemoryWorkspace.open_postgres(workspace_config, name=spec["workspace_name"], embedding_provider=Embedding())
            if config.backend == "postgres" else MemoryWorkspace.open(spec["workspace_dir"], config=workspace_config,
                embedding_provider=Embedding(), single_file_packs=bool(config.pack_path)))
        async with context as workspace:
            async with workspace.namespace("existing", create=False) as memory:
                await verify(memory)
    else:
        raise AssertionError("unknown adapter")
status = {"adapter": spec["adapter"], "protected": spec["protected"]}
with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
    try:
        asyncio.run(run())
        status["outcome"] = "opened"
    except BaseException as error:
        frames = [(frame.name, frame.lineno) for frame in traceback.extract_tb(error.__traceback__)]
        status.update(outcome="refused", exception=type(error).__name__, frames=frames)
        message = str(error)
        status["format_refusal"] = (type(error).__name__ in {"BinderException", "UndefinedColumnError"}
            and "content_hash" in message and any(name in {"initialize_database", "initialize_pg_database"} for name, line in frames))
status["provider_creations"] = provider_creations
print(json.dumps(status))
if spec["protected"]:
    assert status.get("format_refusal") is True and provider_creations == 0, status
else:
    assert status["outcome"] == "opened", status
'''
