# Review: issue #422 profile token budget (checklist 7: data/storage safety, round 2)

Status: COMPLETE

- Base commit: 404993f528a4e2987b00f15436b583b721b296cb (main)
- Final reviewed snapshot: 1e383082b45f76d898b9cf9f76e1626ced6cb8bbeca920f7bad4158fc8bdf499 (not a git object in this clone; the working tree diff is byte-identical to the supplied `/tmp/issue-422-review-round2.diff`, sha256 `eef2413d...`)
- Issue: GitHub #422 (a profile records `max_profile_tokens`; the owner-profile rebuild reuses it)

Verdict: no blocking data/storage-safety defect in the final snapshot. The new durable field is an
additive key on a free-form JSON `metadata` column in both backends, needs no migration or
backfill, leaves every existing record readable, and its writer and both live readers are
exercised by tests. The round-1 LOW-1 documentation finding is resolved; LOW-2 and LOW-3 remain
correctly characterized; the two INFO items are unchanged. One new minor INFO (defensive
consistency check) is recorded.

## Checklist results (final diff)

1. Schema/key compatibility: PASS. `nodes.metadata` stays a free-form `JSON` column in DuckDB
   (src/prme/storage/schema.py:126) and `jsonb` in PostgreSQL (src/prme/storage/pg/graph_store.py:154),
   serialized with `json.dumps` on both write paths (src/prme/storage/duckpgq_graph.py:944-967,
   src/prme/storage/pg/graph_store.py:131-154); the model field is `dict | None`
   (src/prme/models/nodes.py:26-28). No key registry, no DDL, no schema-version bump. `profile_format_version`
   correctly stays 2: the new key is metadata, not text format, and the only version gate is the
   protected projection (src/prme/privacy/journals.py:105/109) which drops it.
2. Migration/backfill safety: PASS. No backfill is added or needed. `prme migrate` copies the whole
   database with `COPY FROM DATABASE` (src/prme/storage/pack_tools.py:228), so nodes and their
   metadata travel verbatim, and the privacy export/migration path copies `dict(original.metadata)`
   (src/prme/storage/privacy_migration.py:684-686) before re-projecting. Old profiles simply lack the
   key and read through the fallback.
3. Preservation of existing records: PASS. The change never rewrites old profile nodes. A rebuild
   still inserts the new node and archives the replaced one (`lifecycle_state = 'archived'`) on both
   backends (src/prme/storage/profile_publication.py:167-171 DuckDB, :261-265 PostgreSQL), so the
   prior record is preserved. `_is_stale()` does not read the new key, so staleness decisions are
   unchanged.
4. Retry/replay behavior: PASS. Within one version the budget is deterministic, so a retry recomputes
   the same `profile_request_hash` (src/prme/models/profile.py:44-56) and reuses the pending plan
   (src/prme/storage/profile_work.py:94-115). A failed rebuild is caught and leaves the previous
   profile and its recorded budget in place (src/prme/organizer/owner_profiles.py:223-240). A
   committed `PROFILE_PUBLISHED` operation replays by checksum (src/prme/storage/profile_publication.py:40-52);
   a new build uses a new node id and therefore a new operation id, so an old payload cannot collide.
5. Writer proof: PASS. `consolidate_knowledge()` validates the budget
   (src/prme/storage/engine.py:4596-4597), selects sources with it (engine.py:4831), and records it
   in the SUMMARY metadata at engine.py:4868 (the only entity-profile writer). The recursive
   omit-scope path forwards the same budget (engine.py:4621-4623). Covered end to end by
   tests/test_owner_profile_context.py:294-319.
6. Reader proof: PASS. `_profile_budget()` at src/prme/organizer/owner_profiles.py:70-88 is called
   only from `_rebuild()` (owner_profiles.py:235), and both live rebuild paths pass the replaced
   profile: `refresh_stale_profiles()` (owner_profiles.py:278-280, organizer pass,
   src/prme/organizer/maintenance.py:187-197) and `refresh_owner_profiles()`
   (owner_profiles.py:321-323, `end_session()`, src/prme/storage/engine.py:5060-5075). Both are
   exercised by tests/test_owner_profile_context.py:294-341. The shared default now lives in
   src/prme/config.py:31-34 and both API signatures use it (src/prme/storage/engine.py:4552,
   src/prme/client.py:1110), so the signature default and the fallback cannot drift.
7. Empty/legacy records: PASS. A missing profile, a non-int, a `bool`, a negative value or a zero
   budget all fall through to `tokens_used`, then to 500 (owner_profiles.py:82-88; covered by
   tests/test_owner_profile_context.py:344-369). A recorded `0` is unreachable from the engine
   (`build_profile()` cannot publish at a zero budget; `max_profile_tokens=0` yields 0 published,
   tests/test_knowledge_profile_scopes.py:182), and the round-2 `> 0` check closes the round-1
   adversarial "zero budget retried forever" path in defense-in-depth.
8. Fallback cannot shrink an already quoted source: PASS under a stable tokenizer and stable source
   ordering. `tokens_used` is the full excerpt count measured before privacy admission
   (engine.py:4829-4833 and metadata at :4866), so it describes the raw source text an unprotected
   rebuild measures, and rebuilding at `max(500, tokens_used)` re-admits every previously accepted
   source. See INFO-1 for the tokenizer-change caveat and LOW-3 for v1 profiles.

## Round-1 finding status

### LOW-1 (protected-store docs overstated): RESOLVED
All four statements now qualify the privacy-policy exception: CHANGELOG.md:12 ("A profile on a store
without a privacy policy now stores its budget... A privacy-protected profile's journal projection
keeps only its source counts and tokens, and the owner-profile refresh does not run for an owner
under a privacy policy."), docs/ENTITY-PROFILES.md:56-64, docs/PACKING.md:506-515,
docs/RFC-0015-Self-Organizing-Memory.md:364-374, documentation/configuration.md:148. The code is
unchanged and still consistent: the closed allowlist at src/prme/privacy/journals.py:108-116 keeps
`source_count_available`, `source_count_included`, `tokens_used` and drops `max_profile_tokens`, and
`_owner_has_policy()` (owner_profiles.py:220-222, checked at :251 and :305) keeps the reader away from
protected profiles.

### LOW-2 (a pre-upgrade prepared plan is not reused): UNCHANGED, still correct
`profile_request_hash()` hashes the whole node including metadata (src/prme/models/profile.py:44-56),
so adding the key changes the hash of the same build and `ProfileWorkStore.reusable()` misses the
older pending plan (src/prme/storage/profile_work.py:94-115); `prepare()` abandons it as
`ReplacedPreparation` and re-prepares once (profile_work.py:166-196). That is a one-time extra
embedding, not data loss, and is the correct "different request" behavior. Resuming an
already-prepared pre-upgrade plan still publishes verbatim (`resume_profile()` ->
`_publish_prepared_profile()`): metadata is unconstrained and `_profile_budget()` falls back to
`tokens_used`.

### LOW-3 (v1 profiles rebuild at 500): UNCHANGED, now accurately documented, no data loss
A `profile_format_version` 1 node has neither key, so `_profile_budget()` returns 500
(owner_profiles.py:88). That matches the pre-change behavior and the docs now state it plainly ("A
profile written before the budget was recorded is rebuilt at the default 500 tokens, or at the
tokens it used when that is larger", CHANGELOG.md:12, docs/ENTITY-PROFILES.md:60-62,
docs/PACKING.md:510-512). The only residual is that a v1 profile genuinely built above 500 is still
rebuilt at 500; no source node is lost (a profile is a derived view), so this is informational.

## Remaining INFO

### INFO-1: recorded budget is reused without considering the recorded tokenizer (unchanged)
`tokens_used` and the budget are counts under `metadata["tokenizer"]` (engine.py:4866-4868), while the
rebuild measures with the current `packing.tokenizer` (engine.py:4831). If an operator changes the
tokenizer config and then a legacy profile is rebuilt, `max(500, tokens_used)` can be below the same
text's cost in the new tokenizer and a source can drop. This was worse before the change (every
rebuild was 500), so it is not a regression, but it bounds the "does not drop a source it already
quotes" wording.

### INFO-2: a recorded budget is trusted with no upper bound (unchanged)
`consolidate_knowledge()` accepts any nonnegative int (engine.py:4596) and the value now propagates
into every automatic rebuild via `_profile_budget()` (owner_profiles.py:82-83). A one-off oversized
budget from an application persists into background passes. No integrity or data-loss problem: the
excerpt is bounded by available sources and a failure is caught by `_rebuild()` leaving the previous
profile in place.

### INFO-3: no cross-check between a recorded budget and the same record's `tokens_used` (new, minor)
`_profile_budget()` trusts any positive recorded `max_profile_tokens` even when it is below that
record's `tokens_used` (owner_profiles.py:82-87). The engine cannot produce such a record
(`build_profile()` guarantees `tokens_used <= budget`), so this only affects tampered or imported
metadata, and the worst case is a smaller derived profile, not lost data. A defensive
`max(recorded, tokens_used)` would remove the edge if desired.

## Notes
- Tests were not executed here (worker-owned). Findings rest on source verification of the final
  snapshot.
- No other data-safety change was introduced by the round-1 fixes: the shared constant adds no
  import cycle (`prme.config` imports no organizer module; `owner_profiles` is imported lazily at
  src/prme/organizer/maintenance.py:193 and src/prme/storage/engine.py:5060), and the `_rebuild()`
  signature change has no caller outside the two verified paths.
