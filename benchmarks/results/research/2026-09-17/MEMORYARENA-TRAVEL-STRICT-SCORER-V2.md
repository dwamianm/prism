# MemoryArena travel strict scorer and paired-run protocol

The pinned MemoryArena travel evaluator at upstream revision `6cd9de14` can
silently shrink its denominators when groups or travelers are omitted, and its
shorter-string prefix comparison gives full credit to values such as `C` when
the reference is `Cedar Lodge`. Those behaviors make the native score unsafe as
the sole decision metric for a memory-system comparison.

`score_strict` now keeps the exact registered cohort as the denominator and
requires one complete submitted object for every registered day. Duplicate,
missing and extra days fail the person. Every one of the six native scoring
slots must be present, and credit requires full Unicode-NFKC, case-folded,
whitespace-normalized string equality. The score reports person success (PS),
constraint-slot success (SPS) and group success (SR) using the same broad units
as the upstream evaluator. It remains a string scorer, not a semantic judge of
whether an itinerary is feasible or whether two different names are legitimate
aliases.

The fresh authored audit retained the unchanged native scorer alongside the new
strict result:

| Authored submission | Exact coverage | Native PS / SPS / SR | Strict PS / SPS / SR |
|---|---:|---:|---:|
| Four complete exact plans | pass | 100 / 100 / 100 | 100 / 100 / 100 |
| One exact plan, three explicit null failures | pass | 25 / 25 / 0 | 25 / 25 / 0 |
| One exact plan, three omitted failures | reject | 100 / 100 / 100 | not scored |
| One-character prefixes in every slot | pass | 100 / 100 / 100 | 0 / 0 / 0 |

The canonical audit artifact is
[`memoryarena-authored-travel-strict-scoring-audit-v2.json`](memoryarena-authored-travel-strict-scoring-audit-v2.json),
SHA-256 `59df40f041d98339093c4d6c01d21a38cea7cf44925f11e4fe0f1ad42d536713`.
It executes the upstream evaluator unchanged on an authored two-group cohort;
it is not a task-performance result.

`benchmarks.integrations.run_memoryarena_travel` adds a preregistered paired
development runner. It binds the pinned upstream source, every local database
file, the Hugging Face dataset bytes, the Ollama model manifest and the selected
whole-group cohort before generation. It counterbalances the two arms within
groups:

- `native_full_history` uses MemoryArena's pinned `memory_system=none`
  behavior, where the base plan and every prior generated plan stay in the
  actor's history.
- `prme` uses the unchanged upstream three-endpoint client and PRME's audited
  raw-trace adapter with a 4,096-token memory budget.

Both arms use the same actor, tools, prompts, no-feedback policy and maximum
step count. Complete group-arm outputs are appended and fsynced, so an
interrupted run can start a fresh PRME pack and skip already completed groups.
The primary result is the strict score; native scores are retained only as a
secondary comparison. The development design uses one generation per arm, so
it cannot estimate actor variance or establish broad product leadership.

Validation: 35 MemoryArena adapter, coverage, scorer and runner tests passed,
including the real FastAPI adapter tests. Ruff passed on all changed Python
files. A separate DeepSeek preflight on excluded group 1 completed in two model
steps, made five real database tool calls, and emitted a parseable itinerary in
22.6 seconds. That preflight is route validation and is excluded from cohort
selection.
