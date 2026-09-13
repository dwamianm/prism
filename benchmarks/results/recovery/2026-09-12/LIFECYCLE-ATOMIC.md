# Atomic lifecycle transitions on both storage backends

Runtime `cd898c8` (identical to frozen development source `e15bb40`) fixes a
reproduced PostgreSQL race: promotion read a tentative node, concurrent archival
committed, then the stale promotion restored the node to stable. A second check
found that PostgreSQL lacked `deprecate`, forcing organizer fallback behavior.
The before run had two failures, one pass and one backend-specific skip.

Both backends now validate the current lifecycle inside the transaction that
updates it and appends a checksummed `LIFECYCLE_CHANGED` operation containing
complete before/after values. PostgreSQL locks the target row before reading.
DuckDB retains its connection lock until native work finishes, including caller
cancellation. Contested-to-deprecated transitions now work directly on both
backends. Existing lifecycle transition rules remain in force.

Focused source checks passed 183 tests with two backend-specific skips. A fresh
Python 3.13.3 installed wheel passed the same 183 checks with the same skips.
All 126 installed Python source files matched the frozen source before and after
testing. Checks include the actual PostgreSQL inter-connection race, state and
journal rollback, restart, invalid terminal transitions, foreign ownership,
corrupted records, native cancellation and post-commit index eviction failure.
The full live-PostgreSQL suite passed 3,054 tests with 84 skips in 682.39 seconds
(native exit 0).

These operations do not have caller-supplied retry IDs. Invalid or repeated
terminal transitions still raise; inspect current state after an ambiguous
outcome. Arbitrary low-level graph updates and old unjournaled mutations remain
outside this coverage. A complete historical replay engine is not implemented.

See [structured evidence](lifecycle-atomic-cd898c8.json),
[installed artifact verification](lifecycle-atomic-installed-verification.json),
and [the execution contract](../../../../docs/RFC-0015-Self-Organizing-Memory.md).
