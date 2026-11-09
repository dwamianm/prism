# Code Review: issue-400-typed-extraction-relation (core, consolidated checklists 1,2,3,4,5,7)

Status: COMPLETE (original review plus fix verification)

- Base commit: `48bb094196d356579600e8f8709afdab405a050f` (origin/main)
- HEAD: `ad81421535fce2df3e3160930d602dcd33f99eb0` plus the uncommitted and new working-tree files listed below
- Reviewed snapshot: `5eec1b079707c54b4241ab12163d2b7368645f1c85fb38a1c3521530ea625e63`; fix round snapshot `f699b811fff6d610ba2603fd4096baf9705cc03fdcbd92ee5c7f6c2cf7f70ddf` (same base, same HEAD, updated working tree)
- Tier: L (DATA, PERSISTED_FIELD, NEW_SYMBOLS). Merged checklists: 1 correctness/logic, 2 security, 3 quality and test coverage, 4 pattern consistency and full-config comparison, 5 redundancy, 7 data safety and read/write proof. This is the ticket's recorded light review budget; every item below is in scope.
- Scope: `enable_typed_extraction` (off by default) asks extraction for each fact's relation/role/status in the graph oracle's vocabulary, stores the admitted fields on the claim node and its `HAS_FACT` link, and relation routing reads them. Changed: `src/prme/{config,config_audit,ingestion/extraction,ingestion/pipeline,ingestion/reingest,ingestion/schema,ingestion/admitted_statements,retrieval/relation_walk,storage/engine}.py`, `benchmarks/{diagnostics/extracted_packs,fast_cycle/store_extract,graph_oracle/run,graph_oracle/extraction}.py`, `tests/{test_config_audit,test_graph_oracle,test_typed_extraction}.py` and `tests/fixtures/extraction/typed_batch_v1.json`, `BENCHMARKS.md`, `CHANGELOG.md`, `docs/RELATIONS.md`, `documentation/configuration.md`.

## Acceptance criteria (as written in #400)

1. **Fidelity reported for the gate's production-shaped model, and does the PR say whether it is enough to route on.** Met. `BENCHMARKS.md:4610-4649` reports store-v1, defects-v1, chat-v1 and draw2-v1 on `deepseek-v4.1-flash:cloud`, and says plainly "This is not enough to route on" (`BENCHMARKS.md:4630`). I reproduced the table from `/tmp/extfid-all/report.md` against the harness output; the numbers match.
2. **Fast gate with the option on not below off on top 3 / top 5, on two live samples.** Not met, and recorded as not met: sample 1 is one lower on defects-v1 top 3 (7 -> 6); both samples are lower on chat-v1 top 3 and top 5 (14/15 -> 12/14) (`BENCHMARKS.md:4633-4653`). Both on runs exited 1, and the option stays off. This is a failed criterion, not a code defect; the ticket cannot be closed as complete while it stands.
3. **Relation in the re-derivation settings.** Met. `extraction_settings` adds `typed_extraction` only while the option is on (`src/prme/ingestion/reingest.py:107-131`), the request digest changes with it (`:120`, `:136`), and staleness is the digest of `derivation_settings` (`src/prme/ingestion/reingest.py:273-274`). `DERIVATION_VERSION` is correctly not bumped (it is a setting and the default derivation is unchanged; `src/prme/ingestion/reingest.py:82-89`), and `tests/fixtures/derivation/trip_derived.json` is untouched.
4. **`uv run pytest -q` passes, tests use recorded extractions with no model call.** Not yet evidenced at review time: `/tmp/full-pytest.log` was still running (last sampled at 33%, no final line). The targeted suites pass (worker evidence: 19 passed in the original round; 108 passed / 6 skipped over `tests/test_typed_extraction.py tests/test_config_audit.py tests/test_relation_routing.py` in the fix round; ruff clean). The new tests do replay a recorded response and stub `extract_batch` (`tests/test_typed_extraction.py:45-47,132-139`).

## Findings (original round, as adjudicated)

No blocking defect found. The option is off by default, the failed fast gate keeps it off, and no finding below violates an acceptance criterion by itself. All findings are now resolved, partly resolved as recorded, or recorded as residual; see "Fix verification".

### F1. The privacy ingestion path silently ignores the option, and would refuse a typed `HAS_FACT` edge if it did not (Should fix / follow-up; checklist 2, 7)

- `GuardedExtractionProvider.extract` builds its request from `ExtractionResult.model_json_schema()` and `_extraction_prompt_for_role(..., windowed=False, resolving=False)` with no `typed` (`src/prme/ingestion/extraction.py:2072-2077`). The privacy admission path calls exactly this extractor (`src/prme/privacy/admission.py:611-639`), so with `enable_typed_extraction` on in a privacy-enabled engine, extraction gets neither the vocabulary, nor the schema fields, nor the storage. Every claim is then logged as `ingestion.typed_relation_dropped` with `reason="missing"` (`src/prme/ingestion/pipeline.py:1752-1754`), which is misleading: the field was never asked for. Under a protected scope the `ScopedLogger` drops even that log (`src/prme/privacy/diagnostics.py:142-149`), so nothing reports the no-op.
- The privacy projection cannot hold the fields even if they arrived: claim-node metadata is copied through a whitelist that does not include `relation`, `role` or `status` (`src/prme/ingestion/admitted_statements.py:26-31,145-182`), so they would be silently dropped; and `project_admitted_edge` raised `PrivacyDenied("privacy_invalid_result")` for any edge metadata whose `relation` was not `"alias"` (`src/prme/ingestion/admitted_statements.py:215-223`), which is exactly the metadata typed extraction writes on `HAS_FACT` (`src/prme/ingestion/pipeline.py:1919-1926`). `project_admitted_graph` runs on every plan edge under privacy (`src/prme/ingestion/planning.py:218-222`).
- Why this was not a blocker or a security issue: the guarded path never produces a typed relation today, so no such edge is projected and nothing leaks (the closed-vocabulary `relation` is not protected content, and the pipeline log is content-free under privacy). It was a documented-limitation gap and one wiring change away from a hard failure.

### F2. `config_audit` reports the off flag as effective; the activation gate is missing (Should fix; checklist 4)

- `enable_typed_extraction` is a `[HYPOTHESIS]` boolean (`src/prme/config.py:1364-1385`) but had no `_ACTIVATION_GATES` entry (`src/prme/config_audit.py:50-369`), while every other boolean gate in that file has the self-gate (`src/prme/config_audit.py:185-287`). Without it, `audit_hypotheses` reported `effective=True` for a flag that is off and does nothing, and the PR raised `effective_count` from 31 to 32 and from 37 to 38 (`tests/test_config_audit.py:113,223`) to keep the tests green.
- Not a blocker: the audit is reporting-only and `discovery`/`activation` are deliberately separate (`src/prme/config_audit.py:46-49`), but a dormant provisional option reported as in effect is the one thing this subsystem is meant to avoid.

### F3. Coverage gaps in the new tests (Consider; checklist 3)

The original round lacked coverage of the single-turn typed path, the typed correcting batch, the `ExtractionCache` typed keys, and the criterion-3 transition (store untyped, turn the option on, `reingest()` re-derives with the fields). The settings digest test (`tests/test_typed_extraction.py:117-121`) proves the mechanism, not the end-to-end re-derivation.

### F4. The status vocabulary and the malformed-item validator are duplicated (Consider; checklist 5)

- The status set lived in `RELATION_STATUSES` (`src/prme/ingestion/extraction.py:60`) and again as prompt text and as the field description, so a status added to the tuple would not reach the model unless both strings were edited.
- `drop_malformed_items` is copied in `_TypedBatchTurnExtraction` (`src/prme/ingestion/extraction.py:1453-1457`) and again in `_TypedCorrectingBatchTurnExtraction` (`:1521-1526`) only to change one model class.

### F5. Typed-run log volume (Consider; checklist 1)

`ingestion.typed_relation_dropped` is INFO per fact (`src/prme/ingestion/pipeline.py:1752-1754`). On a typed run every claim converted from the extractor's relationships list is "missing". The counter the docstring promises ("the caller counts it") is a log line, not a receipt or result field; on a large ingest this is one INFO line per claim.

## Fix verification (fix round, snapshot `f699b811fff6d610ba2603fd4096baf9705cc03fdcbd92ee5c7f6c2cf7f70ddf`)

I re-read the changed source (not just the message) for every listed fix. No new blocking defect and no regression in the changed paths.

| Item | Fix in source | Verification | Result |
|---|---|---|---|
| F1 edge projection | `project_admitted_edge` leaves a closed-vocabulary relation out of the projection and still refuses any other non-alias relation (`src/prme/ingestion/admitted_statements.py:215-238`); test `test_a_typed_has_fact_edge_projects_without_the_routing_hint` (`tests/test_typed_extraction.py:359-364`) | The accepted values are the closed vocabulary's fixed names only, so no free-form text can pass; real typed edges store the casefolded names from `admit_relation` (`src/prme/ingestion/extraction.py:99-101`); the alias branch is unchanged; a non-vocabulary relation still raises. Migration/export paths that call it (`src/prme/privacy/read_graph.py:173`, `src/prme/privacy/journals.py:514`) no longer fail on a typed store | Resolved |
| F1 documentation | Config description says the option does not apply to privacy-protected extraction and that turning it off does not un-type stored claims (`src/prme/config.py:1380-1383`); same two sentences in `documentation/configuration.md:147` | Matches the guarded provider (`src/prme/ingestion/extraction.py:2072-2077`) and the fact that `relation_walk._typed` reads node metadata without consulting config (`src/prme/retrieval/relation_walk.py:255-281`) | Resolved |
| F2 activation gate | `"enable_typed_extraction": ("enable_typed_extraction", "enable_typed_extraction is true", bool)` appended (`src/prme/config_audit.py:288-292`); counts back to 31 and 37 (`tests/test_config_audit.py:113,223`); only `EXPECTED_HYPOTHESES` grows (`:87`) | The entry has the same shape as the neighbouring boolean gates; with the flag off the audit now reports it not effective while still discovering it | Resolved |
| F3 single-turn typed model | test `test_the_single_turn_typed_model_keeps_the_resolved_text_and_the_relation` (`tests/test_typed_extraction.py:303-314`) | Validates the resolving model through `validate_raw_output(..., resolving=True, typed=True)` and asserts both `resolved_text` and the typed fields; `_single_result_model` selects `_TypedResolvingCitedExtractionResult` (`src/prme/ingestion/extraction.py:944-947,1537-1548`) | Resolved |
| F3 typed correcting schema | test `test_the_typed_correcting_batch_model_carries_the_relation_and_the_memory_label` (`tests/test_typed_extraction.py:317-320`) | Asserts `relation`, `role`, `status` and `replaces_memory` together on `_TypedCorrectingCitedFact`, which is the MRO the checklist asked about (`_TypedClaimFields` first, `src/prme/ingestion/extraction.py:1512`) | Resolved |
| F3 cache keys | test `test_the_typed_cache_keys_differ_from_the_untyped_ones` (`tests/test_typed_extraction.py:323-333`) | Asserts the single-turn and batch keys differ with the flag (`benchmarks/diagnostics/extracted_packs.py:348-352,443-450`), so a typed rebuild cannot replay an untyped response | Resolved |
| F3 on-transition | test `test_turning_the_option_on_rederives_a_stored_source_with_the_relation` (`tests/test_typed_extraction.py:367-388`) | Stores and reingests with the flag off, then reopens the pack with it on: `processed == len(RECORDED["turns"])` would be 0 without the settings change, and the stored `works_at` claim carries `relation` and the role. This exercises the criterion-3 path end to end | Resolved |
| F4 status vocabulary | `_STATUS_WORDS = ", ".join(RELATION_STATUSES)` (`src/prme/ingestion/extraction.py:61`), used in the prompt (`:85`) and the field description (`:335`) | One source for both strings | Partly resolved: the `drop_malformed_items` copy remains (`:1453-1457`, `:1521-1526`), as recorded |
| F5 role/status drops | individual logs when an admitted relation loses a non-empty role or status (`src/prme/ingestion/pipeline.py:1755-1759`) | Fires only on a real drop; the message carries the model's `predicate`/`role`/`status`, not source text, and a protected scope still strips it (`src/prme/privacy/diagnostics.py:142-149`) | Partly resolved: the per-fact "missing" INFO line remains (`:1752-1754`), as recorded |
| Adversarial role end | `_typed` takes `role_end` from `normalize_predicate` when the normalizer names the same relation and role (`src/prme/retrieval/relation_walk.py:270-281`); test `test_a_role_is_read_at_the_end_the_predicate_names` (`tests/test_typed_extraction.py:274-297`) | `normalize_predicate("is_child_of")` is `kin_of/child/role_end=subject` (`src/prme/retrieval/relations.py:265-270`); a role-gated route pivoting on the parent (object end) now accepts the claim. The oracle keeps `object` because its link metadata has no `predicate`, so no oracle regression | Verified |
| Adversarial edge precedence | a node-typed relation is no longer overridden by the link (`src/prme/retrieval/relation_walk.py:302-321`); oracle test uses `node_relation=False` (`tests/test_typed_extraction.py:267-271`) | Node rows are read before edge rows and populate `typed`; the oracle's claim nodes carry no relation, so its link still types and still retires a `superseded` claim (`:324-328`) | Verified |
| Adversarial provider shape | `_typed_batch`/`_typed_single`/`_typed_claims` recorded at construction, one warning when neither accepts (`src/prme/ingestion/pipeline.py:380-389`); `extract_turn` uses `_typed_single` (`:1039`), `_request_batch` uses `_typed_batch` (`:1226`), storage uses `_typed_claims` (`:1747`); settings omit the flag in that case (`src/prme/ingestion/reingest.py:110-131`); test `test_the_derivation_settings_ignore_the_option_when_the_provider_cannot_take_it` (`tests/test_typed_extraction.py:350-356`) | `typed_request = typed_batch if batch_target_tokens > 0 else typed_single` matches the constructor's rule that batching cannot combine with the window or fact text resolution (`src/prme/ingestion/pipeline.py:314-319`), so the flag is in the settings exactly when a used path asks for typed fields | Verified |

Residual risks (recorded, not blocking):

- F1's projection choice means a privacy migration or export of a store built with typed extraction drops the hint on both the claim node (still outside `_CLAIM_FIELDS`, `src/prme/ingestion/admitted_statements.py:26-31`) and its link (the new drop branch, `:226-233`), so the migrated store falls back to predicate normalization. The docs cover privacy-protected extraction, not migration. Follow-up: say so in the config row, or project the routing hint into the migrated claim.
- `_typed_claims` is true when either path accepts the keyword, while `_prepare_plan` uses it for every claim (`src/prme/ingestion/pipeline.py:386,1747`). A provider that accepts `typed` on `extract_batch` but not on `extract`, on an engine that uses the single path, would log "missing" for every claim without storing anything. Same class as F5 and unreachable with `InstructorExtractionProvider`.

Fix-round verification notes:

- Full suite: `/tmp/full-pytest.log` was at 33% with no final line at fix-verification time, so acceptance criterion 4 is still unproven.
- Worker evidence for this round: 108 passed, 6 skipped over `tests/test_typed_extraction.py tests/test_config_audit.py tests/test_relation_routing.py`. I did not rerun tests (worker-owned); this section is a source verification.

## Security audit results

| Area | Result | Details |
|---|---|---|
| Secrets or PII in logs | PASS | The new logs carry `predicate`, `stated_relation`, `role`, `status` and `reason` only; no source span, quote or evidence. Under a protected scope `ScopedLogger` replaces them with `privacy_operation_event` (`src/prme/privacy/diagnostics.py:142-149`). |
| Prompt / injection surface | PASS | The typed block is built from the static `RELATIONS` table (`src/prme/ingestion/extraction.py:64-89`); no source text reaches the format string. |
| SQL / command / path injection | PASS | No SQL is new (`walk_statement` is unchanged); the harness runs a fixed `git rev-parse` argv (`benchmarks/graph_oracle/extraction.py:151-157`); `load_gold` path use is operator CLI input, as on the existing `fidelity` command. |
| Tenant scoping and privacy paths | PASS, with the recorded residual | The walk still filters `user_id`/scope in SQL unchanged; the privacy extraction path is unchanged and does not carry the option; the projection now accepts a closed-vocabulary relation without letting free-form text through. |
| External calls | PASS | Product code adds none. The harness reaches only the local Ollama endpoint and uses `build_config`, whose hosted branch is the pre-existing `PRME_BENCH_HOSTED` operator path. |
| Persisted-field exposure | PASS | `relation`/`role`/`status` are a closed-vocabulary name, a short qualifier and a closed status, on a claim the user already stores. |

## Pattern consistency and full-config comparison (checklist 4)

- Option wired exactly once per setting: read at `src/prme/storage/engine.py:837,1030` into both `IngestionPipeline` constructions (the only two in `src/prme`), recorded at `src/prme/ingestion/pipeline.py:380-389`, used by `_extract_turn` (`:1039`), `_request_batch` (`:1226-1228,1241,1271`), `_prepare_plan` (`:1746-1763,1925`) and `reingest.extraction_settings` (`:107-131`). No other runtime reader (grep over `src/prme` for `typed`/`relation` metadata).
- Request/schema selection is consistent: the same recorded `_typed_batch`/`_typed_single` values drive the provider calls, the validation models, the batch record digest and the settings digest.
- Off-path behavior is unchanged: `batch_request_messages(..., typed=False)` and `_extraction_prompt_for_role(..., typed=False)` produce the previous text, so the off digest and existing extraction caches are unchanged (asserted at `tests/test_typed_extraction.py:59-67`; the worker's off fast-gate run reproduced the committed counts).
- Docs/config surface: `documentation/configuration.md:147`, `docs/RELATIONS.md:53-59`, `CHANGELOG.md:12`, `BENCHMARKS.md:4610-4653`, `src/prme/config.py:1364-1385` and `src/prme/config_audit.py:288-292` all updated; `tests/test_config_audit.py:87` updated.
- Reporting: the harness pins `enable_pronoun_repair: False` alongside the option (`benchmarks/graph_oracle/extraction.py:166-167`); `BENCHMARKS.md:4610-4628` names the command, commit, model and fixture set but not that setting. Minor reproducibility nit, not a finding.

## Redundancy check (checklist 5)

The status vocabulary now has one source (fix round). The remaining duplication is the `drop_malformed_items` copy recorded under F4. Otherwise the new symbols are the minimum the feature needs: two model selectors (`src/prme/ingestion/extraction.py:1537-1548`), one admission function (`:91-108`), one prompt block (`:64-89`), one cache flag, one harness module. No dead code or orphaned option.

## Data safety and read/write proof (checklist 7)

| Field | Runtime writer | Runtime reader | Old records |
|---|---|---|---|
| claim node `metadata.relation/role/status` | `src/prme/ingestion/pipeline.py:1746-1763` (inside the per-fact block, before `MemoryNode` creation at `:1863-1880`) | `src/prme/retrieval/relation_walk.py:296-306` via `_typed` (`:255-281`); `benchmarks/graph_oracle/extraction.py:47-53,122-147` reads node first, then link | Missing keys: `_typed` returns `None` and `normalize_predicate` runs (`relation_walk.py:303-305`); no migration, no backfill |
| `HAS_FACT` edge `metadata.relation/role/status` | `src/prme/ingestion/pipeline.py:1919-1926` (only when a subject entity resolved, as before) | `src/prme/retrieval/relation_walk.py:319-330`; harness `:47-53,112-121` | Same fallback; `metadata=None` when untyped, preserving the previous edge; the node's reading wins when both are typed |
| claim metadata across merge | `merged_claim` keeps the new node's metadata (`src/prme/ingestion/claim_merge.py:138-160`), so typed fields survive a repeated-claim merge | as above | unchanged |

- Serialization proof: DuckDB end to end in `tests/test_typed_extraction.py:160-177`, which asserts the exact edge dict. PostgreSQL uses the existing `MemoryEdge.metadata` JSON column and `get_edges` decode; no PostgreSQL-specific code is added, and the local PG suite skips without `PRME_TEST_DATABASE_URL` (residual risk only).
- The fields are optional and additive; nothing reads them unless `relation_routing` routes a question, so an old store and a new one behave the same until a claim states a relation.
- `status` is stored but never retires a claim from the walk; only the lifecycle does, and the oracle's link-level `superseded` still retires because oracle links carry `shape` (`src/prme/retrieval/relation_walk.py:326-328`; tests `tests/test_typed_extraction.py:261-271`). This is the right call: the model's label is not a product lifecycle transition.
- Supersedence and claim merge are unchanged: the detector still reads predicate/object/polarity (`src/prme/ingestion/pipeline.py:1937-1957`), and `claim_key` still ignores the typed fields (`src/prme/ingestion/claim_merge.py:42-62`).
- Re-derivation: turning the option on makes stored sources stale and re-derives them (criterion 3, now tested end to end); turning it off makes typed-derived sources stale again, as documented. No `DERIVATION_VERSION` bump, correctly.

## Break scenarios (data safety, checklist 7)

| # | Scenario | Verdict | Reasoning |
|---|---|---|---|
| 1 | Old claim with no typed fields read by a routed question | Safe | `_typed` returns `None`; the predicate normalizer path at `relation_walk.py:303-305` is unchanged; covered by the existing relation-routing suite |
| 2 | Model invents a relation or a long role | Safe | `admit_relation` drops relation+role+status together on an invented name, drops the role alone on length/character failure, and the claim keeps its predicate (`extraction.py:91-108`); parametrized tests `:76-91` |
| 3 | Model returns a non-text relation/role/status | Safe on the checked path | `_TypedClaimFields.ignore_non_text` nulls it (`extraction.py:340-354`); the batch validator reads facts from the raw entry, so a bad optional field cannot cost the claim (`extraction.py:1602-1620`) |
| 4 | Repeated claim merged into one current record | Safe | `merged_claim` keeps the new metadata, so `relation/role/status` survive (`claim_merge.py:151-160`) |
| 5 | Typed batch replayed after the option is turned off | Safe | `request_sha256` differs, so the old record is stale and re-derived; the untyped model ignores the extra keys |
| 6 | Privacy-enabled engine with the option on | Safe with F1 fix | No typed fields are produced (guarded provider); a typed store can still be migrated/exported, losing the hint (recorded residual) |
| 7 | Provider without the `typed` keyword with the option on | Safe | `_typed_batch`/`_typed_single` are false, the option stores nothing, one warning is logged, and the settings omit the flag (`pipeline.py:380-389`; `tests/test_typed_extraction.py:350-356`) |

## Wiring findings

- Both `IngestionPipeline` constructions get the option (`src/prme/storage/engine.py:837,1030`); no other construction exists in `src/prme`.
- Keyword acceptance is recorded once at construction (`src/prme/ingestion/pipeline.py:380-389`), so a provider written to the old signature keeps working while the option is on, and the cache wrapper's `**extra` shape is recognised (`benchmarks/fast_cycle/store_extract.py:80-117`).
- The benchmark chain keys on the same `typed` value end to end: `ExtractionCache._key/_batch_key` (`benchmarks/diagnostics/extracted_packs.py:348-352,443-450`), `StoredTurnCache._batch_key` (`benchmarks/fast_cycle/store_extract.py:57-60`) and `RequestMeter` (`:80-117`).
- The harness's reader handles both writers (oracle link metadata, typed extraction node+link metadata) and counts typed versus untyped claims (`benchmarks/graph_oracle/extraction.py:104-148`); `run.py:422-428,461-467` exposes `extraction-fidelity` with `--untyped` for the baseline.

## Verification

- Worker evidence used: original round 19 passed / 339 passed over the related modules; fix round 108 passed, 6 skipped over `tests/test_typed_extraction.py tests/test_config_audit.py tests/test_relation_routing.py`; ruff clean; fidelity 2026-11-08 table reproduced; two live fast-gate on samples both exit 1.
- Independently verified here: the diff against the base (both snapshots), every changed production call site, the writer/reader pairs in the data-safety table, the Pydantic model selection and MRO path, the privacy guarded path and projection, the cache keys, `config_audit`, the harness output (`/tmp/extfid-all/report.md`) against `BENCHMARKS.md`, and the fast-gate report (`/tmp/fast-typed-on-1/report.md`).
- Not available at review time: the final line of `/tmp/full-pytest.log`; it was still running (33%, no summary line) during the fix verification. Acceptance criterion 4 therefore remains unproven until that run finishes.

## Resolution status

| Finding | Checklist | Severity | Status |
|---|---|---|---|
| F1 privacy path ignores the option; projection refused a typed edge | 2, 7 | Should fix | Resolved (projection fixed, limitation documented); privacy-migration drop recorded as residual |
| F2 `config_audit` missing activation gate, counts bumped | 4 | Should fix | Resolved |
| F3 test coverage gaps (single-turn typed, typed correcting batch, cache keys, on-transition) | 3 | Consider | Resolved (all four tests added) |
| F4 duplicated status vocabulary and malformed-item validator | 5 | Consider | Partly resolved (vocabulary one source; validator copy remains recorded) |
| F5 per-fact INFO drop log on typed runs | 1 | Consider | Partly resolved (role/status drops now logged; per-fact "missing" line remains recorded) |
| Acceptance criterion 2 (fast gate) | - | Criterion not met | Recorded in `BENCHMARKS.md:4644-4653`; option stays off |
| Acceptance criterion 4 (full pytest) | - | Unproven | Full run still in progress at review time |
