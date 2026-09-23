# Measuring retrieval quality

## Latest complete GPT-5.4 defaults (2026-09-23)

| Benchmark | Complete result | Effective context ceiling |
|---|---:|---:|
| LongMemEval-S | **430/500 (86.0%)** | 3,996 tokens |
| LoCoMo, categories 1–4 | **985/1,540 (64.0%)** | 3,996 tokens |

Both use `gpt-5.4-2026-03-05`, medium reasoning for reader and judge, and default
retrieval over stored conversation turns. All 2,040 questions completed; all
4,080 benchmark model calls succeeded on their first attempt. The full
LongMemEval cohort includes preference and abstention questions. LoCoMo excludes
all 446 adversarial questions prospectively and uses a disclosed semantic yes/no
judge, not upstream token-F1. These scoped registrations differ from the older
adapter defaults documented below.

See the [registered protocol](benchmarks/results/research/2026-09-23/GPT54-COMPARISON-PROTOCOL.md),
[complete results and verification](benchmarks/results/research/2026-09-23/GPT54-DEFAULT-BENCHMARK-COMPARISON.md),
and [evidence-retention diagnostics](benchmarks/results/research/2026-09-23/GPT54-EVIDENCE-DIAGNOSTICS.md).
Zep's published figures are external references with different methods, not a
paired live arm. The earlier DeepSeek 87.4% LongMemEval result remains separate.
All source/answer records are retained locally under `data/gpt54-comparison-v1/`;
public reports contain their checksums. The completed run must not be rerun or
overwritten as part of verification.

## Earlier registered memory-utility comparison

The first registered held-out current-product answer comparison is complete.
On 149 deterministically scored LongMemEval-V2 web-small questions, PRME scored
80/149 (53.69%) versus 10/149 (6.71%) for the same local Qwen 9B reader without
memory. The paired difference was +46.98 points with a question-bootstrap 95%
interval of +37.58 to +55.70 points. The fail-closed comparator accepted every
row, official score, source/configuration binding, saved pack identity, and the
zero-memory contract. See the [complete report](benchmarks/results/research/2026-09-14/LONGMEMEVAL-V2-WEB-UNSEEN-DETERMINISTIC-V1.md).

That run used one reader, one saved memory artifact, no competing memory system,
and a mean 43,195 PRME memory-context tokens. It establishes memory utility for
the named cohort, not market leadership. Historical JSON files without a
complete report and artifact chain remain research artifacts and are not
directly comparable with the current harness. See the [August audit](memory_bank/AUDIT-2026-08-04.md)
for the older README table correction.

The September cleanup removes LoCoMo `observation` ingestion, copied answer
examples, and harness-only query reformulation/entity fan-out. Both LLM adapters
now use one public `retrieve()` call per question. Optional expansion belongs
to the product configuration. These are measurement corrections, not evidence
of improved answer accuracy.

## Contract for the next baseline

Complete full-history development runs from September 12, including the initial
regression and its intent correction, are preserved with per-question evidence
and paired comparisons in the [development report](benchmarks/results/evidence/2026-09-12/README.md).
These measure support retrieval, not end-to-end answer quality. The completed
[held-out report](benchmarks/results/evidence/2026-09-12/heldout/README.md) covers
381 questions with zero errors. Its paired comparisons do not establish an
advantage over vector/RRF baselines. The original-version run also completed:
2,048-token support recall changed from 85.12% to 85.27%, with a paired interval
including zero and a changed evaluation clock. A held-out improvement is not
established; cross-product answer-quality evaluations remain open.

| Layer | Report |
|---|---|
| Candidate retrieval | Evidence recall@k, MRR, nDCG@k |
| Context packing | Supporting-evidence recall at fixed token budgets; unsupported or conflicting context |
| End-to-end answering | Accuracy by category, abstention, repeated-run spread, infrastructure errors |

Record the source commit, dataset hash/split, selected question IDs, complete
engine configuration, embedding version, ingestion mode, generation and judge
models, prompts, token budget, scoring threshold, dependency versions, and elapsed
time. Tune on a development set; reserve held-out questions for final evaluation.

Report sample coverage explicitly. Adapters currently default to subsets;
LongMemEval excludes single-session-preference and LoCoMo excludes category 5.
Keyword containment is a diagnostic, not official benchmark accuracy. LLM
generation and judging can vary at temperature zero. Cached verdicts reduce
judge variation; they do not make generation deterministic.

JSON and terminal reports distinguish `total_queries`, `scored_queries`,
`error_count`, and `coverage` for the selected questions. Query failures retain
their question text and a null score, so retry selection includes them. The
legacy `judge_error` field now covers ingestion/retrieval exceptions as well as
generation/judging failures. Accuracy and category scores exclude these errors;
aggregate scores weight only measured questions. Always report coverage with
accuracy.

A whole-benchmark failure sets `benchmark_error` and `complete=false`; its
question count may be unknown. `complete` means that the selected evaluation
finished without errors, not that the full published dataset was evaluated.
The CLI exits nonzero if any benchmark in any run is incomplete, even when
other questions or later runs score well. It also retains the existing failure
exit for a nonempty benchmark with a zero score.

## Available commands

For host-specific retrieval latency, exact repeatability, and tenant isolation
through the public product path, run:

```bash
uv run python -m benchmarks.operational_eval \
  --sizes 10 50 200 \
  --latency-samples 100 \
  --determinism-samples 100 \
  --isolation-samples 10000 \
  --owners 5 \
  --output /tmp/prme-operational.json
```

The runner fixes the retrieval clock, uses exact vector search, disables
maintenance, records implementation/runtime/feature provenance, writes an
incomplete artifact before measurement, and exits nonzero on any repeatability
or owner-isolation failure. Sizes are eligible objects per owner; the default
five-owner corpus therefore holds 50, 250, and 1,000 total objects. Its latency
is specific to the measured host and its synthetic corpus; it is neither a
relevance score nor an answer-quality result.

For chronological conflict-memory replay with a local model, see the
[MemConflict adapter](benchmarks/integrations/MEMCONFLICT.md). It separates source
dialogues from evaluation labels, reports malformed-message omissions, and
compares PRME context, BM25 context and empty memory. Its raw answers are unjudged
diagnostics; it does not report official accuracy.

For 100K-to-10M conversation histories across ten memory abilities, see the
[BEAM integration](benchmarks/integrations/BEAM.md). It runs behind the pinned
official harness without exposing rubrics or answers to PRME and provides raw
source and full product-extraction profiles. The first validated scored raw
development run completed one 100K conversation and all ten abilities at 12/20
(60.0%), with a 0.49833 mean rubric score and no structural validation errors.
The [report](benchmarks/results/research/2026-09-15/BEAM-100K-RAW-SCORED.md)
records the complete claim boundary and the three rejected precursor trials.
This small cloud-model cohort identifies abstention, broad summarization,
temporal coverage, and contradiction coverage as gaps; it is not an official-scale
or matched cross-system result.

For controlled evidence retrieval without a generation or judge API, use:

```bash
uv sync --dev --extra evaluation
uv run python -m benchmarks.retrieval_eval \
  --dataset data/benchmarks/longmemeval/longmemeval_s_cleaned.json \
  --variant s --split dev --limit 5 \
  --budgets 2048 4096 8192 --output /tmp/prme-evidence-dev.json
```

Download the full-history S file from the
[official cleaned dataset](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/tree/main).
The existing download script fetches **oracle** histories, which contain only
supporting sessions. Explicitly label those runs `--variant oracle`; they are
diagnostics and do not test retrieval through full-history distractors.

This evaluator compares public PRME retrieval with BM25, vector search,
reciprocal rank fusion (constant 60), recency, and empty memory on identical
raw conversation turns. It records source commit and worktree fingerprint,
dataset hash, selected IDs, configuration, dependency versions, errors and
category coverage. Answers, evidence flags, and answer-bearing session IDs
never enter the memory pack. The raw-turn profile uses `store()` with NOTE
nodes and disables QA pairing and opportunistic maintenance. It does not
measure LLM extraction.

The deterministic `dev`/`test` partition is PRME's own 20/80 split by hashed
question ID, keeping abstention variants together. It is not an official split.
Keep the seed fixed, tune only on `dev`, and remove `--limit` for complete split
coverage. Run from a clean source commit with no concurrent source edits for
publication. Five questions are a smoke test, not a quality claim.

Metrics include evidence recall@k, MRR, nDCG, and evidence retained at actual
token budgets using a shared whole-turn packer and a named tiktoken encoding.
The packer includes source headers and separators in the count and never
truncates a turn. This isolates ranking; it does **not** evaluate the product
context formatter. Unlabeled/abstention questions have null evidence metrics;
answering and abstention accuracy need a separate judged evaluation. Timings
use sequential warm shared indexes and must not be presented as independent
cold-start performance. Interrupted runs retain a partial JSON report.

The evaluator passes the dataset's question date as `reference_time` by default,
so relative dates use the conversation's clock. `--clock wall` reproduces the
older behavior for a controlled ablation. Each response records the actual
clock; neither option applies a historical knowledge cutoff.

Use `--concurrency 4` to evaluate independent questions concurrently, each in
its own memory pack. Reports retain frozen question order and per-question
errors. Concurrency is recorded; timing under concurrent workloads is not a
standalone latency measurement and must not be compared as such.

Compare two completed runs on exactly the same selected questions:

```bash
uv run python -m benchmarks.compare_evidence /tmp/before.json /tmp/after.json \
  --output /tmp/comparison.json
```

The comparison rejects errors, missing/duplicate questions, different source
corpora or labels, and mismatched datasets/budgets. It reports paired changes,
wins/ties/losses, and a seeded query-bootstrap interval overall and by category.
Shared histories can make queries dependent, so these intervals are descriptive
and do not establish population-wide superiority. Development results still
need a frozen held-out confirmation. Timing is deliberately not compared.

To measure the real structured-extraction path, run the same source-only
selection once with raw notes and once with synchronous `ingest()`. The worker
writes the selected IDs, source/configuration provenance, and immutable Ollama
model digest before its first extraction call. Every extraction job must reach
its durable completion boundary; the report also records source materialization
coverage and node types. Use the oracle dataset for a bounded extraction study
and label it accordingly: it does not test retrieval through long-history
distractors.

```bash
uv run python -m benchmarks.retrieval_eval \
  --dataset data/benchmarks/longmemeval/longmemeval_oracle.json \
  --variant oracle --split dev --limit 12 --seed prme-ingestion-v1 \
  --ingestion-profile raw --output /tmp/prme-ingestion-raw.json
uv run python -m benchmarks.retrieval_eval \
  --dataset data/benchmarks/longmemeval/longmemeval_oracle.json \
  --variant oracle --split dev --limit 12 --seed prme-ingestion-v1 \
  --ingestion-profile extracted --extraction-provider ollama \
  --extraction-model prme-qwen3.5:9b-8k \
  --extraction-base-url http://127.0.0.1:11434/v1 \
  --output /tmp/prme-ingestion-extracted.json
uv run python -m benchmarks.compare_evidence \
  /tmp/prme-ingestion-raw.json /tmp/prme-ingestion-extracted.json \
  --allow-profile-change --output /tmp/prme-ingestion-comparison.json
```

This comparison expands retrieved node provenance back to neutral source-turn
IDs and then applies the shared source packer. It measures whether extraction
preserves retrievable source lineage. It does not prove that an extracted node's
rendered text contains the answer; that requires the separate generated-answer
and judge layer.

```bash
uv sync --dev
uv run pytest tests/ -q
uv run python -m benchmarks all --json /tmp/prme-synthetic.json
uv run python scripts/download_benchmarks.py --all
uv run python -m benchmarks all-real --json /tmp/prme-real-keyword.json
```

FastEmbed may download a model on first use. LLM diagnostics require explicitly
selected models and credentials for their providers:

```bash
uv run python -m benchmarks all-real --llm \
  --llm-provider "$ANSWER_PROVIDER" --llm-model "$ANSWER_MODEL" \
  --judge-provider "$JUDGE_PROVIDER" --judge-model "$JUDGE_MODEL" \
  --judge-cache /tmp/prme-verdicts.json --runs 3 \
  --json /tmp/prme-real-llm.json
```

This diagnostic command does not yet automate the full publication contract.
Use a fresh verdict cache after changing judge prompts. Never present a merge
of retry-only results and a baseline as a single run.

## Remaining publication work

Tracked in [#64](https://github.com/dwamianm/prism/issues/64):

- Extend the evidence evaluator's provenance contract to generated-answer runs.
- Run and publish a registered `ingest()` versus raw-store source-lineage study,
  then add generated-answer scoring over the extracted product contexts.
- Standardize context budgets and preparation across adapters. LoCoMo still uses
  supplied image captions, omits short turns, and builds knowledge profiles only
  in its keyword path. Document or ablate these before claiming a raw-conversation baseline.
- Require an explicit judge for publication and report category-level coverage.
- Freeze the evaluation split and run repeated evaluations with recorded configuration.

Scope isolation, temporal eligibility, provenance, and deterministic exact
retrieval remain correctness gates regardless of answer-score improvements.


## Precision and causal simulation diagnostics

The [PrecisionMemBench report](benchmarks/results/precision/2026-09-12/README.md)
preserves failures and discloses fixture-supplied behavior. Candidate recall alone
misses irrelevant-memory pollution. Score-floor sweeps on this small synthetic
suite are development calibration, not held-out validation or vendor comparisons.

Run all causal simulation checks, retaining failures, with:

```sh
uv run --no-sync python -m scripts.run_simulations --output /tmp/prme-simulations.json
```

Use repeatable `--scenario NAME` to narrow diagnosis. Every selected checkpoint
must pass for exit status zero; incomplete/empty runs and scenario errors fail.
The previous 80% success threshold has been removed. These keyword/ranking tests
are regression diagnostics and do not establish semantic answer accuracy.


[Local reader diagnostics](benchmarks/results/reader/2026-09-12/README.md)
preserve a failed header clarification and a controlled lifecycle-key ablation.
They explain a source-wording fix; they are not judged accuracy measurements.

[Recovery and developer workflow checks](benchmarks/results/recovery/2026-09-12/README.md)
cover abrupt exits, saved extraction reuse, installed-package behavior, and vector
startup cost. They preserve an incomplete local-model trial and distinguish
component timings from total startup latency. These reliability checks do not
establish semantic answer accuracy or competitive leadership.


[Extraction classification probes](benchmarks/results/extraction/2026-09-12/README.md)
exercise twelve authored source statements through live local-model ingestion.
They distinguish claim kind from epistemic status and check every materialized
claim. A prompt/schema clarification failed to establish a quality gain and was
reverted. The raw trials and corrected namesake evaluator are retained; these
are development diagnostics, not held-out answer accuracy or competitive scores.


[Evidence formatting fidelity](benchmarks/results/fidelity/2026-09-12/README.md)
records provenance preservation, removal of unsupported reasoning directives,
and identity-based context deduplication. Paired local-reader counterexamples
are authored development diagnostics, not independent accuracy measurements.

## Product packing replay

Capture the actual public retrieval response while running a development
source-evidence evaluation, then replay those candidates offline:

```sh
python -m benchmarks.retrieval_eval \
  --dataset data/benchmarks/longmemeval/longmemeval_s_cleaned.json \
  --variant s --split dev --clock question --concurrency 4 \
  --capture-candidates /tmp/prme-dev-candidates --output /tmp/prme-dev.json
python -m benchmarks.diagnostics.product_packing \
  --input /tmp/prme-dev.json --snapshots /tmp/prme-dev-candidates \
  --output /tmp/prme-product-packing.json
```

Use a fresh snapshot directory for each run. Snapshots contain benchmark source
text and full candidate metadata; keep them separate from published summaries.
Their hashes are retained in the report. The benchmark supervisor records the
worker's actual process exit and rejects stale output as completion evidence.

The offline comparator first reproduces every saved product context and token
count exactly. It then compares the current density ordering with composite-score
ordering **within the multi-path tier only**, using all the same candidates,
priorities, representations, provenance labels, timestamps and budgets. It uses
the product's configured tokenizer and reserved overhead, which can differ from
the shared whole-turn evaluator's protocol. Full source text must be present in
a content-bearing JSON representation to receive support credit; reference-only
and key-value pointers receive none. Unlabeled questions remain unscored.

This exploratory command accepts only the development split. It reports paired
support-retention changes and complete-evidence retention, not generated-answer
accuracy. It does not change production packing defaults. Run it separately from
retrieval: its comparator substitution is intentionally confined to a sequential
offline process.

The [product packing development report](benchmarks/results/packing/2026-09-12/README.md)
retains the initial capture/replay check and clearly separates it from the broader
run. The [LongMemEval-V2 assessment](benchmarks/integrations/LONGMEMEVAL_V2.md)
documents the pinned official adapter, registered runner, and remaining
multimodal and comparative work. The [MemoryAgentBench integration](benchmarks/integrations/MEMORYAGENTBENCH.md)
adds outcome-free input registration and fail-closed result verification across
accurate retrieval, test-time learning, long-range understanding, and conflict
resolution. Its real-data ingress path passes, but it has no promoted task score
yet. V1 source recall is not a substitute for either benchmark's task coverage.

Use repeatable `--question-id ID` to reproduce a specific evidence-evaluation
failure within its original split. It cannot be combined with `--limit`; unknown
or out-of-split IDs are rejected. The selected IDs and full dataset hash remain
in the report. A targeted retry is a separate diagnostic, not a replacement for
an incomplete full run or evidence of complete-split coverage.
