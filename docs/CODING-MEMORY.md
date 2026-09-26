# Coding memory for this repository

Routine recall/capture is suspended and integration expansion is frozen pending
the [final bounded study](../benchmarks/coding/FINAL-PROTOCOL.md). The commands
below remain available for explicit manual use; installing the service does
not require agents to query or write it during ordinary coding tasks.

Local agents share one PRME MCP service, with local FastEmbed embeddings.
Direct capture makes no LLM call. Agents explicitly record decisions, lessons
and checkpoints; the adapter does not ingest terminal history automatically.

## Start and use

With Docker running, from this checkout:

```bash
uv run python -m prme.integrations.coding init
uv run python -m prme.integrations.coding start
uv run python -m prme.integrations.coding recall "repair metadata admission"
```

Initialization writes a random bearer credential and Compose configuration to
the Git common directory's `prme-coding/` folder with private permissions.
The service binds `127.0.0.1:18765`; choose `init --port PORT` for another repo.
Repeating init retains existing settings. Worktrees share a service; independent
clones have separate Docker volumes. Run start again to rebuild after updates.
No global Codex or Claude configuration is edited.

For an explicitly requested memory-assisted task, recall its description. The default 2,048-token budget
applies to rendered context, not the optional JSON transport envelope. `--budget`
accepts 256–8192; `--json` includes metrics and context references. Queries use
project scope with cross-scope hints off. The CLI works in any shell-capable agent.

For an explicitly requested capture, pass a concise record through stdin:

```bash
uv run python -m prme.integrations.coding remember \
  --kind lesson --status observed \
  --source docs/METADATA.md:1-19 <<'NOTE'
New metadata must be copied before waiting for storage locks. Admission rejects
non-finite values; legacy journal handling follows a separate compatibility path.
NOTE
```

Kinds: `decision`, `lesson`, `handoff`. Status is a caller report: `observed`,
`hypothesis`, `completed`, `incomplete`. Record actual test commands/outcomes;
distinguish attempts from fixes. `--session` connects related checkpoints.
Sources must be tracked files within the repository, optionally with line ranges.
Exact current excerpts and hashes stay in the immutable event. The concise note,
paths and commit enter retrieval. Metadata includes branch and tracked-file dirty
state; a dirty excerpt is not asserted to equal HEAD. This does not prove the
source entails the note or that a reported test was run.

Memory is historical evidence; current instructions, code and tests remain
authoritative. Revalidate relevant files. `archive NODE_ID` retires a stale record
from normal retrieval; MCP `memory_supersede` supports explicit replacements.
Captures are append-only and not automatically deduplicated. Opportunistic
organization is disabled for this service.

`stop` retains the memory and embedding-cache volumes. First startup downloads
the embedding model. Multiple agents connect to the service instead of opening
DuckDB in separate processes. Local Docker/credential access remains trusted.

## Experiments

The [protocol](../benchmarks/coding/PROTOCOL.md) uses a separate memory container
and fresh networkless containers for generated code. Ollama/controller run on
the host. Trial edits cannot change this checkout or the daily-use memory pack.

```bash
docker build -f docker/coding-memory.Dockerfile --target sandbox \
  -t prme-coding-sandbox:dev .
uv run python -m benchmarks.coding.run \
  --output benchmarks/coding/runs/pilot-v2
```

Use a new output directory per trial. Existing results are not overwritten or
silently resumed. Records include Git/image/model identities and per-arm evidence.
The runner checks original/mutated implementations before answering. Two repeats
cover four authored repairs: development evidence, not an untouched benchmark
or a claim about full Codex/Claude performance.

Raw runs belong in `benchmarks/coding/runs/`, which Git ignores. Keep model
transcripts, generated repairs, retrieved context, corpus copies, receipts,
logs and memory packs there rather than copying them into tracked results.
For a completed experiment, retain only the human-readable report, compact
manifest, summary, per-run metrics, integration verification and an artifact
checksum index under `benchmarks/results/`. The checksum index can cover local
raw evidence without committing that evidence. Share a separate artifact archive
when someone needs the full run; a fresh clone contains only the compact record.

The [first completed pilot](../benchmarks/results/research/2026-09-25/CODING-MEMORY-PILOT-V1.md)
passed 2/8 runs with memory versus 0/8 without it. Both gains repeated the same
legacy serialization task; input tokens increased by 31.4%. This remains an
opt-in development integration, with no claim of general coding improvement.

The [real-ticket follow-up on issue 108](../benchmarks/results/research/2026-09-26/CODING-MEMORY-TICKET108-V1.md)
used the existing daily-use memory, complete frozen source/existing-test access
and twelve actions. Control passed 2/2 repairs; memory passed 0/2 and added
32.9% input tokens. Both memory runs repeated the same syntax error. This is
one development ticket, not a general conclusion; it shows why relevance and
repair outcomes need measurement. No project-history backfill was tested.

Its [protocol](../benchmarks/coding/TICKET-108-PROTOCOL.md) pins the original
buggy revision. To reproduce the trial, build its frozen sandbox, with the
tokenizer cache needed for offline packing checks:

```bash
trial_context=$(mktemp -d)
git archive 4dba69a1a2b14ab980c9ec60744036753f6d97e7 | tar -x -C "$trial_context"
cp docker/coding-memory.Dockerfile "$trial_context/docker/coding-memory.Dockerfile"
docker build -f "$trial_context/docker/coding-memory.Dockerfile" --target sandbox \
  -t prme-coding-ticket108:base "$trial_context"
uv run python -m benchmarks.coding.ticket108 \
  --output benchmarks/coding/runs/ticket108-new-run
```

The runner verifies that the sandbox source matches the pinned revision. Each
invocation reads the current project memory; later invocations do not recreate
the original memory arm, whose exact recall is retained in the raw artifacts.
In particular, a memory containing the completed repair makes this ticket
unsuitable as a fresh test of learning. Repeats remain explicit runs; an existing
output directory cannot be overwritten.
