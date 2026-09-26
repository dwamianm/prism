# Coding memory for this repository

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

Recall the task at the start of substantive work. The default 2,048-token budget
applies to rendered context, not the optional JSON transport envelope. `--budget`
accepts 256–8192; `--json` includes metrics and context references. Queries use
project scope with cross-scope hints off. The CLI works in any shell-capable agent.

After a meaningful result, pass a concise record through stdin:

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
