# Coding-memory integration: shelved after an inconclusive final study

**Decision: shelve automatic coding-memory integration.** The earlier studies
have not established reliable benefit. The final bounded continuation study was
stopped because its bindings acceptance test rejected a valid implementation.
That defect belongs to the study harness. This study does **not** demonstrate
that PRME is harmful, that plain notes are superior, or that PRME generally fails.

Routine recall/capture requirements have been removed from `AGENTS.md` and
`CLAUDE.md`. Manual tools, existing saved memory and credentials are retained.
No replacement trial, retrieval tuning or expanded integration follows this
result. Production engine and retrieval defaults are unchanged.

## What was registered and run

The [protocol](../../../coding/FINAL-PROTOCOL.md) and
[prepared-input registration](coding-memory-final-v1/registration.json) were
committed at `17d70e58128aff3f36957ad33eb52e09984af379` before scored calls.
Four newly authored application scenarios covered corrections, workspace
lifetimes, value bindings and relevance feedback. These were application
scaffolds with explicit contracts, not new production features or held-out real
tickets. Sources, existing tests and documentation were frozen at
`df2d58ea9679a801fc9c176db493008405d7adbe` (588 files).

The corpus contained all eleven active historical project notes and their
immutable source excerpts, frozen before scenario authoring and split into 41
passages. Both notes arms used those exact passages: deterministic BM25 search
versus PRME's existing coding-service retrieval. Both had a 2,048-token context
ceiling and could search the same history files. Control had the same complete
source/test/docs access without saved-note context. All eight prepared contexts
fit the ceiling. No task-specific notes were added.

The local model was Ollama `qwen3.5:35b-a3b`, Q4_K_M, thinking off, temperature
zero, twelve actions per run, with model/template/server identities pinned.
Candidates ran in disposable, unprivileged, networkless Docker containers.
The plan allowed at most 24 runs: four tasks, three arms, two repeats. The
preregistered gates required a substantial correctness or token-efficiency gain
without correctness losses. Inconclusive results triggered shelving.

Ten runs completed, the eleventh was deliberately interrupted, and thirteen
never started. There were no provider failures in completed runs. No paired
second repeat completed. The runner records the operator interrupt as
`KeyboardInterrupt`; [manifest](coding-memory-final-v1/manifest.json) records
its actual reason. No additional model calls were made to repair the study.

## Why the grading is invalid

All three first-repeat bindings candidates were byte-identical. They called the
exported `prme.models.value_bindings.resolve_tool_arguments(bundle, arguments)`
helper and returned its copied arguments and typed binding-use audit. The real
`MemoryBundle.resolve_tool_arguments()` method delegates directly to that helper.

The hidden check supplied a narrow fake bundle containing only the wrapper
method. The real helper needs ordinary `MemoryBundle` fields that the fake did
not implement. Consequently, the grader reported `AttributeError` for a valid
public-API implementation in every arm. Passing the known witness and rejecting
an empty stub had not established that the grader accepted alternative valid
implementations.

A post-hoc diagnostic executed the unchanged generated candidate in the same
frozen Docker image with real PRME objects. It passed checks for exact-value
replacement, substring preservation, copied nested arguments, typed provenance,
already-lookup provenance, empty input, invisible bindings and conflicting
bindings. This was a grader audit with no new model calls, not a replacement
trial or a retrospective promotion score. See
[diagnostics](coding-memory-final-v1/diagnostics.json).

| Scenario | Completed arms | Interpretation |
| --- | --- | --- |
| Corrections | Control, notes, PRME | None passed. Control/notes made no edit; PRME wrote before validating the old node and omitted its scope. |
| Workspaces | Control, notes, PRME | None passed. Control/notes made no edit; PRME omitted existing-only namespace opening. |
| Value bindings | Control, notes, PRME | Registered grading invalid for all three. Identical candidates passed the real-object diagnostic. |
| Feedback | Control; notes interrupted | Control made no edit. No completed comparison. |

The workspace fake also omitted the real bundle's `rendered_context` field.
That field access was valid; the observed PRME candidate nevertheless violated
the explicit existing-only namespace requirement before reaching it.

These partial observations do not establish a comparative success rate. Raw
registered scores and token measurements are preserved with explicit validity
flags in [per-run metrics](coding-memory-final-v1/results.json). Aggregate token
ratios would be misleading because arm counts differ and the grading is flawed.
The [decision record](coding-memory-final-v1/summary.json) marks the evaluation
inconclusive and applies the operational stopping rule; its original registered
gate fields are not claims about a completed study.

## Evidence and limits

The [initial pilot](../2026-09-25/CODING-MEMORY-PILOT-V1.md) passed 2/8 memory runs
versus 0/8 control runs, with both gains on one authored task and 31.4% more input
tokens. The [real issue-108 trial](CODING-MEMORY-TICKET108-V1.md) passed 0/2 memory
runs versus 2/2 control runs and used 32.9% more input tokens. These small,
different development studies are not pooled into one score. Together they have
not justified maintaining automatic memory use. The invalid final study adds no
reliable evidence of benefit or harm.

Validation: 17 targeted harness/integration tests passed and changed Python files
passed Ruff. All four witnesses passed Docker preflight and all four empty stubs
failed, but the audit above exposes the limit of that preflight. No core packing
or retrieval change was made, so no LoCoMo/LongMemEval gate or paid API run was
needed. The separate issue-108 PR remains unmerged.

Raw transcripts, candidates, contexts, source snapshots and the separate memory
pack remain ignored under `benchmarks/coding/runs/final-v1/`. Six compact evidence
files total 22,900 bytes; the [checksum index](coding-memory-final-v1/checksums.json)
covers 69 local raw files, including the post-hoc diagnostic. The experiment
container was removed; the existing manual-use service and its data were kept.
