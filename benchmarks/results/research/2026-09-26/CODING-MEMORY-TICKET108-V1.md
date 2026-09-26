# Coding memory on real issue 108

The control passed **2/2** runs; PRME-assisted Qwen passed **0/2**. Both memory
runs produced the same syntax error and exhausted their action budget. The
current daily-use memory did not help on this ticket and added 32.9% input
tokens. Two repetitions of one selected bug do not establish a general effect.

## Registered setup

- Real [issue 108](https://github.com/dwamianm/prism/issues/108), with no authored
  mutation: escape Unicode line separators in auditable and compact contexts.
- Production input: `4dba69a1a2b14ab980c9ec60744036753f6d97e7`.
- [Protocol](../../../coding/TICKET-108-PROTOCOL.md), runner and checks committed
  as `17e16dea` before scored calls; the
  [issue checkpoint](https://github.com/dwamianm/prism/issues/108#issuecomment-5842794722)
  preceded the run. Both baseline failure and a passing private witness were
  verified in the networkless container before any model answer.
- Local Ollama `qwen3.5:35b-a3b`, Q4_K_M; manifest digest
  `3460ffeede5453ead027dbd2f821b12ad0aa3de54630971993babdb2165221f7`,
  server 0.34.3. Identity checked before and after every arm.
- Temperature 0, thinking disabled, 32,768 context, 2,048 output tokens/action,
  twelve actions with explicit remaining counts. Seeds 20260926 and 20260927;
  order control/memory, then memory/control. Fresh histories and edits each time.
- Both arms could list/read/search 586 frozen files: all Python source, existing
  Python tests, documentation and root instructions. Only the two affected
  renderer functions could be edited. Hidden acceptance checks and the witness
  were unavailable through agent tools.
- PRME arm: one unchanged-task recall, budget 2,048, reused for both repeats.
  The actual daily-use memory supplied eight records, with a persisted receipt
  (`3a5b0bd8-16f8-473a-b1e9-711f82c8655a`). Recall took 0.245 seconds. No
  backfill or task-specific lesson was added before or during scoring.
- Model code ran only in fresh unprivileged, networkless Docker containers.
  All sandbox Python sources matched the frozen snapshot. Tokenizer assets
  were cached in the sandbox image so budget checks worked offline. The
  immutable image ID, asset-bearing Dockerfile hash and all other identities
  are in the [manifest](coding-memory-ticket108-v1/manifest.json).

## Outcomes

| Measure | Control | PRME memory |
|---|---:|---:|
| All acceptance checks passed | 2/2 | 0/2 |
| Input tokens, both runs | 183,822 | 244,362 |
| Output tokens, both runs | 1,476 | 2,318 |
| Median elapsed seconds | 33.84 | 42.06 |
| Read actions, both runs | 14 | 16 |
| Edit actions, both runs | 4 | 6 |
| Public smoke-test actions | 2 | 2 |
| Provider failures | 0 | 0 |

Both control repairs escaped the separators and passed. Both memory repairs
inserted `ensure_ascii=False` twice in the compact renderer's `json.dumps`
call. The public smoke test exposed that syntax error. The final action edited
the other renderer instead, leaving the failure unresolved. There were two
paired losses and no gains; the candidate source hash was identical across
repeats within each arm. These are not two independent tasks. All four runs
are retained in [results](coding-memory-ticket108-v1/results.json) and the
[summary](coding-memory-ticket108-v1/summary.json).

Elapsed times include tool execution and final sandbox grading, exclude setup
and recall, and were measured on a shared development workstation. They are
descriptive, not controlled deployment latency estimates. No context-capacity
failure or provider retry occurred. No failed run was replaced or tuned.

## What the memory supplied

The returned records concerned artifact retention, metadata compatibility,
correction provenance, coding-memory handoffs, value bindings, workspace
isolation and retrieval-learning evidence. None was a prior lesson about this
renderer defect. This is a direct observation about the frozen bundle, not
proof that any particular note caused the coding error. Supplying that bundle
was the experimental difference; it increased prompt cost without a repair
gain here. The task itself was clear and both arms had the relevant source.

This result supports measuring relevance and corpus coverage before assuming
that automatic recall helps. A future, separately registered study could
compare existing memory with selective source-backed project/history backfill
on fresh tickets. This experiment did not test that backfill, accumulated
debugging episodes, full Codex/Claude agents, or unrestricted repository edits.
After recording the fix in memory, issue 108 must not be reused as untouched
evidence of learning.

## Production validation

The reviewed production patch (`538a2c6b`) applies the existing separator map
to both JSON record renderers and gives the shared map a format-neutral name.
Reader bytes and ordinary Unicode rendering remain unchanged. Sources and
persisted receipt checksums are untouched. Auditable/compact contexts containing
these separators gain escape bytes, potentially changing token budgets and
context hashes; [the compatibility note](../../../../docs/PACKING.md) documents
that boundary. No retrieval or format default changes.

Focused validation: **174 passed, 1 skipped** across the new contract/tool
checks, original trial tests, reader/context formatting, packing composition
and receipt compatibility. Ruff and whitespace checks passed. The skipped test
requires the PostgreSQL environment. Offline evidence comparisons are in
progress and will be recorded before this work is marked ready.

## Artifacts

Five compact evidence files are tracked beside this report: manifest, per-run
metrics, summary, preflight and a [checksum index](coding-memory-ticket108-v1/SHA256SUMS.json).
Complete source hashes, recall, transcripts and repairs remain in ignored
`benchmarks/coding/runs/ticket108-v1/`; the checksum index covers every file.
A fresh clone does not contain these raw artifacts. Gate reports/logs likewise
stay in ignored `benchmarks/coding/runs/ticket108-gates/`; compact comparisons
will accompany the completed validation.
