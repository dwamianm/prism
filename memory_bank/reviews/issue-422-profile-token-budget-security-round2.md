# Security review (checklist 2, round 2): issue #422, profile token budget

Status: COMPLETE

- Repository: /Users/dwamianm/Sites/prism
- Base commit: 404993f528a4e2987b00f15436b583b721b296cb (main)
- Reviewed snapshot: 1e383082b45f76d898b9cf9f76e1626ced6cb8bbeca920f7bad4158fc8bdf499
- Diff: /tmp/issue-422-review-round2.diff
- Prior round: memory_bank/reviews/issue-422-profile-token-budget-security.md (snapshot 84d62853...)
- Cross-checked source: src/prme/privacy/journals.py, src/prme/privacy/reads.py,
  src/prme/models/profile.py, src/prme/models/nodes.py, src/prme/retrieval/owner_profile.py,
  src/prme/organizer/profiles.py, src/prme/organizer/maintenance.py, src/prme/config.py,
  tests/test_privacy_journal_scenarios.py, tests/test_privacy_sanitizer_scenarios.py,
  tests/test_knowledge_profile_scopes.py, tests/test_owner_profile_context.py

## Assigned checklist

1. Ownership and scoping of the budget read/reuse.
2. Input validation of the stored/read integer (write, read, malformed values, bounds).
3. Protected journal projection allowlist (src/prme/privacy/journals.py).
4. No new surface or authority from the shared constant or the stricter guard.
5. Secrets/PII in logs or responses; unsafe deserialization; credentials in tests.

Result: no security defect introduced by this change; nothing blocking. Round-1
observation O1 (docs did not name the privacy exception) is resolved in this snapshot.
Two low observations remain, both pre-existing in nature and outside the ticket's
acceptance criteria: an upper bound is still not applied to the recorded budget, and
stored profile metadata is still caller-trusted.

## Table

| Area | Result | Details |
| --- | --- | --- |
| Authorization / ownership and scoping | PASS | The budget is read from a profile the engine already fetched owner-scoped: `query_nodes(user_id=user_id, ...)` (src/prme/organizer/owner_profiles.py:257-258) and `profiles_named(..., user_id=user_id, scopes=[scope])` (:309). `_rebuild` applies it only to `consolidate_knowledge(user_id=user_id, scope=profile.scope, ...)` (:233-236), and `consolidate_knowledge` rebuilds/archives only that owner's and scope's profiles (src/prme/storage/engine.py:4627-4631, 4699-4706). A budget cannot move across owners or scopes. `_rebuild` now takes `profile` as a required argument (src/prme/organizer/owner_profiles.py:223-231); its only two callers pass the fetched profile (:278-280, :321-323). Both refresh paths still refuse an owner under a privacy policy before any budget read (:255-256, :299-300). |
| Input validation: write side | PASS | `consolidate_knowledge` rejects any non-exact-int (`type(...) is not int`, so `bool` is rejected) and any negative value before storage (src/prme/storage/engine.py:4596-4597), covered by tests for `-1`, `True` and `1.5` (tests/test_knowledge_profile_scopes.py:161). The value recorded at :4868 is exactly the validated argument that bounded `build_profile` at :4831. |
| Input validation: read side | PASS | `_profile_budget` uses the same strict test: a recorded budget is used only when `type(budget) is int and budget > 0` (src/prme/organizer/owner_profiles.py:82-84). `bool`, `float`, `str`, `None`, `0` and negatives fall through to the `tokens_used` fallback (:85-87), then to `DEFAULT_MAX_PROFILE_TOKENS` (:88). The stricter `> 0` guard closes round 1's acceptance of a stored `0`: the engine never publishes a profile at `max_profile_tokens=0` (tests/test_knowledge_profile_scopes.py:181-183), so a `0` can only be forged, and it is now ignored. Unit coverage: tests/test_owner_profile_context.py:362-369. `MemoryNode.metadata` is a validated `dict | None` (src/prme/models/nodes.py:26-27), so `.get` cannot fail on a non-dict. |
| Input validation: upper bound | LOW (observation O2) | No maximum is enforced on write or read (src/prme/organizer/owner_profiles.py:83; src/prme/storage/engine.py:4596-4597). A large caller-chosen budget persists and is reused by the opportunistic pass and `end_session()`, where `build_profile` does cumulative token counting over the owner's sources (src/prme/organizer/profiles.py:99-111). Scope is the caller's own owner and scope, reachable only from in-process callers or a forged `store()` node (O3), and a caller could always have passed a large value directly on the explicit call. No privilege change; optional hardening (a configured ceiling) is a follow-up, not this ticket. |
| Protected journal projection allowlist | PASS | `project_generated_node` is still a closed allowlist that builds a fresh dict: for kind `profile` it projects exactly `entity_profile`, `entity_name`, `profile_format_version`, `source_count_available`, `source_count_included`, `tokens_used` (src/prme/privacy/journals.py:108-119). `max_profile_tokens` is deliberately not projected, so a protected profile cannot carry it and no new field reaches a protected read, receipt or snapshot. Because the projection ignores extra keys rather than rejecting them, the new metadata key written at src/prme/storage/engine.py:4868 is dropped for a protected publication without raising. |
| Protected publication integrity | PASS | The protected path replaces the node with its projection before the plan and request hash are built (`admit_generated_node`, src/prme/privacy/journals.py:302-329; call site src/prme/storage/engine.py:4882-4891). `validate_generated_publication` compares that same projected node to its own deterministic re-projection (:224-247), so dropping the extra key keeps checksums consistent. Protected profile publication is exercised by tests/test_privacy_sanitizer_scenarios.py:544-548 and tests/test_privacy_journal_scenarios.py:70. `PRIVACY_KEY`/`JOURNAL_KEY` handling is untouched. |
| Protected profiles and the refresh paths | PASS | Both refresh paths return early for an owner under a privacy policy (`_owner_has_policy`, src/prme/organizer/owner_profiles.py:218-220, 255-256, 299-300), so `_profile_budget` is never reached for a protected profile through a background rebuild. A protected profile is rebuilt only by an explicit `consolidate_knowledge()`, which supplies its own budget. This is now stated in the docs (CHANGELOG.md:12; docs/ENTITY-PROFILES.md:57-63; docs/PACKING.md:507-514; docs/RFC-0015-Self-Organizing-Memory.md:363-372). |
| New surface: shared `DEFAULT_MAX_PROFILE_TOKENS` | PASS | The constant is a module-level `int = 500` in src/prme/config.py:31-34, imported by the engine (:53), the client (src/prme/client.py:43) and the organizer (src/prme/organizer/owner_profiles.py:36). It is not a Pydantic field, so it adds no `PRME_` env var, config dump, `config_audit` entry, HTTP route, MCP tool or CLI flag (verified: no `max_profile_tokens`/`DEFAULT_MAX_PROFILE_TOKENS` in src/prme/config_audit.py, src/prme/api or src/prme/mcp). No import cycle: `prme.config` depends only on `prme.privacy.policy` and `prme.retrieval.*`, none of which import the organizer, storage engine or client. Sharing one constant grants no authority; it only keeps the two API defaults and the fallback in step. |
| New surface: stricter guard | PASS | Requiring `budget > 0` only narrows which recorded value a rebuild uses; it falls back to `tokens_used` or the default and cannot widen access, select another owner's sources, or change what `build_profile` receives (owner+scope-scoped sources, src/prme/storage/engine.py:4823-4833). |
| Secrets/PII in logs or responses | PASS | The only new durable value is a caller-supplied integer (`max_profile_tokens`, src/prme/storage/engine.py:4868); it is not source text, a credential or an identifier. The one new log path is unchanged in kind (`logger.warning(..., exc_info=True)`, src/prme/organizer/owner_profiles.py:237-239) and does not log metadata. Profile content and its other metadata keys are unchanged. |
| Unsafe deserialization / hostile metadata | PASS | Metadata is read as a plain dict with `.get`; no `eval`, `pickle`, `yaml.load` or dynamic import is added. A malformed value cannot raise, loop or select sources outside the owner's scope; it yields the documented fallback (src/prme/organizer/owner_profiles.py:82-88). |
| Credentials / secrets in code and tests | PASS | The new tests use the file's existing `MockEmbeddingProvider`/`MockExtractionProvider` and a `tmp_path` pack (tests/test_owner_profile_context.py:294-343); no keys, tokens, hostnames or paid providers. |

## Round-1 observations re-checked

- O1 (docs stated unconditionally that a profile records its budget): resolved. Every
  doc now qualifies the behavior by privacy policy (CHANGELOG.md:12;
  docs/ENTITY-PROFILES.md:57-63; docs/PACKING.md:507-514;
  docs/RFC-0015-Self-Organizing-Memory.md:363-372). No code change was needed; adding
  the key to the journal allowlist would change protected node checksums.
- O2 (no upper bound on the recorded budget): remains, recorded above as LOW. Not
  introduced by this change in the sense that a caller could already pass any value
  directly; no privilege boundary moves.
- O3 (stored profile metadata is caller-trusted via `store()`): remains, pre-existing.
  A forged active SUMMARY with `entity_profile is True` and a large
  `max_profile_tokens`/`tokens_used` is reused on rebuild, but only within the caller's
  own owner and scope and it is retired like any previous profile. Not introduced by
  this change.

## Unresolved risks

None specific to this change. The worker owns test execution; I confirmed by reading
that the protected-publication checksum path stays consistent after the allowlist drops
`max_profile_tokens`, and that the existing protected-profile tests exercise the path.
