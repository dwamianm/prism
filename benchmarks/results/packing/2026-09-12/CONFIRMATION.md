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

The production packing default remained density throughout the frozen study.

## Completed confirmation: gate failed

All 381 questions completed without errors and with native exit zero. The frozen
comparator also exited zero and reproduced every original control. The
[completion manifest](confirmation-completion-1f5375a.json) records native exits
and hashes; the [complete comparison](product-packing-confirmation-1f5375a.json)
retains all paired results. There are 365 questions with labelled source evidence.

| Budget | Density recall | Score recall | Change | Paired 95% interval | Wins / losses |
|---|---:|---:|---:|---:|---:|
| 2,048 | 53.64% | 77.26% | +23.62 pp | +19.22 to +27.92 pp | 147 / 23 |
| 4,096 | 65.04% | 85.77% | +20.74 pp | +16.52 to +25.00 pp | 122 / 17 |
| 8,192 | 72.90% | 90.03% | +17.12 pp | +12.91 to +21.18 pp | 99 / 12 |

The positive primary mean, positive primary interval and secondary-budget mean
checks passed. The category guard failed: 4K preference recall fell from 78.26%
to 68.12% across 23 labelled questions (−10.14 pp, interval −33.33 to +13.04 pp;
four wins, six losses). Preference means also fell at 2K (−18.12 pp) and 8K
(−13.77 pp). The preregistered guard concerns the mean, so its failure cannot be
dismissed because the 4K category interval includes zero. Other 4K category means
improved; assistant-evidence recall rose from 14.89% to 91.49%.

**Decision:** preserve density as the default and score as an explicit option.
Do not change the comparator, acceptance criteria or excluded cases after this
result. A future policy must address the preference tradeoff and receive new
validation; this completed partition is now development evidence for any such
changes. Neither aggregate source recall nor the earlier development reader
study establishes answer accuracy on this partition or competitive leadership.
