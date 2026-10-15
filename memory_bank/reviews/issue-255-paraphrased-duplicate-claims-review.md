# Code Review: issue-255-paraphrased-duplicate-claims

## Review Scope
- Base: `main` at `58caddb1a88dd60d51fc97c15742aee6ec8cf34a`.
- First review: snapshot `c1cd2f85b78915207bdbbad1a8a70b0dc3f499c2d7b7e4b965438b74c299fade` (head `777bba6c` plus
  the BENCHMARKS.md section and the results folder), tier M from `housekeeping.py review-plan --base main`:
  predicates DATA, INFRA, PERSISTED_FIELD and SECURITY false, NEW_SYMBOLS true.
- Semantic risk check: no flag added. The diagnostic changes nothing in `src/prme`, adds no schema, field or
  durable write, and reads packs only from verified scratch clones with read-only DuckDB connections. SQL uses
  placeholders; the interpolated parts are `?` lists built from enum constants. Inputs are local JSON files.
- Roster: core reviewer (checklists 1, 3, 4 and 5 merged; Sonnet) and adversarial reviewer (checklist 8; Opus).
  Checklists 2, 6 and 7 were not applicable; the core reviewer screened basic security and wiring and found no
  risk needing a dedicated reviewer.
- Revalidation: snapshot `9922775d6bd7fc2ec942757474588d18c2d36a67aa0246a224055b4a1a7acdde` (head `8d3555ff`), core
  checklists over the fixes and the closure of the adversarial scenario (Sonnet). Its should-fix and three of its
  considerations were applied in `c94e5bc9` and checked by the module's tests (15 passed) and by reading.

## Files Changed
- `benchmarks/diagnostics/paraphrased_claims.py` (new): the offline diagnostic. `pairs` clones each complete
  pack of a build, verifies it against its manifest, reads current claims, their HAS_FACT subjects and their
  stored vectors, keeps pairs at a cosine of 0.90 or more, and draws the registered labeling sample (seed 255,
  17/17/16). `replay` runs `product_packing.run_gate` with a temporary capture directory and keeps each context's
  in-context records and token costs from its receipt. `report` checks the labels and applies the #255 rule as
  `READING` reads it, with five other readings and a sampling-error section.
- `tests/test_paraphrased_claims_diagnostic.py` (new): 15 tests, including one that builds a pack through
  `ingest()` with a scripted extractor and topic embeddings and runs all three steps.
- `BENCHMARKS.md`: a "Paraphrased duplicate claims (#255)" section after the folding section.
- `benchmarks/results/research/2026-10-15/`: `PARAPHRASED-DUPLICATE-CLAIMS.md` (note), `paraphrased-claims-locomo.json`
  and `.md` (report), `paraphrased-claims-locomo-labels.json` (the 50 labeled pairs).

## Approach Summary
A measurement with a decision rule fixed in the issue. The labeling rule and the reading of the rule were
committed at `d9a4c94e` before any current pack was read; later commits add descriptive counts, provenance and
guards and do not touch them (checked by both reviewers with `git diff d9a4c94e`).

Alternatives considered (Phase 2, step 9a):
- Embedding the claim texts again with fastembed: rejected. Each pack stores the vector of the node's own
  content (`pipeline.py:959` indexes `fact_content`; `vector_index.py:133` and `:509-522` keep the payload in
  `vector_payloads`), which is what retrieval compares, and reading it needs no model.
- A callback inside the gate's `_replay_case`: rejected. `capture_dir` already writes each question's receipt,
  whose candidates carry `in_context` and the packer's `token_cost` (`models/relevance.py:622`), and
  `_check_replay` already checks that the receipt's in-context set equals the packed bundle
  (`product_packing.py:733-735`). The gate stays unchanged.
- Reimplementing retrieval or packing: rejected by the task.

Shared state: none written. Packs are read from scratch clones that `_verify_copy` checks against the manifest;
the build folder is only read; captures and clones live in temporary directories.

## Must Fix
None from any reviewer.

## Should Fix
| # | Finding | Source | Status |
|---|---|---|---|
| S1 | `check_labels` trusted the sheet's own seed and sizes, so a redrawn sample passed; seed and sizes were not reported | Core 1, adversarial scenario 1 | Resolved in `8d3555ff`: the registered seed and sizes are required and recorded in the report |
| S2 | `code_state` ignored a missing commit | Core 2 | Resolved in `8d3555ff`: raises outside a git checkout |
| S3 | The note's 1,487 identical same-turn pairs were not verifiable from tracked files | Core 3 | Resolved in `8d3555ff`: `identical_same_source_at_or_above` in the report |
| S4 | Prose overstatements: "already folds", abridged labeling rule | Core 4 | Resolved: the note quotes the rule verbatim and states the 0.2% leftover; BENCHMARKS.md says 12 of 13 |
| S5 | `_share` and `_pct` duplicated the gate's helpers | Core 5 | Resolved in `8d3555ff`: imported from `product_packing` |
| S6 | `report_main` did not use `_write_report` and could overwrite an input | Core 6 | Resolved in `8d3555ff`; outputs use `_json_output` |
| S7 | The headline said no reading changed the decision even when one could not be estimated | Revalidation 1 | Resolved in `c94e5bc9`: such readings are named beside the decision |

## Consider
| # | Finding | Source | Status |
|---|---|---|---|
| C1 | Zero-token context divided by zero; empty question list gave an opaque error | Core 7 | Resolved in `8d3555ff` |
| C2 | A missing band rate silently read as "close" | Core 8, adversarial scenario 1 | Resolved in `8d3555ff`: `decide` refuses; other readings become "undetermined" |
| C3 | Point estimates from 13 to 17 labels per band, no interval | Core 9 | Resolved in `8d3555ff`: a sampling-error section (Wilson limits; condition 1 at 2.2% and 6.4%). It showed the reviewer's estimate of 4.5% was low, so the note now says plainly that 50 labels cannot exclude either threshold |
| C4 | Per-band (exclusive) token shares not reported | Core 10 | No action: the issue's thresholds are "at or above", and exclusive shares follow by subtraction |
| C5 | `SELECT *` with positional row conversion | Core 11 | Comment added; the graph store reads rows the same way |
| C6 | Third copy of the progress printer; `_built_cases` read twice | Core 12, 13 | Accepted: pre-existing pattern, harmless |
| C7 | Descriptive extras grew the surface | Core 14 | Kept: they carry the composition result and do not enter the decision |
| C8 | `pairs` or `replay` output could overwrite a labeled sheet | Revalidation 2 | Resolved in `c94e5bc9` |
| C9 | "Most" folded and a Wilson limit on a stratified sample | Revalidation 3, 4 | Resolved: wording in the note |
| C10 | `sheet["pairs"]` read without a default; defaults passed explicitly | Revalidation 5 | Resolved in `c94e5bc9` |
| C11 | One test retired a claim through two filters at once | Revalidation 6 | Resolved in `c94e5bc9`: each filter is checked on its own |

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Secrets or PII in output | PASS | Reports hold public LoCoMo text and node IDs; captures with benchmark text stay in a temporary directory |
| Injection | PASS | SQL placeholders only; `?` lists come from enum constants |
| Path handling | PASS | Paths are operator-supplied CLI arguments; clones go to temporary directories; outputs refuse to overwrite inputs or a labeled sheet |
| Unsafe deserialization | PASS | JSON only |
| Network | PASS | No model or network calls; the gate loads the local query embedding offline |

## Pattern Consistency Assessment
The module follows `extracted_packs.py` and the gate: `main(argv)` with subcommands and `prog=`, `kind` and
`schema_version` envelopes, the gate's `provenance` fields, capture digest checks, `_json_output` and
`_write_report`. Tests follow `tests/test_extracted_packs.py` (a LoCoMo fixture, a scripted extractor, a patched
embedding provider, `build_unit`). Deviations found by the core reviewer were fixed (S5, S6).

## Redundancy Check
`_share`, `_pct` and the report writer now come from the gate. The canonical-JSON `digest` helper matches the
local convention of other diagnostics. No unused functions or constants remain (ruff clean).

## Wiring Findings
The CLI is reachable as `python -m benchmarks.diagnostics.paraphrased_claims`, as BENCHMARKS.md and the note
document. The test file is collected by `pytest tests/`. Nothing else needs registering.

## Data Safety and Read/Write Proof
Not applicable: no new durable field, schema or migration, and no write to a pack or the build folder.
`read_pack` opens scratch clones read-only; `run_gate` replays on its own scratch copies.

## Break Scenarios (adversarial)
| # | Scenario | Introduced | Likelihood | Verdict | Reasoning |
|---|---|---|---|---|---|
| 1 | The report accepts a sheet drawn with any seed or sizes; a band with no labels resolves to "close" | New | Low | Fix now | Cheap, and the guard's stated purpose required it. Fixed in `8d3555ff` (S1, C2) |
| 2 | A full LongMemEval-S run keeps every pack's vectors and a quadratic set in memory | New | Low | Accept | Fails loudly, no wrong result, and no current LongMemEval-S `ingest()` pack exists to run it on. Revisit if LongMemEval-S is measured |

Pre-mortem headline (adversarial reviewer, verbatim): "No plausible incident. The closest is "#255 labeling sheet
re-seeded; report accepted it without showing the seed"."

## Follow-ups Raised
None. The decision rule closes the issue with the numbers, and scenario 2 was accepted.

## Resolution Status
| Item | Status |
|---|---|
| Must Fix | None raised |
| Should Fix S1 to S7 | Resolved |
| Consider C1 to C11 | Resolved, or recorded with reasons (C4, C6, C7) |
| Adversarial scenario 1 | Fix now, resolved |
| Adversarial scenario 2 | Accepted |
| Tests | Module: 15 passed. Full suite with the CI extras: 1 failure, `tests/test_checkout.py::TestMainCheckout::test_a_plain_clone_resolves_to_itself`, which expects a plain clone and fails in any git worktree; unrelated to this change |
