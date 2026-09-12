# Named memory with bounded open engines

`MemoryWorkspace` keeps named projects in separate local packs or PostgreSQL
schemas and limits how many engines stay open. The same owner and entity name
can appear in multiple projects without combining their sources, relationships, maintenance or receipts.

```python
from prme import MemoryWorkspace, PRMEConfig, Scope

config = PRMEConfig(database_url=None, duckdb_threads=1)
async with MemoryWorkspace.open("./memories", config=config, max_open=4) as workspace:
    async with workspace.namespace("client-aurora") as memory:
        await memory.store(
            "Retention is seven days", user_id="alice", scope=Scope.PROJECT,
        )
        result = await memory.retrieve("retention?", user_id="alice")
        print(memory.namespace.id, result.bundle.rendered_context)

    async with workspace.namespace("client-borealis") as memory:
        # This project has its own event log, graph, indexes, jobs and receipts.
        await memory.store(
            "Retention is thirty days", user_id="alice", scope=Scope.PROJECT,
        )
```

Names are exact, case-sensitive keys of 1–512 UTF-8 bytes. Surrounding whitespace
and control characters are rejected. A slash is an ordinary name character,
not a directory separator or a grant hierarchy. `namespace(name)` creates a
registry entry when missing; use `namespace(name, create=False)` to require an
existing project. `await workspace.list_namespaces()` returns sorted immutable
`NamespaceInfo` records with names and stable UUIDs.

## Ownership and capacity

Each namespace context returns a `NamespaceMemory` with the public async
`MemoryEngine` API and signatures. The workspace owns engine lifecycle: direct
`close`, `open`, `create`, `lock` and `unlock` are unavailable on the lease.
Do not access private engine internals. A released lease and saved methods from
that lease reject further operations, even if another lease uses the same engine.

Concurrent contexts for the same project share one capacity slot. Each context
has its own lifetime. On exit, new calls are rejected and operations already
started through that lease finish before its reference is released. Explicitly
close partially consumed async iterators with `aclose()` or `contextlib.aclosing`
before exiting their lease; an unclosed iterator is still an active operation.

When the cache is full, the least recently released engine with no leases is
closed before another engine opens. If every engine is leased, another task
waits for capacity and can cancel that wait. A nested request raises
`WorkspaceError` when its task holds a lease on every engine and would otherwise
wait on itself. Closing a workspace from inside one of that task's leases also
raises instead of deadlocking. Release the contexts first.

Cancellation during native startup or shutdown waits for ownership to settle;
it does not mean the operation was rolled back. Workspace shutdown rejects new
leases and waits for existing leases and their calls to finish. Cleanup failures
propagate and prevent reuse of a failed engine. All workspace and lease calls
must run on the workspace's owning event loop.

`max_open` defaults to four; this is a bounded convenience default, not a
benchmark-proven optimum. One embedding provider is shared across engines.
Supply `embedding_provider=` to use a caller-owned provider, which the workspace
does not close. [DuckDB worker control](LOCAL-RESOURCES.md) can separately limit
workers per pack. Neither setting is a hard process-memory or total-thread cap.

## Ingestion and maintenance

For completed extraction within a lease, call
`await memory.ingest(..., wait_for_extraction=True)`. The default `False` accepts
the source and starts background work. Normal engine shutdown on eviction or
workspace close can cancel that background extraction; its source and durable
work remain available. Inspect `extraction_status` and explicitly run
`process_extractions` after reopening. A lease ending does not claim extraction
is complete. Pending raw indexing can similarly be inspected and resumed through
`processing_status` and `process_pending`; retrieval can materialize raw sources.

Unscoped maintenance applies to users inside the selected project only. Owner
and scope rules within each pack still apply. Scoped feedback receipts and
processing IDs from another project behave as absent; project selection never
turns an owner ID into authorization.

## Durable identity and copying

The workspace stores a transactional `workspace.sqlite3` registry and puts packs
under `packs/<namespace UUID>/`. Each pack also stores its namespace UUID inside
DuckDB. That identity is checked before normal schema migration, backfill, index
opening or recovery. A swapped or unbound database fails explicitly. An
initialized project whose database disappears is not recreated as empty memory.
Existing legacy packs require an explicit import procedure, which is not yet
provided; do not copy them into a generated project directory.

Close the workspace before copying the entire directory, including its registry
and packs. Reopening that copy preserves namespace IDs, sources and histories.
Copying one pack into a differently registered project is rejected. Registry
names and UUIDs are plaintext metadata, even when pack encryption is configured;
the source contents use the existing encrypted-pack behavior. Supply the same
encryption credentials and compatible embedding provider when reopening.

Only one workspace instance may own a directory at a time, including across
processes. Share that instance inside the application. A native file lock is
released on clean shutdown or process exit. This is local storage ownership,
not a distributed lock for shared network filesystems. Symbolic links within
workspace storage are rejected, but the filesystem and application remain
trusted; this is not a boundary against malicious local file mutation.

## PostgreSQL workspaces

Install `prme[postgres]` and supply a database connection through configuration:

```python
# PRME_DATABASE_URL is read from the environment or .env; keep credentials there.
config = PRMEConfig()
async with MemoryWorkspace.open_postgres(
    config, name="assistant-app", max_open=4, max_connections=10,
) as workspace:
    async with workspace.namespace("client-aurora") as memory:
        await memory.store("Retention is seven days", user_id="alice")
        result = await memory.retrieve("retention?", user_id="alice")
```

`name` identifies a workspace in this database; project names are scoped by it.
Names remain exact case-sensitive keys. Independent application instances using
that same database/workspace/project resolve the same stable namespace UUID.
They can run concurrently. Each workspace instance has one asyncpg pool with
`min_connections=1` and `max_connections=10` by default; these are per-instance
bounds. The engine cache and lease rules above apply to both backends. Evicting
one engine drains its namespace checkouts without closing the shared root pool.
An application running many workspace instances must budget their pools together.

The `prme_workspace` schema contains the transactional registry. Each project
uses `prme_ns_<UUID hex>` for its event log, graph, vectors, lexical search, jobs,
profiles and receipts. Registry initialization and the first complete project
schema commit atomically; failed startup can retry the same identity. Initialized
missing tables, sequences, schemas or mismatched identity fail explicitly before
normal migration can recreate them. Loss of the registry while namespace schemas
remain also fails. Existing unbound schemas require explicit import, not adoption.

Every checkout verifies identity, clears temporary relations left on reused
connections, and selects only the project's schema. Missing tables cannot fall
back to `public`. pgvector types, functions, operators and index operator classes
are qualified using its installed extension schema; that schema is not added to
the table search path. Existing extensions keep their location. The initializer
installs missing `vector` and `pgcrypto` extensions in `public`, requiring the
appropriate database privileges or administrator provisioning. These choices
follow PostgreSQL's [schema resolution rules](https://www.postgresql.org/docs/current/ddl-schemas.html)
and asyncpg's [pool reset behavior](https://magicstack.github.io/asyncpg/current/_modules/asyncpg/pool.html).
Do not move extensions while engines are open.

Use PostgreSQL-native backup/restore for these schemas and their registry,
including extension dependencies. They are not local copyable pack directories;
local pack encryption is rejected by this factory. Configure database transport,
at-rest encryption, roles and backups through PostgreSQL deployment controls.
Neither schema routing nor a workspace/project name grants database access.
Privileged SQL and trusted application code can still access multiple schemas.

## Current boundary

This API implements named local and PostgreSQL physical partitions. It does not
implement a synchronous workspace client, hosted credential-to-project grants,
cross-project queries, hierarchy, rename/delete/import, or full RFC-0004
conformance. `open()` remains local; use `open_postgres()` for PostgreSQL. Applications
must authorize the requested project before selecting it. HTTP/MCP servers do not
gain project routing merely by installing this API. Direct unbound `MemoryEngine`
and CLI access remain trusted operator tools, not tenant access controls.

The [resource study](../benchmarks/results/recovery/2026-09-12/PARTITION-RESOURCES.md)
motivated bounded ownership. Correctness checks use authored histories and
controlled extraction output; they do not establish memory-QA superiority.
