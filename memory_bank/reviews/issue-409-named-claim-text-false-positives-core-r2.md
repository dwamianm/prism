# Issue #409 core review (pass 2 plus pass 3 delta), named claim text false positives

Status: COMPLETE

- Repository: `/Users/dwamianm/Sites/prism`
- Base branch: `main`, base commit `48bb094196d356579600e8f8709afdab405a050f`
- Reviewed snapshot (pass 2 body below): `9aac4b971408a2d760e8f1a0f65d411f379fc093c116fb20acfe9ae6053ec0fb` (uncommitted working tree, `git diff main`)
- Pass 3 delta snapshot: `4b9fe04bcc786ed888523ff19fa34e761c351480d4219e41b6f1b072f5a4ed4e` (uncommitted working tree). All four pass-2 Should Fix items are closed; two Consider residuals remain. See "Pass 3 delta verification" at the end.
- Tier: L. Roster: core (this report), security, adversarial.
- Checklist: 1 Correctness & logic (sentinel contracts, edge cases); 3 code quality, naming, test coverage; 4 pattern consistency, `DERIVATION_VERSION`, docs; 5 redundancy / dead code. Security and runtime wiring are screened only enough to confirm nothing new is reachable.

## Verification performed

- Loaded the working-tree `prme.ingestion.named_text` and `main`'s copy (via `git show main:src/prme/ingestion/named_text.py`, exec'd in a separate namespace) and ran them side by side on the nine issue cases, every first-pass finding, every new guard, and constructed adversarial inputs. All behaviour quotes below are from that run; no store, model or I/O was involved.
- Re-read the callers: `src/prme/ingestion/pipeline.py:1432-1448` (the `has_first_person` gate on the carried-name read) and `:1757-1763` (the single `named_claim_text` call site), `src/prme/ingestion/reingest.py:89,96-156`, `src/prme/retrieval/packing.py:45-46,912-923`, `src/prme/config_audit.py:268-272`.
- Confirmed the fixture refresh: `tests/fixtures/derivation/trip_derived.json` carries `derivation_version: 2` and all six `reingest.fingerprint` values are the new digest, with the derivation test pinned to `enable_named_claim_text=False` (`tests/test_derivation_version.py:52-67`).
- Did not run pytest or the full suite (the worker owns build and test execution); the reported targeted pass numbers are taken as given. The pure-function runs above are the independent evidence.

## First-pass findings: resolution

| First-pass item | Verdict |
|---|---|
| Core 1 `_possessive_mine` (negation/adverb) | Fixed. `"The book is not mine."`, `"It isn't mine."`, `"That was never mine."`, `"This is really mine."`, `"The money is all mine."`, `"That is ours, not mine."`, `"A friend of mine called."`, `"That's mine."`, `"Mine is blue."` all rewrite; code now defaults to the pronoun. Residual `mine` noun cases are Consider 5. |
| Core 2 Roman-numeral guard | Reported cases fixed (`"In Denver I found a job."`, `"Last Monday I went home."`, `"Yesterday at Northwind I met Sam."`, `"I told Mom I was tired."` rewrite; `"World War I ended in 1918."` labels). The pass-2 replacement guard introduced an over-refusal class (pass-2 Should Fix 1), closed in pass 3. |
| Core 3 `_INTERJECTIONS` breadth | Reported cases fixed (`"Wow my new phone…"`, `"Hey my ride…"`, `"Ah my head…"`, `"Boy my legs…"`, `"Gosh my day…"` rewrite; `"Oh my god"`, `"Oh, my god"`, `"My god"`, `"Oh my,"`, `"My,"` label). The pass-2 `_MY_SET_PHRASE_AFTER` over-refusal (Should Fix 4) is closed in pass 3. |
| Core 4 dead `_previous_word` | Resolved: `_previous_word` is used again by `_possessive_mine`; `_sentence_initial` / `_SENTENCE_END` are gone with no dangling reference (`rg` over `src/`, `tests/`, `benchmarks/`). |
| Core 5 new-branch coverage | Mostly resolved; the remaining gaps are listed in the pass-3 section. |
| Core 6 contraction lookahead | Resolved: the second alternative requires `(?:m|ve|ll|d)\b` again, and `"She dotted the I's and crossed the T's."` is unchanged. |
| Core 7 all-caps attached contraction | Resolved: `"I'M PREGNANT."` → `"Maya IS PREGNANT."`. The sibling `I'VE` casing hole (pass-2 Should Fix 3) is closed in pass 3. |
| Core 8 `DERIVATION_VERSION` upgrade note | Resolved: `CHANGELOG.md:16`, `docs/ENTITY-IDENTITY.md:117-122` and `documentation/configuration.md:146` say the version is in every fingerprint, that every pre-bump memory reads as stale once with a model request each, including option-off stores, and that the option-off derived records are unchanged. |
| Core 9 misnamed test | Resolved: `test_a_sentence_it_can_rewrite_is_not_labeled` (`tests/test_named_claim_text.py:167-168`) and the parametrized `test_a_text_with_no_first_person_is_left_as_it_is` (`:170-173`). |
| Adversarial 1 single-quoted speech | Fixed for whitespace- and bracket-adjacent quotes; the punctuation-adjacent gap (pass-2 Should Fix 2) is closed in pass 3. |
| Adversarial 2 lowercase `i` | Resolved: documented in `named_text.py:27-29`, `src/prme/config.py:1262-1263`, `documentation/configuration.md:146`, `docs/ENTITY-IDENTITY.md:97-98`, `CHANGELOG.md:16`, and pinned by `tests/test_named_claim_text.py:130-131,188`. |
| Adversarial 3 `-ed` base forms | Justified as follow-up (pre-existing root cause, no bounded general rule); accepted. |
| Adversarial 4 bump blast radius | Resolved with the docs above; cost stated plainly. |

## Pass-2 findings (historical)

### Should Fix

1. **The title-head guard ignores capitalization, so ordinary lowercase nouns before `I` now label sentences that main rewrote correctly.** (Closed in pass 3; see below.)
   `src/prme/ingestion/named_text.py:84-88` defined `_TITLE_HEADS` with the comment "A **capitalized** word before I …", but the pass-2 check used the casefolded `previous`. `"The class I took was hard."`, `"The book I bought was cheap."`, `"The step I took was right."`, `"The part I played was small."`, `"The grade I got was high."`, `"After the war I moved to Denver."` all returned `None` where `main` rewrote them. Pass 3 gates the check on the written span's first letter (`:246`).

2. **Single-quoted speech was still rewritten inside the quotes when the quote followed punctuation other than whitespace or an opening bracket.** (Closed in pass 3.)
   `named_text.py:111-112` only treated `'`/`‘` as speech-opening after `^`, whitespace, `(`, `[` or `{`; `"He said—'call me later.'"`, `"He said:'call me later.'"`, `"He said-'call me later.'"` and `"He said ’call me later.’"` rewrote the quoted `me`. Pass 3 uses `(?<!\w)['‘’](?=[A-Za-z])` (`:114`).

3. **An all-caps `I'VE` / `I’VE` contraction was not recognized as first person at all.** (Closed in pass 3.)
   `named_text.py:107` had `[vV]e`, case-sensitive on the `e`; `"I'VE BEEN THERE."` returned unchanged with `has_first_person` `False`, where `main` named it. Pass 3 uses `[vV][eE]` (`:108`).

4. **The set-phrase guard refused literal possessives away from sentence start (`my word`, `my lord`, `my god`).** (Closed in pass 3.)
   `named_text.py:82-83,213-214` refused position-blind; `"I gave my word on it."`, `"I asked my lord for help."`, `"I thanked my god."` labeled where `main` rewrote them. Pass 3 gates the set phrase on the start of a clause (`:232-234`).

### Consider (pass 2)

5. **The noun `mine` is still rewritten when more than one modifier precedes it (pre-existing, not fixed).** `_possessive_mine` only looks one word back for the determiner, so `"He owns an old coal mine."` → `"He owns an old coal Maya's."` and `"I visited the abandoned gold mine."` → `"Maya visited the abandoned gold Maya's."`, the same as `main`; the single-modifier reported cases are fixed. Follow-up, as adversarial scenario 3.

6. **Remaining new-branch coverage gaps.** Pass 3 closed most of these; see the pass-3 coverage line.

7. **Docs precision on the quote rule.** Pass 3 reworded the docs to "a first person inside double quotes, or anywhere in a text that carries single-quoted speech", which matches the whole-text flag.

8. **`BENCHMARKS.md` recorded the #397 rule narrowing but not #409.** Pass 3 added the #409 sentence at `BENCHMARKS.md:2215-2218`.

## Checklist results

1. Correctness / sentinel contracts: all nine issue cases are fixed and verified (case-sensitive pronouns, double-quote refusal, set phrases, noun `mine`, Roman numeral, `lay`/`weed`, all-caps verb, curly contraction), and both sentinels still behave (`None` discards any partial rewrite before the label is built; unchanged text is returned when there is no first person). Pass 3 closed the four pass-2 defects; two pre-existing residuals remain (pass-3 Consider A and B).
3. Code quality / naming / coverage: helpers are small and named for their role; `_previous_word` and `_previous_word_span` are both used; the removed `_sentence_initial`/`_SENTENCE_END` left no references; no duplicate rewrite logic; worker-reported ruff and mypy clean; the remaining coverage gaps are listed in the pass-3 section.
4. Pattern consistency / `DERIVATION_VERSION` / docs: `config_audit.py:268-272` still registers the toggle; the bump to 2 matches the CLAUDE.md rule (claim-text rules) and the option-off fixture refresh (version plus six fingerprints, no derived record change); all five documentation locations now match the code; the extraction request path is untouched.
5. Redundancy / dead code: none found. Every helper and constant has a caller; packing still reads the key through its own constant.

## Security and runtime screen (not the security reviewer's checklist)

The function is pure string work: no I/O, no environment read, no model call, no regex built from the caller-supplied name, and no new write path. Reachability is unchanged and gated by `enable_named_claim_text` (`pipeline.py:1447,1487,1757-1763`), still off by default (`config.py:1245-1247`). No new committed PII. The pass-3 performance refactor is checked under "Pass 3 delta verification".

## Unresolved risks

- The `BENCHMARKS.md` "Named claim text" numbers predate both the #397 and #409 narrowings; the option is off, so no release gate reads them, but a future flip needs a fresh run (now stated at `BENCHMARKS.md:2215-2218`).
- `DERIVATION_VERSION` 2 makes every pre-bump memory stale once, including option-off stores, as documented; a model request each is the real cost and nothing at upgrade time warns.
- The `-ed` base-form list and the attributive `mine` guard remain heuristic; both are pre-existing and proposed as follow-ups.
- I did not re-run the worker's pytest/mypy/ruff evidence or the full suite.

## Pass 3 delta verification

Snapshot `4b9fe04bcc786ed888523ff19fa34e761c351480d4219e41b6f1b072f5a4ed4e`; only the delta was checked, not the whole diff.

### The four pass-2 Should Fix items: all closed

1. **Title guard is now case-sensitive.** `src/prme/ingestion/named_text.py:246` gates on `span[0][:1].isupper()`. Verified: `"The class I took was hard."` → `"The class Maya took was hard."`, `"The book I bought was cheap."` → `"The book Maya bought was cheap."`, `"the class I took …"` and `"after the war I moved …"` rewrite; `"World War I ended in 1918."`, `"Part I is long."`, `"Title I was amended."`, `"Schedule I was complex."`, `"Appendix I was updated."`, `"Act I was long."` and `"Table I is missing."` all return `None`. Tests at `tests/test_named_claim_text.py:127-128,165`.
2. **Single-quote openers and nested double quotes.** `named_text.py:114` is now `(?<!\w)['‘’](?=[A-Za-z])`, and `:208` labels a text with four or more double-quote characters. Verified: `"He said—'call me later.'"`, `"He said:'call me later.'"`, `"He said-'call me later.'"`, `"He said ’I lost my keys,’ and left."` and `'He said "she said "I lost my keys" yesterday."'` all return `None`; `"I'm 5'6\" tall."` and `"That’s mine."` are unaffected. Tests at `:157-158,181`.
3. **All-caps `I'VE`.** `named_text.py:108` now has `[vV][eE]`; `"I'VE BEEN THERE."` and `"I’VE BEEN THERE."` → `"Maya HAS BEEN THERE."`. Tests at `:136-137`.
4. **Set-phrase clause gate.** `named_text.py:232-234` applies `_MY_SET_PHRASE_AFTER` only when `previous is None`, `","` or an interjection. Verified: `"I gave my word on it."` → `"Maya gave Maya's word on it."`, `"I asked my lord for help."` → `"Maya asked Maya's lord for help."`, `"Oh my phone is dead."` → `"Oh Maya's phone is dead."`; `"Oh my god"`, `"Oh, my god"`, `"Oh my,"`, `"My god"`, `"My, what a surprise."` and `"My goodness"` still refuse. Tests at `:133-134`.

### Refactor correctness and the security fix

- `_previous_word_span` (`named_text.py:127-142`) is equivalent to the pass-2 `text[:end].rstrip()` + `[A-Za-z'’]+$` version: 200,000 random strings over letters, digits, apostrophes, commas, quotes, dashes and whitespace, 0 mismatches, including the comma index, the all-whitespace prefix, trailing digits and Unicode whitespace. `_next_word` (`:151-157`) and `_followed_by_punctuation` (`:160-164`) matched the old slice/`lstrip` forms on the same fuzz.
- The incremental double-quote state (`:209-217`) matches the old per-match prefix scan on 50,000 random pronoun/quote strings, 0 mismatches. `quote_cursor = start` re-scans the previous match's word, but no first-person match contains a quote character, so no toggle is skipped.
- No dead code: `_WORD_CHARS`, `_QUOTE_CHARS`, `_SINGLE_QUOTED_SPEECH_RE`, `_next_word`, `_previous_word_span` and every helper are used; `_sentence_initial`/`_SENTENCE_END` remain absent.
- New branches are covered: lowercase/uppercase title head, `span is None`, all three set-phrase gate arms (`None`, comma, `oh`), `_next_word` returning `None` (`"My 2 kids are here."`, `:139`), the curly `’s` branch (`"That’s mine."`, `:138`), the incremental quote toggle, `several_quotes`, and `[vV][eE]`.
- Performance is linear on the `my`/`me` paths (60 KB 0.017 s / 300 KB 0.084 s; `me` 0.012 s / 0.061 s), but **the `I` path is still O(n²)**: `named_text.py:263-276` still slices `text[position:]` and `text[cursor:]` per match. Measured `"I am. "` repeated: 100,000 (600 KB) 0.93 s, 200,000 (1.2 MB) 3.62 s. The new 20,000-token test (`tests/test_named_claim_text.py:172-177`) exercises only `"my"`. The tail copies are pre-existing, but the "single pass" claim and the security finding do not cover this path.

### Remaining delta findings

**Consider A: dropping `"word"` from the set-phrase list reintroduces a false positive that pass 2 had fixed.** `named_text.py:84` no longer contains `"word"`, so `"My word, that is surprising."` → `"Maya's word, that is surprising."` and `"Oh my word, that is surprising."` → `"Oh Maya's word, that is surprising."` (pass 2 labeled both; `main` also rewrote them, so this is pre-existing versus `main`, not a new regression). The clause-start gate (`:232-234`) now makes the two cases separable: restoring `"word"` gives `"I gave my word on it."` the rewrite (previous `"gave"`) while `"My word"` (previous `None`) and `"Oh my word"` (previous `"oh"`) still label. Add both tests.

**Consider B: the `I`-branch tail slicing above** (pre-existing; bounded fix: forward cursors instead of `text[position:]` / `text[cursor:]`, and extend the performance test to an `"I am. "` input).

Minor coverage left: `_followed_by_punctuation`'s end-of-text true branch (a text ending in `"my"`), and the two-back `None` arm of `_possessive_mine` (`named_text.py:178`); both are untested, neither is a new defect.

### Docs and consistency on this snapshot

The docs now match the code: "a first person inside double quotes, or anywhere in a text that carries single-quoted speech" (`docs/ENTITY-IDENTITY.md:98-100`, `src/prme/config.py:1264-1265`, `documentation/configuration.md:146`, `CHANGELOG.md:16`) matches `named_text.py:206-218`, including the conservative whole-text single-quote flag; "a capitalized title or numbered part" matches `:246`. `BENCHMARKS.md:2215-2218` now records the #409 narrowing alongside the #397 one and says a future flip needs a fresh run, closing pass-2 Consider 8. `src/prme/ingestion/reingest.py:151-156` clarifies that the version bump makes every source stale whether the option is on or off. The derivation fixture is unchanged from pass 2 (version 2, six new fingerprints), and the option-off settings path is unchanged.

Pass-3 checks I did not run: the worker's pytest (reported 138 passed, 12 skipped), ruff and public-API mypy, and the full suite; the pure-function evidence above is my independent verification.
