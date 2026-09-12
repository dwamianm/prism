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
full namespace/grant model remains unimplemented. Entity profiles also still
publish nontransactionally, despite their new source-fidelity guarantees.

The next comparative work needs pinned competitor versions, named model and
embedding conditions, matched questions and token/latency budgets, ingestion
costs, explicit failure coverage, and more than one reader family. Static QA
must be supplemented by task completion, updates, abstention and precision
workflows. These are remaining requirements, not completed evaluation claims.
