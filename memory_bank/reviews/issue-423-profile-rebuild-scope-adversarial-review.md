# Issue #423 profile rebuild scope scan: adversarial review (checklist 8)

Status: COMPLETE

- Base branch: `main`, base commit `52d99de1be90477abcfa7256088fd8359fea2eb5`
- Branch: `issue-423-profile-rebuild-scope-scan`
- Reviewed snapshot: `b352281961d1caf228350041f22588d6b0773c7fa775a93ad18535362fc1c596`
- Reviewer: adversarial (tier L, 7 scenarios kept)

## Checklist results

1. **Premise challenge / sibling entry points, holds.** The named path did page the whole scope on `main`; the narrowing is real. Siblings: `end_session()` (engine.py:5182-5194) and the organizer pass (maintenance.py:193-197) both pass their own `time.monotonic()` deadline down; the HTTP surface exposes no consolidate endpoint; `MemoryClient.consolidate_knowledge` (client.py:1105-1121) does not accept `deadline`, so an embedder's synchronous rebuild still cannot be bounded, which no caller needs today. Not a defect.
2. **Guard loopholes.** The deadline is checked only between pages (S2), is not validated as `time.monotonic()` (a caller passing `time.time()` silently disables it, engine.py:4688), and is absent around `get_edges`/`get_nodes` (engine.py:4799-4807). Scope and owner guards hold: every read passes `scope=scope` and the admitted view re-checks the owner and rejects `operator_unscoped` (read_graph.py:58-76).
3. **Existing data / installs.** Two regressions against pre-existing data: non-ASCII names (S1) and un-normalized legacy speaker values (S3). Merged claims are covered, because the evidence predicate matches any position in `evidence_refs` (duckpgq_graph.py:587-592) while the spoken path only reads the last one (engine.py:4929-4932). Direct-store nodes with no ENTITY node behave as before: the turn-event lookup is skipped (engine.py:4782-4784) and the spoken path already required an entity (engine.py:4903). Uppercase UUIDs cannot occur (`evidence_refs: list[UUID]`, models/nodes.py:73). Unicode normalization forms NFC/NFD fail identically on both sides, so they do not diverge.
4. **State / lifecycle.** One unnecessary widening (S7). Archived, superseded and contested sources are still excluded at `absorb` (engine.py:4721-4722), so the profile's state semantics are unchanged.
5. **Idempotency and retries.** The organizer paths rebuild one name per call, so `ProfileBuildDeadlineExceeded` can never leave a half-published multi-entity call there. A public call with several names can publish some and then raise; the docstring is accurate (engine.py:4609-4611), and a retry rebuilds deterministically because `build_profile` orders by (time, id) (profiles.py:102).
6. **Ordering / timing / consistency.** Pages are still not a snapshot (documented, engine.py:4679-4680); the read count grows from one to four or more, but each node is filtered by state at `absorb` time, and a UUIDv4 insert landing below a cursor was already missed on `main`. Pre-existing, accept.
7. **Scale.** S4 (per-name event walks), plus `scan_pages` now materializing the whole result list before absorbing (engine.py:4685-4705), which roughly doubles the live reference list on the `entity_names is None` path. Parameter limits are not a risk: the evidence list is one JSON/array bind, and DuckDB decorrelates it (measured).
8. **Partial failure.** A failing page propagates out of `consolidate_knowledge`; `_rebuild` logs it at warning and keeps the previous profile (owner_profiles.py:240-242), unchanged from `main`. The new failure mode is the silent one (S5).
9. **Backward compatibility.** `GraphStore.scan_nodes` (graph_store.py:260-272) and both `EventStore.get_by_user` implementations gained optional keyword arguments. A third-party backend that does not accept them now raises `TypeError` from engine.py:4690-4693 for every named build, and `_rebuild` swallows it into a warning. The CHANGELOG states this explicitly. Accept, documented.
10. **Blast radius.** Not limited to the default-off organizer path: `consolidate_knowledge(entity_names=...)` is public and is how the benchmark builds profiles (benchmarks/locomo.py:696 passes `list(speakers)`), so the narrowed read changes every named profile build, not only `enable_owner_profile_context`.
11. **Detection.** No signal distinguishes "stopped at the deadline" from "nothing stale" (S5); the correctness loss in S1 is silent as well.

## Scenarios

### S1. A non-ASCII name loses its sources and can retire a live profile
- **Kind:** newly introduced (the LIKE pattern existed for profile lookup, but it now gates the source set).
- **Trigger:** a store where a person's name, or the text mentioning them, uses a case mapping where Python's `str.lower()` and the backend's `LOWER()` disagree. Verified on DuckDB 1.4.4: `lower('ΑΝΔΡΕΑΣ')` is `ανδρεασ` while Python `.lower()` is `ανδρεας` (final sigma); `lower('İstanbul')` is `istanbul` while Python gives `i̇stanbul` (U+0069 U+0307).
- **Mechanism:** engine.py:4751-4752 builds the content page from `entity_names`; duckpgq_graph.py:574-578 and pg/graph_store.py:509-510 compare `LOWER(content) LIKE '%' || name.lower() || '%'` with the Python-folded pattern. `mentions_entity` (profiles.py:63-66, 80-86) is only the post-filter at engine.py:4884, so a node the prefilter misses is never seen. The person's ENTITY node is missed the same way (engine.py:4739-4742), which also empties `entity_nodes`, so the bound read (engine.py:4794-4805) and the turn-event read (engine.py:4782-4784) return nothing either.
- **Likelihood:** low incidence, high impact for a Greek or Turkish store; zero for ASCII-only stores.
- **Impact:** source counts drop and the profile text changes; at engine.py:4937-4939 the live profile is retired, archived and index-evicted, and every later rebuild repeats the same miss, so the loss is permanent. Directly contradicts CHANGELOG.md:12 ("The profile a given store builds is unchanged").
- **Detection:** silent. No log, no error; only a receipt-to-receipt `source_paths` comparison shows it.
- **Cheapest mitigation:** when an entry of `entity_names` is not ASCII, do not narrow the content read for it and fall back to the two-state whole-scope page (about 10 lines in `consolidate_knowledge`), plus one test with a Greek or Turkish name. Alternatively feed several case variants into the LIKE list, which does not cover final sigma.
- **Verdict:** Fix now.

### S2. The deadline bounds pages, not a page
- **Kind:** newly introduced gap in the fix (the check is new; the underlying predicate cost is pre-existing).
- **Trigger:** a large store where the first filtered page must walk most of the owner's id range before it finds 500 matches: a person named in few records, on `nodes` where `LOWER(content) LIKE` and `evidence_refs ?|`/`json_each` have no index (schema.py:149-164; pg/schema.py:178-190).
- **Mechanism:** engine.py:4688-4689 checks the deadline only before each 500-row page; the page itself is one unbounded statement (duckpgq_graph.py:597, pg/graph_store.py:526-528). The `graph.get_nodes(sorted(bound))` read (engine.py:4807) and each `get_edges` (engine.py:4802-4803) have no deadline check at all.
- **Likelihood:** medium on a 100x store, low otherwise.
- **Impact:** the 1 s `end_session()` budget (engine.py:5190-5193) and the organizer's 200 ms pass can still be overrun by one statement, which is the reported failure. The fix lowers the frequency, not the worst case.
- **Detection:** quiet: the symptom is the original slow `end_session()`, with no counter separating "stopped at the deadline" from "one page was slow".
- **Cheapest mitigation:** a per-statement timeout around the page call (PostgreSQL `SET LOCAL statement_timeout`, a DuckDB interrupt), or split each page into bounded id windows and check the deadline between them. Medium, 30 to 60 lines plus tests.
- **Verdict:** Follow-up, with the stated criterion recorded as only partially met.

### S3. Un-normalized legacy speaker values stop matching
- **Kind:** newly introduced read over pre-existing data.
- **Trigger:** a row whose stored `prme_speaker_v1` is not a normalized name, which models/speaker.py:85-99 says exists ("the key was not reserved before speakers existed"). `metadata_speaker` runs `normalize_speaker`, which strips, then `casefold`; the SQL compares the raw value.
- **Mechanism:** engine.py:4758-4760 filters with `speaker=entity_names`; the predicates are duckpgq_graph.py:579-586, pg/graph_store.py:512-520, event_store.py:676-678, pg/event_store.py:531-534.
- **Likelihood:** low.
- **Impact:** such a turn or claim drops out of `said` (engine.py:4885) and out of the turn-event list, although `main` found it through `said_by` (engine.py:4670-4672). Profile content changes for that store.
- **Detection:** silent.
- **Cheapest mitigation:** `LOWER(TRIM(...))` on both sides in the four predicates. About 4 lines plus one test.
- **Verdict:** Follow-up.

### S4. One full event-log walk per name, and an unbounded evidence parameter
- **Kind:** newly introduced.
- **Trigger:** `consolidate_knowledge(entity_names=[...])` with several speakers, which benchmarks/locomo.py:696 does with `list(speakers)`.
- **Mechanism:** engine.py:4780-4784 calls `turn_events` once per name; engine.py:4762-4774 pages the owner's whole event log by timestamp for each, because no index covers `user_id` plus the speaker predicate (schema.py:51-63). The union (engine.py:4780-4782) becomes one JSON/array bind parameter (engine.py:4789-4790) and is re-sent on every page of the evidence scan.
- **Likelihood:** medium for benchmark-sized inputs, low for the one-name organizer path.
- **Impact:** K event-log walks for K speakers, and a multi-megabyte bind value for a heavy owner; on a 100x store the narrowed rebuild can cost more than the single whole-scope scan it replaced.
- **Detection:** quiet (latency only).
- **Cheapest mitigation:** one event-log pass for all names in the call, partitioning by speaker in Python, and window the evidence ids so each query carries a bounded list. Medium.
- **Verdict:** Follow-up.

### S5. A timed-out rebuild is indistinguishable from a healthy no-op
- **Kind:** newly introduced.
- **Trigger:** any store where the read misses the deadline on every pass, for example through S2.
- **Mechanism:** owner_profiles.py:238-240 catches `ProfileBuildDeadlineExceeded` and returns None "without logging a failure"; refresh_stale_profiles (owner_profiles.py:283-293) and refresh_owner_profiles (owner_profiles.py:333-338) add no entry, and `end_session()` reports `nodes_processed=0`, `errors=0` (engine.py:5204-5213).
- **Likelihood:** medium, coupled to S2.
- **Impact:** the profile silently stops refreshing forever exactly when the store is large enough for the read to be expensive, with no log, no error count and no user-visible difference.
- **Detection:** silent by construction.
- **Cheapest mitigation:** one info log with the name and elapsed time, plus a `deadline_exceeded` count in the job details. About 6 lines.
- **Verdict:** Follow-up, cheap enough to fold into this change.

### S6. The bound-claims read keeps `source_availability`, so protected profiles re-prepare
- **Kind:** newly introduced.
- **Trigger:** an owner under a privacy policy whose person's entity has HAS_FACT edges, through the public `consolidate_knowledge(entity_names=...)` path (the organizer paths refuse protected owners, owner_profiles.py:265, 311).
- **Mechanism:** the strip lives inside `scan_pages` only (engine.py:4697-4700); the bound read at engine.py:4807 calls `graph.get_nodes` directly, and `AdmittedGraphView.get_nodes` returns nodes after `project_node`/`disclose`, which add `source_availability` (reads.py:266-269). `node_checksum` hashes the whole node including metadata (models/derivation.py:34-40) and `profile_request_hash` hashes every source (models/profile.py:51).
- **Likelihood:** low to medium on a protected store.
- **Impact:** the request hash and journal manifest now encode an availability marker that follows evidence state rather than content, so `profile_work.reusable` (profile_work.py:125-145) misses and every pass re-embeds the profile, and the published manifest identity varies with state for otherwise identical sources.
- **Detection:** quiet (extra embedding calls, no user-visible error).
- **Cheapest mitigation:** move the `source_availability` strip from `scan_pages` into `absorb`, so every read path applies it. Move about 4 lines; one test.
- **Verdict:** Fix now (tiny, and it is the privacy path).

### S7. The speaker scan asks for every lifecycle state, which the filter then discards
- **Kind:** newly introduced.
- **Trigger:** a store with many archived or superseded speaker-attributed records, or any protected store.
- **Mechanism:** engine.py:4758-4759 passes `lifecycle_states=list(LifecycleState)`, and `absorb` then drops everything outside TENTATIVE/STABLE (engine.py:4721-4722); `main` read only the two source states. For a protected owner every returned row also goes through `project_node` (reads.py:217-269), which runs the erasure guard and `check_disclosure` and can raise `PrivacyDenied` (reads.py:228-236, 240, 245-249), which aborts the whole rebuild and only logs a warning (owner_profiles.py:240-242).
- **Likelihood:** low to medium.
- **Impact:** wasted paging and projection proportional to the person's retired records; on a protected store it newly touches retired protected nodes whose policy may have changed, and one such node aborts the rebuild that `main` completed.
- **Detection:** quiet.
- **Cheapest mitigation:** pass `source_states` to the speaker scan too. Nothing is lost, because every state outside them is discarded by `absorb` anyway. 1 line.
- **Verdict:** Fix now (one line) or immediate follow-up.

## Hardest attacks that failed

- **SQL and wildcard injection** through `entity_names` or `speaker`: every filter is parameterized; `_escape_like` escapes the backslash before `%` and `_` (duckpgq_graph.py:1134-1140, pg/graph_store.py:247-253) and DuckDB adds `ESCAPE '\'`. A literal `%` in a name still matches a literal `%`.
- **Quadratic `json_each` EXISTS over a large evidence list:** measured on DuckDB 1.4.4 with 2,000 rows and 10, 500, 2,000 and 20,000 wanted ids: 0.002 s to 0.004 s. DuckDB decorrelates the subquery.
- **Uppercase UUIDs in `evidence_refs`:** the field is `list[UUID]` (models/nodes.py:73) and every query normalizes through `UUID(...)` (duckpgq_graph.py:591, pg/graph_store.py:522); no writer can produce a mixed-case stored ref.
- **Pagination termination and skipped ties:** `len(page) < 500` plus `after_id = page[-1].id` (engine.py:4702-4704) terminates for both backends, and the event cursor carries a `(timestamp, id)` tie-break (event_store.py:663-665, pg/event_store.py:513-515), so equal timestamps are not skipped.
- **Profile self-discovery:** the existing profile node is always found by the content page, because `build_profile` writes the requested name verbatim into its header (profiles.py:99, owner_profile.py:44-45), so `previous` and retirement behave as on `main`.

## Pre-mortem headline

Two weeks out: a Greek-named customer's knowledge profile vanished after an upgrade because the new SQL prefilter lowercases names differently from the matcher it feeds, and nobody noticed because the rebuild that dropped it also gave up quietly on the next pass.
