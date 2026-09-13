# Simulation maintenance clock correction

Commit `3280834` extends the checkpoint clock to consolidation age checks,
edge and derivation timestamps, native lifecycle/reinforcement/merge helpers,
extraction-work clocks and retrieval snapshots. Execution budgets retain real
monotonic time. Partial patch setup now unwinds through `ExitStack`.

Before the fix, consolidation could retire one-day-old simulated sources because
its seven-day cutoff used today's real date. Native lifecycle writes similarly
recorded the real date instead of the checkpoint. Two authored integration tests
failed before the change. The focused check afterward passed **11 tests, 4
skipped**, including actual DuckDB transitions, reinforcement, edge validity and
consolidation across the simulated seven-day boundary. The skips are unconfigured
PostgreSQL variants of existing consolidation safety tests; simulations use DuckDB.
Package runtime code was not changed.

The complete scenario command returned **70/74, native exit 1**. API-decision,
database-correction and CEO-correction ranking failures persisted. Consolidation
passed in this run, while procedural memory missed Python in the top five. All
four failures are retained. No retrieval assertion or acceptance guard was relaxed.

A separate three-run observation before the correction produced two passes and
one failure on consolidation despite the same reference clock and all organizer
jobs running. Fresh IDs selected different tied-confidence source excerpts.
Normalized lexical scores changed while the compared source's semantic score,
confidence and salience stayed the same. Each run also created two summaries of
its same selected sources. This identifies lexical variation and duplicate
summary creation as further work; it does not prove identical-log nondeterminism
or establish that this clock fix resolves ranking quality.

The [verification record](simulation-clock-3280834.json) retains hashes, native
exits and every failed checkpoint. Preserve the earlier balanced-packing scenario
failure as separate evidence. A passing unit suite and a selected successful
scenario run are insufficient to claim reliable retrieval or product leadership.
