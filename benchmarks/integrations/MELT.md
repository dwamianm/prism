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

## Run the registered lifecycle suite

Use a clean PRME worktree at the revision named in the registration and a clean
checkout of the pinned MELT commit. The launcher creates the exact configuration
and writes a source-attestation manifest before MELT creates any result:

```shell
uv run python -m benchmarks.integrations.run_melt \
  /absolute/path/to/melt /absolute/path/to/new-melt-results \
  --registration benchmarks/results/research/2026-09-14/melt-lifecycle-v5-core-v1-registration.json
```

The registered protocol uses MELT's `held_out` split, `stress` fixture,
`lifecycle-v5-core` score profile, top-k 12, and the official five-seed schedule.
The split is explicit because MELT defaults to `dev`; a five-run dev report is
still preliminary.

After completion, validate the summary through MELT's own report loader and the
independent source, protocol, run, case, and checkpoint checks:

```shell
uv run python -m benchmarks.integrations.validate_melt \
  /absolute/path/to/new-melt-results/lifecycle_prme_*/summary.json \
  --registration benchmarks/results/research/2026-09-14/melt-lifecycle-v5-core-v1-registration.json \
  --upstream-root /absolute/path/to/melt \
  --output /absolute/path/to/melt-validation.json
```

Use MELT's smaller historical contract as an integration smoke before the
registered five-run profile:

```shell
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
