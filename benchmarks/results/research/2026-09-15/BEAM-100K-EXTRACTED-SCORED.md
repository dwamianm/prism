# BEAM 100K extracted-memory scored development run

PRME scored **12/20 (60.0%)** with a mean rubric score of **0.51875** on one
100K-token BEAM conversation using durable structured extraction. All 94 source
chunks produced 188 durable source events, all 188 extraction jobs completed in
one attempt, every query returned 50 memories, all 20 answers were nonempty, and
all 53 rubric verdicts contained a reason. The fail-closed validator accepted the
run with no errors.

This is tuned development evidence. Two earlier extraction failures from this
conversation directly informed product fixes before this run, so it is not an
untouched confirmation. The source revision, dataset, model manifests,
registration, execution files, and every question artifact are hashed in the
[machine-readable result](beam-100k-extracted-deepseekv41-mistral675b-gptoss120b-dev3-results.json).

## Results

| Ability | Pass | Mean rubric score |
| --- | ---: | ---: |
| Abstention | 0/2 | 0.00000 |
| Contradiction resolution | 2/2 | 0.68750 |
| Event ordering | 0/2 | 0.36665 |
| Information extraction | 0/2 | 0.08335 |
| Instruction following | 2/2 | 0.75000 |
| Knowledge update | 2/2 | 1.00000 |
| Multi-session reasoning | 2/2 | 0.75000 |
| Preference following | 2/2 | 0.75000 |
| Summarization | 1/2 | 0.30000 |
| Temporal reasoning | 1/2 | 0.50000 |
| **Overall** | **12/20** | **0.51875** |

Median PRME retrieval latency was 486.0 ms on the measured host, with a range
of 402.4–837.7 ms. The run searched 1,243 materialized nodes: 587 entities, 513
facts, 66 notes, 54 preferences, and 23 decisions. This is sequential warm
latency for one local pack. It excludes extraction, answer generation, and judge
generation and is not a general service latency claim.

## Paired raw comparison

The accepted raw-memory run used the same conversation, answerer, judge, query
selection, and top-50 cutoff. It scored 12/20 with a 0.49833 mean rubric score.
The extracted profile gained 0.02042 mean score, with three pass-level wins,
three losses, and fourteen ties.

| Ability | Raw pass | Extracted pass | Raw score | Extracted score |
| --- | ---: | ---: | ---: | ---: |
| Abstention | 0/2 | 0/2 | 0.00000 | 0.00000 |
| Contradiction resolution | 1/2 | 2/2 | 0.37500 | 0.68750 |
| Event ordering | 1/2 | 0/2 | 0.46665 | 0.36665 |
| Information extraction | 2/2 | 0/2 | 0.91665 | 0.08335 |
| Instruction following | 2/2 | 2/2 | 0.75000 | 0.75000 |
| Knowledge update | 1/2 | 2/2 | 0.50000 | 1.00000 |
| Multi-session reasoning | 2/2 | 2/2 | 0.87500 | 0.75000 |
| Preference following | 2/2 | 2/2 | 0.75000 | 0.75000 |
| Summarization | 0/2 | 1/2 | 0.10000 | 0.30000 |
| Temporal reasoning | 1/2 | 1/2 | 0.25000 | 0.50000 |
| **Overall** | **12/20** | **12/20** | **0.49833** | **0.51875** |

Median retrieval latency rose from 90.8 ms to 486.0 ms, about 5.35 times the
raw profile. These are development measurements on the same host, rather than a
controlled performance benchmark.

## What the run exposed

Structured extraction fixed the stale-update failure in the raw profile and
improved contradiction, temporal, and one summarization result. The latest
project total reached the answer context instead of the older assertion, which
is evidence that current-state scoring can work when extraction creates explicit
claims.

The offsetting losses identify a retrieval-composition problem. Both direct
information-extraction questions passed with raw notes and failed with the much
larger derived graph, while one event-ordering question also regressed. The
durable source remained in the event store, but derived entities and claims
competed for a fixed 50-result budget. Structured memory needs to preserve exact
source evidence alongside useful abstractions instead of forcing either profile
to carry the whole workload.

Both profiles failed both abstention questions. Related memories still encourage
plausible unsupported answers when the requested relationship is absent. Broad
coverage and answerability remain separate quality gaps.

The next development target is deterministic source-evidence retention during
hybrid candidate generation and packing, followed by latency work on the larger
candidate set. Any tuned change must beat both profiles on this development
conversation and then hold on untouched conversations before it can become a
default.

## Failed-trial handling

Two earlier extracted registrations were retained instead of erased:

1. The first stopped during ingestion when strict nested validation rejected an
   otherwise valid JSON quantity and one missing entity declaration discarded
   valid sibling claims. The product now normalizes exact decimal JSON forms and
   retains grounded, reference-closed siblings.
2. The second stopped when source code containing a Jinja expression was
   evaluated by Instructor's prompt-context mechanism before the model call. The
   product now transports extraction sources literally and keeps grounding state
   in task-local context.

Neither failed run reached question answering or publishes a score. Their
registrations and aggregate failure records remain in this directory.

## Comparison boundary

Hindsight reports 73.4% at BEAM 100K and 64.1% at 10M. Those published results
use a different system and model setup, and this PRME run covers only one tuned
100K conversation, so 60.0% versus 73.4% is not a matched comparison. PRME has
not established BEAM or market leadership. A credible comparison requires the
same complete selection, answerer, judge, prompt, cutoff, and reporting rules,
followed by 500K, 1M, and 10M scale runs.

Source: [Hindsight's published BEAM results](https://hindsight.vectorize.io/blog/2026/04/02/beam-sota).

## Reproduction boundary

The registration pins PRME commit `37f9f4d`, upstream commit `4b61c5d`, the
normalized 100K dataset hash, the complete ten-ability selection, top-50 cutoff,
extraction retry budget, and Ollama manifest digests for the extractor, answerer,
and judge. Ollama does not expose immutable revisions for the remote cloud
weights, so the run cannot claim byte-for-byte model reproducibility. Raw
questions, answers, memories, and judge text are not copied into this repository;
only aggregate metrics and artifact hashes are published.
