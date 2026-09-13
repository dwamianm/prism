# Reject metadata key collisions before durable admission

Runtime `e3350b1`, identical to isolated source `022a4c1`, prevents silent
metadata loss when distinct Python keys serialize to the same JSON object key.
For example, nested integer `1` and string `"1"` previously became duplicate
`"1"` keys; decoding retained only the later value. Sixteen checks reproduced
missing rejection across DuckDB/PostgreSQL and direct/raw ingestion, covering
integer, boolean, null and finite-float keys paired with their JSON strings.

Admission now detects duplicates in the actual serialized object pairs at every
depth, before a backend lock or connection is acquired. It raises a clear
`ValueError` without including metadata values or key names. Existing validation
of finite serializable values remains unchanged. Unambiguous conversions still
work, and identical keys in different objects remain independent.

The focused source suite passed 72 checks with four backend-specific skips.
A fresh Python 3.13.3 wheel passed the same 72 checks and four skips; all 129
installed Python files matched the frozen source before and after testing.
Coverage includes absence of source/node/recovery work after rejected metadata,
direct-node and deferred-extraction admission, caller mutation, legacy metadata
and snapshot compatibility. The full live-PostgreSQL suite exited zero with
3,172 passed and 85 skipped in 551.97 seconds on the same frozen source.

This does not restore previously discarded values or validate arbitrary
low-level graph/table mutations. See [structured evidence](metadata-keys-e3350b1.json),
[installed verification](metadata-keys-installed-verification.json) and
[the metadata contract](../../../../docs/METADATA.md).
