# PostgreSQL workspace workflow protocol

Registered before the diagnostic runs, 2026-09-12. Package source: `b7521bc`.
This follows the local workspace workflow, using the same real BGE provider and
source assertions. It tests the public PostgreSQL workspace contract; it does
not compare memory-answer quality or promote a hosted-storage default.

Run a two-project smoke, then one 100-project workflow from the installed Python
3.13 wheel. Byte-verify all package Python files against the frozen source first.
Use a disposable PostgreSQL server with permission to create temporary databases.
Record Python/package/server versions, the runner/helper hashes, elapsed time,
client RSS/thread/descriptor samples and pool sizes. Sampling after each stage
can miss transient peaks and excludes PostgreSQL server memory. Other regression
work may run concurrently; timing is descriptive, not a throughput comparison.

Each project has the same owner, scope and entity name: store two Aurora entity
sources, atomically deduplicate them, verify both evidence references, and admit
a raw pending retention fact unique to that project. Open no more than four
engines over one pool bounded to three connections. Submit all project retrieval
checks concurrently to exercise capacity waiting and connection reuse. Confirm
own fact/source/entity identity, raw-work completion, and absence of the adjacent
project's fact, source events and entity ID. This is not exhaustive all-pairs or
10,000-query RFC grant conformance testing.

Close the workspace, use native `pg_dump --format=custom`, restore to a newly
created database using `pg_restore --exit-on-error`, then reopen all projects and
repeat source/identity/retrieval checks. Retain the authored dump and its hash;
tear down only the diagnostic's newly generated databases. Store no connection
strings, credentials, or provider error bodies in the report. A failure must
retain `complete=false` and its native exit; never overwrite previous artifacts.
The sampled client budget is 4 GiB RSS or 1,000 threads, inherited from the local
workflow. Exceeding it aborts the run, rather than silently reducing project count.

Tests separately exercise controlled LLM extraction, profiles, failure rollback,
process exit, foreign-ID mutation rejection and relocated pgvector symbols.
This diagnostic uses real embeddings but no LLM extraction or judged answers.
