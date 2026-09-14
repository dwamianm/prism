# MELT lifecycle integration

Reviewed and implemented against
[MELT](https://github.com/shisa-ai/melt/tree/47c819f417b0d81a57f781ec54d8a5cf84e0c833)
at commit `47c819f417b0d81a57f781ec54d8a5cf84e0c833`. MELT is an independent,
deterministic benchmark for memory behavior across writes, corrections,
conflicts, time, consolidation, and retrieval.

`melt_sut.py` exposes PRME through MELT's JSONL subprocess protocol. Each reset
opens a fresh portable PRME pack under MELT's assigned state directory. Raw and
structured writes use `MemoryClient.store`; corrections use explicit
`supersede`, conflicts use explicit `contradict`, consolidation runs the scoped
organizer, and ordinary queries use the complete hybrid retrieval pipeline.
Evidence rows retain the MELT source ID, PRME node ID, content, lifecycle state,
and decay value.

## Run the lifecycle suite

Create a MELT configuration with an absolute PRME checkout path:

```toml
[run]
output_dir = "/absolute/path/to/melt-results"
store_case_io = true

[sut]
adapter = "shisad"
contract_version = "b2"
command = [
  "uv", "--directory", "/absolute/path/to/prism", "run", "python", "-m",
  "benchmarks.integrations.melt_sut",
]
capabilities = [
  "reset",
  "time_control",
  "consolidation",
  "query_as_of",
  "structured_memory_write",
  "answer_generation",
]
timeout_seconds = 60

[suite]
name = "lifecycle"
version = "lifecycle-v5"
fixture = "stress"
profile = "lifecycle-v5-core"
top_k = 12

[answer]
mode = "retrieval_only"
```

Then run from the pinned MELT checkout:

```sh
uv run melt run --config /absolute/path/to/prme-melt.toml \
  --runs 5 --seed-schedule 1103,2207,3301,4409,5501
```

Use MELT's smaller historical contract as an integration smoke before the
registered five-run profile:

```sh
uv run melt run --config /absolute/path/to/prme-melt.toml \
  --suite-version lifecycle-v4 --fixture smoke --score-profile lifecycle-v4-all \
  --runs 1 --seed 42 --top-k 3
```

The bridge accepts one optional SUT override, `min_score`, which is passed to
PRME retrieval. The default is `0.05`. Any result must record this value; tuning
it on the scored cohort turns that cohort into development data.

## Capability boundary

The bridge declares MELT B2. It does not claim B3 project sharing, revocation,
memory export, autonomous memory creation, or answer generation. Those
capabilities require different product and protocol surfaces and therefore
cannot receive a MELT scoped or native-answer score from this adapter.

PRME's unified `retrieve(knowledge_at=...)` is explicitly an ingestion cutoff
over current graph and index state, not historical lifecycle replay. For MELT
as-of probes, this bridge reads retained active and superseded nodes through the
public graph query, applies the structured write and supersession event times,
and ranks eligible nodes by deterministic lexical overlap. Reports must label
this as adapter-assisted B2 historical retrieval. It tests durable retention and
correction lineage, but does not establish exact historical replay in the normal
retrieval API.

MELT's own report, case checkpoints, fixture hash, score identity, SUT metadata,
PRME commit, and full case I/O are the evidence artifacts. Preserve all failed
cases and methodology warnings. A smoke run validates integration only; it is
not a comparative or leadership result.
