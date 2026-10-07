# Four retrieval experiments: #238–241

All four experiments were implemented or measured and reached an offline decision.
None justifies a production-default change or an answer-quality improvement claim.
The three new behaviors remain explicit, disabled options. No reader/judge answer
run or paid API was started. The failed gates stop the proposed answer experiments.

The machine-readable [decision](decision.json) binds every input report by SHA-256.
Paired gate reports retain per-question measurements, category results, settings,
pack identities, code provenance, host load and retrieval latency.

| Ticket | Decision | Main measurement |
|---|---|---|
| #238 deterministic second round | Failed required offline gate; stop answer experiment | LoCoMo multi-hop evidence recall 72.60% → 70.37%; all evidence packed 51/101 → 48/101 |
| #239 claim/source co-packing | Source-record fidelity improves, evidence tradeoff; keep disabled | LoCoMo orphaned packed claims 89.2% → 17.0% at 4K; complete evidence contexts 133/150 → 131/150 at 8K |
| #240 candidate cut × reranking | Tight limit failed; stop before reranking/answers as the ticket specifies | LongMemEval-S mean evidence recall 89.21% → 82.37%, all evidence packed 30/38 → 27/38 |
| #241 read-time supersedence | Authored current-state control works; public slice has no applicable edges | Prior active claims in authored current contexts 4/4 → 0/4; public knowledge-update contexts unchanged |

## Inputs and boundaries

- Retrieval/candidate experiments replay the committed `benchmarks/slices/dev-v1.json`:
  466 LoCoMo questions from conv-26, conv-49 and conv-50, plus 42 LongMemEval-S
  questions. There are 462 and 38 annotated questions respectively. These are
  examined development data, not untouched confirmation.
- The source experiment replays the available complete LoCoMo extraction pack
  conv-26 (152 questions, 150 annotated), and two LongMemEval-S histories
  `7527f7e2` and `f8c5f88b` (both single-session-user). The latter were chosen by
  available-pack completion cost, before comparing outcomes. Copies of their
  legacy packs were completed: zero missing turns for `f8c5f88b`, four missing
  turns for `7527f7e2`, through authorized Ollama DeepSeek extraction. Original
  source packs were preserved. No answer reader or judge was used.
- These extraction packs retain their legacy claim wording and graph state.
  They are not a rebuild under every v0.14.0 ingestion default. The two-history
  LongMemEval-S sample is especially small and cannot establish broad gains.
- Budgets are 4,096/8,192 tokens with 100 reserved overhead tokens, giving actual
  context ceilings of 3,996/8,092. Other retrieval defaults remain in effect.
- Gate intervals resample questions, not LoCoMo conversations. They do not
  satisfy the separate DeepSeek answer/default-change rule. Projected accuracy
  in ordinary gate reports is a planning estimate and is not an answer result.

## #238: one deterministic evidence-conditioned round

`enable_evidence_followup=True` reads the first five eligible ranked records,
constructs at most twelve bounded entity/predicate signals, and performs one
30-per-path graph/vector/lexical search against the complete eligible store.
It preserves the original intent/clocks and does not run dateparser again.
Exact snapshots merge with backend-path union and component maxima before
ordinary rank fusion. Backend failure preserves round one; snapshot conflicts
abort. Receipts retain the exact query, anchors, signals and merge observations.

The required LoCoMo multi-hop gate failed both metrics:

| Metric (101 questions) | Control | Follow-up | Paired delta, 95% question interval |
|---|---:|---:|---:|
| Mean evidence recall | 72.60% | 70.37% | −2.24 pp [−5.13, +0.46] |
| All evidence packed | 50.50% | 47.52% | −2.97 pp [−8.91, +1.98] |

There were 4 recall wins and 11 losses; full-evidence coverage had 2 wins and
5 losses. Across all annotated LoCoMo questions, full-evidence coverage fell
81.82% → 79.44%. LongMemEval-S improved 78.95% → 84.21%, but that does not
rescue the ticket's required multi-hop gate. No answer run was started.

Observed retrieval p50/p95 milliseconds:

| Benchmark | Control | Follow-up |
|---|---:|---:|
| LoCoMo | 172 / 221 | 274 / 350 |
| LongMemEval-S | 177 / 223 | 276 / 341 |

The host was shared with tests and other work. These timings describe the
recorded runs, not an isolated causal latency measurement. The reports include
host load and warm-up policy. [Paired result](raw-followup-comparison.md).

## #239: measure first, then co-pack complete sources

The initial measurement preceded the fix: at 4K, 2,351/2,635 packed LoCoMo
claims lacked their source record. 56 lacked complete source text anywhere in
context. This distinction matters: most legacy claims contain the source's full
passage, so missing a source record is not automatically missing its text.

`packing.co_pack_sources=True` resolves eligible direct source nodes, tries
FULL source turns immediately after a packed claim within the same budget,
preserves explicit selection bounds, and prevents a derived record from folding
away its source. A same-tag source can still absorb a contained claim, so claim
counts and denominators change. Source lines keep their speaker, date, identity
and original bundle section. Version 23 receipts retain source candidates with
replayable zero ranking scores; versions 1–22 keep their canonical bytes.

| Sample / budget | Claims: control → co-pack | Claims missing source record | Source text tokens/context | All evidence packed |
|---|---:|---:|---:|---:|
| LoCoMo / 4K | 2,635 → 559 | 2,351 (89.2%) → 95 (17.0%) | 102.8 → 163.0 | 128/150 → 128/150 |
| LoCoMo / 8K | 4,597 → 1,150 | 3,890 (84.6%) → 102 (8.9%) | 270.4 → 381.2 | 133/150 → 131/150 |
| LongMemEval-S / 4K | 53 → 6 | 53 (100%) → 2 (33.3%) | 0 → 106 | 2/2 → 2/2 |
| LongMemEval-S / 8K | 83 → 14 | 83 (100%) → 2 (14.3%) | 0 → 381 | 2/2 → 2/2 |

Claims missing complete source text fell 56 → 2 and 111 → 7 on LoCoMo,
and 2 → 0 and 3 → 0 on the LongMemEval-S sample, at 4K and 8K respectively.
The added source-record token allocation is roughly 60/111 tokens per LoCoMo
context and 106/381 per LongMemEval-S context. It replaces other content inside
the ceiling; it does not increase the ceiling. Total mean context tokens were
3,993.8 → 3,994.0 and 8,089.8 → 8,089.7 on LoCoMo.

Complete immutable source text for **all annotated evidence** was unchanged:
84.0% at LoCoMo 4K, 87.3% at LoCoMo 8K, and 100% in the two LongMemEval-S
histories at both budgets. Thus the structural fidelity gain does not establish
an annotated-source coverage gain, much less an answer gain. The LoCoMo 8K
coverage tradeoff leaves co-packing disabled. No answer run was started.

Paired results: [LoCoMo 4K](ingest-locomo-copack-4k-comparison.md),
[LoCoMo 8K](ingest-locomo-copack-8k-comparison.md),
[LongMemEval-S 4K](ingest-longmemeval-copack-4k-comparison.md),
[LongMemEval-S 8K](ingest-longmemeval-copack-8k-comparison.md).

## #240: cut-size gate decides whether reranking proceeds

The single contrast is the current generous 500 vector/lexical,
150 graph limits against 30 for all three paths, with default rank fusion and
reranking off. Aggregation widening, supplementary scans and session expansion
remain enabled; 30 per path is not a hard 30-record total pool or three-record
context. Actual candidate counts and per-channel recall are in the reports.

LongMemEval-S mean evidence recall lost 6.84 pp (95% question interval
[−13.42, −1.40]), with six losses and no wins. Complete evidence packing lost
three questions, 30/38 → 27/38. Complete returned-evidence coverage fell
38/38 → 34/38. LoCoMo full-evidence packing rose 81.82% → 83.12%, which does
not offset the required LongMemEval-S guard.

The ticket says to stop when the tight cut loses LongMemEval-S evidence. The
reranker-on cells and DeepSeek answer pair were therefore not started. Answer
accuracy and correct abstention are **unmeasured**, recorded as null in the
machine decision, rather than inferred from coverage. This bounds #87/#88 with
a negative result for this cut; it does not establish that every reranker or
candidate limit is ineffective. [Paired result](raw-tight-30-comparison.md).

## #241: current replacement membership, not newer-is-true

`enable_read_supersedence=True` filters current-state requests after expansions,
using direct replacement pointers or existing SUPERSEDES edges. It checks the
same owner, scope and claim type, current validity, epistemic/lifecycle
eligibility, cycles and direct successor edges of the proposed replacement.
It never deletes nodes, infers replacement from timestamps, traverses chains or
cascades to dependent records. Explicit, historical, aggregation and bounded
clock requests preserve the existing retrieval behavior. Source turns may still
describe prior values; suppressing a claim is not an answer-state guarantee.

Default retrieval already excludes SUPERSEDED/ARCHIVED lifecycle states. The
raw public slice had no applicable replacement relationships: every context
stayed byte-identical. Its LongMemEval-S knowledge-update slice has six
questions, five annotated, all five already fully packed. It showed no policy
exclusions or coverage change, so no answer run was started.

The separate [authored check](authored-supersedence.json) used explicit replacement
edges on otherwise active FACT, PREFERENCE, DECISION and TASK nodes. Prior claims
were present in 4/4 control current contexts and 0/4 suppression contexts. All
four new claims remained. Historical and explicit contexts retained prior claims;
the prior graph snapshots were unchanged. Receipts replayed their exact rankings.
This is node/context correctness evidence, not benchmark answer accuracy.
[Public paired result](raw-suppression-comparison.md).

## Reproduction and validation

Use the gate's immutable archive/pack copies. Example commands from the repository:

```sh
uv run python -m benchmarks.diagnostics.product_packing gate --slice --output <reports>/raw-baseline-4k.json
uv run python -m benchmarks.diagnostics.product_packing gate --slice --set enable_evidence_followup=true --output <reports>/raw-followup-4k.json
uv run python -m benchmarks.diagnostics.product_packing gate --slice --set packing.vector_k=30 --set packing.lexical_k=30 --set packing.graph_max_candidates=30 --output <reports>/raw-tight-30-4k.json
uv run python -m benchmarks.diagnostics.product_packing gate --slice --set enable_read_supersedence=true --output <reports>/raw-suppression-4k.json
uv run python -m benchmarks.diagnostics.product_packing gate --benchmark locomo --packs data/extracted-packs-v1/ingest-baseline --set packing.token_budget=4096 --output <reports>/ingest-locomo-baseline-4k.json
uv run python -m benchmarks.diagnostics.product_packing gate --benchmark locomo --packs data/extracted-packs-v1/ingest-baseline --set packing.token_budget=4096 --set packing.co_pack_sources=true --output <reports>/ingest-locomo-copack-4k.json
uv run python -m benchmarks.diagnostics.read_supersedence --output <reports>/authored-supersedence.json
uv run python -m benchmarks.diagnostics.retrieval_four_ticket_report <reports>
```

Repeat source commands at 8192 tokens and for `--benchmark longmemeval --packs
 data/extracted-packs-v1/four-tickets-lme-sample`, with matching report names.
Pack content stays in ignored `data/`; the tracked reports bind it by identity.
The temporary PostgreSQL test server used an isolated database and no user pack.
Validation results are recorded in `validation.json` beside this report.
The full regression run passed 4,766 tests (933 skipped). The latest focused
DuckDB/PostgreSQL run passed 138 tests (one skipped), including the final
source/replacement guards. Lint, public API typing and whitespace checks passed.
The PostgreSQL result covers the relevant backend/receipt suite, not its entire
test suite.
