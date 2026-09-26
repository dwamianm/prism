# Local coding-memory development pilot

PRME context passed **2/8** repair runs; the control passed **0/8**. Both gains
were the same legacy-journal task, once in each repeat. This is a narrow result
on four authored development problems, not evidence of general coding gains.

## Setup

- Repository: `34b3d290635e` (full identity in the manifest).
- Local model: `qwen3.5:35b-a3b`, Q4_K_M,
  `3460ffeede5453ead027dbd2f821b12ad0aa3de54630971993babdb2165221f7`.
- Ollama: `0.34.3`; local GPU execution, no hosted model or paid API calls.
- Four authored regressions, two repeats, interleaved pairs, six actions per
  arm. Temperature 0, thinking disabled, 32,768 context tokens, 2,048 generated
  tokens per action. Both arms used the same seed within a pair.
- Memory: 98 exact passages from twelve existing repository documents, frozen
  before answers. Up to 2,048 tokens were retrieved once per task and reused.
- Both arms could search/read the same document set and mutated target module.
  Neither could read the gold function or hidden checks through agent tools.
- A constrained local agent performed read/search/edit/test actions. This was
  not a run of the full Codex or Claude Code products.
- Every code execution used a fresh, unprivileged, networkless Docker container.
  The experiment pack was separate from the daily-use repository memory.

The [protocol](../../../coding/PROTOCOL.md) was written before model answers.
All originals passed their regression checks and all mutations failed before
scoring. The [manifest](coding-memory-pilot-v1/manifest.json) includes the exact
Docker image identity and source/settings hashes.

## Results

| Task | Control, two repeats | PRME, two repeats |
|---|---:|---:|
| Metadata admission | 0/2 | 0/2 |
| Scope validation | 0/2 | 0/2 |
| Legacy journal serialization | 0/2 | 2/2 |
| Exact assertion normalization | 0/2 | 0/2 |
| Total | **0/8** | **2/8** |

| Cost measure | Control | PRME |
|---|---:|---:|
| Model input tokens, all eight runs | 155,059 | 203,700 |
| Model output tokens, all eight runs | 1,862 | 6,335 |
| Median agent elapsed time | 10.97 s | 13.32 s |
| Total agent elapsed time | 109.95 s | 185.08 s |
| Provider failures | 0 | 0 |

Memory added 31.4% model input tokens. Agent timing includes tool execution and
final grading, but excludes image build, corpus import, model warmup and memory
preparation. Recorded retrieval took 0.049–0.948 seconds per task. These small,
warm-cache timing measurements are not a deployment latency benchmark.

The paired outcomes were two gains and six both-fail pairs, with no losses.
The two gains repeat one problem and are not independent task-level evidence.
No significance or general superiority claim is made.

## What failed

- Metadata: control used all six actions reading. Memory produced a patch but
  failed the required `ValueError` contract for unsupported metadata objects.
- Scope: both arms spent their budget reading/searching and asking for source
  outside the allowed snapshot. Neither produced a repair. The restricted
  source set is a material harness limitation.
- Legacy snapshots: memory patches passed the frozen finite-byte and non-finite
  round-trip checks; control still emitted bare non-finite JSON constants.
  Passing these checks does not certify all legacy serializer behavior.
- Assertion normalization: control exhausted its budget reading. Memory edited,
  but incorrectly changed spaces in non-predicate fields to underscores and
  used weaker Unicode normalization.

The low control completion rate limits this pilot's usefulness. A next trial
should give both arms complete frozen source access and explicit remaining
action budgets, then use fresh tasks. This is a proposed follow-up, not a result
already measured. The present study measures documentation retrieval, not the
benefit of accumulated debugging sessions, handoffs or long-running agents.
No PRME retrieval defaults were changed and no generated repair was applied to
production source.

## Integration verification and artifacts

The repository now has a running local Docker memory service, six source-backed
bootstrap notes, and recall/capture instructions for Codex and Claude Code.
Rebuilding the container retained all six notes. Four concurrent clients
retrieved successfully with persisted receipts; rendered contexts stayed within
their requested 512-token budgets. The integration evidence is
[recorded here](coding-memory-pilot-v1/integration-verification.json).

Validation: 52 tests passed, 3 skipped across the new adapter/harness tests and
existing MCP/identity tests. Targeted mypy, Ruff lint/format and whitespace
checks passed. This change does not alter backend storage or retrieval logic.

[Summary](coding-memory-pilot-v1/summary.json),
[per-run results](coding-memory-pilot-v1/results.json), and
[artifact hashes](coding-memory-pilot-v1/SHA256SUMS.json) form the compact
version-control record, alongside the manifest and integration verification.
Complete prompts, responses, candidates, checks, retrieved contexts and corpus
receipts are retained locally in the ignored
`benchmarks/coding/runs/pilot-v1/` directory. The checksum index preserves the
original run filenames and covers both the compact record and local raw files.
Raw evidence is not included in a fresh clone; share it as a separate artifact
archive when needed. The original run and closed experiment pack also remain
in the Git common directory under `prme-coding/trials/pilot-v1b/`.

An earlier attempt stopped in preflight before model answers because Python
imported the installed wheel instead of the mutated source. The gate detected
that the mutation also passed. Import cache invalidation and an explicit source
path assertion corrected the harness. Its failed preflight and manifest are
retained locally as `benchmarks/coding/runs/pilot-v1/earlier-preflight-abort.json`,
covered by the checksum index; it contributes no scored runs. No task, model
setting or hidden check was changed to fix it.
