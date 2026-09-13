# Comparative evaluation audit

The completed PRME paired development study held reader, judge, question set,
source capture, context budget and packing runtime fixed while comparing density
and score ordering. The separate 381-question source-retention confirmation also
completed and failed its preference-category guard. Density remains the default.
The completed Mem0 raw-retrieval comparison and PersonaMem pilot/control studies
are documented below and in the research agenda; none establishes leadership.

The [MemDelta primary paper](https://arxiv.org/html/2606.29914v1) reports that
reader behavior and embedding choice can change system rankings, and recommends
controlling them and measuring ingestion cost. Its costly-system comparison
covers only two question types and uses approximately matched embeddings. Its
numbers have not been independently reproduced here. Our own blinded review
also found correct computations followed by refusals, supporting the need to
separate reader task completion from retrieval quality.

The current [Mem0 benchmark repository](https://github.com/mem0ai/memory-benchmarks)
separates managed and OSS configurations and exposes extraction, embedding,
answerer, judge and retrieval-depth choices. Its published OSS runs use different
model conditions from our local reader study. Their headline scores are not a
matched baseline for PRME. The [AMB harness](https://github.com/vectorize-io/agent-memory-benchmark)
also supports broader agent tasks and retrieval-only assertions. An adapter/run
must preserve the same task, inputs, provider semantics and resource measurements
before results can be compared.

## PrecisionMemBench adapter audit

Read-only clone: `/tmp/prme-precisionmembench-upstream`, commit
`85b48d5fd1b38babc7fe922f3beb0308cf3aa6cb` from
[the upstream repository](https://github.com/tenurehq/precisionmembench).
No external provider stacks or benchmark tests were executed.

Inspection of `src/adapters/baseAdapter.ts` and the external runner found:

- Ingestion sends concatenated canonical names, aliases, content and rationale,
  with metadata containing belief ID and only the first scope. The single-turn
  payload does not transmit the fixture's type, supersedence or resolved state.
- Pinned facts and open questions are selected from the in-memory fixture index.
  Relationship participants are also expanded from fixture records. These steps
  do not prove corresponding provider capabilities.
- The persona prelude comes from an injected fixture lookup. It is not generated
  by the memory provider.
- Retrieved IDs are joined back to fixture records. Unknown IDs are dropped.
  This boundary must be reported if extracted memories change identities.
- Context budgets count beliefs, not serialized tokens. These settings cannot be
  compared directly with PRME's 4K-token product context.

The cases are useful prompts for precision, alias, lifecycle and domain-isolation
requirements. Published totals, however, mix provider retrieval with adapter
behavior and asymmetric information. A future PRME adapter must disclose every
mapping and use ordinary public memory APIs; reference expectations cannot drive
selection. We will not copy published totals into a PRME leadership claim.

## Concrete gaps exposed

The original named-project gap now has a public `MemoryWorkspace` API. Named
projects use identity-checked local packs or PostgreSQL schemas, a bounded lease
cache and shared embeddings; PostgreSQL also shares a bounded connection pool.
The installed [100-project native backup/restore workflow](../../recovery/2026-09-12/PG-WORKSPACES.md)
passed, including separate source and merged-entity verification. A metadata
`project_id` remains insufficient for isolation; RFC-0004's full hosted grants
remain unimplemented. Entity profiles now publish atomically, scan complete
scoped sources and journal prepared work for explicit recovery and abandonment.
See the [workspace guide](../../../../../docs/WORKSPACES.md) and
[profile guide](../../../../../docs/ENTITY-PROFILES.md) for supported boundaries.

The next comparative work needs pinned competitor versions, named model and
embedding conditions, matched questions and token/latency budgets, ingestion
costs, explicit failure coverage, and more than one reader family. Static QA
must be supplemented by task completion, updates, abstention and precision
workflows. These are remaining requirements, not completed evaluation claims.

## Pinned Mem0 adapter compatibility

A local isolated environment now contains Mem0 2.0.20 from clean upstream commit
`c7ee362aff94a369af70f13f2b4f853f6793ff4c` and the PRME `107f535` wheel.
`benchmarks.diagnostics.mem0_compatibility` verifies all 148 installed Mem0 Python
source files against that checkout, disables telemetry, and forbids LLM calls.
Its authored `infer=False` probe checks raw writes, owner plus metadata-scope
filtering, and result identities after reopening. The adapter closes both Mem0's
SQLite connection and its Qdrant client explicitly. All checks completed with
native exit zero in `mem0-compatibility-c7ee362.json`.

Both products use BGE small English v1.5, FastEmbed 0.7.4, ONNX Runtime 1.24.2 and
NumPy 2.4.2, with identical model asset hashes. Four one-text embedding calls
match exactly. The first probe accidentally compared PRME's four-text batch to
Mem0's individual calls and failed its 1e-6 tolerance; that failure is preserved
in `mem0-compatibility-c7ee362-attempt1.json`. A corrected comparison retained the
observed batch-versus-single maximum difference of 0.000223577 rather than
loosening the tolerance. This checks compatibility, not extraction quality,
answer accuracy or product leadership. The later 119-question raw-turn
comparison is reported below; matched-reader answer evaluation remains pending.

## Next external protocols: Hindsight and Graphiti

Read-only upstream snapshots are pinned in
`hindsight-graphiti-protocol-audit.json`; no runtime or reported score was
reproduced. These are candidates for additional comparisons, not results.

The subsequent [authored Hindsight preflight](hindsight-authored-preflight.json)
completed with native exit 0 against a fresh local PostgreSQL database. All 323
installed Python source files match the pinned 0.9.2 checkout. Public retain,
close, initialize and recall preserve a seasonal qualification and bank isolation
in the authored example. This uses raw chunks with LLMs disabled, native PostgreSQL
text search and RRF, not the full extraction/consolidation system. No dataset
comparison or competitive performance follows from this preflight.

Hindsight's Pillow requirement conflicts with FastEmbed 0.7.4. Its isolated
environment therefore uses FastEmbed 0.8.0 with the same ONNX Runtime 1.24.2,
NumPy 2.4.2 and BGE assets. All seven authored input vectors match PRME's 0.7.4
runtime exactly, including Unicode, whitespace, empty and long text. This is a
bounded parity check, not proof for every future input. The standalone diagnostic
is `benchmarks/diagnostics/hindsight_preflight.py`; invoke it by file path in the
competitor environment to avoid the benchmark package's eager PRME imports.

Hindsight's current [recall contract](https://github.com/vectorize-io/hindsight/blob/bde55237f53bf55aacd048b01e29d7dc23b83a85/hindsight-api-slim/hindsight_api/engine/memory_engine.py#L7226)
separates the fact-text token limit from entity observations and source chunks.
The chunk limit defaults to 8,192 tokens independently of the fact limit.
Its traversal budget is also distinct: a configurable fixed or adaptive mapping
turns low/mid/high settings into work budgets. Consequently, matching an argument
named `max_tokens` does not establish an equal complete context allowance.

The pinned [shared benchmark runner](https://github.com/vectorize-io/hindsight/blob/bde55237f53bf55aacd048b01e29d7dc23b83a85/hindsight-dev/benchmarks/common/benchmark_runner.py#L645)
requests entities with a separate 2,048-token allowance and includes chunks,
then passes the entire recall result to its answer generator. The
[LongMemEval runner](https://github.com/vectorize-io/hindsight/blob/bde55237f53bf55aacd048b01e29d7dc23b83a85/hindsight-dev/benchmarks/longmemeval/longmemeval_benchmark.py#L332)
defaults to JSON context and an 8,192-token fact request. Its reader also has
specific temporal, counting and preference-answer instructions. Its optional
structured renderer clips source chunks at 1,000 characters. A matched PRME run
must freeze one common reader and measure the actual serialized context, preserving
complete source qualifiers. Existing outcome-based retry/merge options in this
harness must not be used to selectively replace failed predictions in a comparison.
These differences do not invalidate the vendor's results; they define a different
protocol from PRME's current whole-output budget study.

Graphiti's [README](https://github.com/getzep/graphiti/blob/c035afb7990b6077331a81e98b04efcfd9bf8184/README.md#L79)
distinguishes the OSS graph framework from Zep's managed infrastructure. Managed
Zep scores must not be assigned to the OSS package automatically. The inspected
[graph-building evaluation helper](https://github.com/getzep/graphiti/blob/c035afb7990b6077331a81e98b04efcfd9bf8184/tests/evals/eval_e2e_graph_building.py)
defaults to oracle histories, caps ingested messages, and judges candidate graph
extractions against baseline extractions. That measures graph-building regression,
not generated-answer accuracy on the full long-history corpus. Its context flow
also stores strings but later indexes each as though it were an episode sequence
(`message` becomes the first character). That observation is from source inspection,
not a reproduced runtime failure. A fresh public-API adapter and matched answer
protocol are needed before using Graphiti as another comparative baseline.

## Completed Mem0 raw-turn development comparison

Pinned Mem0 OSS `c7ee362` completed all 119 registered questions with zero errors
and native exit zero. The [completion report](mem0-raw-dev-completion-b384095.json)
records the full output hash, plan identity, paired metrics and limitations.
The frozen PRME reference is `1f5375a`; this is not a fresh latest-release run.

| Shared whole-turn budget | Mem0 evidence recall | PRME evidence recall | PRME change | Paired 95% interval | Wins / losses |
|---|---:|---:|---:|---:|---:|
| 2,048 | 87.57% | 91.96% | +4.39 pp | +0.58 to +8.19 pp | 15 / 3 |
| 4,096 | 93.27% | 96.49% | +3.22 pp | 0.00 to +6.87 pp | 10 / 3 |
| 8,192 | 95.91% | 97.95% | +2.05 pp | +0.58 to +4.02 pp | 6 / 0 |

There are 114 labelled questions. The 4K interval's stored lower endpoint is
approximately `2.43e-19`, a floating-point near-zero value; it does not support a
claim of a meaningfully positive lower bound. Query bootstrap intervals also
ignore shared histories and do not establish independent-system superiority.
At 4K, Mem0 retained all preference evidence while PRME retained 92.86% across
seven questions. PRME improved multi-session recall by 8.33 points, with a
category interval spanning zero. Knowledge updates and assistant evidence tied
at 100% in this shared-packer setting.

These comparisons use raw turns, matched dense model assets, Mem0's recommended
NLP/BM25 support and a common whole-turn evaluator packer. Neither product's
context renderer or extraction pipeline is evaluated. In particular, the high
raw-packer results must not be substituted for PRME's lower product-packing
results. Five unlabelled questions have null evidence scores, not demonstrated
abstention. No LLM calls, retries or outcome-based exclusions were used. Runtime
measurements remain in the raw report, but concurrent workloads and separate
runs preclude a fair speed ratio. Preference coverage is a demonstrated remaining
gap; this study does not establish the best end-to-end memory system.

## Fresh PRME–Hindsight public-context capture

The [normalized development protocol](HINDSIGHT-PRME-NORMALIZED-DEV-PROTOCOL.md)
is registered at `f5a2a69`. Fresh installed PRME `b7521bc` and pinned Hindsight
`bde55237` use the same FastEmbed 0.8.0, ONNX Runtime 1.24.2, NumPy 2.4.2,
tiktoken 0.14.0 and BGE assets. Workers receive 119 neutral queries and all 59,021
source turns, with opaque identifiers and no answers, categories or evidence labels.

The prior strict capture is retained as stopped, not a quality result: Hindsight's
native sanitizer removed 29 U+0002 characters from one document, violating the
exact-byte readback gate. Both processes were interrupted with native exit 130.
The fresh protocol applies the same declared control-character normalization to
both inputs (41 removed characters across three source records). No printable
text, turn, blank record or query is removed. No quality outcomes were inspected
before the amendment; interrupted outputs will not be merged into the fresh run.

Both workers verify public source readback and exact returned text. The analyzer
reproduces contexts at 2K/4K/8K and separately reports shared whole-turn ranking,
actual content-bearing source hits and complete source text within a record.
Pointers do not count as content; partial chunks do not count as whole sources.
Hindsight's actual context is an explicitly disclosed adapter rendering of its
returned units. It does not substitute original documents for partial returns.

A fresh [three-case authored integration run](public-capture-authored-verification.json)
passed both workers and the complete analyzer with native exit zero; 14 contract
tests also pass. This verifies the capture/analysis path, not
comparative quality. The normalized run subsequently rejected optional custom metadata missing from
five returned units in `case-0046`; native source role and timestamp fields
remained present. PRME was stopped with native exit 130. Hindsight continues
recording full operational failure coverage. No dataset quality scores have
been inspected; the [native-provenance adapter preflight](HINDSIGHT-NATIVE-PROVENANCE-PREFLIGHT.md)
precedes any complete replacement registration. All 119 successful cases and both
native exit codes remain required before analysis. These raw profiles disable LLM extraction and cannot
establish full-system or answer-quality leadership.


The [common-reader protocol](HINDSIGHT-PRME-READER-PROTOCOL.md) freezes two reader
families, exact verified 4K contexts and fresh empty-memory controls. A separate
worker accepts only neutral questions, dates and context strings. The [live
three-case reader preflight](public-context-reader-authored-verification.json)
completed nine logical predictions from seven unique requests with native exit
zero; offline verification reproduced every prediction from its raw response.
The [separate judge calibration](hindsight-prme-reader-judge-calibration-verification.json)
passed 41/42 authored controls, with zero false accepts and one false rejection.
Both parent and worker exited zero. These are operational/calibration results;
the failed dataset captures remain ineligible for reader generation or scoring.
