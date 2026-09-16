# BEAM 100K raw-memory scored development run

PRME scored **12/20 (60.0%)** with a mean rubric score of **0.49833** on one
100K-token BEAM conversation. All 94 source chunks were ingested, every query
returned 50 memories, all 20 answers were nonempty, and all 53 rubric verdicts
contained a reason. The fail-closed validator accepted the run with no errors.

This is useful development evidence, not a leadership result. It covers one
conversation and 20 questions, uses raw source notes, and relies on mutable
Ollama cloud aliases for Mistral Large 3 and GPT-OSS 120B. The source revision,
dataset, local model manifests, registration, execution files, and every
question artifact are hashed in the
[machine-readable result](beam-100k-raw-mistral675b-gptoss120b-dev4-results.json).

## Results

| Ability | Pass | Mean rubric score |
| --- | ---: | ---: |
| Abstention | 0/2 | 0.00000 |
| Contradiction resolution | 1/2 | 0.37500 |
| Event ordering | 1/2 | 0.46665 |
| Information extraction | 2/2 | 0.91665 |
| Instruction following | 2/2 | 0.75000 |
| Knowledge update | 1/2 | 0.50000 |
| Multi-session reasoning | 2/2 | 0.87500 |
| Preference following | 2/2 | 0.75000 |
| Summarization | 0/2 | 0.10000 |
| Temporal reasoning | 1/2 | 0.25000 |
| **Overall** | **12/20** | **0.49833** |

Median PRME retrieval latency was 90.8 ms on the measured host, with a range
of 71.5–174.5 ms. This is sequential warm latency for one local pack. It does
not include answer or judge generation and is not a general service latency
claim.

## What the run exposed

The strong information-extraction and multi-session results show that raw hybrid
retrieval can surface exact facts and combine evidence across sessions. The
failures are more informative for the next iteration:

- Both abstention questions returned plausible but unsupported narratives from
  semantically related memories. Retrieval and answer generation lack a reliable
  signal that the requested relationship or background is absent.
- The failed summaries were dominated by a narrow subset of the project history;
  one answer discussed Gunicorn and tests instead of the requested end-to-end
  project progression. Top-50 relevance alone does not provide sufficient
  coverage or diversity for broad synthesis.
- One update question returned an older 150-commit assertion instead of the
  later 165-commit assertion. The newer evidence did not survive into the
  answer context despite the current-update scoring path.
- Ordering, contradiction, and temporal failures omitted required episodes or
  dates. They need query decomposition and evidence coverage across distinct
  time ranges, rather than more copies of the most similar episode.

These observations make coverage-aware, diversified retrieval and explicit
answerability the next quality targets. Any tuning from this conversation must
be treated as development work and confirmed on untouched conversations before
changing defaults.

## Failed-trial handling

Three earlier registrations were retained instead of erased:

1. The first trial paired an 8K-context answerer with a roughly 136K-character
   prompt and a local judge that exhausted five timeouts.
2. The second trial's local judge did not finish its first exact-template verdict
   within five minutes.
3. The third trial completed and reported 10/20, but validation rejected six
   empty answers that the upstream harness counted as ordinary failures.

Their registrations and aggregate failure records are in this directory. No
score from those trials is used as quality evidence.

## Comparison boundary

Hindsight reports 73.4% at BEAM 100K and 64.1% at 10M. Those published results
use a different system and model setup, and this PRME run covers only one 100K
conversation, so 60.0% versus 73.4% is not a matched comparison. It does show
that PRME has not established BEAM leadership. A credible comparison requires
the same complete selection, answerer, judge, prompt, cutoff, and reporting
rules, followed by 500K, 1M, and 10M scale runs.

Source: [Hindsight's published BEAM results](https://hindsight.vectorize.io/blog/2026/04/02/beam-sota).

## Reproduction boundary

The registration pins PRME commit `b95931a`, upstream commit `4b61c5d`, the
normalized 100K dataset hash, the complete ten-ability selection, top-50 cutoff,
and local Ollama manifest digests. Ollama does not expose immutable revisions
for the remote cloud weights, so the run cannot claim byte-for-byte model
reproducibility. Raw questions, answers, memories, and judge text are not copied
into this repository; only aggregate metrics and artifact hashes are published.
