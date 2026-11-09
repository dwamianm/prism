# Issue #498 core review - month without year resolves to the past

Status: COMPLETE

Base commit: `d2bc51b3b75f92645f44305bb9955ca37a738ba6`
Reviewed snapshot: `95842b765995c9ef31667c7adc3ca829616bffbbc85a6e4b271d3f654cc86db8`
Reviewer: core (checklists 1, 3, 4, 5)

Note on this file: a checklist-8 (adversarial) report was already written to this path while this
review was running. It is preserved verbatim in the appendix at the end of this file. Nothing of it
was dropped.

## Verdict

The reported member-v1 case and the four acceptance branches the new tests exercise do resolve as
#498 asks, the privacy forward-start path is untouched, and relative and explicit-year references are
unchanged. The change is not ready as written: the future-cue word list contains common nouns and
past-tense verb forms, which moves some clearly past statements from the correct past year to next
year (F1, verified against the real functions), and the recorded derivation version rule was not
followed (F2). The rest are test-coverage and quality items.

## Findings

### F1 - Must Fix - `_FUTURE_CUE` treats nouns and past-tense forms as future, regressing past statements

`src/prme/ingestion/temporal.py:33-48`. The pattern `book(?:s|ed|ing)?` (line 43),
`reserv(?:e|ed|ing)` (line 43), `schedul(?:e|ed|ing)` (line 41), `hope(?:s|d)?` (line 39),
`plan(?:s|ned|ning)?` (line 36) and `expect(?:s|ed|ing)?` (line 38) match the noun readings ("a
book", "a nature reserve", "the schedule") and the past-tense forms ("hoped", "planned", "expected").
`_PAST_CUE` (`temporal.py:51-62`) has no "read", "checked" or "couldn't". Because the nearer cue wins
(`temporal.py:127-131`), the noun is often nearer to the month than the past verb, so the past branch
of the acceptance criteria is not reached.

Verified by calling the real functions with base 2026-11-02 (old code returned the past year for all
of these, so each is a regression, not only an incomplete fix):

- `I am planning a trip for us in December to Jamaica. Do we need vaccines?` -> future, 2026-12-02 (correct, the reported case)
- `I finished a book in December.` -> future, 2026-12-02 (past cue "finished" is 20 characters from
  the month, the noun "book" is 7, so future wins; old code: 2025-12-02)
- `I read a book in December.` -> future, 2026-12-02 (old: 2025-12-02)
- `I hoped to visit in December.` -> future, 2026-12-02 (old: 2025-12-02)
- `We planned the trip in December.` -> future, 2026-12-02 (old: 2025-12-02)
- `I visited a nature reserve in December.` -> future, 2026-12-02 (old: 2025-12-02)
- `I checked the schedule in December.` -> future, 2026-12-02 (old: 2025-12-02)

This violates acceptance branch 2 ("the most recent past occurrence when it talks about something
that already happened") and is a behavior regression introduced by this change.

Fix: keep only verb-distinctive inflections (`booked|booking`, not bare `book|books`) and either drop
the `-ed` forms of `plan|expect|hope|aim|intend|schedule` from `_FUTURE_CUE` or make them ambiguous
(no cue) so the nearer-cue or nearest-occurrence rule decides. Add one test per sentence above
asserting the past branch; the existing tests only use "went" and "planning".

### F2 - Should Fix - `DERIVATION_VERSION` stays 2 although the change alters derived records

`src/prme/ingestion/reingest.py:82-89` states the rule: bump when a change alters what an unchanged
source derives. This change alters the derived `resolved_date`, `event_time` and `valid_from` of every
month-without-year claim (`src/prme/ingestion/pipeline.py:1698-1703`, `1838-1842`, `1871-1891`). The
guard test stays green only because its single recorded month ref is `late March next year`
(`tests/fixtures/derivation/trip_derived.json:999`), which dateparser cannot parse under either
preference (verified: both `parse("late March next year", PREFER_DATES_FROM=past|future)` return
`None`), so the pinned dump does not move. `tests/test_derivation_version.py:43-49` describes the test
as the reminder for exactly this situation, not as the rule itself. `CHANGELOG.md:12` presents the
unchanged version as intended ("reingest() finds every stored memory current"); the consequence is
that no memory stored before the fix is ever corrected by the supported path.

Fix: bump `DERIVATION_VERSION` to 3 in the same pull request and refresh the version in
`tests/fixtures/derivation/trip_derived.json` (the records are unchanged, only the recorded number
moves; see `tests/test_derivation_version.py:138-140`). If the owner decides old memories should keep
their date, record that decision against the rule instead of relying on the fixture's blind spot. Not
an acceptance-criterion blocker for #498, but a repo-convention violation with a user-visible
consequence. The checklist-8 report (checklist 7, appendix) reached "accept as documented"; I
disagree that the guard's silence is evidence, and the bump decision should be explicit.

### F3 - Should Fix - the uncertain branch and the nearer-cue rule have no test

`src/prme/ingestion/pipeline.py:1840-1842` writes `resolved_date_uncertain`, but the only test that
mentions it asserts its absence (`tests/test_month_without_year.py:106`). No test exercises the no-
tense path end to end, so a future edit could drop the write without a failure. The documented
nearer-cue / tie rule (`src/prme/ingestion/temporal.py:108-110`, `127-131`) is also untested: no test
has both a future and a past cue in one sentence.

Fix: add an ingestion test with `temporal_ref="March"` in a no-tense quote, asserting the nearest
`event_time` and `metadata["resolved_date_uncertain"] is True`; add one test for a mixed sentence
where the nearer cue wins and one where the distances tie (expect uncertain). Minor: the
`# noqa: F811` on `tests/test_month_without_year.py:87` is unnecessary; other tests take the imported
`config, user` fixtures as arguments without it (for example `tests/test_durable_ingestion.py:32`).

### F4 - Should Fix - a month equal to the event month jumps a full year ahead

`src/prme/ingestion/temporal.py:155-158`. Verified: ref "November", base 2026-11-02, source
`I am planning a trip in November.` -> 2027-11-02. The old code returned 2026-11-02, the current
month, which is the right reading for "in November" said in November. The checklist-8 report records
the same defect as its Scenario 2 and rates it Fix now; I concur it is a regression for this input.
Fix: when the resolved future parse lands in the same month as `base`, take the same-year occurrence
(or mark the result uncertain) instead of letting dateparser roll the year. Add a same-month test.

### F5 - Consider - a two-digit year is not recognized as a year

`src/prme/ingestion/temporal.py:28`. `_NUMERIC_YEAR` only matches 1600-2099, so `March '24` is
treated as a month without a year and now takes the nearest occurrence: 2027-03-24 (old: 2026-03-24;
dateparser reads `'24` as day 24, so both are wrong, but the new result moves further from 2024).
`CHANGELOG.md:12`'s "References that name a year ... resolve exactly as before" is therefore not
literally true. Fix: also match `(?:'|’)\d{2}\b` in the year test (the apostrophe distinguishes it
from a day of month) and add a test.

### F6 - Consider - the uncertainty flag has no reader and is dropped by the privacy projection

Repo-wide `rg resolved_date_uncertain` finds only the writer (`src/prme/ingestion/pipeline.py:1842`)
and the new test. `project_admitted_node` copies only `_CLAIM_FIELDS`
(`src/prme/ingestion/admitted_statements.py:26-31`) and `_copy_fields` (line 52-58) coerces every value
through `_text` (line 39-42), which raises `PrivacyDenied` for a bool, so the flag cannot be added to
that whitelist naively; it is also outside `claim_merge.claim_key`
(`src/prme/ingestion/claim_merge.py:56-62`). Protected reads (`src/prme/privacy/read_graph.py:146`)
therefore never see the uncertainty. The acceptance requirement is to "record" it, which the durable
unprotected node metadata does, so this is not a blocker. Fix if a reader is wanted: project the flag
as a boolean with its own line and bump `STATEMENT_VERSION`, or document that protected claims carry
no uncertainty signal. The checklist-8 Scenario 4 reaches the same conclusion.

### F7 - Consider - dateparser settings are duplicated between the two modules

`src/prme/ingestion/temporal.py:145-151` rebuilds the settings dict that
`src/prme/ingestion/pipeline.py:2204-2210` owns. A future change to `_resolve_temporal_reference`
(for example a language pin or another dateparser setting) will silently not apply to the month path.
Fix: extract a settings builder into `temporal.py` and use it from both places, or pass a parse
callable into `resolve_month_without_year`.

### F8 - Consider - another month-name table in a repo that already has several

`src/prme/ingestion/temporal.py:23-27` adds a month-name regex alongside
`src/prme/ingestion/resolution.py:86-95` and `116-119`,
`src/prme/retrieval/claim_verification.py:47-49`, `src/prme/retrieval/scoring.py:53-70`,
`src/prme/retrieval/packing.py:90-92`, `src/prme/ingestion/grounding.py:27` and
`src/prme/retrieval/query_analysis.py:80-81`. The new one is the only one that needs abbreviations,
but the maintenance risk is real (a locale or spelling added in one place and not the others). Fix:
move one month-name helper with the abbreviation set into `prme/_temporal.py` and reuse it, or accept
the duplication as the repo's existing per-module style and say so.

### F9 - Consider - casefold offsets are used to slice the original string

`src/prme/ingestion/temporal.py:114-120` finds `start` in `source_text.casefold()` (and `position` in
`sentence.casefold()`) but slices `source_text` with `start` and `len(temporal_ref)`. `str.casefold()`
can change length (for example "ß" -> "ss"), so for non-ASCII turn text the sentence window and the
cue distances can be wrong and the verbatim check can miss the reference. Fix: casefold once and do
all index work on the casefolded string, or use a length-preserving transform. Add a non-ASCII test if
the fallback matters.

### F10 - Consider - the release note's day differs from the reported case

`CHANGELOG.md:12` says the old result was `2025-12-02`; #498 records `2025-12-09`. With
`RELATIVE_BASE` 2026-11-02 the old code does produce `2025-12-02` (dateparser inherits the base day,
verified), so the two numbers differ by the reference day. Reconcile the release note with the issue
so the fixed case is described with the same numbers.

## Checklist results

1. **Correctness.** Reported turn, past branch, no-signal-nearest-forward, no-signal-nearest-backward,
   explicit year, day without year, sentence isolation and relative references all behave as the
   acceptance criteria say (tests and direct probes). Failures: F1 (past branch reached for common
   phrasings, with a regression), F4 (same-month future rollover), F5 (two-digit year). The `_resolve_temporal`
   default change from `"past"` to `None` has three call sites: `pipeline.py:1407` via
   `resolution.py:301`, `privacy/admission.py:638` via `grounding.py:87-88`, and the tests. The privacy
   call passes `prefer_dates_from="future"` explicitly, so the new branch and the protected
   `REQUIRE_PARTS` / date-order / `search_dates` checks are bypassed and unchanged. In `resolution.py`
   the callback receives the replaced segment as `temporal_ref` with no `source_text`, and a bare
   month is gated out by `_RELATIVE_DATE_CUES` (`resolution.py:101-104`); the cue+month phrases
   (`last/next/this/coming December`, `December last/next year`) all parse to `None` under both
   preferences, so that path is unchanged. No caller regression found beyond F1.
2. **Quality and test coverage.** The unit tests are clear and pin the four branches and the exact
   member-v1 turn. Gaps: F3 (uncertain writer, nearer-cue rule), F1 regression sentences, F4 and F5
   cases. No test asserts the exact stored day, only year and month, which is acceptable.
3. **Pattern consistency / full-config comparison.** No config or env surface change and no
   `config_audit` entry is needed. The one preference knob is `prefer_dates_from`; grep confirms no
   caller passes `"past"` and only `grounding.py` passes `"future"`. Retrieval-side resolution is
   consistent enough: `query_analysis` uses dateparser's default `current_period` (which resolves
   "December" from 2026-11-02 to 2026-12), and `temporal_relations._parse_text_date` requires day
   precision (`temporal_relations.py:252-265`), so neither applies an opposite year. F7 and F8 cover
   the duplicated settings and month tables.
4. **Redundancy (NEW_SYMBOLS).** No existing sentence-tense classifier exists; `named_text._past_tense`
   is a morphology predicate, not a cue list, and `resolution._MONTHS` maps words to numbers rather
   than detecting a month ref. `TemporalResolution` and the four functions are new and used from
   exactly one production module (`pipeline.py:43-46`), so they are not dead exports. The real
   redundancy is F7 and F8.

## Acceptance scenarios, verified against the real functions (base 2026-11-02 19:30 UTC unless noted)

| Input | Expected branch | Verified result |
|---|---|---|
| member-v1 `I am planning a trip for us in December ...` | future -> next occurrence | 2026-12-02, uncertain False |
| `We went to Jamaica in December.` | past -> most recent past | 2025-12-02, uncertain False |
| `My birthday is in March.` | no signal -> nearest + uncertain | 2027-03-02, uncertain True |
| `My birthday is in March.`, base 2026-04-02 | no signal -> nearest (backwards) | 2026-03-02, uncertain True |
| `March 2024` | explicit year unchanged | 2024-03-02 |
| `December 9`, planning turn | future with day | 2026-12-09 |
| `yesterday`, base 2026-11-02 19:30 | relative unchanged | 2026-11-01 19:30 |
| privacy `October 21`, explicit future pref | protected path unchanged | 2026-10-21 (existing test) |
| `I finished a book in December.` | past -> 2025-12-02 | **2026-12-02 (F1)** |
| `I hoped to visit in December.` | past -> 2025-12-02 | **2026-12-02 (F1)** |
| `I am planning a trip in November.` | next occurrence of November | **2027-11-02 (F4)** |
| `March '24` | year named -> 2024 | **2027-03-24 (F5)** |

## Security, wiring and tier

No new external calls, credentials, env reads, schema, migration, prompt or extraction-request
change. `dateparser.parse` runs under `prme._temporal.DATEPARSER_LOCK` (`temporal.py:152-153`), one
call when the tense is known and two when it is not; the regexes are linear over the evidence quote,
and the old path already sent the same model-supplied text to dateparser, so the only increment is one
bounded parse on ambiguous month refs. The new metadata value is a JSON boolean and cannot break
serialization. Wiring is correct: imports resolve, one production call site, and ruff passed in the
worker evidence. **No discovered risk raises the tier.** I did not verify the worker's fast-tier or
baseline evidence; the checklist-8 report's process note (appendix) raises that independently and the
worker/test owner owns it.

## Unresolved risks

- Any cue-list heuristic will miss phrasings; F1 and F3 are the ones this change should pin now.
- Old stored month-only claims stay wrong unless F2 is resolved.
- The `resolved_date_uncertain` flag is inert (F6); a future consumer must not assume protected claims
  carry it.

## Evidence I ran (read-only)

- `git diff main -- src/prme/ingestion/{pipeline,temporal}.py CHANGELOG.md`; read the untracked test.
- Repo-wide `rg` for `_resolve_temporal|resolve_month_without_year|names_month_without_year|classify_tense|TemporalResolution|resolved_date_uncertain|PREFER_DATES_FROM|resolve_date`, plus month-name tables and derivation-version readers.
- `.venv/bin/python -c` probes of dateparser 1.3.0 with the same settings dicts, and of the real
  `classify_tense` / `resolve_month_without_year` on the sentences in F1, F4 and the scenario table.
  No files were written outside this report and no install or network call was made.

---

## Appendix - preserved verbatim: checklist-8 (adversarial) report that occupied this path

# Issue #498 — month without year resolves to past — adversarial review (checklist 8)

Status: COMPLETE

Base: `main` @ `d2bc51b3b75f92645f44305bb9955ca37a738ba6`
Snapshot: `95842b765995c9ef31667c7adc3ca829616bffbbc85a6e4b271d3f654cc86db8`
Tier M: 4 scenarios kept. Changed paths reviewed: `src/prme/ingestion/temporal.py`,
`src/prme/ingestion/pipeline.py`, `CHANGELOG.md`, `tests/test_month_without_year.py` (untracked).

## Checklist results

1. **Premise attack (regex tense classifier feeding dateparser).** The layer is defensible for a
   one-line date parser: dateparser has no notion of the turn, and the turn text is the only place the
   signal exists. The premise holds for the reported turn. The heuristic's known weakness is the cue
   vocabulary, not the placement; see Scenario 1.
2. **Caller blast radius.** `_resolve_temporal` is called as a `resolve_date` callback only from
   `resolution.py:301` (fact-text resolution) and from `grounding.py:87` via `validate_private_grounding`
   (`admission.py:638`). Grounding passes `prefer_dates_from="future"` explicitly
   (`grounding.py:87-88`), so the new month branch is bypassed and the forward-start checks
   (`REQUIRE_PARTS`, date-order repeat, `search_dates` verbatim span) are untouched. In `resolution.py`
   the callback receives the *replaced segment* as `temporal_ref` and no `source_text`; a bare month
   segment is gated out by `_RELATIVE_DATE_CUES` (`resolution.py:101-104`), and every cue+month phrase
   I probed (`next December`, `last December`, `this December`, `coming December`, `December last year`,
   `December next year`, ...) parses to `None` under both preferences with dateparser 1.3.0, so that path
   behaves as before. No caller regression found.
3. **Sibling date-resolution entry points.** `store()` never resolves a `temporal_ref` (no call site
   outside `pipeline.py`). Query analysis (`query_analysis.py:332-345`) uses `search_dates` with no
   preference and only shapes the query window; temporal relations (`temporal_relations.py:253-262`)
   keeps `PREFER_DATES_FROM="past"` and a day-precision gate. Neither writes a stored month/year.
   `reingest()` reuses the pipeline path, so re-derived sources get the new rule; old sources are
   skipped (see the DERIVATION_VERSION decision below).
4. **dateparser edge cases.** End-of-month bases do not raise: `February` on 2026-01-31 parses to
   2026-02-28 (dateparser clamps), and `December 31`/`December 30` parse as days. Abbreviated months
   (`Dec`, `Jan`) are covered by `_MONTH_NAME` (`temporal.py:23-27`). Non-English month names
   (`diciembre`) are not matched, so they keep the historical past preference; that is a limitation of
   the new rule, not a regression (old behavior). A same-month reference with a future cue is a
   regression; see Scenario 2. Repeated month mentions are Scenario 3.
5. **Uncertainty flag lifecycle.** Written at `pipeline.py:1840-1842`; read nowhere in `src/` (repo-wide
   `rg resolved_date_uncertain` finds only the writer and the new test). The flag's only real hole is
   the privacy projection; see Scenario 4.
6. **Scale / lock.** A month-without-year claim costs one `dateparser.parse` (tense known) or two
   (no signal) under `DATEPARSER_LOCK` (`temporal.py:152-153`), released between calls; the regex scan
   is over the evidence quote only. Month-only refs are a small fraction of claims, and the old path
   already paid one parse without `languages`, so the increment is bounded and not a finding.
7. **DERIVATION_VERSION / stored memories.** `DERIVATION_VERSION` stays 2 (`reingest.py:89`).
   `reingest()` skips every source already marked current, so claims stored before the fix keep their
   past year forever. The pinned derivation fixture happens not to expose this: its only month-without-
   numeric-year ref is `late March next year` (`tests/fixtures/derivation/trip_derived.json:999`), which
   dateparser cannot parse under either preference, so the dump does not change and the guard stays
   green without a bump. Decision: **Accept as documented**, with a caveat. The CLAUDE.md bump rule says
   to bump when "you alter what an unchanged source derives", and this change does (a planning turn now
   derives a different `event_time`), so the change relies on the issue's scope ("resolves ... against
   the turn's own event time") being future-ingest only. A store polluted by the old default has no
   supported repair path other than deleting and re-ingesting; that is worth a follow-up ticket, not a
   blocker here.

## Scenario 1 — The cue vocabulary leaves confident future plans resolving to the past

- **Trigger.** A plan stated with a phrasing the regex lists do not cover: `I was hoping to go to
  Jamaica in December.`, `I was thinking about Jamaica in December.`, `I'll go to Jamaica in June.`,
  `I start a new job in June.` (event time 2026-11-02).
- **Mechanism.** `_FUTURE_CUE` (`temporal.py:33-48`) has no `hoping` (`hope(?:s|d)?` matches only
  hope/hopes/hoped), no `thinking about`/`considering`, no `'ll`/`’ll` contraction, and no
  present-tense plan verbs (start, begin, leave, fly, arrive). `classify_tense` (`temporal.py:121-131`)
  then either sees only the past cue `was` (nearest-cue wins) and returns `"past"`, or returns `None`.
  Verified with the new module: `I was hoping to go to Jamaica in December.` → `2025-12-02`,
  `uncertain=False`; `I was hoping to visit in March.` → `2026-03-02`, `uncertain=False`;
  `I'll go to Jamaica in June.` → `2026-06-02` (past), `uncertain=True`.
- **Likelihood.** High for conversational data: "I was hoping/had hoped", "I'll", and present-tense
  plan verbs are common, and the benchmark fixtures already contain "I start as analytics manager on
  May 4." (`benchmarks/conversations/chat-v1.json`), which only works because the nearest occurrence
  happens to be future there. The reported turn uses "planning", which is covered.
- **Impact.** A future plan is stored at a past `event_time`/`valid_from` (or, for the no-signal
  cases, the nearest occurrence), so it sorts as old and the year is wrong. The `"past"` cases are not
  marked uncertain, so the only signal the change offers is absent exactly where the guess is wrong.
- **Detection.** Silent. The wrong year is visible only by reading `resolved_date`/`event_time`, and
  the uncertainty flag is not set for the `was hoping` cases.
- **Cheapest mitigation.** Extend `_FUTURE_CUE` with `'ll|’ll`, `hoping`, `thinking about|of`,
  `considering`, and common present-tense plan verbs; add one test per form in
  `tests/test_month_without_year.py`. Size **S** (one regex, a few tests).
- **Verdict.** **Fix now.** It violates the acceptance wording "resolves to the next occurrence ...
  when the turn talks about a plan or the future" for ordinary phrasings.
- **Label.** Pre-existing outcome (the old default also gave 2025-12 for these turns), but the gap is
  in the newly introduced classifier and it is cheap to close here.

## Scenario 2 — A plan made in the current month jumps a full year ahead (regression)

- **Trigger.** A future-cued turn that names the month the turn itself is in, for example
  `I am planning to launch in November.` at event time 2026-11-02 or 2026-11-20.
- **Mechanism.** `resolve_month_without_year` (`temporal.py:155-158`) parses with `PREFER_DATES_FROM=future`;
  dateparser's month-only future parse of a month equal to the base month returns the *next year's*
  occurrence (2027-11-02). The old code used `past`, which returned the current occurrence
  (2026-11-02). Verified both ways against dateparser 1.3.0.
- **Likelihood.** Low to moderate: it needs a plan/belief turn that names the current month, but that
  is a normal thing to say ("I am planning to take the exam in November").
- **Impact.** The claim is dated about twelve months in the future, becomes the newest record under
  recency (`effective_time` = `event_time` = `valid_from`, `pipeline.py:1868-1885`), and is misdated for
  any as-of query. This is a regression, not just an incomplete fix: the old always-past preference
  happened to be right for this case.
- **Detection.** Silent (`uncertain=False`).
- **Cheapest mitigation.** In `resolve_month_without_year`, after a `future` parse, if the resolved
  month equals `base.month` use the same-year occurrence (or mark it `uncertain=True`) instead of
  `base.year + 1`. Size **S**.
- **Verdict.** **Fix now.**
- **Label.** Newly introduced (regression).

## Scenario 3 — The first verbatim month occurrence decides, not the one the claim refers to

- **Trigger.** One evidence passage mentions the same month twice with different tenses and the
  extraction writes the ref without a year, e.g. `We are planning a trip in December. Last December we
  went to Jamaica.` with `temporal_ref="December"` on the past claim, or
  `My sister was born in March. We are planning a party in March.` on the plan claim.
- **Mechanism.** `classify_tense` finds the *first* occurrence (`temporal.py:114`) and classifies only
  that sentence (`temporal.py:119-131`). Verified: the first case returns `2026-12-02` (future,
  `uncertain=False`) for the past claim; the third returns `2026-03-02` (past, `uncertain=False`) for
  the party plan. When the target month is the later one, the result is confidently wrong; in the first
  case the old always-past default was correct. The same mechanism misfires for `May` when the earlier
  "may" is the modal verb.
- **Likelihood.** Low to moderate: it needs an evidence quote that spans both sentences and a model
  that writes the ref without the year (both plausible; extraction quotes are often whole turns, and
  `fact.evidence_quote or event.content` at `pipeline.py:1701` uses the quote).
- **Impact.** A confidently wrong year on the claim, worse than the old uniform past default when the
  first mention is the future one. The uncertainty flag does not fire.
- **Detection.** Silent.
- **Cheapest mitigation.** If the reference occurs more than once in the passage, return `None` from
  `classify_tense` (no signal falls back to nearest + `uncertain=True`). Size **S** (one count check,
  one test).
- **Verdict.** **Fix now** (small), or a follow-up ticket if the parent prefers the reported case only.
- **Label.** Newly introduced (the ambiguity handling is new; the old code never looked at a sentence).

## Scenario 4 — The uncertainty flag is dropped by privacy projection and read by nobody

- **Trigger.** Any privacy-protected ingestion of a claim whose month had no tense signal, or any
  privacy read of such a claim.
- **Mechanism.** The flag is written to node metadata at `pipeline.py:1840-1842`, but
  `project_admitted_node` (`admitted_statements.py:145-182`) copies only `_CLAIM_FIELDS`
  (`admitted_statements.py:26-31`), which omits `resolved_date_uncertain`; `project_admitted_graph`
  (`admitted_statements.py:252-260`, called from `planning.py:219-222`) applies it at write time, and
  `reads.py:263` applies it again on read. The flag is also read nowhere in `src/` (repo-wide `rg`).
  Note that adding it naively to `_CLAIM_FIELDS` would break protected ingestion: `_copy_fields`
  (`admitted_statements.py:52-58`) calls `_text(value)`, which raises `PrivacyDenied` for `True`.
- **Likelihood.** Certain for every protected claim (the projection is unconditional); the flag is
  inert for unprotected stores too, because no consumer exists.
- **Impact.** The acceptance criterion "record the resolution as uncertain" is met only in unprotected
  stores; a protected store cannot tell a guessed year from a parsed one, and any future consumer that
  reads the flag will silently treat protected claims as certain. No current behavior changes.
- **Detection.** Silent.
- **Cheapest mitigation.** Copy the flag as a boolean alongside the projected metadata (a dedicated
  line, not `_copy_fields`) and add it to the admitted-statement test, or explicitly document that
  protected claims carry no uncertainty signal. Size **S**.
- **Verdict.** **Follow-up**, unless the parent wants the recording requirement to hold for every
  store, in which case Fix now.
- **Label.** Newly introduced.

## Attacked and failed (why each did not become a finding)

- **Privacy forward-start path.** `validate_private_grounding` passes `prefer_dates_from="future"`
  explicitly (`grounding.py:87-88`), bypassing the new branch; `REQUIRE_PARTS=["day","month"]`,
  the date-order double parse and the `search_dates` single-span check are unchanged, and a month-only
  forward-start ref still resolves to `None` and is denied as ambiguous (pre-existing). No change.
- **Fact-text resolution callback.** `resolution.py:301` passes the replaced segment as
  `temporal_ref` with no `source_text`; only segments that also carry a relative cue reach dateparser,
  and every such phrase I probed parses to `None` in dateparser 1.3.0, so the loss of the past
  preference is unobservable. Residual risk only if a future dateparser learns `next December`; low.
- **Retrieval / query paths.** Query analysis uses dateparser defaults and never stores a month/year;
  temporal relations requires day precision with `past`; neither is changed.
- **dateparser crashes.** End-of-month bases clamp (`2026-01-31` + `February` → `2026-02-28`), and
  `None` results are handled by both branches. No exception path found.
- **Performance and lock.** At most one extra `dateparser.parse` per month-only claim; the lock is
  acquired per parse and released between the two, so no cross-call state is held. Not material.
- **Derivation guard.** The pinned fixture's `late March next year` is unparseable under both
  preferences, so `tests/test_derivation_version.py` stays green without a bump (see checklist 7).

## Test-coverage gap

`tests/test_month_without_year.py` covers the happy paths (plan → next, completed → past, no signal →
nearest uncertain, explicit year, day without year, sentence isolation, relative ref, end-to-end
ingestion) but has no case for Scenarios 1-4: contracted future forms / "was hoping", a same-month
plan, a passage with two mentions of the month, or a protected ingest. Each fix above should add one.

## Process note (not a code finding)

The change is unconditional: it alters the stored `event_time` of month-only claims in the fast-tier
fixtures, for example `May 17` in chat-v1 moves from 2025 to 2026 now that the nearest occurrence is
used. The committed `benchmarks/fixtures/fast-cycle/baseline-main.json` was not refreshed in this
change, and the CHANGELOG records no fast-tier run. Under the release rules this needs a fast-cycle
run, and a baseline refresh if a gated measure moves on purpose. The worker/test owner can report
that evidence; I did not run it.

## Pre-mortem headline

"Claims for 'I was hoping to go in December' are still dated a year early, and a plan made in November
is dated November 2027, because the tense classifier is a word list and nothing reads the uncertainty
flag."

---

# Worker adjudication and resolution (Phase 4 step 16a)

## Review scope

- Base: `main` at `d2bc51b3b75f92645f44305bb9955ca37a738ba6`.
- Tier: M. Predicates: NEW_SYMBOLS true; SECURITY, INFRA, DATA and PERSISTED_FIELD false and no semantic risk raised the tier.
- Roster: core (checklists 1, 3, 4, 5; Sonnet) and adversarial (checklist 8; Opus). Merged checklists: 3, 4, 5 into core. Not applicable: 2, 6, 7.
- Snapshot reviewed: `95842b765995c9ef31667c7adc3ca829616bffbbc85a6e4b271d3f654cc86db8`. The fixes below changed the tree, so the final snapshot differs; the fixes are listed here with the checks that verify them.
- Files changed: `src/prme/ingestion/temporal.py`, `src/prme/ingestion/pipeline.py`, `CHANGELOG.md`, `tests/test_month_without_year.py`.

## Adjudication table

| Item | Severity | Verdict | Reasoning |
|---|---|---|---|
| F1 future cues match nouns and past forms | Must Fix | Fix now | Verified regressions. `_FUTURE_CUE` now requires verb-distinctive forms (`planning`, `plans to`, `hoping to`, `scheduled for`, `booked a`, `reserved a`, contractions, present-tense plan verbs). Past-tense `planned`, `hoped` and `expected` are no longer future cues, so they fall to the nearest occurrence with `uncertain=True`. Four past sentences are now pinned as past and two ambiguous ones as uncertain. |
| F2 `DERIVATION_VERSION` stays 2 | Should Fix | Dismissed for this run | The spawn brief fixes the version at 2 unless the pinned derivation fixture moves. The fixture's only month ref is `late March next year`, which dateparser cannot parse, so the dump does not move (guard passes twice). The CHANGELOG states that a memory stored before the fix keeps its date, so the consequence is explicit. |
| F3 uncertain branch and nearer-cue rule untested | Should Fix | Fix now | Added end-to-end ingestion with a no-tense quote asserting `resolved_date_uncertain is True`, a nearer-cue test, and a tie test. |
| F4 same-month plan rolls a year ahead | Should Fix | Fix now | Added the same-month branch: a future parse that lands in the event's own month takes that year's occurrence. Test added. |
| F5 two-digit year treated as missing | Consider | Fix now | `_SHORT_YEAR` recognizes `'24`; the reference keeps the old path. Test added. |
| F6 uncertainty flag has no reader and privacy drops it | Consider | Follow-up | The common protected path is safe (see Scenario 4), but legacy explicit-capability admissions without a source snapshot can produce the flag and the projection drops it. Closing that needs a boolean projection and a `STATEMENT_VERSION` change, so it is filed as a follow-up. No current reader exists, so nothing misreads the absent flag today. |
| F7 dateparser settings duplicated | Consider | Fix now | Extracted `dateparser_settings()` into `temporal.py` and used it from both modules. |
| F8 another month-name table | Consider | Accept | The repo already keeps per-module month tables with different jobs (number mapping, cue detection, ordering). Consolidating them is a wider refactor with no defect behind it. |
| F9 casefold offsets slice the original string | Consider | Fix now | Reference location now uses `re.finditer` with `re.IGNORECASE` on the original text, so no length-preserving assumption is made. |
| F10 release note day differs from the issue | Consider | Fix now | The note now says "a date in December 2025", which is true for both the issue's replay time and the member-v1 session time. |
| Scenario 1 cue vocabulary misses contractions and ordinary plan phrasings | High | Fix now | Added `'ll`, `hoping to`, `thinking about/of`, `considering`, and present-tense plan verbs (`start`, `begin`, `fly`, `arrive`). Tests added for a contraction, a present-tense plan verb, and "was hoping". |
| Scenario 2 same-month plan jumps a year | Medium | Fix now | Same fix as F4. |
| Scenario 3 first month mention decides | Medium | Fix now | `classify_tense` returns no signal when the reference appears more than once, so the claim falls to nearest plus uncertain. Test added. |
| Scenario 4 uncertainty flag dropped by privacy projection | Medium | Follow-up | For admissions with a source snapshot, `validate_private_grounding` (`src/prme/privacy/admission.py:634-638`) runs the explicit-future path with `REQUIRE_PARTS=["day","month"]` and `search_dates` checks (`src/prme/ingestion/grounding.py:67-97`), so a month-without-year claim is denied or converted to an ISO date before materialization and the new branch is unreachable. Legacy explicit component capabilities without `source_snapshot` skip that check (`admission.py:444`, `634`), and `project_admitted_node` copies only `_CLAIM_FIELDS` (`src/prme/ingestion/admitted_statements.py:26-31, 52-58`), whose `_text` coercion raises on a boolean. That narrow path needs its own projection line and a `STATEMENT_VERSION` bump, so it is a follow-up. |

## Follow-ups raised

1. #508, "A privacy-protected month guess loses its uncertainty flag": privacy-protected admissions that use a legacy explicit component capability without a source snapshot drop `resolved_date_uncertain` when the admitted graph is projected. Add a boolean projection for the flag and bump `STATEMENT_VERSION`, so a protected store can tell a guessed year from a parsed one.

## Resolution status

| Item | Status | Evidence |
|---|---|---|
| F1, Scenario 1 | Resolved | `uv run pytest -q tests/test_month_without_year.py` covers past nouns, ambiguous `planned`/`hoped`, contractions, present-tense plan verbs, and "was hoping". |
| F3, Scenario 2, Scenario 3 | Resolved | Tests exercise the uncertain writer end to end, the same-month plan, the two-mention passage, the nearer cue, and the tie. |
| F4, F5, F7, F9, F10 | Resolved | Same test file and CHANGELOG wording; `ruff check` passes. |
| F2 | Dismissed with reason | Spawn brief fixes the version; derivation guard passes. |
| F6, Scenario 4 | Follow-up filed as #508 | See Follow-ups raised. |
| F8 | Accepted | Existing per-module style; no defect. |
