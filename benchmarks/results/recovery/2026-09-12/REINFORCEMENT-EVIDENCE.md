# Explicit reinforcement: evidence boundary and monotonic increments

At `2ec255c`, `reinforce()` requires any supplied evidence ID to identify an
existing event in the node's owner and scope. This also applies to an unscoped
operator call. Missing, foreign, cross-scope and malformed IDs fail before any
confidence, boost, timestamp or evidence-reference update. Existing source
records are not rewritten.

The increment caps previously lowered values already above the cap: confidence
0.99 became 0.95, and reinforcement boost 0.75 became 0.5. Reinforcement now
preserves those higher values and continues applying the existing bounded
increments below the caps.

Eight authored DuckDB regressions failed before the fix: six invalid-evidence
variants and both decreasing-value cases. The same selection had one passing
positive control and nine PostgreSQL skips. The expanded focused selection
then passed **129 tests with no skips**, including live PostgreSQL, automatic
re-mention/instruction reinforcement, owner-scoped mutations and HTTP routes.
The earlier local selection passed 47 with 28 skips.

A fresh Python 3.13.3 environment installed the built wheel. All 124 installed
Python source files matched the committed package, and the same selected
contracts passed **129 tests with no skips** in 30.32 seconds. Its one warning
is an upstream Starlette use of a deprecated AnyIO alias. The installed test
selection ran from outside the repository using the frozen `2ec255c` checkout;
the separate frozen reader/judge and extraction environments were preserved.

The final full source suite passed **2,988 tests with 81 skips** in 478.41 seconds,
including live PostgreSQL, with native exit zero. Runtime code remains `2ec255c`;
the test collection is `2ab0787`, including the corrected existing evidence
fixtures and the three outer evaluation-driver checks. The
[machine-readable record](reinforcement-evidence-2ec255c.json) retains native
exits, source identities, wheel and log hashes.

The initial full run is retained: **2 failed, 2,983 passed, 81 skipped** in
482.52 seconds, native exit 1. Both failures were older positive tests that
invented UUIDs without storing evidence. They now create real owned source
events, preserving the existing evidence-accumulation assertions. The corrected
selection passed 20 tests with 11 PostgreSQL skips; the nine legacy tests also
passed against the installed wheel. The subsequent full run used a fresh test
database. All disposable databases from these validations were removed after
their processes completed; existing application databases were not modified.

These checks establish provenance-boundary validation and monotonic increments.
They do not establish semantic support of an event, independent corroboration,
idempotent repeated confirmations, atomic concurrent reinforcement accumulation
or complete historical reinforcement replay. Omitting evidence remains an
explicit caller confirmation. No confidence defaults or retrieval policies changed.
