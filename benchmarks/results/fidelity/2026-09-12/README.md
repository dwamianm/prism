# Evidence formatting fidelity — 2026-09-12

Production changes:

- `82f7ccf` preserves the stored `source_type` in every packed representation.
  Provenance metadata is counted against the context budget. The field describes
  the record's classification; it does not independently verify speaker identity
  or truth. Storage, retrieval and small-budget tests cover all five source types.
- `d4e29a4` corrects the alternate `format_for_llm()` renderer. It removes repeated
  node IDs instead of merging text prefixes, retains source/epistemic/lifecycle
  labels in profiles, bodies and conflicts, and replaces unsupported reasoning
  instructions with evidence-based guidance. Chronological markers no longer
  instruct the reader to treat the newest value as necessarily true.
- `79a9707` updates the remaining older profile/conflict expectations and stale
  documentation. The implementation behavior is the same as `d4e29a4`.

Distinct records can describe different events or carry different uncertainty
and provenance even when their text matches. Prefix-based deduplication also
removed exceptions appearing after character 100. The new identity boundary
keeps those records available to the reader, while removing duplicate appearances
of the same record across profile and body. Extra evidence and metadata can mean
fewer records fit at a fixed token budget; both formatters still measure the
complete rendered output. Semantic deduplication and event counting must not be
inferred merely from record identity.

## Live source-provenance experiment

The [MemConflict factorial diagnostic](../../memconflict/2026-09-12/provenance-5b2baa9-summary.json)
used frozen candidates, the original `fefa7e1` packer, and harness `5b2baa9`.
It compared density and relevance ordering, each with and without the stored
source-type field, at the same 2,048-token budget with 100 tokens reserved.
All three baseline contexts reproduced exactly; all twelve local-reader calls
completed, and the process exited zero. No upstream dialogue is redistributed
in the committed summary.

In the relocation case, relevance ordering without provenance led the reader
to attribute a system-inferred statement to the user. With the field present,
it described the conclusion as an inference from a system statement. Density
ordering still omitted the relevant evidence and the reader abstained. Both
provenance variants retained the professional-study condition in the audiobook
answer; both variants without provenance omitted it. Static-conflict answers
still omitted acknowledgment of the labeled contradiction.

This is a small, unjudged development observation. Adding metadata changes packing
costs and selected records, so the conditional-answer difference cannot be
attributed to the semantic effect of the label alone. The baseline uses pre-UTC
rendering; UTC normalization was validated independently. The production density
ordering remains unchanged, and this experiment does not establish general
answer accuracy or benchmark leadership.

## Authored reader counterexamples

[Before contexts](before-82f7ccf.json) and [after contexts](after-d4e29a4.json) use
identical fixed record identities, content, timestamps, questions, reference date
and 2,048-token budgets. The [paired reader report](reader-08d8705.json) retains
both complete contexts and outputs. Harness `08d8705` verifies fixture/protocol
agreement and unique case identity before any reader requests; evaluator
expectations never enter model inputs. All ten calls completed and the supervised
process exited zero.

These five counterexamples were authored after inspecting and fixing the code.
They are **not held-out, independently labeled or competitively scored**. The
following observations are manual readings of the retained outputs, not an
accuracy headline:

| Counterexample | Before | After |
|---|---|---|
| Grandmother's country does not establish user's former country | Abstains | Abstains |
| Youngest child is one of two named children | Identifies two | Identifies two |
| Future conditional Oracle evaluation versus current PostgreSQL | PostgreSQL | PostgreSQL |
| Later system guess versus user's stated home city | Abstains because of apparent conflict | Identifies Kyoto as the supported statement |
| Protected-region exception after a shared long prefix | Exception is missing; reader abstains | Manual approval for Orion |

The local reader already resisted some unsupported old directives. Removing them
is still necessary: the formatter must not instruct a downstream model to invent
connections or treat recency as truth. The observed answer differences support
the source-label and qualifier-preservation fixes in these cases, not a general
accuracy claim.

Run the diagnostic's render mode once with each package version on `PYTHONPATH`,
using the same current harness, then compare the saved contexts:

```sh
python -m benchmarks.diagnostics.formatter_fidelity --output /tmp/contexts.json
python -m benchmarks.diagnostics.formatter_fidelity \
  --before /tmp/before.json --after /tmp/after.json --output /tmp/reader.json
```

The selected reader was local `qwen3.5:4b`, digest
`2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd`.
The report retains its system prompt, temperature zero, seed 42, context size
16,384 and generation cap 512. Token budgets apply to memory, not the full reader
prompt. No production extraction or same-model judged benchmark was run here.

## Verification

The frozen final source `79a9707` passed the full Python 3.11 suite against a
live PostgreSQL service: **1,988 passed, 51 skipped**, 242.16 seconds, process
exit zero. The first full run at `d4e29a4` exposed five older tests that expected
the removed labels; `79a9707` updated those assertions to check explicit
provenance and lifecycle while retaining chronological content checks.

The installed Python 3.13 wheel passed **146 focused checks, 5 skipped** in
2.49 seconds, process exit zero. Coverage includes context fidelity and budgets,
UTC rendering, packed provenance, startup recovery, profile/conflict behavior
and both diagnostic harnesses. These are correctness checks, not evidence of
competitive memory quality.
