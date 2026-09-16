# Atomic reinforcement and mutation provenance

Commit `483cc68` fixes a reproduced lost-update bug: three concurrent successful
confirmations previously retained only a 0.15 boost instead of 0.45, and could
overwrite one another's appended evidence. The regression failed on DuckDB before
the fix (one failed, one PostgreSQL case skipped in that initial local command).

`reinforce()` now reads and validates current state, applies the existing capped
increments and appends a checksummed `REINFORCE` record in one backend
transaction. The record includes complete before/after nodes, the evidence event
and the named policy. Outputs are read back before journaling to preserve actual
database numeric precision. PostgreSQL locks the owned row before reading;
DuckDB holds its connection lock until the native transaction finishes.

All 39 focused tests passed on source with live PostgreSQL and DuckDB, then on a
fresh Python 3.13 installed wheel with both backends (39 passed, no skips in each
run). The installed package matched all 125 source files from the frozen commit.
Tests cover concurrent confirmations, ownership/scope rejection, above-cap value
preservation, rollback after validation/update/journal fault injection, exact
before/after records, and persistence after reopening. The regression's old
read-barrier hook was confined to the concurrent calls after moving validation
into the backend; it must not stall the unrelated post-operation inspection.

The full source suite completed with native exit 0: 2,510 passed and 577 skipped
in 432.36 seconds. PostgreSQL was not configured for that full invocation; its
39-test focused run and installed run above used a live isolated database. The
full result must not be described as a complete live-PostgreSQL suite. See [structured evidence](reinforcement-atomic-483cc68.json)
and [installed verification](reinforcement-atomic-installed-verification.json).

Separate calls still count as separate signals; no caller-selected idempotency
key was added. DuckDB transaction conflicts may fail explicitly and require a
fresh call after confirming abort. Evidence ownership does not establish semantic
support. New records cannot reconstruct earlier unjournaled updates or complete
all historical graph replay. No benchmark accuracy gain is claimed for this fix.
