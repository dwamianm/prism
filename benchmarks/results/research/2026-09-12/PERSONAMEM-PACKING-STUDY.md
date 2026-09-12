# PersonaMem-v2: completed persona-hidden packing pilot

The experimental alpha .25 length penalty answered six more questions correctly
than density packing, but its persona-cluster confidence interval includes zero.
This pilot does not justify a default change or establish a reliable advantage
over the no-memory reader. The failed LongMemEval confirmation gate still stands.

| Arm | Correct / 96 | Accuracy | Difference from density, 95% cluster interval |
|---|---:|---:|---:|
| Density, current default | 36 | 37.50% | reference |
| Alpha .25 | 42 | 43.75% | +6.25 points [0.00, 13.54] |
| Score ordering | 41 | 42.71% | +5.21 points [−5.21, 15.63] |
| No memory | 33 | 34.38% | diagnostic control |

Alpha .25 versus no memory was +9.38 points [−3.13, 22.92]; versus score ordering
it was +1.04 points [−7.29, 9.38]. These are small-panel estimates, not superiority
claims. Resampling used all 24 personas with their four questions kept together,
2,000 draws and seed 42. All 384 primary answers were valid JSON. The 24 repeated
answers had no observed disagreements; that does not guarantee determinism on
other inputs or hardware.

The all-category report preserves important losses. For questions about other
people, density answered 4/10 correctly versus 1/10 for both alternatives. In the
health/medical category, density answered 5/11 versus 3/11 for both alternatives.
Updated-preference questions improved from 8/18 to 10/18 with alpha .25, while
score ordering achieved 7/18. Small categories and correlated observations limit
these comparisons; the aggregate gain must not hide them.

## Integrity and scope

The [registered plan](personamem-v2-packing-plan.json) froze `a331479`, dataset
revision, cohort, embedding assets and the local Gemma 4 26B reader before the
run. All 96 questions, 384 answers and 24 repeats completed with native exit 0.
Only then did the [independent verifier](personamem-v2-packing-completion-a331479.json)
join predictions to correct-letter labels. It reproduced all 288 memory contexts
and original product receipts, checked every reader request and raw response,
and read all 24 durable packs without mutation to verify source text, roles,
owner/scope and event provenance. The verifier itself also completed with native
exit 0. Raw reader output remains in
`data/benchmarks/personamem-packing-a331479.json`; the verification report records
its hash and every per-question score.

This is the [custom persona-hidden 32K text protocol](../../../integrations/PERSONAMEM_V2.md):
the supplied initial persona and hidden annotations are absent from memory.
There are no observed session IDs or event dates. The single local reader and
synthetic labels have not received an independent human label audit. These
results are not official PersonaMem-v2 leaderboard scores.

## Post-hoc evidence diagnosis

After completion, a separate [annotated-snippet audit](personamem-v2-snippet-audit.json)
matched each labelled snippet message to actual history by exact role and text.
Of 294 question/message references, 282 matched retained history; all 282 reached
the candidate set. The 12 unmatched references occur in the six sensitive-info
questions. They remain in every answer denominator and were not introduced into
memory by the audit.

| Packing | Matched snippet messages retained | Questions retaining the entire annotated snippet |
|---|---:|---:|
| Density | 69 | 1 |
| Alpha .25 | 102 | 9 |
| Score | 96 | 9 |

This shows substantial loss of annotated messages during packing, but annotations
are not an independently validated or exhaustive set of necessary/sufficient
evidence. Six of alpha .25's nine cases with the full annotated snippet were
still answered incorrectly. All three other-person cases lost by alpha .25 had
no annotated message in the density context; their density successes therefore
do not establish that alpha .25 removed the required attribution evidence.

The next diagnostic should give the unchanged reader only the annotated messages
that actually exist in history, in their original order, with no persona or
correct-answer leakage. In this fixed cohort those contexts require at most
1,102 `cl100k_base` tokens. This intentionally annotation-selected control can
separate evidence selection from reader/label limitations; it is not a deployable
retrieval method or a new confirmation test. Keep every case, including empty
contexts, and retain a fresh no-memory control.
