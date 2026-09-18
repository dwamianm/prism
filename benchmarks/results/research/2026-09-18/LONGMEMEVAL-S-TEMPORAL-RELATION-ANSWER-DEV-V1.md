# LongMemEval-S temporal relation answer trial

**Decision:** advance the Jev-gated provenance-linked temporal relation policy
to a separately frozen confirmation. It passed every registered development
gate, improving the 29-question paired result from 17 correct answers to 21
with four wins and zero losses.

## Registered policy

The candidate changed only the nine questions whose deterministic relation
passed the development-calibrated Jev minimum operand probability of 0.85. For
each changed context it:

- required every cited source record at its control representation;
- used only records already present in the auditable control;
- prepended the generic temporal guidance and the inferred relation;
- kept the same token budget; and
- evicted exactly one uncited whole record when the relation needed space.

The remaining 20 arm pairs were byte-identical. The runtime deduplicated their
model calls, so both reader and judge made 38 unique calls for 58 logical arm
predictions.

DeepSeek v4.1 Flash answered both arms at temperature zero in counterbalanced
order. The calibrated GPT-OSS 120B judge scored all saved answers after reader
generation completed. The runner failed closed on provider or identity errors.

## Result

| Metric | Auditable control | Temporal relation |
|---|---:|---:|
| Correct | 17/29 | 21/29 |
| Accuracy | 58.62% | 72.41% |
| Paired wins / losses / ties | — | 4 / 0 / 25 |

Within the nine changed questions, the control scored 5/9 and the candidate
scored 9/9, again with four wins and zero losses. Both reader and judge
completed with zero failed attempts.

The four gains were exact date arithmetic that the control evidence contained
but the reader mishandled:

- whitewater rafting relative to the question date: abstention to 3 days;
- Holi to Sunday mass: 7 days to 21 days;
- gardening workshop to tomato planting: 12 days to 6 days; and
- herb harvest relative to the question date: abstention to 3 days.

Result identity:
`293cbb495e92ecc31aa161cba7ec215488a925f6860a5d5ba7b34124a07806c0`.

## Packing and artifact verification

All nine changed contexts retained their cited records and dropped exactly one
uncited record. No candidate introduced a record outside its control pool. The
candidate used 115,046 memory tokens across the cohort versus 115,332 for the
control because each whole-record eviction was larger than its relation hint.
The maximum candidate context remained below the registered budget.

The result self-hash, registration
`2d3997e4f400a6dabe0ce6f491b51c67fc0982af75e3fbeabbc92a89d410d1ca`,
and execution
`fef2dfa54647f3db3432202de5d7e887438adbd01b5059b52267f3ac69ba4e75`
all revalidated. Raw prepared contexts, reader answers, judgments, and retry
state remain under
`data/benchmarks/longmemeval-s-temporal-relation-answer-dev-v1/`.

## Claim boundary and next gate

This is encouraging development evidence, not confirmation. The questions,
resolver outputs, relation correctness labels, and Jev threshold had all been
observed before this trial. One generation per distinct context also does not
estimate hosted-reader variance.

Do not expose the policy or merge it into the main tree yet. Freeze a disjoint
confirmation cohort before making new resolver or gate calls. The confirmation
must rerun event alignment, deterministic validation, Jev gating at the fixed
0.85 threshold, bounded packing, paired answers, and artifact verification. It
must improve answer accuracy with more wins than losses, zero accepted-hint
losses, and no provider failures. A failure rejects the candidate without
changing PRME's current default.
