# Issue #400 typed extraction relations: adversarial review

Status: COMPLETE

- Issue: dwamianm/prism#400, `enable_typed_extraction` (off by default)
- Base commit: 48bb094196d356579600e8f8709afdab405a050f (origin/main)
- HEAD: ad81421535fce2df3e3160930d602dcd33f99eb0 plus working-tree changes
- Reviewed snapshot: 5eec1b079707c54b4241ab12163d2b7368645f1c85fb38a1c3521530ea625e63
- Tier: L. Checklist 8 (adversarial scenarios) only. Max 8 scenarios.
- The scenarios below record the reviewed snapshot. The current fix state, on
  snapshot f699b811, is in "Fix verification" at the end of this report.

## Checklist (assigned)

8. Adversarial scenarios: production-incident postmortem. Existing data and
   installs (pre-option claims/edges, fallback, reingest markers, no
   DERIVATION_VERSION bump, option on then off). Guard loopholes (providers not
   accepting typed, `_accepts_keyword`, untyped parsing relation fields, any
   ungated read of `fact.relation`). Status label trust and claim hiding.
   Claim merge / supersedence / repeated-claim interaction. HAS_FACT edge
   metadata overwritten by other writers. Reingest settings, cache key, saved
   typed records parsed by the base ExtractionResult, partial failure and retry.
   Benchmark harness candidate_graph correctness and fidelity overstatement.
   Fast-gate regression as code defect vs extraction quality.

## Bottom line first

The default path is untouched. With the option off the extraction prompt, the
batch messages, the cache keys, the derivation settings and the dump of an
extraction record are all byte-identical to the base commit, so no existing
store, pack or cache changes. Everything below needs `enable_typed_extraction`
turned on, and four of the eight also need `enable_relation_routing` (off by
default, issue #399). The failures are therefore future-flip failures, not
today's incident, which is why most are Follow-up rather than Fix now.

The two I would not ship as written are the role-end assumption (S1) and the
privacy projection (S4): both are deterministic, neither was tested, and both
are cheap to close.

## Scenario 1: a typed claim's role is always read as the object's qualifier, so role-gated routes reject the claims they were built for

- **Trigger.** `enable_typed_extraction` and `enable_relation_routing` on; a
  claim whose model-stated role belongs to the subject end, which is every
  predicate of the `X_of` form the prompt tells the model to keep ("is_child_of",
  "is_sister_of", "is_married_to"). A question that routes a role-gated step
  ("children", "dependents", or any question naming a people word) then pivots
  on the object end.
- **Mechanism.** `src/prme/retrieval/relation_walk.py:270` returned
  `ClaimRelation(relation, role, "object" if role else None)` - the role end was
  hardcoded to the object whenever a role exists. The deterministic normalizer
  this replaces derives the end from the predicate text:
  `src/prme/retrieval/relations.py:265` sets `role_end = "subject" if words[-1]
  == "of" else "object"`, and the routing tests pin that pair
  (`tests/test_relation_routing.py:84-86`: `is_child_of` -> subject,
  `has_child` -> object). `_accepts` compares the stored end to the answered
  end (`relation_walk.py:347`), so a typed claim "Nico is my child" that pivots
  on the owner is rejected: the answer lives at the subject end, the claim says
  object. The extraction guidance asks for the family word directly
  (`src/prme/ingestion/extraction.py:81-84`), so this is the normal shape for a
  family claim, not an edge case.
- **Likelihood.** Medium. Two opt-in options, but once both are on the failure
  is systematic for the `X_of` form and silent for every affected claim. No
  test covered a typed claim with a role through the walk;
  `tests/test_typed_extraction.py` asserts stored metadata only.
- **Impact.** High for the feature's own purpose: relation-routed recall drops
  below the option-off baseline for family and role questions, because the walk
  uses the typed relation *instead of* the normalizer that was right.
- **Detection.** Quiet. Nothing logs a claim that fails `_accepts`; the only
  evidence is a lower route candidate count in the retrieval receipt, which
  reads like ordinary routing variance.
- **Cheapest mitigation.** In `_typed`, when a role is present and
  `normalize_predicate(predicate)` names the same relation and role, take its
  `role_end`; otherwise keep the current value. Add one routing test
  (`is_child_of` typed claim, question "who is Maya's child"). Rough size: S
  (10-15 lines plus a test).
- **Verdict.** Fix now.
- **Label.** Newly introduced. The hardcoded object end predates the change but
  only the graph-oracle harness used it; the product path is new.

## Scenario 2: a stated relation silently overrides a deterministic predicate mapping, with no agreement check and no measured precision on the claims that matter

- **Trigger.** `enable_typed_extraction` and `enable_relation_routing` on; a
  claim whose predicate the normalizer maps to the right relation and whose
  model-stated relation is a different vocabulary name, or the right name with a
  wrong role.
- **Mechanism.** `src/prme/retrieval/relation_walk.py:286-294`: a stored
  relation on the claim node wins outright, and the normalizer is not consulted
  at all (`elif claim_id not in typed`). There is no agreement check, no
  confidence, and no receipt flag distinguishing "routed from a model label"
  from "routed from the predicate".
- **Likelihood.** Medium to high if the option is flipped. The change has no
  guard, and this branch has no A/B against the normalizer. Note honestly what
  the evidence does and does not say: `BENCHMARKS.md` relation precision is 0.27
  to 0.44, but that denominator is every candidate relation, and extraction
  over-generates entities and claims (entity precision 0.41 to 0.67), so those
  numbers do not isolate "wrong relation on a claim the gold graph holds". The
  risk is real and unquantified, which is the point: the change flips the
  authority from a deterministic mapping to a model label on the strength of a
  table that cannot measure it.
- **Impact.** Medium to high, same shape as S1: a route candidate is lost or a
  wrong relation is reported in the receipt, and nothing falls back to the
  normalizer to catch it.
- **Detection.** Quiet. The receipt records the relation used, but a wrong
  relation is indistinguishable from a right one without the gold graph.
- **Cheapest mitigation.** Trust the stored relation only when it agrees with
  `normalize_predicate(predicate)`, or when the normalizer returns None; record
  a `relation_source: model|predicate` in the claim receipt field. Rough size: S
  (10 lines), plus one fast-cycle run with both options on.
- **Verdict.** Follow-up (blocking for the flip, not for the merge).
- **Label.** Newly introduced.

## Scenario 3: the option is silently inert with any provider that does not take `typed`, while the derivation marker says it was on

- **Trigger.** The option is on and the extraction provider's `extract`/
  `extract_batch` does not accept `typed`: a custom provider, a subclass written
  against the older signature, a `**kwargs` wrapper that accepts and ignores the
  keyword, or the privacy-guarded path, whose
  `GuardedExtractionProvider.extract(content, snapshot, *, guard)`
  (src/prme/ingestion/extraction.py:2072-2077) can never be asked and whose
  schema is the base `ExtractionResult`.
- **Mechanism.** The *request* was gated on `_accepts_keyword`
  (`src/prme/ingestion/pipeline.py:1030-1032`, `1217-1219`) but the *storage*
  was gated on the config flag alone (`pipeline.py:1738`), so every fact took
  the missing-relation branch and logged `ingestion.typed_relation_dropped`
  with `reason="missing"` at INFO, once per fact (`pipeline.py:1743-1745`).
  Meanwhile `extraction_settings` recorded `typed_extraction: True` from the
  config flag regardless (`src/prme/ingestion/reingest.py:124-127`), while the
  request digest it stores alongside was the untyped one (`reingest.py:109`,
  `:118`). So turning the option on marked every memory stale, paid a full
  re-derivation that could not change anything, and turning it off marked them
  stale again.
- **Likelihood.** Medium. Any privacy-guarded engine (`enable_privacy` is a
  supported deployment) hits this on every ingest; so does any caller-supplied
  provider, which is the documented extension point.
- **Impact.** Medium: one INFO line per fact per turn (tens of thousands on a
  real session), model spend with no effect, and a plausible misreading that the
  extractor is "dropping" relations it was never asked for. The message names
  the fact, not the provider, so the real cause is invisible.
- **Detection.** Quiet. No warning, no counter, no receipt field; the drop log
  says "missing", which points at the model instead of at the wiring.
- **Cheapest mitigation.** Warn once when the option is on and the provider does
  not accept `typed`, and leave the flag out of `extraction_settings` in that
  case; log the per-fact drop at debug or aggregate it per turn. Rough size: S
  (10 lines plus a test).
- **Verdict.** Fix now (cheap, prevents a misdiagnosis).
- **Label.** Newly introduced.

## Scenario 4: a typed claim's HAS_FACT edge cannot pass the privacy projection, so migrating or exporting a store that ever ran typed extraction aborts

- **Trigger.** A pack built with `enable_typed_extraction` on (any store, not
  necessarily a privacy one), then `prme privacy migrate` to a protected
  destination, or `prme privacy export`, or any graph read through the privacy
  guard's `read_graph.get_edges`.
- **Mechanism.** Typed extraction writes `relation` into the HAS_FACT edge
  metadata (`src/prme/ingestion/pipeline.py:1912`). The admitted-edge projection
  treated that key as the alias barrier and rejected anything that was not
  `"alias"`: `src/prme/ingestion/admitted_statements.py:220-222` raised
  `PrivacyDenied("privacy_invalid_result")`. Every privacy path projects each
  edge it keeps: `src/prme/storage/privacy_migration.py:954`,
  `src/prme/storage/privacy_export.py:104`, `:302`,
  `src/prme/privacy/read_graph.py:173`. The migration therefore raised on the
  first typed claim and marked the job `interrupted`
  (`privacy_migration.py:986`).
- **Likelihood.** Low to medium. Two opt-ins plus an explicit, documented,
  one-way operator flow ("Inspect with privacy dry-run, then explicitly migrate
  to a separate empty destination": `src/prme/privacy/cli.py:401`). Rare, but
  the flow is the one you run once and cannot half-finish.
- **Impact.** High when it happens: the migration is blocked, the target must be
  discarded, and the only recovery was to re-derive the source without the
  option (S6), which throws the relations away. The privacy *read* path
  additionally denied the whole edge batch rather than one edge.
- **Detection.** Loud (`PrivacyDenied`/`ValueError`), but with no message naming
  the relation key, so the operator sees an opaque `privacy_invalid_result` on a
  store that has nothing to do with privacy.
- **Cheapest mitigation.** Admit `relation`, `role` and `status` in
  `project_admitted_edge` through the same vocabulary and status validation the
  pipeline uses, or strip them (they are routing hints, not evidence). Rough
  size: S (6-10 lines plus a test that a typed edge survives projection).
- **Verdict.** Fix now.
- **Label.** Newly introduced.

## Scenario 5: the fidelity table that gates the option mixes harness defaults with extraction quality

- **Trigger.** Reading the `extraction-fidelity` table in `BENCHMARKS.md` and
  treating relation precision/recall and status accuracy as extraction quality,
  or re-running the command and getting different numbers.
- **Mechanism.** `benchmarks/graph_oracle/extraction.py`:
  (a) for a `has` claim the attribute falls back to the predicate
  (`:141`, `metadata.get("attr") or metadata.get("predicate")`), and the product
  never writes `attr`, so the harness compares a made-up predicate against the
  gold attribute - a systematic false positive and recall miss for every literal
  attribute claim the typed guidance tells the model to emit;
  (b) an unstated status is scored as `"current"` (`:139`), which is also gold's
  common status, so part of the reported 0.87 to 1.0 status accuracy is the
  default, not the model;
  (c) the object is the first `MENTIONS` target that is not a text mention
  (`:143-144`), and the only exclusion is `basis == "text_mention"`; a
  `group_member` link (`enable_owner_group_links` is on by default) can therefore
  be taken as the claim's object, and the choice depends on the order
  `get_edges` returns, so the numbers are not reproducible;
  (d) `fidelity.py:56` credits a candidate key that matches an *inferred* gold
  claim to relation precision while excluding inferred claims from the recall
  denominator (`:41-47`, `:61-62`), which inflates precision.
- **Likelihood.** High that the table is wrong to some degree in at least one
  fixture; the direction is mixed (a and c understate, b and d overstate
  different columns), so the summary sentence cannot be read as a clean
  measurement.
- **Impact.** Medium. This table is the issue's evidence that the option stays
  off; a future flip will read it again, and a fix aimed at the wrong column
  wastes a cycle.
- **Detection.** Silent. The report prints a single run, with no interval and no
  sensitivity.
- **Cheapest mitigation.** Fix the four items above (store `attr` for literal
  claims or compare through `predicate` in both directions; score an absent
  status as absent, not `current`; exclude every non-object `basis` and choose
  the object by the entity the gold-style subject does not already explain; drop
  the inferred credit from precision or report precision split). Rough size: M
  (a day), all in `benchmarks/`.
- **Verdict.** Follow-up (required before any later flip reads the table as
  evidence).
- **Label.** Newly introduced (the harness is new; `fidelity.py` is pre-existing).

## Scenario 6: no clean rollback - turning the option off neither restores the old routing nor keeps the relations

- **Trigger.** Turn the option on, ingest, then turn it off, on the same store.
- **Mechanism.** Stored claim nodes keep `metadata.relation`, and the walk reads
  it as authoritative (`relation_walk.py:286-291`), so
  `PRME_ENABLE_TYPED_EXTRACTION=false` does not restore the pre-option routing
  for those claims. Conversely the derivation marker omits the flag when it is
  off (`reingest.py:124-127`), so every memory derived while it was on reads as
  stale (`DERIVATION_VERSION` unchanged, correctly, because the setting shows
  the change), and a reingest re-derives from a fresh untyped extraction: the
  relations are gone, the claims are a new sample, and there is no path back.
- **Likelihood.** Medium. An operator who turns a gated option off after a bad
  fast-cycle sample has exactly this expectation.
- **Impact.** Low to medium: an irreversible difference between two stores that
  both report the option off, plus a full model pass.
- **Detection.** Quiet. `documentation/configuration.md` says routing reads the
  stored fields, but nothing at the flag, in the reingest report, or in the
  receipt says "this store is still typed".
- **Cheapest mitigation.** Report the typed claim count in the reingest result
  (and in diagnostics), and say in the option's description that turning it off
  does not un-type existing claims. Rough size: S.
- **Verdict.** Accept (documented), with the two reporting additions.
- **Label.** Newly introduced (by design).

## Scenario 7: claim merge and supersedence cannot tell a plan from a done action, so the new status field is lost on the second mention

- **Trigger.** `enable_typed_extraction` on, `enable_claim_merge` on (default in
  the alpha channel); the same predicate and object stated twice with different
  statuses and no date ("I'm going to call Sam" then "I called Sam").
- **Mechanism.** `claim_key` (`src/prme/ingestion/claim_merge.py:42-62`) is
  node type, epistemic type, source type, normalised predicate, normalised
  object, object entity, polarity and resolved date - status is not part of the
  identity. The repeated-claim finder merges the two, `merged_claim` keeps the
  new node's metadata (`claim_merge.py:151-160`), and the earlier copy is
  superseded, so the planned status is gone from the graph. Nothing routes on
  status today, so the loss is invisible to retrieval.
- **Likelihood.** Medium for a plan/action pair without a date; low for a dated
  plan, which `claim_key` already keeps apart.
- **Impact.** Low to medium: the field the issue adds to distinguish "planned"
  from "done" (its stated purpose) is dropped precisely when the two statements
  meet, and the fidelity graph sees only one of them.
- **Detection.** Silent.
- **Cheapest mitigation.** Add `metadata.get("status")` to `claim_key`, so a
  planned and a done statement stay two records; rough size: S, but it changes
  merge behavior for every store, so it needs the fast cycle.
- **Verdict.** Follow-up.
- **Label.** Newly introduced (the status field is new; the merge rule is
  pre-existing).

## Scenario 8: a dropped role or status is never logged or counted, and the status vocabulary has two copies that can drift

- **Trigger.** A role or status the model states but that fails admission: a
  role over 50 characters, a role with a non-ASCII letter (a French or Spanish
  source's "soeur"/"frere"), or a status outside the constant.
- **Mechanism.** `admit_relation` dropped the role alone (`extraction.py:103-104`)
  and the status alone (`:105-106`) and still returned a relation, so the
  pipeline's only log (`pipeline.py:1743-1745`) did not fire - the drop was
  invisible, and the claim was stored with a relation and no role, which routes
  differently for every role-gated step. Separately, `RELATION_STATUSES`
  (`extraction.py:60`) and the prompt's literal status list (`:85-86`) were two
  copies that could drift; a drift would drop statuses on replay with no
  request-digest change and therefore no re-derivation marker
  (`reingest.py:118-127`).
- **Likelihood.** Medium for the silent role drop on any non-English source;
  low for the vocabulary drift.
- **Impact.** Low: a claim is admitted with a relation that cannot serve a
  role-gated route, and no one can find out why.
- **Detection.** Silent.
- **Cheapest mitigation.** Count the role and status drops in the same log
  record (or a per-turn counter) and build the prompt's status sentence from
  `RELATION_STATUSES` so the two cannot drift. Rough size: S (5 lines).
- **Verdict.** Follow-up.
- **Label.** Newly introduced.

## The three I attacked hardest, and why each held

1. **The model's `status: superseded` hiding or retiring a stored claim.**
   Held. The walk retires a claim only from its lifecycle
   (`relation_walk.py:297-299`), and the one status read that can retire
   requires `shape is not None` on the edge (`relation_walk.py:314`), a key only
   the graph oracle writes (`benchmarks/graph_oracle/graph.py:89-91` puts it in
   every `_meta`, and `:130` writes the HAS_FACT edge with it, so no existing
   oracle pack changes behavior either). Typed extraction never writes `shape`.
   I searched every writer and reader of edge or node `status` in `src/`: no
   product reader exists, the graph oracle's own SQL and strategies are
   benchmark-only, node `status` is not read anywhere, and the privacy node
   projection drops it. So the model's label cannot hide a claim.
2. **Saved typed records and replay under the option off.** Held. The fields
   live on `ExtractedFact` itself (`src/prme/ingestion/schema.py:238-245`), so
   the base `ExtractionResult` parses a typed record without the option; the
   `exclude_if` default keeps them out of every dump when unset (verified in the
   project venv's pydantic 2.12.5: a `None` field with `exclude_if` is absent
   from `model_dump` and `model_dump_json`), so the off path stores byte-identical
   records. A raw typed output replayed with `typed=False` is ignored as an
   extra field rather than failing validation, and the pronoun-repair path uses
   `model_copy` (`src/prme/ingestion/pronoun_repair.py:316`), so a repaired claim
   keeps its relation instead of losing it - which is the failure I expected.
   The extraction-record settings also differ the moment the request really
   changes, because the stored request digest is the typed one
   (`reingest.py:118`).
3. **Cache invalidation and the off-side fast gate.** Held. With `typed=False`
   the prompt (`extraction.py:1178-1185`), the batch messages (`:1382-1403`) and
   therefore both cache keys are byte-identical to the base commit, so the
   committed caches replay and the off side is unchanged; the on side keys
   apart, which is why the on runs were live. The `store_extract` positional
   `typed` argument lands in the right slot
   (`benchmarks/fast_cycle/store_extract.py:57-60`). I also checked whether the
   fast-gate regression is a code defect: it is not. `enable_relation_routing`
   defaults to `False` (`src/prme/config.py:1338-1339`), so the fast cycle never
   runs the walk, and S1/S2 cannot explain the top-3 drop; the on side's extra
   repeated claims are extraction over-generation, and the 4 repeated
   subject/predicate/object claims on chat-v1 are the pre-existing `claim_key`
   guard, not the typed fields. Two live samples both landing lower, with an off
   replay reproducing the baseline, is the evidence the gate asks for, and the
   option stays off.

## Pre-mortem headline

A gated option flips on for its stated purpose and the routing it was built to
improve gets worse, silently: the model's role label is read at the wrong end of
the claim and its relation label overrides the deterministic mapping that was
right, while a store that later tries to become a privacy pack cannot migrate
because a routing hint on an edge is read as a forbidden alias field.

---

# Fix verification (snapshot f699b811fff6d610ba2603fd4096baf9705cc03fdcbd92ee5c7f6c2cf7f70ddf)

Same base `48bb094196d356579600e8f8709afdab405a050f`, HEAD `ad814215` plus the
current working tree. I re-read the changed source ranges and the new tests;
the worker's rerun (108 passed, 6 skipped across the typed, config-audit and
relation suites) is reported evidence, not something I re-executed.

## S1 - role end: fixed, and the edge no longer overrides the node

- `src/prme/retrieval/relation_walk.py:255-281`: `_typed` now takes
  `role_end = "object"` and replaces it with `normalize_predicate(predicate).role_end`
  when that normalizer names the same relation *and* the same role, so
  `is_child_of` lands on the subject end and `has_child` on the object end. The
  condition requires `normalized.role_end is not None`, so a normalizer result
  without a role cannot blank the end.
- `relation_walk.py:316-319`: the HAS_FACT branch is now
  `if found is not None and claim_id not in typed`, so the node's reading wins.
  I checked both row orders: the `"n"` branch assigns `claim.relation`
  unconditionally (`:293-298`) and the `"e"` branch only when it is not already
  typed, so the node's `role_end` survives whichever row the engine returns first.
  Polarity is still read from the node metadata (`:300-301`), which is where the
  pipeline writes it, so skipping the edge branch loses nothing.
- `tests/test_typed_extraction.py:274-297` is a true regression test: with the
  old `_typed` the claim would be rejected whichever row order was used, because
  the edge's `"object"` either overwrote the node's reading or stood because the
  node branch did not populate `typed`. The walk is asserted through
  `walk.scores`, not through internals, which is the right level.
- Residual (narrow, not a regression): the fix inherits the normalizer's own
  ambiguity. A bare-noun predicate ("sister" with no `_of` or `has_`) still
  gives `"object"`, exactly as the normalizer did before the option existed. A
  caller-defined concept that names a role on a relation the normalizer does not
  name by role (`retrieve(concepts={"lead": ["involved_in:leads"]})`) still gets
  `"object"`; no built-in route can, since `role_relation`
  (`src/prme/retrieval/relations.py:154-165`) only returns kin, partner,
  manager and friend roles. Worth a sentence in `docs/RELATIONS.md`, not a fix.

## S3 - inert option: fixed for declared signatures

- `src/prme/ingestion/pipeline.py:378-389` computes `_typed_batch`,
  `_typed_single` and `_typed_claims` once, from `_accepts_keyword`, with
  `getattr(provider, "extract_batch", None)` guarding the missing method, and
  logs `ingestion.typed_extraction_unsupported` once per engine when the option
  is on and neither path can take it.
- Storage and the drop log are now gated on `_typed_claims`
  (`pipeline.py:1743-1759`), so a provider without `typed` no longer takes the
  per-fact "missing" branch at all - the flood and the misleading reason are
  gone, not just quietened.
- `src/prme/ingestion/reingest.py:108-131` omits `typed_extraction` unless the
  request that engine actually makes is typed: `typed_request` picks the batch
  or single path from `_batch_target_tokens`, matching how the pipeline chooses
  between `_request_batch` and `_request_single`. With the option on and a
  provider that cannot take it, the settings are the engine's pre-option
  settings, so no source is marked stale and no re-derivation is paid.
- `tests/test_typed_extraction.py:336-356` uses a provider whose two methods
  declare no `typed` and asserts both the three flags and the absent settings
  key, which is exactly the S3 symptom pair.
- Residual (worth one line in the PR, not a blocker): `_accepts_keyword`
  (`pipeline.py:253-259`) answers True for any `**kwargs` provider, so a wrapper
  that accepts and swallows `typed` still reports the option as supported - no
  warning, the per-fact drops return, and the flag *is* recorded, so the
  re-derivation-for-nothing half of S3 survives for that shape. The benchmark
  cache wrappers forward the keyword correctly
  (`benchmarks/diagnostics/extracted_packs.py:411-413`), so this needs a
  caller-written swallowing wrapper. Second, a provider whose two methods
  disagree (only one takes `typed`) while the engine runs the path that does not
  gets `_typed_claims` True from the other path, so storage runs on an untyped
  request and the per-fact drop log returns with no once-per-engine warning.
  Neither is reachable with the shipped provider or the guarded one.

## S4 - privacy projection: fixed on the reachable paths

- `src/prme/ingestion/admitted_statements.py:215-230` now keeps
  `relation == "alias"`, silently drops a value that is a member of `RELATIONS`,
  and still raises `PrivacyDenied("privacy_invalid_result")` for free-form text.
  The import is function-local, so no cycle is added.
- Consequences checked one call site at a time:
  `privacy_migration.py:954` appends the projection and the target is written
  from it, so the migration no longer aborts and the target carries no
  vocabulary relation (routing falls back to the normalizer there, which is the
  right outcome for an evidence-free hint);
  `privacy/read_graph.py:173` and `planning.py:219-222` project-and-keep, so
  reads and plans are fine;
  `privacy_export.py:104`/`:302` still raise `ValueError` when an edge differs
  from its projection, but both sites validate an *already projected* bundle
  (the plan edges are rewritten by `project_admitted_graph` before journaling,
  and the export bundle is the migrated one), so an unprojected relation cannot
  reach them through the supported flow. I did not find a path that hands an
  unmigrated pack's edges to those two sites.
- `tests/test_typed_extraction.py:359-364` asserts the drop
  (`metadata is None`) and the free-form rejection in one place.
- Residual (accepted): the loosening is on any edge type, not just HAS_FACT -
  an edge whose `relation` happens to be a vocabulary name is now stripped
  instead of rejected. The value is a hint with no evidence role, so stripping
  is the safe reading, but a future writer that abuses the key will be silently
  accepted rather than flagged. A comment or a narrower type check would close
  it if the privacy reviewer wants the barrier strict.

## S6 - documentation: fixed

- `src/prme/config.py:1361-1383` says the option "does not apply to
  privacy-protected extraction, whose guarded provider keeps the untyped schema,
  and turning it off does not un-type claims already stored".
- `documentation/configuration.md` (the `PRME_ENABLE_TYPED_EXTRACTION` row) says
  the same and adds "the stored relation keeps routing until a re-derivation
  replaces it", which is the consequence S6 actually needed stated. Both also
  now match the S3 behavior: the re-derivation-settings sentence is no longer
  claimed unconditionally, which is correct for a provider that cannot take the
  fields.

## S8 - silent drops: fixed, folded in as reported

- `src/prme/ingestion/pipeline.py:1753-1758` logs `ingestion.typed_role_dropped`
  and `ingestion.typed_status_dropped` separately, only when the model stated a
  value and admission dropped it, and only inside the branch where the relation
  was admitted (so a rejected relation still produces one message, not three).
- `src/prme/ingestion/extraction.py:60-61` is now the one source:
  `_STATUS_WORDS = ", ".join(RELATION_STATUSES)` feeds both the prompt
  (`:85-86`) and `_TYPED_STATUS_DESCRIPTION` (`:334-338`), so the vocabulary and
  the text cannot drift.

## Fix verification summary

| Item | State | Evidence |
|---|---|---|
| S1 role end | Fixed | `relation_walk.py:255-281`, `:316-319`; test at `tests/test_typed_extraction.py:274-297` |
| S3 inert option | Fixed for declared signatures | `pipeline.py:378-389`, `:1743-1759`; `reingest.py:108-131`; test at `tests/test_typed_extraction.py:336-356` |
| S4 privacy projection | Fixed on reachable paths | `admitted_statements.py:215-230`; test at `tests/test_typed_extraction.py:359-364` |
| S6 documentation | Fixed | `config.py:1361-1383`; `documentation/configuration.md` |
| S8 silent drops | Fixed | `pipeline.py:1753-1758`; `extraction.py:60-61`, `:85-86`, `:334-338` |
| S2 stated relation overrides the normalizer | Open follow-up, as stated | `relation_walk.py:293-298` unchanged in that respect |
| S5 fidelity harness | Open follow-up, as stated | `benchmarks/graph_oracle/extraction.py:139-146`, `fidelity.py:56` |
| S7 claim merge and status | Open follow-up, as stated | `claim_merge.py:42-62` |

No fix regressed a scenario that previously held: the status label still
cannot retire a claim (`relation_walk.py:319-324`), the off path is still
byte-identical, and the node's relation is still the only authority the walk
consults (`relation_walk.py:293-298`), which is why S2 stays open.

Status: COMPLETE
