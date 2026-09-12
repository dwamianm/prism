# Frozen packing comparison

The complete 119-question development run at `1f5375a` finished with zero errors
and actual process exit zero. All 119 captured baseline contexts reproduced
exactly through that runtime before comparing priorities. The comparison changes
only the multi-path tier from score/token density to composite score; rendering,
fidelity, other priority tiers, candidate identities and scores are unchanged.

Of 119 questions, 114 have labelled source evidence. Complete source text must be
present to receive credit. Pointers and blank text do not qualify.

| Budget | Density recall | Score recall | Change | Paired interval | Wins / losses |
|---|---:|---:|---:|---:|---:|
| 2,048 | 65.57% | 88.16% | +22.59 pp | +15.28 to +29.97 pp | 42 / 5 |
| 4,096 | 75.51% | 93.49% | +17.98 pp | +12.13 to +24.27 pp | 31 / 0 |
| 8,192 | 81.29% | 96.35% | +15.06 pp | +9.87 to +20.91 pp | 27 / 0 |

At 2K, the seven-question preference category regresses by 14.29 points; the
other category means improve. The five individual losses are `ef66a6e5`,
`95228167`, `dfde3500`, `gpt4_7fce9456`, and `51c32626`. At 4K and 8K there are
no individual losses. These are source-retention results, not answer accuracy,
abstention quality, or competitive memory-system results.

The first comparator attempt failed because Python `splitlines()` treated valid
Unicode separators inside JSON strings as record boundaries. Three authored
tests reproduced the error. `b173048` fixes the harness to use literal LF
boundaries; the full comparison was rerun against the same complete source input
and captured candidate hashes. The source run was neither rerun nor merged.
The corrected comparator completed with actual process exit zero.

[The full development comparison](product-packing-dev-1f5375a.json) retains
per-question results, source and implementation hashes, category results and
limitations. Source input and 141 MiB of captured benchmark text are retained
locally under `data/benchmarks/packing-dev-1f5375a-source.json` and
`data/benchmarks/packing-dev-1f5375a-candidates/`; those raw artifacts are ignored
by Git. Their hashes are validated by the comparison.

## Confirmation registered before execution

[confirmation-plan.json](confirmation-plan.json) fixes the 381 test-partition
question identities, dataset digest, `1f5375a` runtime, `dc7878c` comparison
harness, tokenizer/packing settings, budgets, method hashes and bootstrap settings.
The harness rejects changed identities, settings or implementation hashes and
requires registration to predate the source run.

The primary gate is positive 4K source-evidence recall gain with a positive lower
endpoint of the paired-question bootstrap 95% interval. Secondary guards require
nonnegative mean recall changes at 2K and 8K and nonnegative 4K category means for
categories with at least five labelled questions. No comparator or gate changes
will be chosen using partial or completed confirmation results.

This partition has appeared in earlier aggregate source-retrieval experiments.
The new packing comparison is prospectively frozen, but this is not a pristine
unseen benchmark. Shared histories can also make query intervals optimistic.
Subsequent answer-level and independent benchmark validation remain necessary.

The production packing default remains density while confirmation is pending.
