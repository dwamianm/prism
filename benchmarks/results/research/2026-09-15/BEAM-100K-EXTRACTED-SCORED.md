# BEAM 100K extracted-memory scored development run

PRME scored **13/20 (65.0%)** with a mean rubric score of **0.56750** on one
100K-token BEAM conversation using a completely admitted extracted-memory pack.
Schema-5 fail-closed validation accepted the run with no errors after inspecting
the final DuckDB state: all 188 source events had completed raw materialization
and completed structured extraction.

All 94 chunks completed, every query returned 50 memories, all 20 answers were
nonempty, and all 53 rubric verdicts contained a reason. The source revision,
dataset, model manifests, registration, durable database, execution files, and
every question artifact are hashed in the
[machine-readable result](beam-100k-extracted-deepseekv41-mistral675b-gptoss120b-dev5-results.json).

This is tuned development evidence. Four earlier extracted attempts from this
conversation informed product, adapter, validator, and timeout changes before
this run, so it is not an untouched confirmation.

## Results

| Ability | Pass | Mean rubric score |
| --- | ---: | ---: |
| Abstention | 0/2 | 0.00000 |
| Contradiction resolution | 1/2 | 0.37500 |
| Event ordering | 0/2 | 0.31665 |
| Information extraction | 1/2 | 0.45835 |
| Instruction following | 2/2 | 0.75000 |
| Knowledge update | 2/2 | 1.00000 |
| Multi-session reasoning | 2/2 | 0.87500 |
| Preference following | 2/2 | 1.00000 |
| Summarization | 2/2 | 0.65000 |
| Temporal reasoning | 1/2 | 0.25000 |
| **Overall** | **13/20** | **0.56750** |

Median PRME retrieval latency was 198.05 ms on the measured host, with a range
of 158.5–530.2 ms. The pack contained 1,344 nodes: 564 entities, 511 facts, 188
raw notes, 57 preferences, and 24 decisions. This is sequential warm latency for
one local pack. It excludes extraction, answer generation, and judge generation
and is not a general service latency claim.

## Paired raw comparison

The accepted raw-memory run used the same conversation, answerer, judge, query
selection, and top-50 cutoff. It scored 12/20 with a 0.49833 mean rubric score.
The corrected extracted profile scored one additional question and gained
0.06917 mean score, with three pass-level wins, two losses, and fifteen ties.

| Ability | Raw pass | Extracted pass | Raw score | Extracted score |
| --- | ---: | ---: | ---: | ---: |
| Abstention | 0/2 | 0/2 | 0.00000 | 0.00000 |
| Contradiction resolution | 1/2 | 1/2 | 0.37500 | 0.37500 |
| Event ordering | 1/2 | 0/2 | 0.46665 | 0.31665 |
| Information extraction | 2/2 | 1/2 | 0.91665 | 0.45835 |
| Instruction following | 2/2 | 2/2 | 0.75000 | 0.75000 |
| Knowledge update | 1/2 | 2/2 | 0.50000 | 1.00000 |
| Multi-session reasoning | 2/2 | 2/2 | 0.87500 | 0.87500 |
| Preference following | 2/2 | 2/2 | 0.75000 | 1.00000 |
| Summarization | 0/2 | 2/2 | 0.10000 | 0.65000 |
| Temporal reasoning | 1/2 | 1/2 | 0.25000 | 0.25000 |
| **Overall** | **12/20** | **13/20** | **0.49833** | **0.56750** |

Median retrieval latency rose from 90.8 ms for raw memory to 198.05 ms for
extracted memory. These are separate development executions on the same host,
rather than a controlled performance benchmark.

## Source diversity correction

The superseded dev3 run allowed repeated structured siblings from one source to
occupy many of the 50 result slots. Its result sets contained 7–35 unique passage
texts, with a median of 15. Dev5 caps byte-identical siblings with the same exact
evidence set to one result per source. Its result sets contained 45–49 unique
passage texts, with a median of 48.

Against superseded dev3, dev5 gained two pass-level questions, lost one, and
tied seventeen. Mean score rose by 0.04875, and median retrieval latency fell
from 486.0 ms to 198.05 ms. Dev3 was not a fixed-pack execution, so this is a
diagnostic comparison rather than accepted paired evidence.

## Remaining measured gaps

Both abstention questions still failed. Related memories continue to encourage
plausible unsupported answers when the requested relationship is absent.

Both event-ordering questions failed, and one direct information-extraction
question regressed against raw memory. The next retrieval work should improve
chronological evidence selection and preserve direct source details without
giving repeated derived claims most of the context budget.

Hosted extraction reliability also remains operationally important. Dev5 needed
one retry and 189 total attempts for 188 events. The 14,181-character source that
invalidated dev4 completed in about 168 seconds under dev5's registered
180-second provider bound.

## Trial history

The evidence record retains every material development outcome:

1. Dev1 stopped during ingestion when strict nested validation rejected one
   response and a missing entity declaration discarded valid sibling claims.
2. Dev2 stopped when Instructor evaluated source code containing a Jinja
   expression before the model call.
3. Dev3 scored 12/20 but was later superseded after an audit found 122 pending
   raw-source materializations and question-order-dependent graph growth.
4. Dev4 stopped during ingestion after a long source exhausted four registered
   120-second extractor calls.
5. Dev5 completed and passed schema-5 validation with a 180-second bound.

The failed and superseded registrations and aggregate result records remain in
this directory. None of the incomplete attempts publishes an answer-quality
score.

## Comparison boundary

Hindsight reports 73.4% at BEAM 100K and 64.1% at 10M. Those published results
use a different system and model setup, and this PRME run covers only one tuned
100K conversation. PRME has not established BEAM or market leadership. A
credible comparison requires the same complete selection, answerer, judge,
prompt, cutoff, and reporting rules, followed by untouched conversations and
500K, 1M, and 10M scale runs.

Source: [Hindsight's published BEAM results](https://hindsight.vectorize.io/blog/2026/04/02/beam-sota).

## Reproduction boundary

The registration pins PRME commit `08f27a8`, upstream commit `4b61c5d`, the
normalized 100K dataset hash, all ten abilities, top-50 cutoff, source-diversity
policy, dual-admission requirement, extraction timeout and retry settings, and
Ollama manifest digests for the extractor, answerer, and judge. Ollama does not
expose immutable revisions for the remote cloud weights, so the run cannot claim
byte-for-byte model reproducibility. Raw questions, answers, memories, and judge
text are not copied into this repository; aggregate metrics and artifact hashes
are published.
