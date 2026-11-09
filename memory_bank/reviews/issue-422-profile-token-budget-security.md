# Security review (checklist 2): issue #422, profile token budget

Status: COMPLETE

- Repository: /Users/dwamianm/Sites/prism
- Base commit: 404993f528a4e2987b00f15436b583b721b296cb (main)
- Reviewed snapshot: 84d62853cb28868fa2a295fa7d9adc5f8754803b858e6b4d313064d33e0fde33
- Diff: /tmp/issue-422-review.diff
- Changed files reviewed: src/prme/storage/engine.py, src/prme/organizer/owner_profiles.py,
  tests/test_owner_profile_context.py, CHANGELOG.md, docs/ENTITY-PROFILES.md, docs/PACKING.md,
  docs/RFC-0015-Self-Organizing-Memory.md, documentation/configuration.md
- Cross-checked source: src/prme/privacy/journals.py, src/prme/privacy/reads.py,
  src/prme/models/profile.py, src/prme/retrieval/owner_profile.py, src/prme/organizer/maintenance.py,
  src/prme/organizer/profiles.py, src/prme/models/nodes.py, tests/test_knowledge_profile_scopes.py

## Checklist

Audit changed code against repo CLAUDE.md security guidance and general practice: secrets/PII in
logs or responses, missing authorization/ownership checks, input validation, unsafe
deserialization, overly permissive surfaces, credentials in code/tests. Plus the sweep's privacy
path: protected profile metadata allowlist, protected publication, type/range validation of the
stored int, and hostile/malformed metadata.

Result: no security defect introduced by this change. No blocking finding. Three low-severity
observations, all out of the ticket's acceptance criteria; recorded for the parent to route.

## Table

| Area | Result | Details |
| --- | --- | --- |
| Secrets/PII in logs or responses | PASS | The only new value is a caller-supplied integer (`max_profile_tokens`, src/prme/storage/engine.py:4868). It is an int, never source text, a credential or an identifier. The one new log line is unchanged in kind (`logger.warning(..., exc_info=True)`, src/prme/organizer/owner_profiles.py:235-236) and does not log metadata. Profile content and its existing metadata keys are unchanged by this diff. |
| Authorization / ownership checks | PASS | The budget is read from a profile the engine already fetched owner-scoped: `query_nodes(user_id=user_id, ...)` (src/prme/organizer/owner_profiles.py:255-258) and `profiles_named(..., user_id=user_id, scopes=[scope])` (:307). It is applied only to `consolidate_knowledge(user_id=user_id, scope=profile.scope, ...)` (:231-234), so a budget cannot move across owners or scopes. Both refresh paths refuse an owner under a privacy policy before any read (:222-224, :253-254, :297-298). No new authorization decision is added; budget selection changes only which of the same owner's sources fit, and `build_profile` receives only owner+scope-scoped sources (src/prme/storage/engine.py:4823, :4830-4833). |
| Input validation (stored/read int) | PASS | Write side requires `type(max_profile_tokens) is int and >= 0` (src/prme/storage/engine.py:4596-4597), covered by existing tests for -1, True and 1.5 (tests/test_knowledge_profile_scopes.py:160-172). Read side uses the same strict check and rejects bools, floats, strings and negatives (src/prme/organizer/owner_profiles.py:85-92; tested at tests/test_owner_profile_context.py:336-337). No upper bound, see observation O2. |
| Hostile or malformed metadata | PASS | A non-int, bool, negative or absent value yields the documented fallback (`tokens_used`, else 500), so a malformed value cannot raise, loop or select sources outside the owner's scope (src/prme/organizer/owner_profiles.py:85-92). `MemoryNode.metadata` is a validated `dict | None` (src/prme/models/nodes.py:26), so `.get` cannot fail on a non-dict. A very large value only widens the budget for that same owner and scope; see O2/O3. |
| Unsafe deserialization | PASS | Metadata is read as a plain dict with `.get`; no eval, pickle, `yaml.load` or dynamic import is added. Node metadata round-trips as JSON in the existing storage path, unchanged. |
| Protected-read allowlist (privacy/journals.py) | PASS | `project_generated_node` is still a closed allowlist: the profile branch projects exactly `entity_profile`, `entity_name`, `profile_format_version`, `source_count_available`, `source_count_included`, `tokens_used` (src/prme/privacy/journals.py:108-119). `max_profile_tokens` is not projected, so a protected profile cannot carry it and no new field reaches a protected read, receipt or snapshot. Because the allowlist dropped it, protected publication stays checksum-consistent: `admit_generated_node` replaces the node with the projection before the plan and request hash are built (src/prme/storage/engine.py:4882-4891), and `validate_generated_publication` compares that same node to its own deterministic re-projection (src/prme/privacy/journals.py:246-247). No `PRIVACY_KEY`/`JOURNAL_KEY` handling is touched. Non-profile behavior is untouched. |
| Protected publication cannot leak source text | PASS | The new key is an int and is discarded by the allowlist; the sanitizer path for content and `entity_name` is unchanged (src/prme/privacy/journals.py:310-328). Nothing new is logged, indexed or returned. |
| Overly permissive surface | PASS | No HTTP route, MCP tool, CLI flag or config/env read is added (`max_profile_tokens` appears only in src/prme/storage/engine.py, src/prme/client.py:1104-1119, docs and tests). The only callers are in-process: the async engine and `MemoryClient`. The feature remains behind `enable_owner_profile_context` (off) at both refresh call sites (src/prme/organizer/maintenance.py:187-199, src/prme/storage/engine.py:5056). |
| Credentials / secrets in code or tests | PASS | The new test uses the file's existing `MockEmbeddingProvider` / `MockExtractionProvider` fixtures and a tmp_path pack (tests/test_owner_profile_context.py:55-63, :294-317). No keys, tokens, hostnames or paid providers. |
| Determinism / replay of the new field | N/A | `profile_request_hash` hashes the whole node (src/prme/models/profile.py:41-56), so the new key changes hashes for newly prepared, non-protected profiles. That is a data/determinism concern for the data and core reviewers, not a security boundary; it exposes nothing and grants nothing. |

## Observations (low; not acceptance criteria, not blocking)

O1. Protected profiles never record their budget; the docs state without qualification that they do.
`project_generated_node` omits `max_profile_tokens` (src/prme/privacy/journals.py:111-119), so a
profile published under a privacy policy is stored without the key. The documented behavior
("The profile records the budget it was built with") is unconditional in
docs/ENTITY-PROFILES.md:57-61, docs/PACKING.md:507-512 and docs/RFC-0015-Self-Organizing-Memory.md:363-368.
This is not a leak: the allowlist fails closed (src/prme/privacy/journals.py:177-178) and both
refresh paths skip a protected owner (src/prme/organizer/owner_profiles.py:253-254, :297-298).
If a protected profile is ever rebuilt after its owner's policy records go away, it falls back to
`max(500, tokens_used)`. Severity low; route to the data reviewer or a follow-up if the docs
should name the privacy exception (adding the key to the allowlist would change protected node
checksums and needs a migration decision, so it does not belong in this bounded fix).

O2. The persisted budget has no upper bound and is reused by background passes.
`_profile_budget` accepts any non-negative int (src/prme/organizer/owner_profiles.py:87) and
`consolidate_knowledge` only rejects negative or non-int (src/prme/storage/engine.py:4596-4597).
A large value now outlives the call and is reused by the opportunistic pass and `end_session()`
(src/prme/organizer/maintenance.py:195-197, src/prme/storage/engine.py:5066-5068), where
`build_profile` does cumulative token counting over the owner's sources
(src/prme/organizer/profiles.py:99-111). The effect is bounded by the caller's own owner id and
scope, is reachable only from in-process callers, and a caller could always have passed a large
value directly. Optional hardening: clamp to a configured maximum when reading or writing. No
privilege change; recorded only.

O3. A forged profile node can steer its own budget.
`store()` accepts caller metadata and node type (src/prme/storage/engine.py:1107-1128), and the
refresh treats any active SUMMARY whose content starts `[Knowledge Profile: ...]` with
`entity_profile is True` as a profile (src/prme/retrieval/owner_profile.py:62-98,
src/prme/organizer/owner_profiles.py:255-266). A caller could therefore store a node with a huge
`max_profile_tokens` or `tokens_used` and have it reused on rebuild. The scope is the caller's own
owner/scope, no boundary is crossed, and the forged node is retired like any previous profile.
Pre-existing metadata trust; not introduced by this change.

## Unresolved risks

None specific to this change. The worker owns test execution; the privacy regression I checked by
reading (protection drops the key before the plan and hash are built) deserves confirmation from
the existing protected-profile tests (`tests/test_privacy_journal_scenarios.py`,
`tests/test_privacy_sanitizer_scenarios.py`, `tests/test_privacy_migration.py`) if they have not
been run on this snapshot.
