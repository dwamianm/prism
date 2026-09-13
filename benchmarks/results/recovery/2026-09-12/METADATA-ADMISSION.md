# Portable metadata admission and retained legacy values

Runtime `7b21af5` (identical to frozen source `82ea929`) addresses a reproduced
backend difference and legacy data loss. Before the change, twelve admission
checks failed: DuckDB accepted non-finite metadata while PostgreSQL returned
driver-level JSON errors. An installed-package probe then confirmed that direct
storage retained `NaN` in the event but converted it to null in the initial node
snapshot. Raw ingestion retained `NaN` in both, and archival failed when the
new lifecycle journal attempted to serialize it.

New event/direct-node admission now validates and copies finite JSON metadata
before awaiting a lock or connection. Invalid inputs publish no event or work.
A controlled caller mutation during that wait cannot change the stored source.
Existing events are not rewritten. Lifecycle, reinforcement and merge journals
use an explicit versioned path encoding for special floats from legacy nodes.
The complete envelope remains strict JSON; ordinary metadata cannot collide
with the tags. Finite snapshots retain their previous raw bytes and checksums.

Source checks passed 142 tests with four backend-specific skips. A fresh Python
3.13.3 wheel passed the same 142 checks and skips; all 129 installed Python files
matched the frozen source before and after testing. Coverage includes finite
metadata restart, rejected admission and work, nested-input mutation, raw legacy
reinforcement/retry/archival/restart, journal byte compatibility, special-float
reconstruction, malformed paths and existing atomic lifecycle/merge workflows.
The full live-PostgreSQL suite is running and is not counted as passed here.

Three earlier focused failures reflected the old requirement to reject any
non-finite merge journal. Those tests now enforce strict JSON, restored numeric
kinds and a readable retry record, retaining the original no-silent-null-loss
requirement. That unsuccessful attempt remains recorded.

Previously lost node values are not retroactively inferred from events. This
does not validate arbitrary low-level graph edits or implement complete historical
replay. Special-float snapshots need the new readers and preserve numeric kinds,
not platform-specific NaN payload bits.

See [structured evidence](metadata-admission-7b21af5.json),
[installed verification](metadata-admission-installed-verification.json), and
[the metadata contract](../../../../docs/METADATA.md).
