# LongMemEval-S grouped conversation answer development v1

**Decision:** Reject grouped conversation packing in its tested form. Do not
expose the private hook or change the product default.

**Date:** 2026-09-18

**Registration SHA-256:**
`ca1c37cab914af89b222454ee4f5792d08792e133dc44db61f6f79507771fafa`

## Question

Does named conversation grouping help a fixed reader understand the same
evidence, and does spending the saved space on additional records improve
answers?

## Protocol

The trial used the stable 119-question PRME LongMemEval-S development split.
It froze three exact 4K contexts per question before reader generation:

- `control`: current balanced auditable packing;
- `grouped_same_set`: exact control node IDs and representations rendered as
  named conversations;
- `grouped_fill`: the same grouped rendering, with saved space spent on later
  candidates from the unchanged balanced order.

The same-set arm isolates presentation from evidence selection. The fill versus
same-set comparison isolates the added evidence. Reader arm order used a
deterministic question-hash rotation and direction. References were absent from
the prepared reader inputs and became available only after all 357 logical
reader prompts completed. The registered DeepSeek 4.1 Flash reader and
calibrated GPT-OSS 120B judge each used one generation per distinct prompt and
forbade selective retries.

## Result

The run completed with zero reader or judge failures. It produced 356 unique
reader generations for 357 logical prompts and 298 unique judge calls for 357
logical verdicts. All three registered comparisons failed.

| Arm | Correct | Accuracy | Versus control wins / losses |
| --- | ---: | ---: | ---: |
| Control | 71/119 | 59.66% | — |
| Grouped same set | 68/119 | 57.14% | 6 / 9 |
| Grouped fill | 64/119 | 53.78% | 5 / 12 |

Grouped fill also lost directly to grouped same set, 3 paired wins to 7 losses.

| Comparison | Accuracy delta | Category regressions | Gate |
| --- | ---: | ---: | --- |
| Same set versus control | -2.52 points | 2 | Fail |
| Fill versus control | -5.88 points | 2 | Fail |
| Fill versus same set | -3.36 points | 3 | Fail |

Same-set grouping improved abstention by one and single-session-user by two,
but lost two knowledge-update and four multi-session answers. Fill versus
control lost three knowledge-update and six multi-session answers, while gaining
one abstention, one single-session-user and two net temporal answers. The three
development questions that had source-recall gains in the preceding 500-case
trial remained incorrect in all three answer arms.

## Failure localization

Same-set grouping changed no evidence identity, representation or guidance, so
its 15 answer transitions come from presentation. Exact-context inspection found
that coalescing all records for a session at the session's first selected record
moved later members away from the control's relevance order. Turns inside each
group were then sorted by turn index. For example, a charity-total context put
the `$500`, `$1,000`, `$2,000`, then `$250` evidence in control relevance order;
grouping moved the `$250` session first. Several losses answered that the
available records were incomplete or conflicting even though the control reader
correctly summed distinct amounts or used the newest dated value.

This establishes that the current renderer is not a lossless semantic
compression for this reader. The ordering change is a concrete mechanism, but
the trial does not prove it explains every loss. Explicit session structure may
also make the reader more cautious about cross-session aggregation. Adding more
records amplified that behavior and introduced two internally inconsistent date
calculations, so the source-recall gain cannot rescue this composition.

## Verification and artifacts

An independent pass verified the result self-hash, registration hash, execution
file hash, complete reader and judge coverage, exact execution snapshots, native
exit zero and zero failed attempts. The result identity is
`b33f1a734e85eb4e67bdf8e91a4af1c69f3f394aceaca132ee5c8992aa144b37`.
The summary artifact is
[longmemeval-s-grouped-conversations-answer-dev-v1-results.json](longmemeval-s-grouped-conversations-answer-dev-v1-results.json).
Complete prepared inputs, references, responses, verdicts and resumable states
remain under
`data/benchmarks/longmemeval-s-grouped-conversations-answer-dev-v1/`.

## Consequence

Remove the rejected private grouped renderer after preserving this result. A
follow-up may test named metadata factoring only if it preserves the control
record order byte-for-byte at the identity level and does not expose raw session
IDs as a reasoning signal. It must first show noninferior same-evidence answers;
source retention alone is no longer a sufficient reason to run a fill arm.

This is development evidence from an already inspected benchmark, one hosted
reader alias and a custom calibrated judge. It is not an official LongMemEval
score, independent confirmation, competitor comparison or universal rejection
of conversation-aware memory.
