# Issue #409 security review (checklist 2)

Status: COMPLETE

- Base branch: main
- Base commit: 48bb094196d356579600e8f8709afdab405a050f
- Reviewed snapshot: 4b9fe04bcc786ed888523ff19fa34e761c351480d4219e41b6f1b072f5a4ed4e (uncommitted working tree, `git diff main`); pass 2 was 9aac4b971408a2d760e8f1a0f65d411f379fc093c116fb20acfe9ae6053ec0fb
- Tier: L (SECURITY, NEW_SYMBOLS)
- Changed files reviewed: BENCHMARKS.md, CHANGELOG.md, docs/ENTITY-IDENTITY.md, documentation/configuration.md, src/prme/config.py, src/prme/ingestion/named_text.py, src/prme/ingestion/reingest.py, tests/fixtures/derivation/trip_derived.json, tests/test_named_claim_text.py

## Checklist 2

| Area | Result | Details |
|---|---|---|
| Secrets / credentials in code, tests, fixtures, logs | PASS | A scan of `git diff main` for key/credential patterns (`sk-`, `AKIA`, `ghp_`, PEM, `xox*`, `password =`) found nothing added, in this pass and the previous one. The only `sk-...` strings in the touched docs are the pre-existing placeholders at `documentation/configuration.md:17` and `:19`, not part of this diff. `src/prme/ingestion/named_text.py` has no logger and no secret material. |
| PII in committed text | PASS | New test and doc examples use fictional names only (Maya, Theo, Nico, James, Dana, Jordan, Sam, Kai, Alex Rivera, "Maya Lone Star") and fictional or generic places (Denver, Augusta, ME, Northwind). No email, phone or SSN pattern is added anywhere in the diff. The CHANGELOG, `BENCHMARKS.md` and `docs/ENTITY-IDENTITY.md` examples (`ME/CFS`, a pregnancy, "a coal mine", "World War I") are generic, not personal data. `tests/fixtures/derivation/trip_derived.json` stays synthetic; the diff changes only `derivation_version` and six fingerprint digests. |
| Caller-controlled input written into stored text | PASS | The `name` reaches `named_claim_text` (`pipeline.py:1757-1763`) and is concatenated into claim content and `metadata.named_text`; it is never compiled into a regex, never interpolated into SQL or a shell, and never rendered as HTML (the API returns JSON). The regexes are module-level constants (`named_text.py:106-114`) with no caller input inside them. Caller-supplied `speaker` is bounded and sanitized by `normalize_speaker`: at most 200 chars, control chars, line/paragraph separators and bidi controls rejected (`src/prme/models/speaker.py:24,31-60`, applied by the API validator at `src/prme/api/models.py:234` and by `metadata_speaker` at `speaker.py:87-103`). Introduced names go through `normalize_speaker` (`speaker.py:153-156`); carried names come from a scoped entity lookup (`src/prme/ingestion/owner_name.py:95-98`). So the name cannot forge reader-context lines or inject into a pattern. Storing the name in claim text adds no new prompt-injection class: claim text is already caller/model text packed into the reader context, and the label form predates this diff. |
| DoS / catastrophic regex backtracking in the changed expressions | PASS | Was FAIL in pass 2; resolved by the pass 3 single-pass rewrite (`named_text.py:127-164,202-222`). The regexes are linear and the rewrite loop is now linear on the flagged `my`-heavy input and no longer hits the anchored-word blowup. One small pre-existing residual on `I`-dense text remains; see "Pass 3 delta verification". |
| Authorization / ownership / tenant isolation | PASS | Unchanged in pass 3. The option only rewrites text on the ingestion path (`pipeline.py:1757-1763`); scope and `user_id` are the current event's, the carried-name lookup is scoped (`owner_name.py:87-91`), and no read path changes. `src/prme/retrieval/packing.py` is not in this diff (`git diff main -- src/prme/retrieval/packing.py` is empty), so `_folds_into` (`packing.py:888-938`) and its metadata validation are as on main. A named claim is written and read inside the same admission/scope rules as any other claim. Nothing here can reach another tenant or a protected read. |
| Unsafe deserialization / network / file I/O | PASS | `src/prme/ingestion/named_text.py` still imports only `from __future__ import annotations` and `re` (`:48-50`); no `open`, socket, URL, JSON/pickle/YAML loading is added. `reingest.py` changes only the `DERIVATION_VERSION` integer (`reingest.py:89`) plus a comment (`:154-155`); `config.py` changes only description text. |
| New durable field | N/A | No new field. `metadata.named_text` and its strict shape check in packing predate this diff (`pipeline.py:1762`, `packing.py:908-920`, neither in the diff). The only durable change is the derivation-version bump, which rewrites no records but makes every existing fingerprint stale, so `reingest()` re-derives them once; this is documented in `CHANGELOG.md`, `docs/ENTITY-IDENTITY.md`, `BENCHMARKS.md` and the `documentation/configuration.md` row, and is not a security issue. |

## Pass 3 delta verification

Snapshot 4b9fe04 (working tree). No source was edited by this review; the timing probes below were run read-only with `uv run python`.

What changed. `name_first_person` now advances a single quote cursor instead of rescanning the prefix per match (`named_text.py:204-217`); `_previous_word_span` scans backward and copies only the word, with no `text[:end].rstrip()` and no anchored `[A-Za-z'’]+$` search (`:127-142`); `_next_word` uses `_WORD_RE.match(text, cursor)` with no tail slice (`:151-157`); `_followed_by_punctuation` indexes forward (`:160-164`); the single-quote and four-double-quote guards run once per text (`:206-208`).

Rerun timings (same shapes as pass 2):

```
"my " * 10666 (~32k chars): 0.0090s   (pass 2: 3.34s)
"my " * 21333 (~64k chars): 0.0177s   (pass 2: 13.46s)
"my " * 42666 (~128k chars): 0.0354s  (pass 2: 53.98s)
"my " * 85333 (~256k chars): 0.0714s
"my " * 170666 (~512k chars): 0.1397s   -> linear
"a"*40000 + "! my wallet is here.": 0.0019s  (pass 2: 3.42s)
"a"*80000 + "! my wallet is here.": 0.0039s
"I left " * 9142 (~64k chars): 0.0226s (pass 2: 6.55s)
"I ate " * 10666 (~64k chars): 0.0254s
many quotes / many single-quotes, 128k-512k: 0.0000-0.0046s, early None
has_first_person and _SINGLE_QUOTED_SPEECH_RE.search at ~510k chars: < 0.005s
```

The exact regression-test input (60,007 chars, "my " * 20000 + "wallet.", `tests/test_named_claim_text.py`, `test_a_long_text_is_rewritten_in_one_pass`) measured 0.0168 s against its 5 s bound. F1 is resolved: the per-match prefix scans and the anchored-word blowup are gone, and the formerly catastrophic input is now linear.

Residual (not re-opening F1, pre-existing, low severity). The `I` branch still does `rest = text[position:]` at `named_text.py:263` and `text[cursor:]` at `:276`, so `I`-dense text still copies the tail per match: 0.0593 s at 128 KB, 0.174 s at 256 KB, 0.585 s at 512 KB, 2.10 s at 1 MB. That is a low-constant quadratic, present on main as well, and far below the pass 2 finding (54 s at 128 KB on the `my` path). Removing it is a cheap follow-up (index forward rather than slice, as `_next_word` now does), but it is not required by this ticket. No new input path, I/O, durable field or caller was added in pass 3; the only callers remain `pipeline.py:1757-1763` (`ingest()`/reingest materialization) and the tests.

## Notes (not findings)

- `_SINGLE_QUOTED_SPEECH_RE` (`named_text.py:114`) now treats a word-initial apostrophe or curly quote before a letter as speech, and `several_quotes` (`:208`) labels any text with four or more double-quote characters. Both are conservative correctness choices; neither affects the security checks above.
- A caller-set `speaker` such as "System" can appear as a `Name: text` claim label in the reader context, but the same caller already controls the packed event text and the label form predates this diff; `normalize_speaker` keeps the name a single line of at most 200 chars, so this is not a new injection surface.
- `DERIVATION_VERSION` 1 to 2 (`reingest.py:89`) makes every stored memory stale once and costs one model request each under the opt-in `reingest()`; the CHANGELOG and the docs call this out, and the off-path derived records are unchanged (the fixture diff touches only the version and fingerprints).
