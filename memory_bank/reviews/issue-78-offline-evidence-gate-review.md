# Code Review: issue-78-offline-evidence-gate

## Files Changed
- `benchmarks/diagnostics/product_packing.py`: new offline evidence gate section (`gate` and
  `gate-compare` subcommands). It loads the saved 2026-09-23 LoCoMo and LongMemEval-S questions
  from the local archive, verifies the archive manifest, captures and pack tree identities, replays
  the public `retrieve()` on a scratch copy of each pack, and reports context, evidence, rank and
  projection metrics. The existing `measure`, `compare` and legacy CLI are unchanged; `main()`
  dispatches the two new subcommands.
- `tests/test_product_packing_diagnostic.py`: gate tests (real replay on a small pack, config
  isolation, projection tables against the tracked diagnostics, summaries, Markdown, comparison,
  CLI, loaders).
- `BENCHMARKS.md`: new "Offline evidence gate" section documenting it as the first gate before any
  paid answer run, plus a pointer from "Product packing replay".

## Approach Summary
The issue asks to extend `benchmarks.diagnostics.product_packing` rather than write another packer.
The gate calls `MemoryEngine.retrieve()` with each question's recorded reference time and user,
reads `bundle.render()` and the persisted receipt, and counts evidence from the receipt's
`in_context` and `has_content` flags. That keeps the metrics independent of the context format, so
the gate still works after the reader-format ticket (#79). "All evidence packed" uses `in_context`,
which is the definition behind the saved baselines (`analyze_gpt54_evidence.py`,
`run_longmemeval_s_baseline._evidence_metrics`); evidence packed with its text is reported beside it.

Configuration is current defaults over each pack copy, built with no environment variables or
`.env` files, with opportunistic maintenance off. `--set` accepts dotted keys, rejects unknown keys,
and refuses storage, embedding, extraction, model-backed query features, organizer, API and MCP
settings.

Result at current defaults (full run, about 10 minutes): 1540/1540 LoCoMo and 500/500
LongMemEval-S saved contexts reproduced. LoCoMo 25.2 records per context, 28.8% memory text, 45/282
multi-hop with all evidence packed. LongMemEval-S 23.9 records, 32.0% memory text, 403/470.
Projected 64.0% and 86.0%, equal to the measured run.

Alternatives considered: extending `retrieval_eval` (rejected: it scores with its own whole-turn
packer, which is the proxy the issue describes); re-packing frozen candidates as the existing
comparator does (rejected: it cannot see ranking or candidate-generation changes); parsing the JSON
lines as the audit script does (rejected for accounting: the format is about to change); a separate
module (not adopted: the issue names this module, and the file hash change breaks no live
verification, see Wiring Findings).

## Must Fix (all resolved)
- **Wall-clock maintenance breaks reproduction after about 2026-09-30** (Agents 1 and 7). The first
  `retrieve()` per engine scheduled an opportunistic organizer pass that promotes records older than
  7 days, and `memory_lifecycle` is rendered into every record. Agent 1 measured 2/12 matches with the
  clock moved 8 days ahead. Resolved: `gate_config` turns `organizer.opportunistic_enabled` off and
  `organizer` is a fixed setting. Covered by the config and CLI tests; full rerun reproduced 2040/2040.
- **Nested settings still read `.env`** (Agents 1, 2 and 7). Only the top-level `PRMEConfig` got
  `_env_file=None`; a `.env` could switch the embedding provider to a paid one. Resolved: every
  nested `BaseSettings` is built with `_env_file=None` inside the cleared environment. Test writes a
  `.env` in a temporary working directory and checks the defaults hold.
- **A misspelled nested override was silently ignored** (Agents 4, 5 and 7). Resolved:
  `_check_override_keys` walks the config models and rejects unknown keys; `parse_overrides` also
  rejects a key set twice.
- **The saved inputs were not verified** (Agents 1, 4 and 7). Resolved: loaders check dataset
  checksums, `prepared.json` completeness, every saved context against the archive manifest, each
  LongMemEval-S capture against its recorded checksum, and each pack copy's tree identity against the
  identity the saved run recorded (LoCoMo excludes the `capture-manifest.json` written afterwards).
  Symlinks in a copy are refused.

## Should Fix (all resolved)
- Receipt checks split into separate messages naming the benchmark and question; added
  `receipt_persisted`, the token limit (budget minus overhead), and a two-way in-context check
  (Agents 1, 3, 4).
- Packed memory text must appear in the rendered context, so the text share cannot be met on paper
  (Agent 7).
- Provenance is recorded before the replay and a missing commit is refused; dataset checksums come
  from the verified constants instead of rehashing (Agents 1, 3, 5).
- `--output` must be a `.json` path; `gate-compare` refuses to overwrite an input (Agents 2, 3, 4, 6).
- `run_gate` rejects unknown or empty benchmark selections, including a bare string; empty reports
  are refused (Agents 1, 3).
- The comparison also rejects different archives, tokenizers, projection constants and baselines,
  checks per-question annotations, pairs "all evidence packed with memory text", records the
  bootstrap seed, notes that LoCoMo intervals are question-level over 10 conversations, and shows
  each side's reproduced-context count (Agents 1, 4, 7).
- Projection text now states that LongMemEval-S uses pooled rates (as the issue specifies), and the
  unscored rates are per category, so LoCoMo category projections equal the measured rates at
  baseline (Agents 1, 4, 5).
- A test rebuilds every projection constant from the tracked
  `gpt54-posthoc-evidence-diagnostics.json` (Agents 3, 4, 5).
- The default archive is the main checkout's, so worktrees work; a missing archive or pack gives a
  clear message (Agents 3, 6).
- Structlog output is filtered to warnings and model downloads are disabled for the CLI run
  (Agents 3, 4, 6).
- Scratch cleanup covers a failed clone; the contiguity check runs before any replay; an existing
  scratch path is refused (Agents 2, 3).
- Tests added for the CLI end to end, failure branches, Markdown branches and loaders (Agent 3).

## Consider (acknowledged, not changed)
- **Move the gate to its own module to keep `product_packing.py`'s hash stable** (Agents 3, 6).
  Not adopted. The issue asks to extend this module. The only plan pinning the old hash, the
  2026-09-12 product-packing confirmation, is complete and already needs its own commit to verify;
  other registrations that pin the file already fail on other source files.
- **Supervised worker with a native exit-code boundary** (Agent 4). Not adopted: a report is written
  only after every question succeeds, so a partial report cannot exist. The audit recommends keeping
  checksum chains for release claims, not development probes.
- **Cold and warm determinism check per question** (Agent 4). Not adopted: reproducing every saved
  context at defaults is a stronger determinism check, and LoCoMo packs must replay in order.
- **Derive LoCoMo resolvability from pack nodes instead of `source_turns`** (Agent 1). Accepted: the
  harness function is frozen by the GPT-5.4 registration, and the drift test guards the totals.
- **Redact home paths in provenance, `--` in the clone helper, question-id filename checks**
  (Agent 2). Not adopted: paths come from checksum-verified datasets and archives, and the clone
  helper is shared by more than ten diagnostics.

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Secrets in provenance | PASS after fix | `.env` no longer reaches nested settings; keys are `SecretStr` and `redact` hides `*_key`; API and MCP overrides are refused. |
| Benchmark text in reports | PASS | Rows hold ids, hashes and counts only; captures are opt-in and documented as containing benchmark text. |
| No paid calls | PASS after fix, with a follow-up | Embedding, extraction, reformulation and temporal relations are fixed; config is built without environment or `.env`; model downloads are off. Future model-backed features: follow-up. |
| Path traversal and injection | PASS | Ids come from checksum-verified datasets; the clone helper passes a list with no shell. |
| Destructive filesystem operations | PASS | `rmtree` only targets `scratch/pack-NNNNN` inside a temporary directory; copies with symlinks are refused. |
| Writes into the saved archive | PASS | Packs are copied before opening; the source pack identity is unchanged after replay (tested). |
| Credentials in code or tests | PASS | None. |

## Pattern Consistency Assessment
The replay follows `run_gpt54_comparison.prepare_locomo` and `run_opt_in_interactions.capture`:
receipt persisted, no backend failures, ranking replay, exact token count within the budget, and
identity checks on inputs. It reuses `_clone_pack`, `_tree_identity`, `paired_statistics`,
`retrieval_eval.provenance`, `question_rows` and `source_turns`. It turns maintenance off as about 18
other replays do. The comparison applies the same kind of input checks as
`compare_evidence.compare`.

## Redundancy Check
No new dependencies. Reuse over new code where it fits; the new `_source_key` and `_turn_key` exist
because the existing LongMemEval helpers use tuple keys and do not cover LoCoMo dialog ids. The
hard-coded projection tables are kept for readability and pinned by a test against the tracked
diagnostics file. The test helper that hashed pack trees was replaced by `pack_identity`.

## Wiring Findings
Documented commands work as written; CI picks up the tests (they use a mock embedding provider and
no local data). On Linux, `cp -cR` fails before creating the destination and the helper falls back to
`copytree`. Importers of `measure` are unaffected: the GPT-5.4 harness is loaded lazily. Ruff passes.
The full suite passed before and after the review fixes.

## Break Scenarios (adversarial)
Pre-mortem headline (Agent 7): "A week after the packs were built, the organizer woke up inside the
replay: the gate stopped reproducing its own baseline and disagreed with itself between runs, while
store-time 'changes' measured as exact ties."

| # | Scenario | Introduced | Likelihood | Impact | Verdict | Reasoning |
|---|---|---|---|---|---|---|
| 1 | Wall-clock maintenance promotes records during LoCoMo replays after 2026-09-30 | New | High | Medium | Fix now | Certain after the date; one setting fixes it. Done and verified. |
| 2 | Overrides that do nothing (typos, store-time settings) are accepted silently | New | Medium-High | Medium-High | Fix now, plus Follow-up | Unknown keys are rejected, the summary warns when overrides change nothing, and the docs say store-time changes need new packs. Measuring store-time changes needs a re-ingestion mode: #102. |
| 3 | The baseline packs live in two removable research worktrees and were not verified | New dependency | Medium | High | Fix now, plus Follow-up | Captures and pack identities are verified and the docs name the paths to keep. Moving the packs into the archive (about 10 GB) is an owner decision: #103. |
| 4 | Comparison headlines credit pointer-only records | New | Medium | Low-Medium | Fix now | Paired "all evidence packed with memory text" added; the in-context metric stays because the baselines use it. |
| 5 | Comparison pairs reports with different projection constants, archives or tokenizers | New | Low-Medium | Medium | Fix now | All are now rejected, and each side's reproduced count is shown. |
| 6 | Memory-text share is counted from fields, not the context | New | Low-Medium | Medium | Fix now | Packed text must appear in the rendered context or the replay fails. |
| 7 | A future model-backed retrieval feature makes paid calls during replay | New | Low | High | Follow-up | Needs a network-level guard or a registration rule for model-backed options: #104. |
| 8 | `.env` reaches nested settings | New | Low-Medium | Low-Medium | Fix now | Nested settings are built without `.env`. |

Tally: Fix now 6 (two with follow-ups for the remainder), Follow-up 1 standalone (3 issues raised),
Accept 0, Dismissed 0.

## Follow-ups Raised
- #102: The evidence gate cannot measure changes that act when memories are stored.
- #103: The evidence gate baseline packs live in two research worktrees that can be removed.
- #104: A future retrieval feature that calls a model could make the evidence gate spend money.

## Resolution Status
| Finding | Source | Status |
|---|---|---|
| Wall-clock maintenance during replay | Agents 1, 7 | Resolved |
| Nested settings read `.env` | Agents 1, 2, 7 | Resolved |
| Unknown nested overrides ignored | Agents 4, 5, 7 | Resolved |
| Saved inputs not verified | Agents 1, 4, 7 | Resolved (move of packs: #103) |
| Receipt checks and messages | Agents 1, 3, 4 | Resolved |
| Memory text must be in the context | Agent 7 | Resolved |
| Provenance timing and missing commit | Agents 1, 3, 5 | Resolved |
| `.md` output overwrote JSON; comparison overwriting inputs | Agents 2, 3, 4, 6 | Resolved |
| Empty or unknown benchmark selection | Agents 1, 3 | Resolved |
| Comparison input checks, with-text metric, interval note | Agents 1, 4, 7 | Resolved |
| Projection description and per-category unscored rates | Agents 1, 4, 5 | Resolved |
| Projection constants drift test | Agents 3, 4, 5 | Resolved |
| Default archive in worktrees; docs name pack locations | Agent 6 | Resolved |
| Log noise and offline model assets | Agents 3, 4, 6 | Resolved |
| Scratch cleanup and ordering checks | Agents 2, 3 | Resolved |
| Missing tests for CLI and failure branches | Agent 3 | Resolved |
| Store-time changes cannot be measured | Agent 7 | Follow-up #102 |
| Packs in removable worktrees | Agent 7 | Follow-up #103 |
| Paid-call guard for future features | Agent 7 | Follow-up #104 |
| Separate module, supervised worker, cold/warm check, path hardening | Agents 2, 3, 4, 6 | Not adopted (see Consider) |
