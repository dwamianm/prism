# Review: issue #422 profile token budget (checklist 7: data/storage safety)

Status: COMPLETE

- Base commit: 404993f528a4e2987b00f15436b583b721b296cb (main)
- Snapshot: 84d62853cb28868fa2a295fa7d9adc5f8754803b858e6b4d313064d33e0fde33
- Issue: GitHub #422 (bounded defect: profiles record `max_profile_tokens`; organizer rebuilds reuse it)
- Scope: schema/key compatibility, migration/backfill safety, preservation of existing records,
  retry behavior, writer plus reader proof for the new durable field.

Verdict: no blocking data/storage-safety defect in the bounded change. The new metadata key is
additive on a generic JSON dict, the writer and reader are both live and exercised, old records
read safely, and retry/replay paths stay consistent. Findings below are LOW/INFO, mostly a
documentation overstatement for protected stores and residual tokenizer/oversized-budget risks.

## Findings

### LOW-1: Protected-store publication drops `max_profile_tokens`, but the docs say a profile records it
`admit_generated_node()` returns `project_generated_node()` (src/prme/privacy/journals.py:302-329),
whose profile projection is a closed allowlist at src/prme/privacy/journals.py:108-119: it keeps
`source_count_available`, `source_count_included` and `tokens_used` but not `max_profile_tokens`.
Since the projected node is what `consolidate_knowledge()` puts in the plan
(src/prme/storage/engine.py:4882-4887) and `validate_generated_publication()` re-projects it before
the checksum comparison (src/prme/privacy/journals.py:238-247), the field is dropped consistently:
the stored protected profile never has it, so `validate_generated_publication` and
`validate_snapshot` cannot see an inconsistent node.

There is also no inconsistent reader: both organizer rebuild paths refuse protected stores via
`_owner_has_policy()` (src/prme/organizer/owner_profiles.py:222-224, checked at :253 and :297), so
`_profile_budget()` never reads a protected profile. A protected profile that later becomes
unprotected (or an explicit rebuild) falls back to `tokens_used`
(src/prme/organizer/owner_profiles.py:89-91), which preserves what the build had included.

The defect is documentary: the CHANGELOG line, docs/ENTITY-PROFILES.md:56-60, docs/PACKING.md:506-511
and docs/RFC-0015-Self-Organizing-Memory.md:364-370 state unconditionally that a profile records
the budget it was built with. That is false for protected stores. Suggest one qualifier ("not
stored for privacy-protected profiles, which are rebuilt explicitly") rather than a code change.
No data loss occurs either way.

### LOW-2: A profile prepared before this change is not reused after the upgrade (one extra embedding)
`profile_request_hash()` hashes the whole node (`node.model_dump(mode="json")`, which includes
metadata) at src/prme/models/profile.py:44-56. Adding `max_profile_tokens` changes the hash of any
profile node for the same key. `ProfileWorkStore.reusable()` matches on the stored `request_hash`
(src/prme/storage/profile_work.py:94-115), so a pending plan prepared before the upgrade no longer
matches; `prepare()` then abandons it as `ReplacedPreparation`
(src/prme/storage/profile_work.py:166-196) and a fresh plan and embedding are made. This is a
one-time cost, not data loss, and it is the correct behavior for "different request".

Resuming an already-prepared pre-upgrade plan (`resume_profile()` ->
`_publish_prepared_profile()`, src/prme/storage/engine.py:4961-4972) still works: the plan publishes
verbatim without the field, `ProfilePublication` validation does not require it
(src/prme/models/profile.py:127-181, and `metadata` is an unconstrained dict while only top-level
fields are `extra="forbid"`), and `_profile_budget()` falls back to `tokens_used`. Safe.

### LOW-3: Profiles older than `tokens_used` (profile_format_version 1) fall back to 500
`tokens_used` and `profile_format_version = 2` were added together in 88cc5e24, after
`consolidate_knowledge()` already accepted a `max_profile_tokens` argument. A v1 profile has neither
key, so `_profile_budget()` returns 500 (src/prme/organizer/owner_profiles.py:92) even if it was
built larger. This is not a regression (rebuilds used 500 before this change too), but the CHANGELOG
and docs qualify the fallback only by `tokens_used` existence; the "cannot shrink" guarantee does
not cover v1 profiles. Worth a sentence if v1 packs are still in the field, otherwise ignore.

### INFO-1: The recorded budget is reused without considering the recorded tokenizer
`tokens_used` and the budget are token counts under `metadata["tokenizer"]`
(src/prme/storage/engine.py:4866-4868). `_profile_budget()` applies the stored number under the
current `packing.tokenizer` on rebuild (src/prme/storage/engine.py:4831). If an operator changes the
tokenizer config and then a legacy profile is rebuilt, `max(500, tokens_used)` can be below what the
same text costs in the new tokenizer and sources can drop. This was worse before the change (every
rebuild was 500), so it is not a regression, but it bounds the absolute wording in the docs.

### INFO-2: A recorded budget is trusted with no upper bound
`consolidate_knowledge()` validates only `type is int and >= 0`
(src/prme/storage/engine.py:4596-4597) and the value is now propagated into every automatic rebuild
via `_profile_budget()` (src/prme/organizer/owner_profiles.py:86-88). A one-off oversized budget
from an application therefore persists into background passes. There is no integrity or data-loss
problem: the excerpt is bounded by available sources, and an embedding/size failure is caught by
`_rebuild()` and leaves the previous profile in place
(src/prme/organizer/owner_profiles.py:235-237). Recorded value 0 is unreachable in practice because
`build_profile()` returns `None` with a zero budget, and a tampered 0 yields no new profile
(`published == 0`), not a retirement.

## Checklist results

1. Schema/key compatibility: PASS. `nodes.metadata` is a free-form `JSON` column in DuckDB
   (src/prme/storage/schema.py:119-127) and serialized with `json.dumps` in both backends
   (src/prme/storage/duckpgq_graph.py:944-967, src/prme/storage/pg/graph_store.py:131-144); the
   model field is `dict | None` (src/prme/models/nodes.py:26-28). No key registry or schema version
   enumerates profile metadata keys. No migration needed.
2. Migration/backfill safety: PASS. No backfill. The privacy export/migration path copies
   `original.metadata` and projects it
   (src/prme/storage/privacy_migration.py:681-699), so old profiles simply lack the key and read
   through the fallback.
3. Preservation of existing records: PASS. The change never rewrites old profile nodes; a rebuild
   still archives the replaced profile through `publish_profile()`
   (src/prme/storage/profile_publication.py:167-171). `_is_stale()` does not read the new key.
4. Existing records without the field: PASS. `_profile_budget()` rejects non-int/bool/negative
   values and falls back `tokens_used`, then 500
   (src/prme/organizer/owner_profiles.py:85-92), covered by
   tests/test_owner_profile_context.py:320-337.
5. Writer proof: PASS. `consolidate_knowledge()` writes
   `"max_profile_tokens": max_profile_tokens` into the SUMMARY profile metadata at
   src/prme/storage/engine.py:4868 (dict at :4861-4869), built with the same budget at :4831 and
   validated at :4596. Durable through `publish_profile()` ->
   `_create_node_sync()` (src/prme/storage/profile_publication.py:166,
   src/prme/storage/duckpgq_graph.py:944-967). Covered end to end by
   tests/test_owner_profile_context.py:294-317.
6. Reader proof: PASS. `_profile_budget()` at src/prme/organizer/owner_profiles.py:75-92, called
   only from `_rebuild()` at :233, with the profile passed by both live rebuild paths:
   `refresh_stale_profiles()` at :276-278 (organizer opportunistic pass,
   src/prme/organizer/maintenance.py:187-197) and `refresh_owner_profiles()` at :319-321
   (`end_session()`, src/prme/storage/engine.py:5056-5068). No other internal caller of
   `consolidate_knowledge()` exists.
7. Retry/replay behavior: PASS with LOW-2. Within a version the budget is deterministic, so retries
   recompute the same `profile_request_hash` and reuse the pending plan; a failed rebuild is caught
   and leaves the previous profile and its recorded budget in place
   (src/prme/organizer/owner_profiles.py:227-238). Pre-upgrade plans are abandoned/re-prepared once
   and resumed plans publish safely without the field.
8. Fallback cannot shrink a larger build: PASS under a stable tokenizer and stable source ordering.
   `tokens_used` is the full excerpt count (`build_profile()` returns `count_tokens(text)`,
   src/prme/organizer/profiles.py:99-114) and is <= the old budget, so rebuilding at
   `max(500, tokens_used)` re-accepts every previously accepted source and rejects the same ones;
   see INFO-1 for the tokenizer-change caveat and LOW-3 for v1 profiles.
9. `profile_format_version`: PASS. Stays 2 at src/prme/storage/engine.py:4866; the only version gate
   is the protected projection (src/prme/privacy/journals.py:105/109), which drops the new key, so
   protected nodes still satisfy version 2. Unprotected readers ignore unknown metadata keys
   (src/prme/retrieval/owner_profile.py:62-73, src/prme/retrieval/profile_sources.py:43-62).

No unresolved blocking risk found. Tests were not run (worker-owned); findings rest on source.
