# Scoped evidence for explicit memory corrections

Runtime `ca045a9` (identical to frozen source `c6023c7`) rejects unavailable
evidence in supersedence, contradiction and contradiction resolution. Before
the fix, 24 backend checks showed that missing, malformed, foreign-owner and
foreign-scope evidence failed to raise. Invalid UUIDs could be silently discarded
while the state change proceeded.

Each operation now checks optional evidence inside its existing state/edge
transaction. The event must exist in the affected nodes' owner and scope.
Unavailable references share one error; invalid replacement-batch evidence
rolls back earlier entries. Omitted evidence remains permitted. The synchronous
`MemoryClient.supersede()` method now exposes the engine's correction workflow.

The final source and fresh installed-wheel runs each passed 112 tests with live
DuckDB and PostgreSQL, with no skips. The installed source manifest matched all
127 Python files before and after testing. The published correction guide also
executed unmodified in a fresh temporary directory using the installed package
and real default embeddings; its source-retention assertions passed and the
process exited zero. The full source suite with live PostgreSQL is still running.

The initial new test harness incorrectly called a keyword-only method and was
corrected before reproducing the defect. An attempted focused command referenced
a nonexistent test file and collected nothing. One existing contradiction fixture
used a fabricated evidence UUID; it now appends an actual same-owner/scope event
and checks the relationship's provenance. These unsuccessful attempts remain
hashed in the structured record rather than being counted as product findings
or passing verification. The first full suite passed 3,098 tests with 84 skips
and failed one additional end-to-end fixture that also supplied a fabricated
evidence UUID. That fixture now appends a real source and checks edge provenance.
The focused source follow-up passed 50 tests, and all five tests in the corrected
end-to-end module passed against the installed wheel. A fresh full suite is running
on the corrected source; the unsuccessful full attempt remains recorded.

Membership and ownership checks do not prove entailment. Arbitrary low-level
edge writes, caller retry keys for corrections, and complete historical replay
remain outside this change.

See [structured evidence](transition-evidence-ca045a9.json),
[installed verification](transition-evidence-installed-verification.json), and
[the developer guide](../../../../docs/MEMORY-CORRECTIONS.md).
