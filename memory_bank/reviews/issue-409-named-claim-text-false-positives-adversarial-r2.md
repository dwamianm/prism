# Adversarial review r2: issue #409, named claim text false positives (checklist 8)

Status: COMPLETE (pass 2, plus pass 3 delta)

- Repository: /Users/dwamianm/Sites/prism
- Base branch: main, base commit `48bb094196d356579600e8f8709afdab405a050f`
- Reviewed snapshots: pass 2 `9aac4b971408a2d760e8f1a0f65d411f379fc093c116fb20acfe9ae6053ec0fb`; pass 3 delta `4b9fe04bcc786ed888523ff19fa34e761c351480d4219e41b6f1b072f5a4ed4e` (uncommitted working tree, `git -C /Users/dwamianm/Sites/prism diff main`)
- Tier: L, adversarial, cap 8 scenarios; 7 pass-2 scenarios plus 2 pass-3 deltas.
- Method: read the changed source, its single call site and its callers, and re-ran the pure rewrite functions against the working-tree `src/prme/ingestion/named_text.py` with no model, store or I/O, comparing each result with `git show main:src/prme/ingestion/named_text.py`. No build, full suite or test run (the worker owns those).

## Verdict on the first pass's open scenarios (pass 2 snapshot, superseded where pass 3 says so)

- **Scenario 1, single-quoted speech: closed for the two reported forms, not fully.** `My husband said 'call me later.'`, `He said 'I lost my keys.'` and `‘I was fired,’ my husband said.` now label (`Maya: ...`) because `_SINGLE_QUOTED_SPEECH_RE` sees the opening quote at whitespace or text start (src/prme/ingestion/named_text.py:112) and `‘` is in both first-person lookbehinds (:105-107). Three residuals survived: a quote opened after a non-space delimiter (`He said—'I lost my keys.'`), a quote opened with U+2019 (`He said ’I lost my keys,’ and left.`), and an even-parity nested double quote. Pass 3 closed the first two and the nested case; see P3-2 for the malformed-input residue.
- **Scenario 3, `-ed` base forms: still open in code, disposition not verifiable from this snapshot.** `I breastfeed my newborn.` still yields `Maya breastfeed Maya's newborn.` `_BASE_ED` gained only `weed` and `_PAST` lost `lay` (src/prme/ingestion/named_text.py:64-72), so the whitelist root cause is unchanged. The changed files and `memory_bank/reviews/` contain no reference to a follow-up issue number (grep for `409`, `breastfeed`, `follow-up`), so "left to a follow-up issue" can only be confirmed from the PR/issue tracker, not from the diff.
- **Scenario 4, `DERIVATION_VERSION` staleness: documented in three of five surfaces; one source comment now says the opposite.** CHANGELOG.md:16, docs/ENTITY-IDENTITY.md:114-122 and documentation/configuration.md:146 now state that the version is in every fingerprint, so every store, option-off included, reads stale once and pays one model request per source. The `PRMEConfig` field description (src/prme/config.py:1245-1281) did not mention the bump and src/prme/ingestion/reingest.py:151-155 still ended "so nothing stored reads as stale for an engine that leaves it off", which version 2 falsifies. Pass 3 fixed both; see the pass-3 table.

## Pass 3 delta (snapshot 4b9fe04, 138 focused tests passed / 12 skipped)

Re-attacked only the changed logic, with the same pure-function probe (no model, store or I/O). The pass-2 scenarios now stand as follows:

| Pass-2 scenario | Status on 4b9fe04 |
|---|---|
| 1, single quotes | Closed for the em dash, colon, hyphen, a spaced U+2019 opener and same-type double nesting of four or more quote characters (`He said—'I lost my keys.'`, `He said:'I lost my keys.'`, `He said-'I lost my keys.'`, `He said ’I lost my keys,’ and left.`, `He said "she said "I lost my keys" yesterday."` all label). Two malformed-input residuals and one over-refusal remain (P3-2). |
| 2, multi-modifier mine | Open, adjudicated into the single follow-up issue. `My uncle owns a very old coal mine.` still gives `Maya's uncle owns a very old coal Maya's.`; `an old gold mine` likewise. |
| 3, title heads | Closed for `Title`, `Schedule`, `Appendix`, `Exhibit`, `Table`, `Figure`, `Rule`, `Group`, `Paragraph`, `Subsection`, `Annex` and `Clause` when the written head is capitalized; all were verified to label. Regnal numerals (`Henry I was king.` -> `Henry Maya was king.`) remain, adjudicated into the same follow-up. One new residual: a lowercase head bypasses the capitalization requirement (P3-1). |
| 4, relative-clause over-refusal | Closed. `The class I took was hard.` -> `The class Maya took was hard.`, `The book I bought was cheap.` and `In this stage I was ready.` rewrite again, while `World War I`, `Part I` and `Title I` refuse. |
| 5, stale docs | Closed. src/prme/ingestion/reingest.py:151-156 now says a `DERIVATION_VERSION` bump makes every source stale whether the option is on or off, and src/prme/config.py:1267-1272 states the same, alongside the three docs. BENCHMARKS.md:2215-2219 records that the pinned "on" columns used earlier rules and a future flip needs a fresh run. |
| 6, set phrases | Closed. `Oh my phone is dead.`, `Oh, my phone is dead.` and `I gave my word on it.` rewrite; `Oh my god`, `Oh, my god`, `Oh my,`, `My,`, `My god`, `Oh my goodness` and `My lord` refuse; `My Word is my bond.` now rewrites as the literal possessive. A clause-initial literal `My God answered my prayer.` still labels (by design, safe). |
| 7, quadratic scans | Closed. Measured 40 KB in 0.0072 s, 160 KB in 0.0399 s and a 100 KB anchored prefix in 0.0056 s, against 1.9 s at 40 KB and 12.2 s at 100 KB in pass 2. |

### P3-1. A lowercase or mixed-case title head bypasses the new capitalization check

- **Trigger.** `enable_named_claim_text` on with a speaker bound, and a sentence whose numeral head is written lowercase or mixed case: `world war I was a global conflict.`, `World war I was a global conflict.`
- **Mechanism.** The title guard now requires the written head to start uppercase before it consults `_TITLE_HEADS` (src/prme/ingestion/named_text.py:246), so `war`/`war` in `world war` fails the test, the `I` is treated as a subject and the following `was` rewrites it: `world war Maya was a global conflict.` The pass-2 snapshot refused any `_TITLE_HEADS` head regardless of case; base `main` had no title guard at all, so this is a regression against the pass-2 intermediate and pre-existing against `main`.
- **Likelihood.** Low to medium: lowercase and mixed-case prose is normal in chat, and `world war I` is the natural lowercase spelling.
- **Impact.** High: a wrong person substituted into the claim text of the exact "I is not the speaker" class, silently and durably.
- **Detection.** Silent.
- **Cheapest mitigation.** Fold into the single follow-up issue with the regnal numerals, or refuse when a casefolded head in `_TITLE_HEADS` follows a clause boundary and the numeral-like `I` is followed by one of the invariant verb forms; the second is not bounded enough to recommend in this PR.
- **Recommended verdict.** Follow-up, grouped with pass-2 scenarios 2 and 3.
- **Label.** Newly introduced by this pass's capitalization check; pre-existing against base `main`.

### P3-2. Two malformed-input quote leaks survive, and the four-quote guard over-refuses ordinary text

- **Trigger.** `He said’I lost my keys.’` (no space before a U+2019 opener), `He said "she said "I lost my keys."` (three double quotes, unmatched outer), and the harmless over-refusal `He said "hi" and then "bye" and I left.` or `I read "Dune" and "Emma" last year.`
- **Mechanism.** `_SINGLE_QUOTED_SPEECH_RE` is now `(?<!\w)['‘’](?=[A-Za-z])` (src/prme/ingestion/named_text.py:114), so an opener preceded by a letter is read as a contraction and skipped. `several_quotes` labels the whole text only when it counts four or more double-quote characters (:207-208, :218), so an unmatched nesting with three stays under it and the parity toggle (:213-217) sees the first person as outside; conversely, any two double-quoted spans reach four and the whole text is labeled even when the first person is outside them.
- **Likelihood.** Low for the two leaks (malformed punctuation); medium for the over-refusal (two quoted spans in one message is common).
- **Impact.** High for the leaks (wrong person stored), low for the over-refusal (the label still names the owner).
- **Detection.** Silent.
- **Cheapest mitigation.** Accept and document the malformed-input leaks in the named_text.py docstring and docs/ENTITY-IDENTITY.md (2 to 3 lines); for the over-refusal, replace the quote count with a nesting-aware scan that keeps a stack of open spans, or document the label case (~10 lines and 2 tests).
- **Recommended verdict.** Accept the leaks as documented limits; Follow-up on the over-refusal wording.
- **Label.** Pre-existing leaks of the reported class; the over-refusal is newly introduced by this pass's four-quote guard.

## Checklist results

| Check | Result |
|---|---|
| Premise: fixes address the reported class at the root, or move the whitelist? | Mixed. Case sensitivity (:105-107), quote scanning (:112-114, :213-222) and the mine determiner test (:167-178) are structural; the title heads (:86-89), interjections/set phrases (:82-84) and `-ed` base forms (:73) are still hand-maintained lists. Pass 3 closed the title-list holes that were reported and removed the common relative-clause over-refusal; the remaining small-list gaps are grouped into one follow-up. |
| Quote guard loopholes (unmatched, nested, mixed straight/curly, quote after an earlier first person) | Pass 2 residuals closed. Pass 3 residue: a no-space opener after a word and an unmatched nesting of three double quotes (P3-2); mixed straight/curly, unmatched opening quotes, inch marks and a quote after an earlier first person fall to the safe label, and two quoted spans now over-refuse. |
| `mine` guard | `a coal mine`, `the old mine`, `This book is mine`, `A friend of mine`, `It isn't mine`, `The money is all mine` behave; a two-adjective chain does not (pass-2 scenario 2, follow-up). |
| Title/numeral guard | The pass-2 list gaps are closed when the head is capitalized; `Henry I` and a lowercase head still rewrite (pass-3 P3-1 and the grouped follow-up), and common relative clauses rewrite again. |
| Set-phrase and interjection guard | `Oh my god`, `Oh, my god`, `Oh my,`, `My,`, `My god`, `Oh my goodness`, `My lord` refuse; `Oh my phone`, `Oh, my phone`, `Wow my phone`, `I gave my word on it.` rewrite, with or without the comma. A clause-initial literal `My God answered...` labels (safe). |
| Contraction logic | `I'M`/`I’M` -> `Maya IS`, `I can’t` keeps the curly apostrophe, `I'D`/`I’D` label, and `She dotted the I's and crossed the T's.` is unchanged; no new defect found. |
| Lowercase `i` and all caps | Behaviour documented in the module docstring (:26-29), docs/ENTITY-IDENTITY.md:95-102, documentation/configuration.md:146 and src/prme/config.py:1260-1266, and pinned by tests. A mixed turn (`i lost my keys.`) still rewrites its `my` while keeping the lowercase subject; the docs say only "writes only i is left unnamed". |
| Existing data and installs (version-1 memories, option-off stores) | Forward-only as before: stored claims keep old text until `reingest()`; the version bump makes every source stale and keys out saved reingest extractions (`derivation_version` is inside `extraction_settings`, reingest.py:111, and inside the fingerprint, :143-156). Documented in all five surfaces after pass 3. |
| State and lifecycle | Unchanged. No new state, no migration, no write on the read path; toggle semantics documented at docs/ENTITY-IDENTITY.md:114-122. |
| Idempotency and retries | `named_claim_text` is not idempotent (`Maya: I lost my keys.` -> `Maya: Maya lost Maya's keys.`) but unreachable: the only producer call site is pipeline.py:1760, and both the engine path and `reingest._prepare_plan` rebuild the text from the saved `ExtractionRecord`, which the rewrite does not mutate. A stale-plan retry rebuilds a fresh plan. |
| Ordering and consistency | Deterministic and order-independent. The duplicate-sentence set `claim_texts` (pipeline.py:1765-1780) holds labels too; two claims of one sentence produce the same label, so the statement path fires as before. Claim merge and supersedence compare subject/predicate/object, not text. |
| Scale and regex behaviour | Closed in pass 3 (see the table); 20,000 tokens under 5 s is pinned by a test and 160 KB measured at 0.04 s. |
| Partial failure | Unchanged: per-source transaction, saved extraction before publishing, failed source counted and retried later; name, text and `metadata.named_text` are written together. No new failure mode. |
| Backward compatibility (packing, indexes, receipts) | A label folds as a rewrite does: `_folds_into` needs the name in the inner text and `named_text.original` in the outer line (src/prme/retrieval/packing.py:888-928), both true for `Maya: ...`; a caller's own metadata under the key is ignored (packing.py:908-918). Lexical and vector documents are rebuilt on reingest through the existing eviction path. Retrieval receipts carry no claim text and no new feature flag. BENCHMARKS.md:2215-2219 now records that the pinned option-on numbers used earlier rules and a future flip needs a fresh run. |
| Blast radius | Option-off stores: cost only (global staleness; documented). Option-on stores: existing claims keep old text until reingest, and pass 3 restores the natural rewrite for common "noun + I" relative clauses; the remaining stored-text changes are the label fallbacks (safe) and the small-list residuals. |
| Detection | Silent. Nothing logs, counts or receipts a rewrite, a label, a refusal or a stale source at upgrade time; `metadata.named_text.original` is the only forensic trail and nothing compares it with the claim text. |

## Scenarios (pass 2, retained for history; superseded where the pass-3 table says closed)

Ranked by likelihood x impact. Each is labelled pre-existing or newly introduced relative to base commit `48bb0941`.

### 1. Single-quote guard misses a quote opened after a dash or with U+2019, and nested double quotes flip parity

- **Trigger.** `enable_named_claim_text` on with a speaker bound, and a turn that reports speech as `He said—'I lost my keys.'`, `He said ’I lost my keys,’ and left.`, or `He said "she said "I lost my keys" yesterday."`.
- **Mechanism.** `_SINGLE_QUOTED_SPEECH_RE` only accepted text start, whitespace or `([{` before a straight `'` or `‘` (src/prme/ingestion/named_text.py:112 in pass 2), so an em dash, ellipsis or slash immediately before the quote was not seen and U+2019 `’` used as an opener was not in the class at all; `_inside_quotes` toggled on every `"`/`“`/`”`, so an even number of quotation marks before a token read as outside.
- **Likelihood.** Medium for the em dash, low for a U+2019 opener and for same-type nested quotes.
- **Impact.** High: the claim text attributed the quoted speaker's possession or object to the owner.
- **Detection.** Silent.
- **Cheapest mitigation.** The pass-3 pattern `(?<!\w)['‘’](?=[A-Za-z])` plus a nesting-aware guard.
- **Recommended verdict.** Fixed in pass 3 for the reported forms; see P3-2 for the residue.
- **Label.** Pre-existing leak, closed.

### 2. The `mine` guard follows only one or two words back, so an adjective chain still becomes a possessive

- **Trigger.** `enable_named_claim_text` on, and a turn with `My uncle owns a very old coal mine.` or `My uncle owns an old gold mine.`
- **Mechanism.** `_possessive_mine` returns the possessive unless the immediately preceding word is a determiner or the word two back is one (src/prme/ingestion/named_text.py:167-178). With `old`/`gold` immediately before `mine`, it returns True and appends the possessive: `Maya's uncle owns a very old coal Maya's.` The determiner and one-adjective forms (`a coal mine`, `the old mine`) are correctly refused.
- **Likelihood.** Medium: the reported example was exactly this construction, and adding one adjective is natural.
- **Impact.** Medium-high: ungrammatical text that silently turns a mine the uncle owns into an owner possession.
- **Detection.** Silent.
- **Cheapest mitigation.** Walk back over the preceding words until a determiner or a clause boundary; about 5 to 8 lines and 3 to 4 tests. Adjudicated into the single follow-up issue.
- **Recommended verdict.** Follow-up.
- **Label.** Pre-existing; the fix closes the reported one-adjective form and leaves the chain open.

### 3. The `_TITLE_HEADS` whitelist misses `Title I`, `Schedule I`, `Appendix I` and regnal names, and the docs claim title/numbered-part coverage generally

- **Trigger.** `enable_named_claim_text` on, and a sentence with a numbered part or regnal numeral the list omits followed by a rewriting verb.
- **Mechanism.** The pass-2 guard consulted only a 21-word set (src/prme/ingestion/named_text.py:85-88 in pass 2), so `Title I was amended.`, `Schedule I was complex.`, `Appendix I was updated.` and `Henry I was king.` rewrote the numeral.
- **Likelihood.** Low to medium.
- **Impact.** High: a wrong person substituted into the claim text.
- **Detection.** Silent.
- **Cheapest mitigation.** Pass 3 added the omitted heads and a capitalization requirement; regnal names remain in the grouped follow-up.
- **Recommended verdict.** Closed for the listed heads in pass 3; regnal names remain a follow-up.
- **Label.** Pre-existing corruption; the documentation overstatement was corrected.

### 4. `_TITLE_HEADS` over-refuses ordinary relative clauses, so the option now labels text it used to rewrite

- **Trigger.** `enable_named_claim_text` on, and `The class I took was hard.`, `The book I bought was great.`, `The part I liked most was the end.`, `In this stage I was ready.`
- **Mechanism.** The pass-2 guard refused whenever the lowercase word before `I` was in the title set. Base `main` rewrote these; pass 2 labeled them.
- **Likelihood.** High.
- **Impact.** Low to medium: no corruption, but a coverage regression.
- **Detection.** Silent.
- **Cheapest mitigation.** Require the written head to be capitalized; done in pass 3.
- **Recommended verdict.** Closed in pass 3.
- **Label.** Newly introduced in pass 2, closed in pass 3.

### 5. The version-bump change is documented in the operator docs but contradicted by the reingest source comment and absent from the config field description

- **Trigger.** A maintainer reading src/prme/ingestion/reingest.py:151-155, or an operator reading the `PRME_ENABLE_NAMED_CLAIM_TEXT` description.
- **Mechanism.** The pass-2 comment still said "nothing stored reads as stale for an engine that leaves it off", which version 2 falsifies; the config description did not mention the bump.
- **Likelihood.** Certain for anyone reading the comment.
- **Impact.** Low: no runtime effect, but misleading source documentation.
- **Detection.** A reader comparing surfaces; not automated.
- **Cheapest mitigation.** One comment sentence and one config-description sentence; done in pass 3.
- **Recommended verdict.** Closed in pass 3.
- **Label.** Newly introduced by the bump; fixed.

### 6. The set-phrase guard over-refuses legitimate possessives and behaves differently with and without a comma

- **Trigger.** `enable_named_claim_text` on, and `Oh my phone is dead.` versus `Oh, my phone is dead.`, or `My Word is my bond.`
- **Mechanism.** The pass-2 interjection test fired on any preceding `oh` and `_MY_SET_PHRASE_AFTER` contained `word`.
- **Likelihood.** Medium.
- **Impact.** Low: the label still names the owner.
- **Detection.** Silent.
- **Cheapest mitigation.** Restrict the set-phrase test to a clause start and drop `word`; done in pass 3.
- **Recommended verdict.** Closed in pass 3; the remaining clause-initial literal case labels safely.
- **Label.** Newly introduced in pass 2, closed in pass 3.

### 7. Per-match prefix scans are quadratic on a long claim text

- **Trigger.** `enable_named_claim_text` on, a long `user` turn whose extraction supplies no evidence quote, and many first-person tokens.
- **Mechanism.** For every match, `_inside_quotes` scanned the whole prefix, `_previous_word_span` copied and regex-searched the prefix and `_next_word` copied the remainder: 10 KB 0.12 s, 40 KB 1.9 s, 100 KB 12.2 s.
- **Likelihood.** Low.
- **Impact.** Medium: one slow `ingest()` call per affected claim.
- **Detection.** Quiet.
- **Cheapest mitigation.** Carry quote state across matches, scan backward, and index forward; done in pass 3 (160 KB in 0.04 s).
- **Recommended verdict.** Closed in pass 3.
- **Label.** Pre-existing O(n^2) pattern, fixed.

## Attacked and did not break

- **Double-quoted speech, including curly, mixed straight/curly, unmatched and after an earlier first person.** `My husband said "I was fired."`, `He said “I’m tired.”`, `He said “I lost my keys."`, `He said "I lost my keys.` and `I said "call me later."` all label; the function returns None before any partial output, so a quote that opens after an earlier first person discards the whole rewrite.
- **Straight single quotes and the pass-3 openers.** `He said 'I lost my keys.'`, `He said—'I lost my keys.'`, `He said:'I lost my keys.'`, `He said-'I lost my keys.'` and `He said ’I lost my keys,’ and left.` label; contractions do not trip the guard (`It's my car.` rewrites, `He is 6' tall and I am 5' tall.` rewrites).
- **Set phrases.** `Oh my god`, `Oh, my god`, `Oh my`, `Oh my goodness`, `My gosh`, `My lord` and `My, what a surprise` refuse; `Wow my phone`, `Oh my phone`, `Oh, my phone`, `Ah my head` and `I gave my word on it.` rewrite.
- **Contractions and apostrophes.** `I'M`/`I’M` -> `Maya IS`, `I can't`/`I can’t` keep the typed apostrophe, `I'D`/`I’D` label, `She dotted the I's and crossed the T's.` is unchanged.
- **Mine pronouns.** `Mine is blue.`, `That's mine.`, `A friend of mine called.`, `The blue book is hers and mine.`, `It isn't mine.`, `The money is all mine.` rewrite; `a coal mine`, `the old mine`, `This mine produces coal.` refuse.
- **Title/numbered forms the list covers.** `World War I`, `Part I`, `Chapter I`, `Type I`, `Section I`, `Volume I`, `Stage I`, and the pass-3 additions `Title I`, `Schedule I`, `Appendix I`, `Exhibit I`, `Table I`, `Figure I`, `Rule I`, `Group I`, `Paragraph I`, `Subsection I`, `Annex I`, `Clause I` refuse when the head is capitalized.
- **Abbreviations and case.** `I have ME/CFS.` -> `Maya has ME/CFS.`, `Augusta, ME is cold.` and `She dotted the I's...` are unchanged, and `has_first_person` no longer fires on the all-caps abbreviation.
- **Lowercase i.** `i think so.` is unchanged; `i lost my keys.` -> `i lost Maya's keys.` is the documented/pinned mixed case.
- **The statement sibling path.** `_claim_statement` composes from subject/predicate/object (pipeline.py:190-217), never from free text; the duplicate-sentence set now holds labels and two claims of one sentence produce the same label.
- **Double application and retries.** `Maya: I lost my keys.` compounds the label, but the only producer call site is pipeline.py:1760 and the saved `ExtractionRecord` is never mutated.
- **Packing, indexes and receipts.** A label folds into the turn that shows its sentence (`_folds_into`, packing.py:888-928); a caller's own value under `named_text` is ignored; old claims without the key pack as before.
- **Ordering, partial failure, lifecycle and concurrency.** The busy guard, per-source transaction, saved-extraction-before-publish, text-independent merge/supersedence and deterministic rewrite are untouched.

Not kept: an inch mark (`The monitor is 27" wide and I like it.`) and an unrelated single-quoted word (`She said 'hello' and I left.`, `rock 'n' roll and I love it.`) label the whole text (safe over-refusal); a lowercase `i` turn that also has `my` keeps the lowercase subject while naming the possessive (documented and pinned); `I need a break.` and `In May I went home.` label because of `_BASE_ED` and `_AUXILIARIES` (pre-existing, safe); a clause-initial `My God answered my prayer.` labels (pass-3 set-phrase rule, safe).

## Pre-mortem headline (revised for pass 3)

The pass-2 headline no longer applies as written: the em-dash opener, `Title I`/`Schedule I` heads and the relative-clause over-refusal are closed, and the reingest cost is now documented in every surface. The narrowed headline is:

"The em-dash and title cases are fixed, but `Henry I`, a lowercase `world war I` and `I breastfeed` remain in one follow-up, and every store that never turned the option on still re-extracts its whole history once to repair text it never wrote."
