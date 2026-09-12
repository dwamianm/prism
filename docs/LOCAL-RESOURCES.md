# Resource control for multiple local packs

Separate directories are currently the supported way to keep two named projects
isolated. Use one pack per project, even when they share a user and `Scope.PROJECT`.
A metadata label does not prevent entity matching or maintenance across projects.

Each open DuckDB database has its own worker pool. Applications opening several
packs can explicitly choose a worker count:

```python
from prme import MemoryEngine, config_from_directory
from prme.storage.embedding import FastEmbedProvider

provider = FastEmbedProvider()  # Caller-owned; share on the application's loop.
config = config_from_directory("./projects/aurora")
config.database_url = None
config.duckdb_threads = 1

async with MemoryEngine.open(config, embedding_provider=provider) as memory:
    await memory.store("Retention is seven days", user_id="alice")
```

`PRME_DUCKDB_THREADS=1` configures the same setting through the environment.
An explicit constructor argument takes precedence. The default is `None`, which
preserves DuckDB's default. This setting applies when opening a local engine;
PostgreSQL ignores it. It controls DuckDB workers, not embedding inference,
Tantivy, USearch, Python workers, total process memory or all native threads.

Concurrent engines opening the same file must use matching thread settings,
including matching explicit/default choices. DuckDB rejects conflicting opens
without changing the already-open engine. Close all connections before changing
the setting for that file. Worker settings are runtime configuration, not durable
memory content; reopening a copied pack uses the caller's configuration.

Closing idle engines can reduce active resource use; applications should keep
the engine context alive until all operations finish. A shared provider avoids
loading another embedding model per pack. It does not share graph or search
indexes. A bounded cache must also handle leases, concurrent open/close,
cancellation and resource ownership. The [local workspace API](WORKSPACES.md)
now provides that bounded cache with lease ownership and durable project identity.

The [completed resource probe](../benchmarks/results/recovery/2026-09-12/PARTITION-RESOURCES.md)
uses tiny authored packs and a shared warmed model. Results do not establish an
optimal thread count for larger histories or concurrent workloads. Measure your
application before changing its worker count.

DuckDB describes its per-instance thread configuration in the
[configuration reference](https://duckdb.org/docs/current/configuration/overview)
and the runtime scope of settings in its
[SET documentation](https://www.duckdb.org/docs/current/sql/statements/set).
