# Named local workspace evidence

`217facb` adds a public `MemoryWorkspace` API for named local projects. A
transactional registry maps exact names to stable UUIDs. Each pack carries that
UUID inside DuckDB, checked before ordinary startup. The cache closes only
unleased engines, waits for calls already started through released leases, and
settles cancelled opens/closes before releasing ownership.

The [usage guide](../../../../docs/WORKSPACES.md) documents API signatures,
capacity behavior, background extraction, encryption metadata and copying.
The [JSON record](workspaces-217facb.json) retains commits, native exits and hashes.

The full frozen regression at `217facb` passed **2,843 tests, with 81 skips in
397.78 seconds**, native exit 0, including live PostgreSQL, research and examples.

## Covered contracts

The focused source suite passed **34 tests in 15.23 seconds**, native exit 0.
It covers:

- Same-owner projects with identical entity names and different claims, including
  structured ingestion using controlled extraction output, duplicate maintenance,
  scoped receipts/feedback, deferred raw indexing and explicit recovery.
- Separate lease lifetimes on one engine, LRU eviction, nested-capacity errors,
  cancelled capacity waits, cancelled startup/eviction/shutdown, and a lease
  exiting while another task is still writing through it.
- Saved methods and iterators rejecting use after lease release, iterator
  lifetime tracking, foreign-loop rejection and lifecycle ownership guards.
- Startup retry retaining identity, wrong-pack rejection before ordinary schema
  initialization, refusal to recreate a missing initialized database, and explicit
  refusal to adopt existing unbound packs.
- A real child process being excluded while a workspace is owned, and process
  exit releasing its lock while preserving a registered source for later recovery.
- Encrypted eviction and complete closed-workspace copying; cleanup errors remain
  visible and prevent engine reuse. Registry corruption and symlink redirection
  fail instead of silently initializing a different workspace.

Normal eviction may interrupt background extraction started with
`wait_for_extraction=False`. A paused-extractor test confirms the event/work
survive and explicit processing after reopening completes the derivation. This
is durable acceptance, not a promise that lease exit finishes extraction.

Two test-fixture mistakes are retained in the record. The first assumed
maintenance would leave a raw indexing job pending, but maintenance drains that
work; the deferred source is now admitted after maintenance. A second fixture
omitted required extractor provider/model identity. Neither is reported as a
production defect or used to hide a failed product gate.

## Installed package and public workflow

The Python 3.13 wheel matches all **121 package Python files** at `217facb`.
Its combined workspace, resource-control, startup, configuration and atomic-merge
suite passed **161 tests with five skips in 34.61 seconds**, including live
PostgreSQL tests for existing engine behavior. These tests do not imply a
PostgreSQL workspace implementation. A strict typed public consumer also passed.
All 34 workspace tests additionally passed with the minimum FileLock 3.16.0.

The installed real-BGE workflow at runner `4a45553` created **100 projects** with
the same owner, project scope and entity name. Each retained its own source
events and retention fact through maintenance, four-engine cache eviction,
retrieval, complete workspace copying and reopening the copy. Adjacent foreign
node/event IDs were absent. All 100 projects completed, native exit 0.

On this host, the workflow took 51.23 seconds, with peak sampled RSS of 596.7 MiB,
104 sampled threads and 16 file descriptors. It includes population, indexing,
maintenance, two retrieval passes and copying; it is not a comparable speed
ratio against the earlier plain-pack resource probe. Other tests ran concurrently.
A separate two-project smoke run passed first. These authored workloads use local
embeddings and make no cloud extraction or memory-QA accuracy claim.

## Remaining boundaries

PostgreSQL project routing, hosted credential-to-project grants, explicit legacy
import, rename/delete and a synchronous workspace client remain unimplemented.
The application and filesystem are trusted; direct engine/CLI access remains an
operator interface. Registry names and IDs are plaintext metadata even when packs
are encrypted. Tests do not prove hardware power-loss resilience, hostile local
filesystem protection, full historical graph replay or RFC-0004 conformance.
Resource limits bound open engines, not total process memory or all native threads.
