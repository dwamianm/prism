# Real-ticket coding-memory trial: issue 108

Registered before model answers on 2026-09-26. This is one selected development
ticket, not a held-out benchmark or a general coding-quality claim.

## Task and acceptance

Fix [issue 108](https://github.com/dwamianm/prism/issues/108): auditable and
compact records must stay on one line when source text contains U+0085, U+2028
or U+2029. Preserve JSON round trips, ordinary Unicode bytes, record fields,
references, missing-representation errors and exact packing budgets. Auditable
speaker labels are untrusted text too. Existing reader output stays unchanged.

The task starts from actual production source at
`4dba69a1a2b14ab980c9ec60744036753f6d97e7`, without an authored mutation.
Both arms may edit `_render_entry` and `_render_compact_entry` in
`src/prme/retrieval/packing.py`. Acceptance checks must fail on this baseline and
pass on a private minimal witness before any scored call. The witness and hidden
checks are never available through agent tools.

## Comparison

- Control: task plus read/search/list access to all tracked Python source,
  existing Python tests, documentation and root project instructions at the
  frozen revision. New tests, trial artifacts, Git, secrets and benchmark answers
  are excluded. Agent edits update only its private in-memory file snapshot.
- Memory: identical setup plus one frozen recall from the existing daily-use
  project service, queried with the unchanged task prompt and a 2,048-token
  budget. Save its exact context, receipt reference and returned node IDs before
  model answers. No backfill, task-specific lesson or answer is added first.
- No memory writes during the trial. The controller can save a lesson afterward.
  This tests the existing integration, not a hypothetical richer corpus.

Use local Ollama `qwen3.5:35b-a3b`, temperature 0, thinking off, 32,768 context
tokens and 2,048 output tokens per action. Pin and recheck model digest, template
hash and server version before and after each arm. Two paired repeats use seeds
20260926 and 20260927. Order is control/memory, then memory/control. Each arm has
a fresh conversation and file snapshot, twelve actions, and explicit remaining
action counts. Invalid actions consume a step; provider failures are retained,
count as unsuccessful, and are not retried. Context exhaustion is explicit.

Code runs only in the existing unprivileged, networkless Docker sandbox, built
from the frozen revision and executed by immutable image ID. Only generated
target source and controller checks are mounted; no host checkout, memory pack,
credentials or Docker socket is exposed. Ollama stays on the host GPU.

## Evidence and production work

Primary success requires both renderers to pass all frozen checks. Record
paired outcomes, edits/read/search/test actions, tokens, elapsed time and provider
failures. Timing includes sandbox checks, excludes recall/setup, and is descriptive
only. Report all four runs; two repeats are still one independent ticket.
The task and equal source access may already make memory unnecessary. A null or
negative result remains useful evidence and must not trigger a tuned rerun.

Raw transcripts, generated files, contexts and full source hashes stay under
ignored `benchmarks/coding/runs/`. Track this protocol, runner, regression tests,
compact metrics, manifest, checksum index and human report. The prior pilot is
unchanged. Review the final production patch separately, run packing/receipt
regressions and the offline evidence gate, document rendering compatibility,
then record what the actual memory contributed. No retrieval defaults change.
