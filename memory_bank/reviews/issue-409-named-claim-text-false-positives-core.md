# Core review: issue #409, `enable_named_claim_text` false positives

Status: COMPLETE

- Base commit: 48bb094196d356579600e8f8709afdab405a050f (main)
- Reviewed snapshot: 1de1a440f1128085adb672331670ee83f2e2c2e4fa9c28ec78ca9a5ab882fc82 (uncommitted work; HEAD equals base)
- Tier: M
- Checklist: 1 Correctness & Logic (sentinel contracts, edge cases); 3 Code Quality, complexity, naming, test coverage; 4 Pattern Consistency & full-config comparison (extraction corrections, speaker reference, DERIVATION_VERSION, docs); 5 Redundancy / duplicated logic / dead code; plus security and runtime-wiring screen.

## Scope

`src/prme/ingestion/named_text.py` rewritten for the nine reported false
positives (case-sensitive matching, quote refusal, interjection/set-phrase
refusal, noun `mine`, Roman-numeral `I`, `lay`/`weed`, all-caps verbs, curly
apostrophe), with tests in `tests/test_named_claim_text.py`, a
`DERIVATION_VERSION` bump to 2 in `src/prme/ingestion/reingest.py` and the
refreshed `tests/fixtures/derivation/trip_derived.json`, plus CHANGELOG,
`docs/ENTITY-IDENTITY.md`, `documentation/configuration.md` and
`src/prme/config.py` description updates.

Support reads: `src/prme/ingestion/pipeline.py:1435-1490,1733-1782`;
`src/prme/ingestion/reingest.py:89,140-156,258-302`;
`src/prme/storage/reingest.py:103-107`;
`src/prme/retrieval/packing.py:45-46,895-920`;
`src/prme/config_audit.py:258-272`;
`src/prme/config.py:1245-1272`;
`tests/test_derivation_version.py:30-158`.

## Verification performed

- Ran the function against all nine issue cases and the pre-existing
  regression cases (`uv run python`): all nine now behave as the issue asks.
  Quoted speech (straight and curly), `ME/CFS`, `Augusta, ME`, `Oh my god` /
  `Oh my,`, `a coal mine`, `World War I`, `lay`, `weed`, all-caps `I AM`
  (`Maya IS PREGNANT.`) and `I can’t` (curly preserved) are correct; the
  existing rewrite/refusal cases are unchanged.
- Compared against `main`'s `named_text.py` loaded in memory: every reported
  false positive was real on main (`Maya has Maya/CFS`, `Augusta, Maya is
  cold.`, `Maya's husband said "Maya was fired."`, `Oh Maya's god`,
  `a coal Maya's`, `World War Maya`, `Maya lay`, `Maya weed`, `Maya Is`,
  `Maya can't`).
- `uv run pytest -q tests/test_named_claim_text.py tests/test_derivation_version.py`
  -> 81 passed, 12 skipped; `tests/test_config_audit.py` -> 7 passed.
- `uv run ruff check` on the four changed Python files -> clean.
  `uv run mypy --strict src/prme/ingestion/named_text.py` reports one
  `words_after` var-annotation error, but that line is unchanged from main
  and CI's mypy only checks `tests/typing/public_api.py` with
  `--follow-imports=silent`, so it is not introduced here.
- No coverage tooling is installed; branch coverage below is by inspection.

## Findings

### Must Fix

None.

### Should Fix

1. **`_possessive_mine` refuses the common possessive `mine` after a
   negation or adverb, and the docs say the opposite.**
   `src/prme/ingestion/named_text.py:81-83,147-150,188-196` treat `mine` as
   the possessive only when the preceding word is a copula, `of`, a
   coordinator, a possessive noun, or start of text; every other predecessor
   is assumed to be the noun. That is wrong for a normal pronoun use, and it
   is a new regression versus main:
   `"The book is not mine."`, `"It isn't mine."`, `"That was never mine."`,
   `"This is really mine."`, `"That book is also mine."`,
   `"The car is definitely mine."`, `"The money is all mine."`,
   `"That is ours, not mine."` all now return `None` (whole sentence labeled
   `"Maya: ..."`), where main rewrote them to `... Maya's.`. The docs
   (`docs/ENTITY-IDENTITY.md:99-100`, `documentation/configuration.md:146`,
   CHANGELOG.md:16) say `mine` is the noun only "after a determiner or a
   noun", which does not match the code. Suggested fix: invert the test -
   default to the pronoun and refuse only after an article/determiner or in
   an attributive-noun context (e.g. `a`, `the`, `this`, `that`, `gold`,
   `coal`), or add the negation/adverb words to a pronoun-before set. Add
   tests for at least `"That is not mine."` and `"A friend of mine"`.

2. **The Roman-numeral guard over-refuses a genuine subject `I` after a
   proper noun.**
   `src/prme/ingestion/named_text.py:203-205` refuses whenever the word
   immediately before `I` is capitalized and not sentence-initial. That
   catches `World War I`, but also every ordinary adverbial or prepositional
   phrase ending in a proper noun. New regressions versus main:
   `"In Denver I found a job."` -> `"In Denver Maya found a job."` on main,
   now labeled `"Maya: In Denver I found a job."`; same for
   `"On Monday I went home."`, `"Last Monday I went home."`,
   `"Yesterday at Northwind I met Sam."`. The label form is safe, but this is
   a common sentence shape and the docs
   (`docs/ENTITY-IDENTITY.md:100-101`, `documentation/configuration.md:146`)
   state the rule as "a capitalized word before the I reads as a title"
   without noting these false negatives. Suggested fix: require the
   capitalized predecessor to itself be part of a title-like phrase (e.g. a
   preceding non-sentence-initial capitalized word, so `World War I` matches
   but `In Denver I` does not), or otherwise narrow the guard. Add a test for
   a proper-noun adverbial, e.g. `"In Denver I found a job."`.

3. **`_INTERJECTIONS` is broader than the set phrases the issue names and
   refuses ordinary possessives.**
   `src/prme/ingestion/named_text.py:79-80,188-191`. The comment claims "An
   interjection before 'my' makes it part of a set phrase", but only `Oh my`
   (and `Oh my,`) is a set phrase here; `wow`, `hey`, `ah`, `boy`, `gosh`,
   `gee`, `ooh`, `ugh`, `hmm`, `alas` do not form one. New regressions versus
   main: `"Wow my new phone is great."`, `"Hey my ride is here."`,
   `"Ah my head hurts."`, `"Boy my legs are tired."`, `"Gosh my day was
   long."` are now all refused and labeled, where main rewrote the `my`.
   Suggested fix: restrict the set to the actual set phrase (e.g. `{"oh"}`
   or match the two-word phrase), keeping `_followed_by_punctuation` for the
   `Oh my,` form. No test covers these interjections.

### Consider

4. **Dead code left by the `_previous_word` refactor.**
   `src/prme/ingestion/named_text.py:121-124` defines `_previous_word` as a
   lowercase wrapper, but after `name_first_person` switched to
   `_previous_word_span` at line 185 nothing calls it (`main` used it at the
   old line 137). The only references are the definition and the wrapper's
   own call to `_previous_word_span`. Remove it, or keep it if a caller
   outside the diff is planned (none exists in `src/` or `tests/`).

5. **New branches not exercised by tests.** In
   `src/prme/ingestion/named_text.py`, the true return of
   `_followed_by_punctuation` (line 142-144) is never reached:
   `"Oh my,"` short-circuits on `previous in _INTERJECTIONS`
   (`"oh"` is in the set) before the `or`, and no test has `my` followed by
   punctuation after a non-interjection (e.g. `"My, what a surprise."`).
   In `_possessive_mine`, only `is` (true) and `a` (false) are exercised;
   the `None`, `of`, coordinator and `'s` paths are not, and `"A friend of
   mine called."` (documented in the code comment) is untested.
   `tests/test_named_claim_text.py:115-137,154-159`.

6. **The contraction lookahead was broadened and now refuses non-contraction
   apostrophes.** `src/prme/ingestion/named_text.py:95` dropped the
   `(?:m|ve|ll|d)\b` group from the second alternative, so any `I` followed
   by `'` or `’` is a match. `"the I's in the report"` and
   `"She dotted the I's and crossed the T's."` now return `None` (labeled),
   where main returned the text unchanged because the apostrophe was not a
   contraction. Restoring `(?=(?:m|ve|ll|d)\b)` after the apostrophe would
   keep the contraction cases and drop the false refusal.

7. **All-caps emphasis is not handled in the attached-contraction branch.**
   The new `verb_word.isupper()` handling
   (`src/prme/ingestion/named_text.py:244-250`) is only in the
   `key in _AFTER_I` path; the contraction path at lines 206-215 emits the
   lowercase form, so `"I'M PREGNANT."` becomes `"Maya is PREGNANT."`, not
   `"Maya IS PREGNANT."`, while the CHANGELOG entry (CHANGELOG.md:16) states
   that an all-caps verb keeps its emphasis. Optional: uppercase `form` when
   the matched contraction group is uppercase.

8. **Upgrade note does not state that the global version bump makes
   option-off stores stale too.** `DERIVATION_VERSION` is always part of the
   fingerprint (`src/prme/ingestion/reingest.py:89,108-112`), and
   `repo.current` matches on that fingerprint
   (`src/prme/storage/reingest.py:103-107`), so the bump to 2 makes every
   memory stored under version 1 stale for `reingest()`, including stores
   that never enabled the option. `docs/ENTITY-IDENTITY.md:112-114` still
   says memories derived without the option "stay current while it is off",
   and CHANGELOG.md:16 says "its records with the option off are unchanged"
   without noting the one-time re-derivation (a model request each).
   CLAUDE.md ("Two fundamental blockers"/derivation section) requires a bump
   to be justified precisely because of that cost; the CHANGELOG should state
   it plainly. The bump itself is the right call (the old and new rules have
   identical derivation settings while the option is on, so nothing else
   would re-derive the old claims).

9. **Misnamed test.** `tests/test_named_claim_text.py:141-144`
   `test_a_sentence_with_no_first_person_is_left_as_it_is` starts by
   asserting a rewrite of `FAMILY_SENTENCE`, which does have first person
   ("My husband ... my child ..."). Split the unchanged-text assertions from
   the rewrite assertion, or rename the test.

## Checklist results

1. Correctness / sentinel contracts: all nine reported cases fixed and
   verified against main; `None` and unchanged-text sentinels behave
   correctly (a refused sentence discards any partial rewrite). Two new
   over-refusals (findings 1, 2) and one broader-lookahead over-refusal
   (finding 6).
3. Code quality / naming / coverage: helpers are small and well named;
   ruff clean; dead wrapper (finding 4), coverage gaps (findings 5, 9), one
   inconsistent caps branch (finding 7).
4. Pattern consistency / docs: `config_audit.py:268-272` already registers
   the toggle; `config.py:1257-1263`, `documentation/configuration.md:146`,
   `docs/ENTITY-IDENTITY.md:95-101`, CHANGELOG.md:16 and
   `reingest.py:151-155` are updated consistently with the
   extraction-corrections and speaker-reference options; the
   `DERIVATION_VERSION` bump matches the CLAUDE.md rule. One upgrade-note
   accuracy issue (finding 8).
5. Redundancy / dead code: `_previous_word` is newly unused (finding 4); no
   duplicated rewrite logic elsewhere (retrieval only reads
   `packing.py:46,908-920`).

## Security and runtime wiring screen

- `named_text.py` is a pure string function: no I/O, no env reads, no model
  call, and the caller-supplied `name` is only concatenated into the text
  (no regex built from it, no `eval`/format injection risk). No change.
- Reachability: the rewrite and the `has_first_person` name lookup are both
  gated by `enable_named_claim_text`, still off by default
  (`pipeline.py:1447,1487,1757-1763`, `config.py` default `False`,
  `tests/test_named_claim_text.py:329-333`). With the option off, storage and
  retrieval are unchanged; packing only reads the metadata key.
- No new PII in committed text: tests and docs use fictional names (Maya,
  Theo, Nico, James, Dana); no real user data is added.
- The new behavior does not widen what the option can touch: no new read or
  write path, no network, no file.

## Confirmed intentional trade-offs (not findings)

- Only the plain spellings match, so all-caps `ME`/`MY`/`MINE`/`MYSELF` and
  lowercase `i` are left alone (`"Send it to ME."`, `"MY HUSBAND IS THEO."`,
  `"i think so"`). Documented in `named_text.py:26-32,93-95` and the docs; the
  issue explicitly asks to stop matching `ME/CFS` and `Augusta, ME`. It does
  mean an emphatic all-caps first person is no longer named; noted here so
  the trade-off is visible, not as a defect.
- The existing test `("How do i enroll my newborn? I am pregnant.", None)` was
  changed to expect a rewrite (`"How do i enroll Maya's newborn? Maya is
  pregnant."`) because the lowercase `i` no longer matches; the resulting
  text is coherent and the change is a direct consequence of the requested
  case-sensitivity.
