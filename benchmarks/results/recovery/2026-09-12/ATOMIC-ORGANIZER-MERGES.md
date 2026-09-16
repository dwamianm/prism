# Atomic organizer merge evidence

The previous identity safeguards prevented unsafe merge admission, but a merge
still performed evidence updates, relationship copies and retirement separately.
At `fffc8da`, sixteen authored tests reproduced partial state after failures and
two supersedence edges for one successful merge across DuckDB and PostgreSQL.

`31946c2` moves admission, canonical selection and publication into a backend
transaction. Current node values are checked again inside that transaction.
PostgreSQL locks shared nodes in UUID order before reading evidence, preserving
unions when merges overlap. DuckDB holds the shared connection lock until its
native transaction finishes; an independent conflicting writer can fail safely
and retry against current state.

Each commit includes canonical evidence, copies retaining relationship validity
and provenance, source retirement, one supersedence edge, and an
`ORGANIZER_MERGED` operation. The checksummed record contains complete before/after
nodes and original/published relationships. Its identity binds the unordered
node pair and merge kind. A repeat returns that committed identity without
reactivating a later-retired node. Exact partial copies left by older versions
can be recognized through their deterministic IDs; conflicting copies fail.

## Validation and retained failures

The frozen full collection at `31946c2` completed with native exit 0:
**2,793 passed, 81 skipped in 367.55 seconds**, including live PostgreSQL,
research and examples. Installed Python 3.13 organizer/identity checks passed
178 tests. These checks include failures during relationship insertion and after
evidence, retirement and journal writes; overlapping writers; owner/scope
admission; and retries after restart and lost acknowledgments.

Real child processes exited during evidence, edge, retirement and journal stages,
and after successful commit, on both backends. Before commit, reopening observes
the original state and a retry publishes the merge. After commit, reopening
observes the durable operation and a retry does not apply it again. These are
process-exit checks, not claims about hardware power loss.

External cancellation was tested in both execution models. A paused DuckDB
thread completes its transaction while the caller's cancellation waits; its
retry finds the committed operation. A PostgreSQL coroutine cancelled during
an awaited statement rolls back, and retry performs the merge. The general
contract remains that cancellation is not proof of rollback.

Two initial journal-comparison tests queried the retired node through the default
active-only read. That test mistake is retained; comparisons now explicitly
request retired state. It did not expose a production retirement defect.

The subsequent `4e43d0b` serialization guard closes a separate fidelity issue:
Pydantic JSON serialization can silently turn non-finite metadata into null.
Three tests reproduced that behavior. An initial `mode="json"` guard still
performed the conversion and failed those same tests. The final encoder uses
Python values with explicit UUID/datetime encoding and rejects non-finite JSON
numbers. Original compact JSON journals keep their bytes, checksum and retry
identity. All 65 source contract tests then passed, and type checking included
untyped function bodies.

The final installed wheel is verified against all 119 package source files.
Its Python 3.13 checks passed **183 tests in 25.12 seconds**, native exit 0.
Its real-BGE public workflow stores two entity mentions, organizes them, retrieves
only the active entity, and reopens the pack. Both source events and the combined
evidence remain intact; another organizer pass performs no second merge. The
[accompanying JSON record](atomic-organizer-merges-4e43d0b.json) distinguishes the full `31946c2` regression from the
later serialization-specific checks. They are not one combined full-suite run
of the final commit.

## Limits

External index eviction remains after graph commit and is repairable through
compaction; graph lifecycle filtering excludes retired candidates in the meantime.
This record does not prove full replay of all historical organizer/manual
mutations, hardware crash resilience, complete coreference, named-project grants,
or better memory-QA accuracy. No scoring defaults or competitive quality claims
were changed by this work.
