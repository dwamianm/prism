# Action-based memory evaluation: source audit and adapter preflight

The [MemoryArena paper](https://arxiv.org/abs/2602.16313) evaluates interdependent
sessions in which earlier actions and experience inform later tasks. This adds
an important test beyond PRME's current conversation-answer comparisons.
The [upstream audit](agentic-memory-upstream-audit.json) pins MemoryArena
`6cd9de14` and MemoryAgentBench `fe1735de` with hashes of inspected files.
Neither dataset nor a task agent was run in this audit.

## Implemented MemoryArena interface

`benchmarks.diagnostics.memoryarena_server` implements the pinned client's
`initialize`, `add` and `wrap_user_prompt` endpoints. Each initialization gets
fresh logical memory, including repeated initialization of the same external
task ID; previous events remain stored. Requests are serialized in this benchmark
adapter. It stores complete raw traces as NOTE records with external-document
provenance and no source-clock inference. It uses immediate indexing and refuses
to report successful addition while indexing remains pending.

Retrieval uses the actual default-density PRME bundle, with no source replacement
or text truncation by the adapter. The 4K default limit includes the memory wrapper;
the unchanged query is outside that budget. The adapter reserves wrapper headroom
and verifies the complete rendered block. It runs on loopback and requires a new
directory for each server run. This is a local evaluation adapter, not PRME's
authenticated production HTTP API, and does not add extraction or trace synthesis.

All seven adapter contract tests passed. The [native preflight](memoryarena-native-preflight-verification.json)
also passed using the **unmodified upstream client**, an installed `f9665fb` wheel,
real BGE retrieval and a loopback HTTP server. It verified source text, task
isolation, reset, memory budget, and retained external-trace provenance after
shutdown. The preflight process exited zero. The daemon exited **-15** following
the explicitly requested SIGTERM and complete application shutdown.

The [initial preflight](memoryarena-preflight-initial-shutdown-failure.json) is
retained as failed: its request checks passed, but it incorrectly required daemon
exit zero after sending SIGTERM. Inspection of installed Uvicorn showed that it
re-raises captured signals after graceful shutdown. The
[amendment](memoryarena-preflight-shutdown-amendment.json) was recorded before
the fresh complete preflight; no product code, request sequence or task score
changed. This amendment does not change the native-zero requirements of the
separate capture/reader/judge studies.

Reproduce with the pinned upstream checkout and an installed PRME package.
Run from a checkout containing these benchmark modules; they are not part of
the distributed PRME wheel:

```bash
python -m benchmarks.diagnostics.memoryarena_preflight \
  --upstream /path/to/MemoryArena --output fresh-preflight.json
```

Start a fresh adapter for a later registered study:

```bash
python -m benchmarks.diagnostics.memoryarena_server \
  --directory /path/to/new-study-pack --port 8008 --memory-tokens 4096
```

Configure the upstream client with `memory_system_name="prme"` and
`base_url="http://127.0.0.1:8008"`.

## Next study decision

The [travel setup](https://github.com/ZexueHe/MemoryArena/blob/6cd9de14b71915e39ac742a20dc33785e14b6aab/setup_travel.md)
is the first candidate: sequential travelers have linked constraints, and tools
use local tables. Its flight table requires a separate download. Before any
quality run, pin dataset and table hashes, verify the native evaluator, freeze
complete group selection, and match actor, prompts, context budget and allowed
feedback across memory arms. Preserve whole groups and per-person outcomes;
the authored adapter preflight is not a benchmark success rate.

The [authored native scorer audit](MEMORYARENA-TRAVEL-SCORER-AUDIT.md) found two
issues that must be addressed before interpreting a travel quality result:
missing travelers disappear from the denominator, and short matching prefixes
can receive full slot credit. Exact coverage validation rejects missing,
duplicate and unexpected submissions. The later
[strict scorer audit](../2026-09-17/MEMORYARENA-TRAVEL-STRICT-SCORER-V2.md)
also requires complete day/slot structure and full normalized-string equality;
the native metric remains available only as a secondary comparison.

The [formal reasoning runner](https://github.com/ZexueHe/MemoryArena/blob/6cd9de14b71915e39ac742a20dc33785e14b6aab/run_math.py)
requires an additional model judge. Its default no-judge-feedback path passes
no reward to `build_memory_entry`; the inspected builder stores task, solution
and tool trace. Freeze that setting and validate exact agent-visible payloads
before running. The search agent also has a character-based prompt truncation
path, so its final transmitted prompt needs separate verification.

[MemoryAgentBench](https://github.com/HUST-AI-HYZ/MemoryAgentBench/tree/fe1735de8cf8b9908e1e3d3b5612afc815698062)
adds incremental memory construction and conflict-resolution workloads. Its
current wrapper eagerly imports multiple model libraries and dispatches among
vendored implementations. It needs a separate environment and explicit PRME
integration; this audit did not execute those stacks. These integrations remain
development work and do not establish general memory-system leadership.
