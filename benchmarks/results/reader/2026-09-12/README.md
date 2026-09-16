# Local reader diagnostics — 2026-09-12

Three failed causal ranking checkpoints were passed to local Ollama
`qwen3.5:4b`, using the actual packed context. All three returned the expected
technology/person (GraphQL, PostgreSQL, Jane Doe), but this is **not** judged
answer accuracy or a reason to erase the ranking failures. In the GraphQL case,
the reader added "tentatively" to a source that simply reported a decision.
The record's `state` was `tentative`; that described memory lifecycle.

The original outputs, a header-clarification attempt, and a controlled key
ablation are retained. The extra explanatory header did not remove the error
and was not adopted. On the identical original context, renaming `state` to
`memory_lifecycle` removed the unsupported qualifier. A synthetic contrast
replaced that one source sentence with an actually provisional decision; the
reader retained its uncertainty with either key. Omitting active lifecycle
metadata also worked in this diagnostic, but the adopted rename preserves it.

PRME now uses the explicit key in packed text; the public MemoryNode lifecycle
field is unchanged. The system prompt, full contexts, model name and generation
settings are in the JSON files. Conditions named `actually_provisional` are
synthetic source substitutions for this diagnostic, not original scenario data.
No output was judged by an external model or a human participant. One local
model and six synthetic probes cannot establish general reader reliability.

Reproduce the key ablation with the same local model installed:

```sh
python -m benchmarks.diagnostics.lifecycle_reader \
  --input benchmarks/results/reader/2026-09-12/original.json \
  --output /tmp/lifecycle-reader.json
```

The original simulation capture was made after `604dbc6` plus the report-only
`rendered_context` field (committed with `e5befa6`). The key ablation changes only
rendered JSON metadata and the explicitly labeled provisional source sentence;
it does not alter the event log or any retrieval score.
