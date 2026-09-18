# LongMemEval-S monotonic compact answer trial

**Decision:** reject monotonic compact packing for confirmation and product
integration. It preserved every control record and improved labeled-source
coverage, but reduced answer accuracy on the registered development trial.

## Protocol and recovery

The trial used 119 frozen LongMemEval-S development questions. DeepSeek v4.1
Flash read counterbalanced auditable-control and monotonic-compact contexts at
temperature zero. A separately calibrated GPT-OSS 120B judge scored the saved
answers without seeing an arm label. The prepared reader inputs contained no
reference answers.

The original judge run stopped fail-closed after one response exhausted its
512-token allowance in hidden reasoning and returned no verdict. All 238 reader
answers had already completed with zero failures. A new registration bound the
exact reader snapshot, raw reader state, original registration, prepared inputs,
references, source-retention result, judge declaration and passing 40/42 judge
calibration. It increased only the judge output allowance to 2,048 tokens and
made no new reader calls. The continuation completed all 238 logical judgments
with zero failed calls.

## Registered result

| Metric | Auditable control | Monotonic compact |
|---|---:|---:|
| Correct answers | 76/119 | 72/119 |
| Accuracy | 63.87% | 60.50% |
| Paired wins / losses / ties | — | 8 / 12 / 99 |

The candidate failed non-inferiority, wins-at-least-losses, and the zero
category-regression gate. Three categories regressed:

| Category | Control | Candidate | Wins / losses |
|---|---:|---:|---:|
| Multi-session | 15/30 | 13/30 | 2 / 4 |
| Single-session user | 16/17 | 15/17 | 0 / 1 |
| Temporal reasoning | 17/28 | 13/28 | 3 / 7 |

Abstention improved 6/8 to 7/8, single-session assistant improved 8/9 to
9/9, preference improved 0/7 to 1/7, and knowledge update tied at 14/20.
These gains do not offset the registered losses.

Result identity:
`ed6ff69b10ee2e6543ac34de4bb7d9589c112a219c40b510eaf4cf29ba60eb59`.

## What the failure localizes

The candidate added between 4 and 15 records per question on this cohort, with
a median addition of 9. It never removed a control record. Only two development
questions gained labeled-turn coverage. One became an answer win; the other
remained incorrect in both arms. All twelve answer losses occurred while the
same complete labeled evidence remained present. Eight candidate losses changed
a control answer into an explicit inability to answer; other losses miscounted,
misordered events, or introduced qualification that the judge correctly treated
as failing the reference.

This rules out missing required source evidence as the cause of the losses. It
does not isolate compact serialization from the additional records because the
candidate changed both. The result shows that source-superset packing alone is
not answer-monotonic: extra, topically related memories can increase uncertainty
or distract temporal and aggregation reasoning even when required evidence is
retained.

## Artifact verification

The final result binds registration
`598a7a76b4f53377c871097849ef2d16510dea2efdc7fb6d903ab0a9398dc67b` and
execution
`af73fb74bdeebbc2db9f099812df3c2b916ea4dcc0f95e44dafa350f4b913896`.
The saved reader artifact is byte-identical to the original completed snapshot.
The registration, prepared inputs, references, reader state, calibration and
source-retention result hashes all revalidated after completion. Raw contexts,
answers, judge outputs and retry state remain outside Git under
`data/benchmarks/longmemeval-s-monotonic-answer-dev-v2/`.

## Next experiment

Do not run the 381-question confirmation and do not expose this policy through
configuration. A small post-hoc diagnostic may compare the auditable control
with a compact serialization of exactly the same selected records and
representations. That can separate serialization effects from extra-record
effects, but it cannot promote a policy because the failed questions are now
observed. Any later candidate must be preregistered on a different task cohort
and must gate answer quality directly, especially temporal reasoning and
complete-set questions.
