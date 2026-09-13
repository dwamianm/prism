# Durable confirmation retries across Python and HTTP

Runtime `eb6fdba` (identical to frozen development commit `ab4f362`) adds an
optional owner-scoped `request_id` UUID to `MemoryEngine.reinforce()` and the new
synchronous `MemoryClient.reinforce()` wrapper. HTTP accepts the equivalent UUID
`Idempotency-Key` header and an optional evidence-event body. Node responses
expose confidence/salience bases, reinforcement boost and its timestamp.

One request key binds the owner, node and optional evidence. The existing atomic
transaction stores a version 2 before/after record with that identity. Retrying
a committed request returns without changing the node or journal, including
after restart, cancellation or a lost acknowledgement. Later archival is not
undone. A changed request using the same key fails; a competing PostgreSQL insert
rolls back the losing node mutation. Separate owners can use the same UUID.
Unkeyed calls and new keys continue to record separate confirmations. Version 1
records remain readable under their original raw checksums.

Before implementation, four local request-ID workflow checks failed because the
argument was unavailable (four PostgreSQL variants skipped in that initial local
command). The final focused run passed 115 tests with live DuckDB/PostgreSQL;
one DuckDB-specific native cancellation test is skipped on PostgreSQL. A fresh
Python 3.13.3 wheel passed the same 115 tests with that same single skip. All 125
installed source files matched the frozen source. There was one upstream
Starlette/AnyIO deprecation warning.

Coverage includes concurrent identical and changed-node keys, restart, lost
acknowledgement, cancelled native work, rollback before commit, scoped ownership,
changed evidence, retention of later archival, legacy record parsing, the sync
client, real HTTP request/response behavior, existing reinforcement semantics,
and the API suite. The full suite with live PostgreSQL is still running at this
checkpoint. That pending run is not represented as passed.

Request identity is not semantic evidence verification or independent-source
corroboration. New records cannot reconstruct older unjournaled mutations or
complete every historical replay. OpenAI was rechecked with the root-file key
and still returned HTTP 429; local reader studies continue independently.

See [structured verification](reinforcement-retries-eb6fdba.json),
[installed artifact verification](reinforcement-retries-installed-verification.json),
and [developer usage](../../../../docs/REINFORCEMENT.md).
