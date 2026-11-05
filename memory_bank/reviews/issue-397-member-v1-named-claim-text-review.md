# Review: issue-397-member-v1-named-claim-text

## Review Scope

- Issue: dwamianm/prism#397 (OM-03), base `main` at `1cf42f95`.
- Reviewed snapshot: `4410cf83b3aadbe8313516a41608e890af70569473a5c29739da9da21fe02332` (the rebased commit `255523bb` plus the gate docs). Fixes re-reviewed on snapshot `c392176b7902ac10d563e38e5f4895d776a35022e6f22e4550dc674dbd7800ec`.
- Tier L. Predicates: SECURITY (detected), DATA and PERSISTED_FIELD (raised: `metadata.named_text` is a new durable metadata key), NEW_SYMBOLS. INFRA not applicable.
- Roster, under the light review budget the owner chose for this project (two reviewers per issue): core reviewer (checklists 1, 3, 4, 5, with security 2 and data safety 7 merged into it; Opus) and adversarial reviewer (checklist 8; Opus). Infrastructure (6) not applicable. No checklist was skipped; 2 and 7 are recorded in the core report's own sections.

## Files Changed

AGENTS.md, BENCHMARKS.md, CHANGELOG.md, CLAUDE.md, benchmarks/conversations/member-v1.json, docs/ENTITY-IDENTITY.md, documentation/configuration.md, src/prme/config.py, src/prme/config_audit.py, src/prme/ingestion/named_text.py, src/prme/ingestion/pipeline.py, src/prme/ingestion/reingest.py, src/prme/retrieval/packing.py, src/prme/storage/engine.py, tests/test_config_audit.py, tests/test_derivation_version.py, tests/test_named_claim_text.py.

## Approach Summary

The local branch `named-claim-text` (commit `ddee3f3c`) rebased cleanly on `main`. It adds `enable_named_claim_text` (off), its rewrite rule, tests, docs, the `member-v1` conversation and the "Named claim text" benchmark section. On top of it, `member-v1` joins the fast gate's procedure in `CLAUDE.md`, `AGENTS.md` and `BENCHMARKS.md` with commands and a pass rule, and `BENCHMARKS.md` records three off-side samples (`--speaker`, speaker grounding on, `deepseek-v4.1-flash:cloud` `e04da138`, Ollama 0.40.14): top 3 10, 8 and 9 of 12; top 5 11, 10 and 11; user claims on the named owner 7, 11 and 10.

## Must Fix

| # | Finding | Resolution |
|---|---|---|
| C-F1 | Real-looking member names and health details, and a private project name, in a public repository (BENCHMARKS.md, CHANGELOG.md, docs, config text, tests, commit message) | Fixed: names replaced by the fixture's fictional Maya, Theo and Nico; the project's measured questions read `<name>`; the project name removed; heading cites #397; the commit message rewritten |

## Should Fix

| # | Finding | Resolution |
|---|---|---|
| C-F2 | A comma, or a clause "and", before I read as a coordinated subject ("Yes, Maya have two kids") | Fixed in `named_text.py`: comma is not a coordinator; after and/or/nor the name is written only before a verb that reads the same both ways, otherwise the sentence is labeled; tests added |
| C-F3 / A-1 | `_folds_into` raised AttributeError on a caller's non-dict `metadata.named_text`, also with the option off | Fixed: only a dict with a str name shown in the text and a str original counts; test added (fails on the old code for str, list and int) |
| C-F4 | Baseline paragraph rank "4, 7 and 7" should be "4, 7 and 6" | Fixed |

## Consider

| # | Finding | Resolution |
|---|---|---|
| C-F5 | Rewrite edge cases: noun "mine", "Oh my", Roman numeral I, "lay"/"weed", all caps, curly apostrophes | Follow-up #409 (grouped with A-3) |
| C-F6 | `named_text.original` on a statement is the source sentence, and the statement folds into its turn | Documented in docs/ENTITY-IDENTITY.md |
| C-F7 | First-person regex and pronoun set duplicated with grounding.py | Accepted: different lookarounds on purpose; noted |
| C-F8 | Staleness wording ambiguous | Fixed in docs/ENTITY-IDENTITY.md and documentation/configuration.md |
| C-F9 | "both probes" predates the third probe | Fixed in CLAUDE.md |
| R1 | Re-review: a contraction after a coordinator ("Jordan and I've") was still rewritten as a clause | Fixed: "and I've" is labeled; tests added |
| R2 | Re-review: after the rename, a BENCHMARKS.md line about the project's turns read as a member-v1 result | Fixed: placeholders `<name>` and `<child>` |
| R3 | Re-review: "the text before the rewrite" left in the CHANGELOG, configuration page, config description and module docstring | Fixed: "the sentence the claim was written from" |
| R4 | Re-review: commit 255523bb and the local pre-rebase branch still carry the names | Fixed: the branch's history is one rewritten commit; the local `named-claim-text` branch is not pushed |

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Secrets or credentials | PASS | none in the diff |
| PII in committed text | FAIL, then fixed | C-F1 |
| Input handling | PASS | the name is concatenated, never used as a regex; no model call or outbound I/O in named_text.py |
| Caller-controlled metadata | FAIL, then fixed | C-F3 / A-1 |
| Privacy-protected reads | PASS | admitted claims are reprojected, so named text does not reach a protected read |

## Pattern Consistency Assessment

The option's wiring matches `enable_owner_group_links` and `enable_owner_name_carry`: config field with `[HYPOTHESIS]`, config_audit activation gate, both pipeline constructions in engine.py, conditional `derivation_settings`, configuration page row, CHANGELOG, `EXPECTED_HYPOTHESES`, pinned derivation config. No `DERIVATION_VERSION` bump is needed: the off path derives the same records and the on path shows in a setting.

## Redundancy Check

C-F7 only. No duplicate of the verb table, `possessive()` or the rewrite elsewhere.

## Wiring Findings

The member probe commands match `benchmarks/diagnostics/chat_probe.py`'s CLI (`--conversation` before the subcommand, `--speaker`, repeatable `--set`, `compare <labels>`).

## Data Safety and Read/Write Proof

New durable field `metadata.named_text` {name, original}: writer `src/prme/ingestion/pipeline.py` (claim metadata at the rewrite), reader `src/prme/retrieval/packing.py` `_folds_into`. Memories stored before the option have no key and pack as before. With the option off nothing writes the key. Toggling it on and off makes the affected memories stale for `reingest()` in both directions, as documented.

## Break Scenarios (adversarial)

Pre-mortem headline (verbatim): "Off by default" was not off for retrieval: a customer's own `named_text` metadata key made every retrieve of their history throw, while the new member-probe gate passed and failed alpha flips by coin toss.

| # | Scenario | Label | Likelihood / impact | Verdict | Reasoning |
|---|---|---|---|---|---|
| A-1 | Caller metadata `named_text` that is not a dict crashes retrieve() on default settings | newly introduced | Low / High | Fix now | regression on the default retrieval path; fixed with a type-checked read and a test |
| A-2 | Member probe pass rule has no tolerance against measured off-side noise; owner-claims direction; `--set` contradicts the tier preamble | newly introduced | Medium / Medium | Fix now | doc-only: CLAUDE.md and BENCHMARKS.md now give the rebuild-once handling for answers and owner claims, the replay case, the intentional-move exception and the `--set` exception |
| A-3 | The rewrite misattributes quoted first person, `ME/CFS`, "Oh my", "World War I" | newly introduced, option on only | Low / Medium | Follow-up | the option is off and no acceptance criterion is violated; a case-sensitive and quote-aware rule is its own change; grouped with C-F5 |
| A-4 | A named claim folds into a turn line that does not show the name (carried-name case) | newly introduced, option on only | Low / Low | Accept | the context is no worse than with the option off; the source text stays the reader's line by design. Alternatives re-checked: folding only when the outer shows the same speaker would restore a repeated line, which is what the fold exists to remove |
| A-5 | Turning the option on, then off, leaves named claims until `reingest()` | newly introduced, documented | Low / Low to Medium | Accept | the content is append-only; `reingest()` is the documented recovery, and the docs now say so plainly |

Tally: Fix now 2, Follow-up 1, Accept 2, Dismissed 0.

## Follow-ups Raised

- #409: Named claim text rewrites quoted speech, abbreviations and set phrases as the owner (A-3, C-F5).

## Resolution Status

| Finding | Status |
|---|---|
| C-F1 | Resolved |
| C-F2 | Resolved |
| C-F3 / A-1 | Resolved |
| C-F4 | Resolved |
| C-F5 / A-3 | Follow-up #409 |
| C-F6 | Resolved (documented) |
| C-F7 | Accepted |
| C-F8 | Resolved |
| C-F9 | Resolved |
| R1 | Resolved |
| R2 | Resolved |
| R3 | Resolved |
| R4 | Resolved |
| A-2 | Resolved (docs) |
| A-4 | Accepted |
| A-5 | Accepted |

## Verification on the final revision

- `uv run pytest -q` (paid providers blocked): 6733 passed, 158 failed, 1485 skipped in 43 min 46 s. 157 of the failures were the environment, not this change: the local virtual environment lacked the `privacy` and `langchain` extras that CI installs (`privacy_capability_unavailable`, missing `presidio_analyzer` and `langchain_core`), and one single-file pack test saw packs those failing tests left open. After `uv sync` with CI's extras, `pytest --lf` passed all 157. The remaining failure is `tests/test_derivation_version.py` under the local `.env` extraction model, which the issue anticipates; with `PRME_EXTRACTION_PROVIDER=openai PRME_EXTRACTION_MODEL=gpt-4o-mini` it passes (2 passed).
- `uv run ruff check src/ tests/`: clean.
- Offline evidence gate, `main` (`1cf42f95`) against this branch, both with the current defaults: every row of `gate-compare` ties (LoCoMo 1,536 and LongMemEval-S 470 questions tied on all evidence packed; projected accuracy 77.6% and 91.4% both sides); no question lost all its candidates.
- Member probe off side: two new live samples (top 3 8 and 9 of 12, top 5 10 and 11, owner claims 11 and 10) beside the 2026-11-04 sample (10, 11, 7).
