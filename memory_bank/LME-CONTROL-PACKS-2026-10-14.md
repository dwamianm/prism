# LongMemEval-S control packs can be rebuilt from the master packs (2026-10-14)

This note records a finding made while preparing the single-file memory pack
prototype (issue #247): the LongMemEval-S control captures that the offline
evidence gate replays were missing from the current machine, and it turned out
they can be rebuilt from packs that are still here. Read this before searching
for the controls again or before building a fresh `store()` set.

## What the gate needs

The LongMemEval-S half of the evidence gate
(`_longmemeval_cases` in `benchmarks/diagnostics/product_packing.py`) reads each
saved context in `data/gpt54-comparison-v1/longmemeval/contexts/<qid>.json`,
follows its `source_capture` path to a `capture.json`, takes `user_id` and
`reference_time` from that capture's receipt, and replays the pack that sits
next to it. The saved contexts point at
`/Users/dwamianm/Sites/prism-opt-in-study-2026-09-22/data/opt-in-study/opt-in-successor-v2/baseline/<qid>/`,
the 2026-09-22 opt-in study's `baseline` arm. That directory is not on this
machine.

## Where we looked

The controls were not found in: the default AWS account's S3 (all 52 buckets,
72,281 objects, no key naming the study, no large or recent archive), the
bucket names of the other nine AWS profiles, Google Drive, Spotlight, external
volumes, Dropbox or CloudStorage, shell history, or any note in this repository.
Do not repeat that search; if the original directory is ever found, it only
adds the original study receipts, nothing the gate needs.

## The finding

The study's `baseline` arm never ran `store()`. In
`benchmarks/diagnostics/opt_in_arm_worker.py` the registered `baseline` arm has
no ingestion flags, so it is the `historical` stratum: its `capture()` clones the
2026-09-17 GPT-5.4 baseline pack for the question, checks the clone against the
saved identity, and runs the cold and warm retrievals. Only the `fresh_baseline`
arm ran `fresh_ingest()` (`store()` under `matched_admission()`).

Those master packs are on this machine, complete and verified:

- `data/benchmarks/longmemeval-s-prme-baseline-v1/packs/<qid>/` (500 packs,
  7.8 GiB, 246,738 stored turns), with the 2026-09-17 captures in
  `data/benchmarks/longmemeval-s-prme-baseline-v1/captures/` and the run's
  `identity.json`.
- All 500 packs match the tree identity recorded in their saved captures
  (checked read only on 2026-10-14).
- The saved 2026-09-23 contexts equal the master captures' own contexts for 500
  of 500 questions.

So the lost control packs were byte copies of these master packs, plus the two
retrieval receipts the study wrote into each copy.

## How to rebuild the controls

Clone each master pack, require the clone's tree identity to equal the master's
saved identity, run one retrieval with the current defaults, and write
`capture.json`. This takes about 20 minutes for all 500 questions and calls no
LLM: `store()` is not run, and the retrieval uses only the local query
embedding. A fresh `store()` build is not needed and would be worse, because its
node IDs and tie order would differ from the archive.

The regenerated set lives under a new root in ignored `data/opt-in-study/`
(never under a `v2` path), with a `manifest.json` that records, per question,
the master pack path and identity, the clone's identity before retrieval, the
final pack identity and the capture's SHA-256, plus the build date, commit and
library versions. The compact manifest and a short report are tracked under
`benchmarks/results/research/2026-10-14/`.

## What cannot be reproduced, and what is verified instead

- `artifact_checksum` in the saved contexts is the lost control's pack identity
  after the study's two retrievals wrote receipts into it. A clone cannot match
  it, before or after a new retrieval (0 of 500 by construction).
- `source_capture_sha256` is the hash of the lost `capture.json`, whose receipts
  carried request IDs and wall-clock times. A new capture cannot match it.

The replay therefore verifies the root manifest, each capture against the
manifest, each capture's input pack against the master identity (and against
the master's own saved capture when present), and each pack copy against the
manifest's final identity. It reports the two archive hashes as not applicable.
The gate option that does this, `--lme-captures <root>`, was added on branch
`feat/single-file-pack`; its provenance names the root and the manifest digest.

The proof of equivalence is a gate run with the pre-2026-09-25 defaults restored
through `--set` (the environment variables in `docs/PACKING.md`, "Default
retrieval settings"): it must reproduce the saved 2026-09-23 contexts for all
500 questions.

## Result of the 2026-10-14 rebuild

- Driver: `benchmarks/diagnostics/lme_control_captures.py`, which hands each
  verified master pack to the study's own capture function
  (`run_opt_in_interactions.capture`). 12 worker processes, 84 s in total.
- Root: `data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline/`
  (ignored), 11.24 GB logical (9.63 GB packs, 1.61 GB captures). The manifest
  copy and the report are tracked in `benchmarks/results/research/2026-10-14/`
  (`lme-control-captures-manifest.json`,
  `LME-CONTROL-CAPTURES-REGENERATED.md`).
- Proof: with the pre-2026-09-25 settings, the code from just before
  `538a2c6b` reproduces 500 of 500 saved contexts. `main` reproduces 499 of 500:
  since `538a2c6b` every context format escapes U+2028, and one stored turn of
  question `06db6396` holds that character raw in the saved context. The packs
  are not the cause (a fresh clone gives the same context on `main`).
- With the current defaults, `main` and branch `feat/single-file-pack` give
  identical `context_sha256` values for all 500 questions (445 of 470 annotated
  questions with all evidence packed, 94.7%).
- Still open: the DeepSeek answer track's `prepare prme` does not take
  `--lme-captures`, so it cannot prepare LongMemEval-S contexts on this machine
  until it learns the option.

## Consequence for the DeepSeek track

Because the stored records are byte-identical to the archive's packs, the
regenerated set is the same LongMemEval-S baseline under the epic #77 rules,
not a new one. Only the capture receipts are new, and the gate uses them for
`user_id` and `reference_time` alone.

## LoCoMo, for completeness

The LoCoMo half runs here already. Its packs are under
`data/gpt54-comparison-v1/locomo/packs/` and
`data/gpt54-comparison-v1/source-children/`, but `locomo/prepared.json` records
them under the sibling path `/Users/dwamianm/Sites/prism-locomo-baseline-2026-09-22/...`,
which `BENCHMARKS.md` expects beside the checkout. A symbolic link from that
sibling path to this checkout makes the gate resolve them; remove it afterwards
or keep it, either is fine.
