# Comparative evaluation audit

The current PRME paired study holds reader, judge, question set, source capture,
context budget and packing runtime fixed while comparing density and score
ordering. It is a development experiment, not a comparison against other products.
A separate source-retention confirmation is running. Neither establishes leadership.

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

PRME distinguishes users and six scope types, but currently has no named project
or arbitrary domain namespace field in its memory objects or retrieval API.
Two projects belonging to the same owner therefore require separate packs for
isolation; a `project_id` metadata key is not an enforced boundary. RFC-0004's
full namespace/grant model remains unimplemented. Entity profiles originally also
published nontransactionally. That gap is now fixed by atomic publication
(`430e1b3`) and complete scoped source scans (`107f535`), with backend fault,
concurrency and abrupt-exit coverage. Durable preparation/retry remains separate.

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
answer accuracy or product leadership. Real comparative evaluation remains to
be registered and run with matched inputs, reader, budgets and cost accounting.
