# Adversarial review: issue #409, named claim text false positives (checklist 8)

Status: COMPLETE

- Repository: /Users/dwamianm/Sites/prism
- Base branch: main, base commit `48bb094196d356579600e8f8709afdab405a050f`
- Reviewed snapshot: `1de1a440f1128085adb672331670ee83f2e2c2e4fa9c28ec78ca9a5ab882fc82` (uncommitted working tree, `git diff main`)
- Tier: M, adversarial, 4 scenarios.
- Method: read the changed files and their callers; ran no build and no test suite (the worker owns those). The behaviour quotes below were produced by loading `src/prme/ingestion/named_text.py` alone and calling the pure functions on the working-tree file, with no model, store or I/O.

## Checklist results

| Check | Result |
|---|---|
| Premise: each of the nine reported cases fixed at the root | The reported examples are fixed and tested, but two are whitelist patches with sibling leaks: quoted speech only for double quotes (single quotes still leak, scenario 1) and the `-ed` past-tense heuristic only for the two named words (scenario 3). Abbreviations, all caps, set phrases, noun "mine", Roman-numeral I and curly apostrophes hold for the reported inputs. |
| Sibling paths that produce claim text | No change needed. The statement built from subject/predicate/object (`_claim_statement`, src/prme/ingestion/pipeline.py:190-217, call at :1767-1779) composes text from structured fields with `_speaker_reference` and `_FIRST_PERSON_SINGULAR`; it never scans free text, so none of the nine text cases can occur there. There is a single writer call site for the rewrite (pipeline.py:1760), used by both engine pipelines (src/prme/storage/engine.py:836, :1028) and by pronoun repair through `_prepare_plan`. |
| Guard loopholes | Double quotes (straight and curly), abbreviations, all caps, curly apostrophes, coordinated subjects, adverbs and contractions hold. Single quotes do not (scenario 1). The interjection guard misses "Oh, my god" (comma) because `_previous_word_span` returns "," (named_text.py:112-118, :190). The title guard over-refuses a legitimate I after a capitalized name (:203-205). Unmatched and inch-mark quotes fall to the safe label. A quoted span that opens after an earlier first person discards the whole rewrite (the function returns None), so no partial rewrite is written. |
| Existing data and existing installs | Forward-only. Stored claims keep the old text until `reingest()` runs; reingest is behind `enable_reingest` (default false) and asks the model once per source. No in-place migration and none is possible. The refreshed fixture (tests/fixtures/derivation/trip_derived.json) changes only `derivation_version` and the `reingest.fingerprint` of six claims, with the option off (tests/test_derivation_version.py:59-67), which is exactly what the bump produces. |
| State and lifecycle (toggle on/off, concurrency) | Unchanged and documented (docs/ENTITY-IDENTITY.md:112-117): on reads memories stale when off, off reads them stale when on. Reingest is serialised per user (`ReingestBusyError`) and each source commits in one transaction with a stale-plan retry; the rewrite adds no state and no I/O. |
| Idempotency and retries | `named_claim_text` is not idempotent on an already labeled text (a second application compounds the label), but no path can do that: the single call site always works on text rebuilt from the extraction record, and a reingest retry rebuilds a fresh plan from the record. |
| Ordering and consistency | Deterministic and order-independent. The duplicate-sentence set `claim_texts` (pipeline.py:1768-1778) now holds labels too; two claims of one sentence produce the same label, so the statement path fires as before. Claim merge and supersedence compare subject/predicate/object, not text, so they are unaffected. |
| Scale | The new `_inside_quotes` scans the prefix before every matched token and `_previous_word_span` copies and searches the prefix per match, so the rewrite is O(matches x length) where it was O(matches) for `I`; a 100 KB passage with thousands of tokens is the worst case, and the prefix regex `[A-Za-z'’]+$` was already quadratic per call. Claim text is a model sentence or the turn's text, so I found no realistic pathological input; not kept as a scenario. |
| Partial failure | Per-source transaction, saved extraction before publishing, and a failed source counted and retried later; the name, text and `metadata.named_text` are written together. No new failure mode. |
| Backward compatibility (packing, indexes, receipts) | Packing accepts a label: `_folds_into` reads `named_text` and needs the name in the text and the original in the outer line (src/prme/retrieval/packing.py:906-923), both true for "Maya: ...". Old claims without the key pack as before. Lexical and vector documents are rebuilt on reingest through the existing eviction path. Receipts carry no claim text and no new feature flag. The fast-tier numbers pinned to the option (BENCHMARKS.md, "Named claim text") were measured under the old rule; the option stays off, so no release gate reads them, but a future flip needs a fresh run. |
| Blast radius of the `DERIVATION_VERSION` bump | Every stored source becomes stale, including stores with the option off (scenario 4). The bump is mandated by CLAUDE.md ("claim text rules ... a bump makes every stored memory stale once and costs a model request each, so it must be needed") and is needed for stores that ran 0.17.0a1/a2 with the option on, which would otherwise never be re-derived. The cost is real and the release note should say it plainly. |
| Detection | Silent. Nothing logs, counts or receipts a rewrite, a label or a refusal, so neither a wrong rewrite nor a silent coverage loss surfaces; `metadata.named_text.original` is the only forensic trail and nothing compares it with the claim text. |

## Scenarios

Ranked by likelihood x impact. Each is labelled pre-existing or newly introduced.

### 1. Single-quoted speech is still rewritten in the wrong person's voice

- **Trigger.** A store with `enable_named_claim_text` on, and a turn that reports someone else's speech in single quotes, typed straight (`My husband said 'call me later.'`) or with smart punctuation (`‘I was fired,’ my husband said.`).
- **Mechanism.** `_inside_quotes` (src/prme/ingestion/named_text.py:133-139) toggles only on `"`, `“` and `”` (`_QUOTE_CHARS`, :98). The lookbehind `(?<![\w'’])` (:95) spares only the token immediately after a straight or right-curly apostrophe, so `'I` survives but a later `my` or `me` in the same quoted span still matches, and U+2018 `‘` is not in the class at all. `name_first_person` then writes the name into the quoted possession or object (:187-199). Observed on the working tree: `My husband said 'call me later.'` -> `Maya's husband said 'call Maya later.'`; `He said 'I lost my keys.'` -> `He said 'I lost Maya's keys.'`; `‘I was fired,’ my husband said.` -> `‘Maya was fired,’ Maya's husband said.`
- **Likelihood.** Medium-high. Straight single quotes are what a keyboard without smart punctuation produces, and iOS and Word smart punctuation produce U+2018/U+2019, which the guard misses entirely. The ticket's own flagship example is this pattern with double quotes.
- **Impact.** High. The claim's text and its `named_text.original` disagree about who did what: a husband's request becomes a request to the owner, and a quoted speaker's possession becomes the owner's. The text is what retrieval indexes, the reader context shows, `results` returns and #448 profiles quote, and it is stored durably.
- **Detection.** Silent. No log, counter or receipt records a rewrite, so only a human reading the claim finds it; `named_text.original` allows a diff but nothing performs one.
- **Cheapest mitigation.** Treat a single quote as opening speech when it is preceded by whitespace or open punctuation and followed by a letter, and match it at the close; or, cheapest and conservative, return None (label the sentence) when `re.search(r"(?:^|[\s(\[{])['‘][A-Za-z]", text)` hits, and add `‘` to both regex lookbehinds. About 5 to 10 lines and 4 to 6 tests in tests/test_named_claim_text.py.
- **Verdict.** Fix now. Same reported defect class ("quoted speech"), cheap, silent corruption. Label: pre-existing behaviour that this change leaves open; the guard is documented as double-quote only (docs/ENTITY-IDENTITY.md:97, documentation/configuration.md:146), but the issue's criterion is quoted speech.

### 2. Lowercase `i` silently turns the option off for a turn

- **Trigger.** `enable_named_claim_text` on, and a chat turn whose only first-person token is lowercase (`i have a dog.`, `i think so.`, `i'm tired`).
- **Mechanism.** `_FIRST_PERSON_RE` is now case-sensitive (src/prme/ingestion/named_text.py:95) and matches only `I`, `Me`/`me`, `My`/`my`, `Mine`/`mine`, `Myself`/`myself`; `i` never matches, so `has_first_person` is False, the carried-name read at src/prme/ingestion/pipeline.py:1447 does not happen, and pipeline.py:1757-1764 writes neither a name nor a label. Mixed turns are half-rewritten: `i lost my keys.` -> `i lost Maya's keys.` (subject stays lowercase), where 0.17.0a2 produced `Maya lost Maya's keys.`; `i have a dog.` is unchanged.
- **Likelihood.** High. Mobile and informal chat routinely drops capitalization, and the option was built for hosted-chat traffic (CLAUDE.md, CHANGELOG 0.17.0a1).
- **Impact.** Medium. No wrong text is written, but the option silently does nothing for those turns: the claim names nobody, retrieval cannot match an owner-name query, and a mixed claim keeps a lowercase `i` subject with a named possessive. The documentation states only the all-caps consequence (docs/ENTITY-IDENTITY.md:95-97, documentation/configuration.md:146, src/prme/config.py:1260-1262), not the lowercase one, so an operator cannot predict it.
- **Detection.** Silent; no metric counts rewritten, labeled or untouched claims.
- **Cheapest mitigation.** Either restore lowercase `i` where it cannot be an abbreviation (followed by a known adverb and a verb in `_AFTER_I`: `i am`, `i have`, `i was`, `i can’t`), about 8 lines and 5 tests; or, keeping the current choice, state it in docs/ENTITY-IDENTITY.md, documentation/configuration.md and the module docstring and pin it with a test.
- **Verdict.** Follow-up (document at minimum). Label: newly introduced.

### 3. The `-ed` past-tense heuristic still rewrites base-form verbs it does not know

- **Trigger.** A present-tense claim whose verb ends in `ed`, is a base form, and is not in the 20-word list: `I breastfeed my newborn.`
- **Mechanism.** `_past_tense` (src/prme/ingestion/named_text.py:158-160) treats every word of four or more letters ending in `ed` as a past tense unless it is in `_BASE_ED` (:71-72); for a word not in `_AFTER_I` it appends the name and leaves the base form unconjugated (:255). This change removed `lay` from `_PAST` and added `weed` to `_BASE_ED`, fixing exactly the two reported words while keeping the hand-maintained list as the root cause. Hyphenated compounds are spared only because `_WORD_RE` stops at the hyphen. Observed: `I breastfeed my newborn.` -> `Maya breastfeed Maya's newborn.`, while `I bottle-feed the baby.` and `I hand-feed the dog.` label.
- **Likelihood.** Medium. The persona the option targets is a mother of a newborn (tests/test_named_claim_text.py:1-5, the `member-v1` probe); "I breastfeed" is a natural present-tense sentence in that chat.
- **Impact.** Medium. Ungrammatical claim text is stored, indexed and shown; the referent is still the owner, so there is no wrong-person attribution and no data loss, but it is the same user-visible class the ticket claims to fix.
- **Detection.** Silent. A reader sees the bad agreement; nothing alerts.
- **Cheapest mitigation.** Add the realistic compounds to `_BASE_ED` (`breastfeed`, `overfeed`, `underfeed`) with tests: about 3 lines and 3 tests. A root-cause rule (treat an unknown `-ed` word as past only before nothing, a preposition or a determiner) is not bounded enough to recommend.
- **Verdict.** Follow-up. Label: pre-existing root cause, only point-fixed by this change.

### 4. `DERIVATION_VERSION` 2 makes every stored source stale, including stores with the option off

- **Trigger.** Any store upgraded to the release carrying this fix, and an operator or script that runs `prme reingest` / `MemoryEngine.reingest()`.
- **Mechanism.** `DERIVATION_VERSION = 2` (src/prme/ingestion/reingest.py:89) is inside `extraction_settings` (:111) and so inside `derivation_settings` for every pipeline, option or not. `_run` computes the fingerprint (:264), finds no marker under it, and selects every source (:282-292). Because the version is also in `extraction_key` (:263, :111), the saved reingest extractions from a previous run are keyed out too, so the first reingest after the upgrade calls the model once per request even for a store that had already saved its outputs. For an option-off store nothing derived changes, yet re-deriving rewrites every claim (new node ids, supersedence and archive links, index eviction and re-add, :413-475). The refreshed fixture shows only version and `reingest.fingerprint` moving, with the option pinned off.
- **Likelihood.** Medium. Deterministic once reingest runs, but reingest is behind `enable_reingest` (default false), so only stores that opted in pay.
- **Impact.** Medium. A model request per source plus a full claim rewrite for stores where the option is off and the derived content is identical; for a store with the option on, this is also the only path that repairs 0.17.0a1/a2 text, and it is opt-in and model-backed, so the false-positive text survives an upgrade until an operator chooses to pay for it. The bump itself is mandated (CLAUDE.md, "claim text rules") and needed for the option-on alpha stores; the scoped alternative (a `named_text_rule` key inside the `if pipeline._named_claim_text` branch at reingest.py:151-156) would confine staleness to them but contradicts the standing rule.
- **Detection.** Quiet. `reingest(dry_run=True)` reports the selected count; nothing warns at upgrade time, and the wording at CHANGELOG.md:16 ("re-derives the memories an engine stored under the earlier rule") and docs/ENTITY-IDENTITY.md:112-117 ("memories derived without it stay current while it is off") understates the global staleness, while the 0.17.0a1/a2 notes state it plainly.
- **Cheapest mitigation.** Keep the bump; add one sentence to CHANGELOG.md and the next release note ("every stored memory goes stale once; reingest re-derives it") and clarify docs/ENTITY-IDENTITY.md:112-117. If the owner prefers the scoped key, that is a separate decision.
- **Verdict.** Accept the bump; Follow-up on the release-note and doc wording. Label: newly introduced, by design.

## Attacked and did not break

- **Double-quoted speech, including a quote that opens after an earlier first person.** `My husband said "I was fired."` and `He said “I’m tired.”` label the whole sentence; the earlier `My` rewrite is discarded because the function returns None, so no partially named text is written.
- **Coordinated subjects, adverbs and contractions.** `Jordan and I are` is not consumed as a verb, `I really am` -> `Maya really is`, `I can’t` keeps the curly apostrophe, `Jordan and I've` and `I'd` label, and an all-caps verb keeps its emphasis (`I AM PREGNANT` -> `Maya IS PREGNANT`).
- **Abbreviations and case.** `I have ME/CFS.` -> `Maya has ME/CFS.`, `Augusta, ME is cold.` unchanged, and `has_first_person` no longer fires on an all-caps abbreviation, so the carried-name read is correctly skipped.
- **The statement sibling path.** Built from structured fields, never from text, and its `named_text.original` is the source sentence; `_folds_into` accepts a label as well as a rewrite.
- **Double application and retries.** Not idempotent on a labeled text, but unreachable: one call site, always on text rebuilt from the extraction record; a stale-plan retry rebuilds a fresh plan.
- **Concurrency, partial failure and ordering.** The busy guard, per-source transaction, saved-extraction-before-publish and statement-based supersedence are untouched; the rewrite is deterministic.

Not kept, noted for the follow-up list: `Oh, my god, I won.` -> `Oh, Maya's god, Maya won.` (the interjection guard does not look past the comma); `I told Mom I was tired.` labels instead of rewriting (the title guard treats the capitalized name as a title); `I'M SURE.` -> `Maya is SURE.` (the contraction branch has no all-caps path, pre-existing); proper nouns beginning My/Me are still rewritten (`My Chemical Romance ...` -> `Maya's Chemical Romance ...`, pre-existing); an inch mark or an unmatched quote falls to the safe label.

## Pre-mortem headline

"Off by default was not off for cost: the #409 fix marked every stored memory stale, and a store that never turned the option on re-extracted its whole history to repair text it never wrote."
