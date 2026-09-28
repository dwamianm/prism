# Final bounded coding-memory continuation study

Registered 2026-09-26 before any scored model calls. This is the last study in
this integration effort. It tests reuse of prior-session material, including a
source-backed backfill, against a simpler notes search. It does not test an
agent learning to write memories or establish general coding improvement.

## Frozen tasks and history

Four newly authored application adapters cover explicit corrections, workspace
lease lifetimes, tool value bindings and sparse relevance feedback. They start
as unimplemented functions; they are not historical mutations or unresolved
production tickets. All contracts are explicit in `continuation_tasks.py`.
Private acceptance checks cover the stated behavior with public model types and
contract-enforcing client/workspace doubles. Known witnesses must pass and empty
stubs must fail before scored calls. No candidate enters production code.

Source, existing tests and documentation are frozen at
`df2d58ea9679a801fc9c176db493008405d7adbe`. Each run adds just its application
scaffold at `src/prme/integrations/_coding_trial.py`. The model cannot access the
new harness, witnesses, hidden checks, Git, previous answers or host filesystem.
All arms have identical complete frozen source/test/document access.

The history is the existing daily service's eleven active project notes and their
immutable source events, copied before authoring these tasks. Use every event,
including unrelated notes. No new solution notes or task-specific corpus edits.
Split each event at paragraph boundaries once at least 1,000 characters have
accumulated (retain final remainder), in stable event-ID order. Preserve exact
text, source event ID and original line interval. Hash the complete snapshot,
chunks and source files. This backfills source excerpts already captured in
prior sessions, not a new whole-project ingest.

## Three arms

- **Control:** source/test/docs and task; no saved-note context.
- **Notes:** deterministic local BM25 search of the frozen chunks, k1=1.2,
  b=0.75, case-folded `[a-z0-9]+` tokens, unique query terms, positive Robertson
  IDF, descending score and chunk-ID tie break. Greedily include positive-score
  chunks that fit, at most eight and 2,048 cl100k_base tokens including source
  headers. No model, embeddings, tuning or oracle selection.
- **PRME:** a fresh isolated pack containing exactly those same chunks as direct
  project notes with source provenance. Existing coding-service retrieval
  settings, eight results and a 2,048-token context ceiling; no organizer,
  learning, supersedence or task-time writes. Preserve the exact recall/receipt.

Both notes arms also expose identical chunk files through read/search/list.
Query both retrievers with the unchanged complete task prompt. Freeze all four
pairs of contexts before any scored response; never adjust queries after looking
at retrieval or repair outcomes. Retrieval/setup cost is reported separately.

Use local Ollama `qwen3.5:35b-a3b`, thinking off, temperature zero, 32,768 context
and 2,048 output tokens/action. Pin/recheck model digest, template and server.
Two repeats use seeds 20260926 and 20260927. Each arm has a fresh conversation,
file snapshot and twelve actions, with explicit remaining counts. For task index
`i` and repeat `r`, rotate `[control, notes, prme]` left by `(i+r)%3`, reversing
the rotated list on repeat 1. Maximum 24 scored runs, no replacements or retries.
Provider/context failures are unsuccessful and retained. A setup or identity
failure makes the study inconclusive and ends it; it does not authorize a new
trial. All generated code runs in unprivileged, networkless Docker sandboxes
using a source-verified immutable image. Ollama stays on the host GPU.

## Decision fixed before answers

A task is reliably solved only if both repeats pass all acceptance checks.
A repeat loss means PRME fails a task/repeat that either baseline passes.
Retain every run, including failures. PRME earns further *limited evaluation*
only if the study is complete, has no provider failures, has no repeat losses,
solves at least three of four tasks reliably, and meets either rule:

1. **Correctness:** at least two more reliably solved tasks than each baseline,
   with total coding-model input tokens no more than 110% of the notes baseline.
2. **Efficiency:** total coding-model input tokens at most 80% of the notes
   baseline, with the preceding no-loss and correctness floor satisfied.

These are practical continuation thresholds, not a significance test. Two
repeats do not create eight independent tasks. Timing and output tokens are
reported but are not promotion gates. The notes comparison measures the extra
value of PRME beyond retaining searchable notes. Current documentation may
already suffice; a ceiling result does not prove memory is generally useless.

Any failed or inconclusive result shelves automatic integration: remove routine
recall/capture requirements, retain manual tools and existing data, and record
that this integration has not earned its complexity. No tuned rerun, extra tasks
or expanded integration follows. A positive result permits only a later scoped
proposal; it does not turn on more integrations automatically.

No core retrieval/packing change is included, so LoCoMo/LongMemEval answer or
packing gates are outside this study. Raw contexts, transcripts, generated code,
corpus and pack stay in ignored `benchmarks/coding/runs/final-v1/`. Track only
protocol/harness and compact manifests, metrics, checksums and the final report.
Commit registration and prepared-input hashes before running the model.
