# MemoryArena travel scoring: authored audit

The pinned, unmodified travel evaluator at upstream `6cd9de14` was executed on
an authored cohort of two groups with two travelers each. Only its data-loading
dependency was replaced by the fixture; no dataset, actor or memory system ran.
The complete inputs, outputs and evaluator hash are retained in
[the audit artifact](memoryarena-authored-travel-scoring-audit.json).

| Submission | Native person success | Native constraint success | Native group success | Exact cohort coverage |
|---|---:|---:|---:|---|
| All four exact plans | 100% | 100% | 100% | Pass |
| One exact plan, three explicit null failures | 25% | 25% | 0% | Pass |
| Same successful plan, three failures omitted | 100% | 100% | 100% | Reject |
| All slots contain only `C` instead of `Cedar Lodge` | 100% | 100% | 100% | Pass |

These are controlled scorer behaviors, not task-performance measurements.
The native evaluator intersects submitted groups with reference groups and
iterates submitted travelers. Its similarity function compares strings only
through the length of the shorter string. Thus incomplete submissions change
denominators, and a short shared prefix can receive full similarity credit.

`benchmarks.diagnostics.memoryarena_travel_audit.validate_coverage` now requires
the exact independently registered group/person cohort before evaluation.
Duplicates, unexpected identities and missing rows fail. Explicit null plans
remain failures and count toward the denominator. Fifteen coverage tests passed;
the native authored audit itself exited zero. This guard has not yet been wired
into a dataset travel runner, and it does not repair the prefix scoring issue.

Before a task study, freeze complete group selection and apply the coverage
guard to every arm. Retain the upstream metric under its own name. Any stricter
task score needs its own documented rules, authored positive/negative controls
and validation of legitimate itinerary aliases before dataset outcomes are
inspected. Native travel success alone cannot establish complete, valid planning.

Reproduce without downloading the dataset or calling a model:

```bash
python -m benchmarks.diagnostics.memoryarena_travel_audit \
  --upstream /path/to/pinned/MemoryArena --output fresh-audit.json
```
