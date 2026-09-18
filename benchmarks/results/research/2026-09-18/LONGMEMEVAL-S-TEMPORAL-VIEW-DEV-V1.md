# LongMemEval-S temporal evidence view trial

**Decision:** reject the chronological temporal view for confirmation and
product integration. It retained the exact auditable record set and cut context
tokens by 45%, but reduced paired answer accuracy on the registered temporal
development cohort.

## Question

The preceding compact trials showed that a positional serialization can hurt
answers even when it retains every selected record. This trial tested a named,
human-readable alternative already available in PRME's context formatter. It
sorted the same records chronologically, labeled their source and epistemic
state, and showed each record's date and offset from the question date.

## Registered protocol

The cohort contains all 29 questions labeled `temporal-reasoning` in the frozen
119-question development split. The questions and prior outcomes were already
observed, so this is development evidence for choosing the next experiment.

For every question, the runner reopened the frozen PRME pack and reproduced the
saved auditable context byte for byte. The candidate rendered exactly those
selected records with `format_for_llm`; it could neither add nor remove a
record. DeepSeek v4.1 Flash generated one fresh answer per arm in
counterbalanced order. The calibrated GPT-OSS 120B judge scored all answers
after reader generation completed. Both stages failed closed on any model
error.

## Result

| Metric | Auditable control | Temporal view |
|---|---:|---:|
| Correct | 18/29 | 14/29 |
| Accuracy | 62.07% | 48.28% |
| Paired wins / losses / ties | — | 2 / 6 / 21 |
| Mean context tokens | 3,977 | 2,185 |
| Maximum context tokens | 3,996 | 2,592 |

Reader and judge executions completed all 58 calls each with zero failed
attempts. The candidate failed both required quality conditions: it did not
improve correct answers and wins did not exceed losses. The metrics classifier
reports 28 cases as temporal reasoning and one as abstention based on answer
semantics; all 29 source questions carry the frozen `temporal-reasoning` label.

Result identity:
`c2347d08b31f907d5234a8b08c0ba2bcb62a1406bf946da6809ca6f32e042c94`.

## Failure analysis

All eight changed outcomes had the same evidence records in both arms. The two
wins correctly converted a dated event into a duration. Four losses did the
opposite: the reader saw the correct endpoint dates but either abstained or
subtracted them incorrectly, including reporting 22 days for March 4 to March
18 and 14 days for March 15 to March 19. Two ordering losses selected the later
device or medical event despite dates that established the opposite order.

The offsets attached to each row describe its distance from the question date.
They do not directly encode the interval between the two events named by a
question. They therefore saved tokens and made chronology visible without
reliably performing the comparison the answer required. Chronological ordering
alone is insufficient for temporal arithmetic and can still draw attention to
irrelevant dates or alternative event interpretations.

The fresh auditable control agreed in correctness with the earlier registered
control on 27/29 questions, although only 4/29 answer strings were identical.
This run therefore shows normal wording variance but a comparatively stable
control outcome. The paired four-answer regression cannot be explained by
missing records or a broad control collapse.

## Consequence

Do not expose this formatter as the default retrieval context or run a
confirmation. Keep the auditable bundle as product behavior. The next temporal
experiment should compute a small, provenance-linked relation for the events
explicitly named in the query, such as their dates, ordering, and exact calendar
interval. It must preserve the underlying records and distinguish deterministic
date arithmetic from uncertain event resolution. This shifts the hard step
from presentation toward query-aware temporal reasoning while keeping the
answer auditable.

The registration binds commit `2476a7a`, the frozen answer trial, exact prepared
inputs, references, formatter implementation, judge calibration, and model
identities. Registration
`e7b68ae33e6782b3335813dc43fbc93bed4ab19f0f13469ee06b698da378be4a`
and execution
`d8ac51da2700eea1dba36d9d927249eb80d3ac362a5218eb8188496ccccacfbd`
revalidated after completion. Raw contexts, model responses, judgments, and
retry state remain under
`data/benchmarks/longmemeval-s-temporal-view-dev-v1/`.
