# Review: issue #409, named claim text false positives

## Review Scope

- Issue: dwamianm/prism#409 (`enable_named_claim_text` rewrites quoted speech, abbreviations and set phrases as the owner).
- Base branch `main`, base commit `48bb094196d356579600e8f8709afdab405a050f`.
- Final reviewed snapshot: `4b9fe04bcc786ed888523ff19fa34e761c351480d4219e41b6f1b072f5a4ed4e` (uncommitted working tree; `git diff main`). Rebased onto `2014d15a` (dwamianm/prism#400, #470); the changed source files are identical after the rebase, a one-row conflict in the configuration table was resolved by keeping the base's new row and this change's row, and the targeted and full suites were rerun on the rebased revision.
- Tier L. Predicates: SECURITY true (helper detection; the change is a pure string rewrite with no I/O, and a dedicated reviewer confirmed that), NEW_SYMBOLS true; DATA, INFRA, PERSISTED_FIELD not applicable.
- Roster run: core (checklists 1, 3, 4, 5 merged), security (2), adversarial (8). Checklists 6 and 7 not applicable (no infrastructure, no new persisted field). No checklist was skipped.
- Reports read: `issue-409-named-claim-text-false-positives-core.md`, `...-core-r2.md` (including its Pass 3 section), `...-security.md` (including its Pass 3 section), `...-adversarial.md`, `...-adversarial-r2.md` (including its Pass 3 delta). Every required report was complete and matched its snapshot before synthesis.

## Files Changed

CHANGELOG.md, BENCHMARKS.md, docs/ENTITY-IDENTITY.md, documentation/configuration.md, src/prme/config.py, src/prme/ingestion/named_text.py, src/prme/ingestion/reingest.py, tests/fixtures/derivation/trip_derived.json, tests/test_named_claim_text.py.

## Approach Summary

The bounded English rewrite in `src/prme/ingestion/named_text.py` matched its words case-insensitively and without regard to quotations. The fix:

- makes first-person matching case-sensitive for the plain spellings, so `ME/CFS` and `Augusta, ME` are not `me`, and a lowercase `i` is not `I`;
- keeps the sentence whole under the name label when a first person sits inside double quotes, when the text carries single-quoted speech, when the text has two or more double-quoted spans or a same-type nested quote, when "my" forms a set phrase ("Oh my god", "Oh my,", "My god"), when "mine" follows a determiner or a noun ("a coal mine"), and when a capitalized title or numbered part precedes the I ("World War I", "Part I");
- removes `lay` from the past-tense table, adds `weed` to the base forms, keeps an all-caps verb's emphasis, and keeps the apostrophe the user typed;
- carries the quote state across matches and scans words backward, so the rewrite stays linear on long caller-controlled text;
- bumps `DERIVATION_VERSION` from 1 to 2 (a claim text rule change) and refreshes the derivation fixture, which changes only the version and reingest fingerprints.

Tests in `tests/test_named_claim_text.py` cover every reported case and every new branch, plus a long-input regression test. Docs, the config description, the configuration page, CHANGELOG and BENCHMARKS limits were updated.

## Must Fix

1. Security F1 (resolved): the first version of the fix made the rewrite quadratic on caller-controlled text through per-match prefix scans and tail slices (13.5 s at 64 KB, 54 s at 128 KB of "my "-heavy text). Fixed by carrying quote state across matches, scanning words backward without copying the prefix, and indexing forward without slicing; measured 0.035 s at 128 KB and 0.0019 s for the anchored-word case, with a 20,000-token regression test.

No Must Fix item is open.

## Should Fix (all resolved)

1. `_possessive_mine` over-refused the possessive pronoun after a negation or adverb. Now defaults to the pronoun and refuses only a determiner or a determiner two words back.
2. The first Roman-numeral guard over-refused a subject I after a proper noun. Narrowed to a capitalized title head.
3. `_INTERJECTIONS` was broader than the issue's set phrase. Restricted to "oh", with the set-phrase check applying only at a clause start.
4. `_MY_SET_PHRASE_AFTER` over-refused literal possessives. "word" removed and the clause-start condition added.
5. The second-pass title guard ignored capitalization. It now requires the written head to be capitalized, so relative clauses rewrite while titles refuse.
6. The single-quote guard missed quotes after a dash, colon, hyphen or a U+2019 opener. The opener pattern now accepts any non-word character before the quote, and a four-quote guard closes nested same-type double quotes.
7. An all-caps `I'VE` was no longer recognized. The contraction lookahead now accepts `[vV][eE]`.
8. The version-bump wording was missing from a source comment and the config description. Both now state the global staleness.
9. The misnamed test was split, and the missing coverage for the new branches was added.

## Consider (resolved or carried as follow-ups)

1. Multi-modifier attributive "mine" ("an old gold mine") is still rewritten. Pre-existing, needs a noun-phrase walk. Follow-up A.
2. The 21-word title list misses regnal names ("Henry I") and lowercase title heads ("world war I"). Follow-up A.
3. `-ed` base forms outside the small list ("breastfeed") are still read as past tense. Follow-up A.
4. The I branch still copies the rest of the sentence per match, quadratic on very long text (2.1 s at 1 MB). Pre-existing. Follow-up B.
5. Single-quote detection misses an opener fused to the previous word ("He said’I ...") and odd-count nested double quotes. Follow-up C.
6. "My word" is no longer treated as a set phrase, so "My word!" rewrites. Accepted: the literal "my word" is at least as likely, and the label fallback would be a false negative either way.
7. BENCHMARKS.md "Named claim text" numbers predate the #397 and #409 narrowings. Updated with a sentence; a future flip needs a fresh run.

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Secrets or credentials | PASS | No credential material in the diff; the placeholder keys in the configuration page predate it. |
| PII in committed text | PASS | Fictional names only; the derivation fixture is synthetic and changes only the version and fingerprints. |
| Caller-controlled input | PASS | The name is concatenated, never compiled into a regex, never used in SQL, a shell or HTML; `normalize_speaker` bounds it. |
| Denial of service | PASS after F1 | The rewrite is linear; the regexes were timed linear at 400 KB. |
| Authorization / tenant isolation | PASS | Ingestion path only; the name lookup is scoped; no read path changes. |
| Deserialization / I/O | PASS | `named_text.py` imports only `re`; no I/O, network or deserialization. |
| New durable field | N/A | `metadata.named_text` predates the diff; the only durable change is the derivation-version bump. |

## Pattern Consistency Assessment

The option remains wired as its analogues are: the config field, the activation gate in `config_audit.py`, the conditional `derivation_settings` entry, the configuration page row, the CHANGELOG, the docs and the pinned derivation config. The `DERIVATION_VERSION` bump matches the CLAUDE.md rule for claim text rules, and the fixture refresh changes only the version and fingerprints. The five documentation surfaces (module docstring, config description, configuration page, identity doc, CHANGELOG) now agree with the code.

## Redundancy Check

No new duplicate logic. `_previous_word` and `_previous_word_span` are both used; the removed `_sentence_initial` and `_SENTENCE_END` leave no references. Retrieval still reads the metadata key through its own constant. Every new helper and constant has a caller.

## Wiring Findings

No new runtime configuration, route, CLI command or dependency. The behavior is reachable only through `enable_named_claim_text` on the ingestion path, and the option stays off by default. No new field or migration. The derivation version participates in every fingerprint, which is what the docs now state.

## Data Safety and Read/Write Proof

No new persisted field. The rewrite writes claim text and `metadata.named_text` together on the ingestion path, inside the existing per-source transaction. Old claims keep their text until `reingest()`, which is opt-in and model-backed; the bump makes every stored source stale once, including option-off stores, and with the option off the derived records are unchanged (the fixture diff proves it). Packing folds a label as it folds a rewrite, the lexical and vector documents are rebuilt through the existing eviction path, and receipts are unchanged.

## Break Scenarios (adversarial) and Adjudication

| # | Scenario | Label | Verdict | Reasoning |
|---|---|---|---|---|
| A1 | Single-quoted speech rewritten | pre-existing, left open by the first pass | Fix now | Same reported class; contained; fixed for whitespace, bracket, dash, colon, hyphen and U+2019 openers, and nested same-type double quotes now label. |
| A2 | Lowercase `i` silently leaves a turn unnamed | newly introduced | Fix now (document and test) | The issue asks for case-sensitive matching; documented in five surfaces and pinned by tests. |
| A3 | `-ed` base forms outside the list | pre-existing | Follow-up A | No bounded general fix without part-of-speech knowledge. |
| A4 | `DERIVATION_VERSION` 2 makes every store stale once | newly introduced by design | Accept the bump | The rule mandates it and option-on stores need re-derivation; cost documented. Wording fixed now. |
| A5 | Multi-modifier attributive `mine` | pre-existing | Follow-up A | Same small-list root cause as A3. |
| A6 | Title list misses regnal names, lowercase heads | pre-existing corruption; docs claim general title coverage | Follow-up A for regnal/lowercase; rest fixed now | The common title and numbered heads were added and the capitalization requirement restored relative clauses. |
| A7 | Title guard over-refuses relative clauses | newly introduced | Fix now | Capitalization requirement restores "The class I took". |
| A8 | Source comment and config description contradict the bump | newly introduced | Fix now | Both updated. |
| A9 | Set-phrase guard over-refuses literal possessives | newly introduced | Fix now | Clause-start condition and "word" removal; literal cases rewrite. |
| A10 | Quadratic prefix scans on long text | pre-existing pattern widened | Fix now (security F1) | Single pass; linear measurements and a regression test. |

Tally: Fix now 7 (A1, A2, A4 wording, A7, A8, A9, A10), Accept 1 (A4 bump cost), Follow-up 4 scenarios grouped into 3 issues (A3, A5, A6, plus the pass-3 residuals: I-branch tail copies and quote openers), Dismissed 0.

## Follow-ups Raised

- #474 (A): `-ed` base forms, multi-modifier attributive "mine", regnal numerals and lowercase title heads still rewrite. One issue for the shared small-list/part-of-speech gap.
- #475 (B): the I branch still copies the rest of the sentence per match, quadratic on very long text.
- #476 (C): single-quote detection misses an opener fused to the previous word and odd-count nested double quotes.

## Resolution Status

| Item | Status |
|---|---|
| Nine reported cases | Fixed, tested, verified independently against `main` |
| Core pass 1: 3 Should Fix, 1 dead code, coverage and docs | Fixed or resolved |
| Core pass 2: 4 Should Fix, 4 Consider | 4 fixed; remaining Consider carried as follow-ups |
| Core pass 3: helper/quote-state refactor | Fuzz-clean, 0 mismatches; two residuals carried |
| Security F1 | Resolved; linear timings confirmed |
| Adversarial pass 1 and 2 scenarios | Fixed now, accepted, or followed up as recorded |
| Full test suite | `uv run pytest -q` on the rebased revision: 7204 passed, 1534 skipped, 2 xfailed |
| Static checks | ruff clean; public-API mypy clean |
| Follow-up issues | #474, #475, #476 filed and recorded |
