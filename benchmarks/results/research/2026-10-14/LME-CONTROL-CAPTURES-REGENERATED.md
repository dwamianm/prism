# Regenerated LongMemEval-S control captures (2026-10-14)

The offline evidence gate's LongMemEval-S half replays the control captures
and packs of the 2026-09-22 opt-in study's `baseline` arm, which each saved
2026-09-23 context names. Those captures are not on the current machine, so
they were regenerated. `BENCHMARKS.md` ("Regenerated LongMemEval-S controls")
explains how to replay them with `gate --lme-captures`.

## What the controls were

The `baseline` arm belongs to the study's historical stratum and never stored
anything. For each question it cloned the 2026-09-17 master pack from
`data/benchmarks/longmemeval-s-prme-baseline-v1/packs/<question>` (the GPT-5.4
baseline's `store()` pack), required the clone to equal the master's saved
identity, and ran one cold and one warm `retrieve()`
(`benchmarks.diagnostics.run_opt_in_interactions.capture`). On 2026-10-14 all
500 master packs matched the tree identities that their saved captures record.

## Build

| Item | Value |
|---|---|
| Driver | `benchmarks/diagnostics/lme_control_captures.py` (sha256 `b4b6300841053a723871f06e24465b5034164757ca341dbb480d7c031f7fb10f`), which calls the study's own capture function |
| Code | `main` at `5e4c3ae6`, with the driver and the gate option applied as a patch |
| Retrieval settings | The gate's: the current defaults, organizer maintenance off, no environment or `.env` values (configuration sha256 `2f72f478e4a599fa4078e123e1419ca6900969d210bf2b375431c0ff1af46c1b`) |
| Model calls | None. No `store()` or `ingest()`, every outbound connection refused in the workers, only the local query embedding (`BAAI/bge-small-en-v1.5`, 384 dimensions) |
| Workers and time | 12 processes on an 18-core machine; 56 seconds capturing, 84 seconds in all |
| Questions | 500, holding the masters' 246,738 stored turns unchanged |
| Bytes | 9,633,223,374 in packs and 1,611,647,266 in captures, 11,244,870,640 in all (the packs are copy-on-write clones of the masters) |
| Root (ignored) | `data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline/<question>/`, each with `pack/` and `capture.json` |
| Manifest | The root's `manifest.json`, sha256 `0103b9509a35cc8cb79614c3e6f3def4a8216f8528da4e7533867b1b37e6bb4f`; [`lme-control-captures-manifest.json`](lme-control-captures-manifest.json) here is a byte copy |
| Versions | Python 3.11.6, prme 0.14.2, duckdb 1.4.4, usearch 2.26.2, tantivy 0.25.1, fastembed 0.7.4, onnxruntime 1.24.2 |

The manifest records, for each question, the capture's sha256, the final
pack's tree identity, and the master pack's identity and saved capture digest.
Each clone matched its master before retrieval, and the manifest step hashed
every master and final pack again.

The saved contexts' `source_capture_sha256` (the digest of the lost capture
file) and `artifact_checksum` (the lost control pack's identity after the
study's own two retrievals wrote their receipts) cannot match a regenerated
capture. The gate therefore checks instead that each capture's input pack
equals its master pack's saved identity, which the study required of every
control clone, and that each capture and pack copy equals the manifest.

## Equivalence: the settings before 2026-09-25

Both runs replay the regenerated controls with `scoring.fusion="weighted"`,
`packing.context_format="auditable"` and `packing.multipath_ordering="balanced"`;
`main` also takes `packing.fold_repeated_text=false`, which the older code does
not have. The older code has no `--lme-captures`, so a small script ran its own
gate with each saved context's capture path, capture digest and pack identity
pointed at the regenerated root, after checking them against the manifest.

| Code | Saved 2026-09-23 contexts reproduced | Records per context | Memory text share | All evidence packed | Projected accuracy |
|---|---:|---:|---:|---:|---:|
| `17e16dea`, the parent of `538a2c6b` (a `git archive` export) | **500 of 500** | 23.9 | 32.0% | 403 of 470 | 86.0% |
| `main` at `5e4c3ae6` with the patch | **499 of 500** | 23.9 | 32.0% | 403 of 470 | 86.0% |

The one difference on `main` is question `06db6396`. One of its stored turns
contains U+2028, which every context format escapes since `538a2c6b`
(2026-09-25, "escape Unicode record separators in every context format"),
while the saved context holds it raw. A fresh clone of that master pack gives
the same context on `main`, so the difference comes from the code and not from
the controls. Both rows match the previous-defaults column in `BENCHMARKS.md`.
The stored records are byte-identical to the originals', so under the epic #77
rules these controls are the same LongMemEval-S baseline, not a new one.

## Gate on `main` and on `feat/single-file-pack` (current defaults)

| Side | Code | Records per context | Memory text share | All evidence packed | Projected accuracy | Retrieval p50 / p95 |
|---|---|---:|---:|---:|---:|---:|
| Before | `main` at `5e4c3ae6` with the patch | 39.9 | 82.6% | 445 of 470 (94.7%) | 91.4% | 0.183 / 0.280 s |
| After | `feat/single-file-pack` at `d73b041d` with uncommitted working-tree changes, `src/prme/` included (worktree digest `729140c121b1`) | 39.9 | 82.6% | 445 of 470 (94.7%) | 91.4% | 0.177 / 0.233 s |

All 500 contexts have the same `context_sha256` on both sides, and
`gate-compare` shows no win or loss on any metric or category. Both sides match
the current-defaults column in `BENCHMARKS.md`. No saved context matches under
the current defaults, by design. The machine was shared during both runs (load
average 5 to 8), so the latency is not comparable.

## Replay

```sh
uv run python -m benchmarks.diagnostics.product_packing gate --benchmark longmemeval \
  --lme-captures data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline \
  --output /tmp/gate-lme.json
```
