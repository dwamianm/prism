# Changelog

All notable changes to PRME will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Add an opt-in reader context format (`PackingConfig.context_format="reader"`,
  or `PRME_PACKING__CONTEXT_FORMAT=reader`) that renders each packed record as
  one line: its event time or caller-supplied validity range, tags for
  non-default epistemic and lifecycle states, and the complete text as a JSON
  string. The renderer adds no node IDs, source type, `created_at` or
  admission-time `valid_from`, and it excludes records that fit only as a
  fallback without their own text. The full record stays in
  `MemoryBundle.sections` and the retrieval receipt. `context_citations=True`
  adds `[m3]` references and fills `context_references`, which MCP now returns
  with `include_context`. Reader receipts use schema version 14; versions 1 to
  13 keep their canonical bytes. It is now the default (see Changed).
- Add opt-in reciprocal rank fusion for retrieval ranking
  (`ScoringWeights.fusion="rrf"`, or `PRME_SCORING__FUSION=rrf`, with
  `rrf_k`, default 60). Each candidate is ranked within the pool on the semantic
  and lexical channels and scored by `1/(k + rank)` per channel, scaled so first
  place on both is 1.0. Epistemic, node-type and temporal adjustments then
  apply relative to the pool's largest value. Graph proximity, recency,
  salience and confidence, which were constant or uninformative in the
  2026-09-23 benchmark archive, are not used (recency is available through an
  opt-in, below). Score provenance records formula
  version 2 with the saved ranks and factors, and these receipts use schema
  version 16 (version 15 receipts from earlier development builds, which lack
  `semantic_relevance`, stay readable). Because a fused score says where a result
  ranks rather than how relevant it is, `min_score` under rank fusion compares
  against each result's `semantic_relevance` instead: the semantic cosine
  similarity of the memory behind it (for session, episode and evidence
  context, the memory that pulled it in), recorded on every result, cross-scope
  hint, selection exclusion and receipt candidate, and in the LangChain and
  LlamaIndex result metadata. A floor tuned on weighted scores does not carry
  over, cosine ranges depend on the embedding model, and a low-cosine exact
  keyword match is filtered out when a floor is set. Non-neutral request ranking
  multipliers are rejected before
  retrieval (HTTP 422); weighted ranking profiles are inapplicable with reason
  `rank_fusion_scoring`; learning evaluation skips rank-fused receipts
  (`rank_fusion_receipt_records`); and the `feedback_apply` job reports
  `not_applicable` and keeps its signals. `PRMEConfig` now uses it by default
  (see Changed); `ScoringWeights()` remains the weighted sum, whose serialized
  settings, version ID and receipts are unchanged. As with earlier receipt
  versions, a release without version 16 support cannot read rank-fusion
  receipts.

- Add an opt-in session decay for rank fusion
  (`PackingConfig.session_context_rank_fusion_score_decay`, or
  `PRME_PACKING__SESSION_CONTEXT_RANK_FUSION_SCORE_DECAY`). Rank-fused scores
  are compressed, so at the default session decay of 0.85 a first-place
  result's adjacent turns outrank every result from about twelfth place down,
  and they crowded LongMemEval-S multi-session evidence out of the context.
  When set, it replaces `session_context_score_decay` for results scored by
  rank fusion; the offline evidence gate favored 0.6, which is now the default
  (see Changed). None restores the previous behavior. Receipts that record it
  use schema version 17.

- Add a fail-open for a rank fusion `min_score` floor, which used to empty
  every retrieval while vector search was failing or had detected an embedding
  model mismatch. No result has a semantic cosine then, so the floor is skipped
  for that request: results keep their fused order, and
  `RetrievalMetadata.min_score_skipped` (in the HTTP and MCP `metrics`) is set
  next to the `backend_failures` entry for the vector path. Cross-scope hints
  skip it too unless one of them has a cosine. The floor still applies
  whenever a result has a cosine, and weighted scoring is unchanged.
  Receipts of these retrievals use schema version 18, which records
  `min_score_skipped`. The LangChain and LlamaIndex retrievers accept an
  optional `min_score` and add `min_score_skipped: True` to each result's
  metadata when the floor was skipped.

- Add two opt-in rank fusion settings for conflicting memories
  (`ScoringWeights.rrf_recency_boost` and `ScoringWeights.rrf_tie_break`, or
  `PRME_SCORING__RRF_RECENCY_BOOST` and `PRME_SCORING__RRF_TIE_BREAK`). Rank
  fusion ignores recency, so on a current-state question the older of two
  conflicting memories could rank above the newer one. With the recency boost
  set, those questions multiply each fused score by `1 + boost x recency`,
  relative to the pool's largest value, where recency is the weighted
  formula's current-state recency (with lambda at least 0.05). With
  `rrf_tie_break="event_time"`, equal fused scores are ordered newest first
  instead of by path count and node ID. Both apply only under rank fusion.
  `PRMEConfig` now sets a boost of 0.25 and the tie-break by default (see
  Changed); `ScoringWeights()` leaves both unset, and then nothing changes. A
  boost of 0.25 with the tie-break
  passed every simulation with the reader format, score order and a 0.6 rank
  fusion session decay, and changed no benchmark's share of questions with all
  evidence packed significantly on the offline evidence gate. Receipts that
  record either use schema version 19.
- Add conversation participants (#84). `store()`, `store_with_receipt()`,
  `ingest()`, `ingest_batch()`, `ingest_fast()` and `FastIngestItem` accept an
  optional `speaker` name, as do HTTP `POST /v1/store`, `/v1/ingest` and
  `/v1/ingest/fast` and the MCP `memory_store`, `memory_ingest` and
  `memory_ingest_fast_many` tools. The new `participant` role marks a human in
  the conversation other than the memory's owner: like `user`, its sources are
  `USER_STATED` (with the default confidence matrix, 0.80 for an asserted claim
  instead of the 0.60 an `assistant` source gets), but its statements do not
  reinforce the owner's instructions. The speaker is kept in the reserved
  `metadata.prme_speaker_v1` key of the source event and its direct node or raw
  note, not yet on extracted claims; passing that key in `metadata` is now
  rejected. The reader context format prints it as `"Caroline": "text"` unless
  the text already begins with it, and the auditable format adds a `speaker`
  key. With `enable_qa_pairing=True`, a change of speaker within one role also
  creates a merged Q-A node, whose halves start with their speakers' names.
  Contexts for records without a speaker keep their exact bytes. The legacy
  LoCoMo harness (`benchmarks/locomo.py`) now stores both people as
  participants, so its scores are not comparable with its earlier runs; the
  registered 2026-09-23 comparison and its saved packs are unchanged.

- Add an opt-in event-time clock for the weighted formula's recency
  (`ScoringWeights.recency_time="event_time"`, or
  `PRME_SCORING__RECENCY_TIME=event_time` with
  `PRME_SCORING__FUSION=weighted`). Unset, the weighted formula measures
  recency on questions that are not about the current state from when a
  memory was last updated or created, so history imported with a past event
  time and retrieved at a past reference time scored full recency on every
  memory (issue #83). With the setting, every memory is dated by when it was
  stated: its event time, else when it was stored, never when it was last
  updated and never its validity start. Questions that are not about the
  current state measure that back from the request's reference time;
  current-state questions keep measuring back from the newest candidate.
  Memories without an event time, such as entity, consolidation and profile
  nodes, count from when they were stored. Rank fusion, the default, already
  uses this clock for its recency boost and tie-break, so it drops the setting
  with a warning, and nothing changes by default. Receipts that record it use
  schema version 20; to stop using it, unset the variable rather than
  returning to an earlier release, which cannot read version 20 receipts.
  On the offline evidence gate it lost LoCoMo evidence (-4.8 points of
  questions with all evidence packed with the previous auditable format and
  balanced order, -4.5 with the reader format, both intervals excluding zero)
  and left LongMemEval-S unchanged, so it stays off.
- Add an opt-in temporal-first query intent
  (`PRMEConfig.query_intent_order="temporal_first"`, or
  `PRME_QUERY_INTENT_ORDER=temporal_first`). By default a question that names a
  person, place or organization, such as "When did Caroline go to the support
  group?", is classified as an entity lookup before its temporal wording is
  checked, so it never gets temporal affinity scoring (issue #85). With the
  setting, temporal wording or a date in the question makes it temporal first,
  so it gets temporal affinity. A present-tense question that only its wording
  put on the current-state path leaves it unless it says current, now, latest
  or similar, and the temporal context guidance is added where the question's
  wording does not already select it. A lone capitalized month or weekday word
  that is part of a name ("Who is June dating?") is read as the name, not a
  date. Entity names are extracted either way. Receipts record the setting only
  when it is `temporal_first`, so default receipts keep their bytes. The
  offline evidence gate now records, for each replayed question, whether its
  candidates share one temporal affinity and whether the current-state path
  applied; its comparison counts the first per category and lists the
  questions that enter or leave the path. On the gate the setting packed all
  annotated evidence for 5 more LoCoMo questions and 1 more LongMemEval-S
  question and for none fewer (+0.3 and +0.2 points); it stays off until the
  paired answer run.

### Fixed

- The LlamaIndex chat store reads a session turn whose role LlamaIndex has no
  value for, such as `participant` or `human`, as a user turn instead of
  failing to read or clear the session.
- The HTTP API docs and the example Dockerfile now start the server with
  `python -m prme.api`. The `uvicorn prme.api:app` command they showed never
  loaded the app, because `prme.api:app` names the `prme.api.app` module.
- Query reformulation (`enable_query_reformulation=True`) now uses the
  extraction endpoint, credential and timeout as well as its provider and
  model. It used to build its own client from the provider and model alone, so
  its requests went to the provider's default endpoint (for OpenAI,
  `api.openai.com` with `OPENAI_API_KEY` from the process environment) even when
  extraction was pointed at another endpoint or credential. It also ignored
  provider settings in `.env`, used Instructor's tool mode with Ollama models
  that list tool support, and had no time limit of its own. It now reaches the
  extraction `base_url` with the extraction `api_key` (or the provider's own
  settings from the environment or `.env`, as extraction does), and returns no
  alternate queries when a call takes longer than the extraction `timeout` (30
  seconds by default). With Ollama it uses JSON mode and a reasoning effort of
  `"none"`, as extraction does, and it passes the model on each call, which
  Bedrock requires. Its other sampling is unchanged: the provider's default
  temperature and two schema-validation retries. Each engine keeps its own
  reformulation clients, so they are not reused on another event loop.
- Extraction, answerability and reformulation now build their Instructor
  clients in one place, `prme.model_runtime`. An Anthropic endpoint
  (`base_url` or `ANTHROPIC_BASE_URL`) now works: Instructor 1.14 built
  Anthropic clients without it and passed it to every call, which failed.
- `ExtractionConfig.timeout` must now be a positive, finite number. Zero or a
  negative value made every extraction time out.
- Context ablation now keeps a compact or reader bundle's format and references
  instead of re-rendering the counterfactual as auditable JSON.
- A text-bearing `PackingConfig.min_fidelity` (`full`, `prose` or
  `structured`, set in code, with `PRME_PACKING__MIN_FIDELITY` or per request)
  now keeps every packed record's memory text in the auditable and compact
  formats too. A record whose stored text is blank is excluded and listed in
  `MemoryBundle.excluded_ids` instead of being packed with empty text, and it no
  longer takes the reserved first place under balanced ordering. Set such a
  floor to keep the text-free `key_value` and `reference` fallbacks out of the
  context. The default floor stays `reference`, so default contexts are
  unchanged.
- Aggregation coverage no longer reports a blank record's exclusion as a
  `token_budget` limit when the reader format or a text-bearing floor excludes
  it.
- A per-request `min_fidelity` passed to `MemoryEngine.retrieve()` as a plain
  string is now converted to `RepresentationLevel`, so receipts serialize it
  without warnings and an unknown value fails before retrieval runs.

### Changed

- **Retrieval defaults.** `PRMEConfig()` now retrieves with rank fusion
  (`scoring.fusion="rrf"`, `rrf_k=60`) with a current-state recency boost of
  0.25 (`scoring.rrf_recency_boost`) and an event-time tie-break
  (`scoring.rrf_tie_break="event_time"`), the reader context format
  (`packing.context_format="reader"`, previously `auditable`), score ordering
  (`packing.multipath_ordering="score"`, previously `balanced`; `balanced` is
  the default again, see the next entry) and a rank fusion session decay of 0.6
  (`packing.session_context_rank_fusion_score_decay`, previously unset). The
  context budget and session expansion are unchanged. The combination (the
  DeepSeek-track variant `prme-reader-rrf-sd06-rec`) passed the epic #77
  default-change test twice on the DeepSeek answer track at a 3,996-token
  context: LoCoMo went from 65.7% to 80.9% and from 65.8% to 81.6% (+15.2 and
  +15.7 points, 95% intervals +13.0 to +17.4 and +13.5 to +17.9), and
  LongMemEval-S went from 85.8% to 86.8% and from 86.4% to 86.6% (+1.0 and
  +0.2 points, intervals -1.6 to +3.6 and -2.6 to +3.0). See `BENCHMARKS.md`.
  What this changes for existing installs:
  - Contexts are one plain line per record instead of JSON objects, so more
    records fit in the same budget. The lines carry no node ID or source type.
    Callers that parse the context text as JSON must set
    `PRME_PACKING__CONTEXT_FORMAT=auditable`.
  - Answerability checks and `verify_bundle()` raise `ValueError` for a reader
    bundle without citations. Set `PRME_PACKING__CONTEXT_CITATIONS=true` or use
    the `auditable` format for those callers.
  - Rank fusion ignores the additive scoring weights, graph proximity,
    salience and confidence, and uses recency only through the current-state
    recency boost, so salience decay, reinforcement and confidence changes no
    longer move a result's rank. Candidates found only through the graph or a
    pin score 0 (pins are still packed first), and `min_score` is compared
    with the semantic cosine instead of the composite score. Non-neutral
    request `ranking_multipliers` are rejected (HTTP 422, MCP error), learned
    ranking profiles are not applied (`rank_fusion_scoring`), and
    `feedback_apply` reports `not_applicable`. Set
    `PRME_SCORING__FUSION=weighted` to keep the weighted formula, including
    any tuned `PRME_SCORING__W_*` weights.
  - The new defaults live in `PRMEConfig` (`default_scoring_weights()` and
    `default_packing_config()`), and `PRME_SCORING__*` and `PRME_PACKING__*`
    environment variables, `.env` entries and secrets that set only some values
    keep them. Setting `PRME_SCORING__FUSION=weighted` also drops the rank
    fusion recency boost and tie-break, which weighted scoring cannot use.
    `ScoringWeights()` and `PackingConfig()` built in code keep their field
    defaults (the weighted formula; `auditable`, `balanced` and no rank fusion
    session decay), because stored receipts and configurations omit some of
    those values and must keep their meaning; code that builds them from
    scratch gets the previous behavior. Copy `config.packing` or
    `config.scoring` with `model_copy(update=...)` to change one setting and
    keep the new defaults.
  - Default retrievals write receipt schema version 19. Stored receipts keep
    their bytes and replay unchanged.
  - To restore the previous defaults, set `PRME_SCORING__FUSION=weighted`,
    `PRME_PACKING__CONTEXT_FORMAT=auditable` and
    `PRME_PACKING__MULTIPATH_ORDERING=balanced`. The recency boost and
    tie-break are then dropped, and weighted scoring never applies the 0.6
    session decay (its receipts omit it), so retrieval, contexts and receipts
    are those of the previous defaults. In code, pass
    `scoring=ScoringWeights()` and `packing=PackingConfig()` to `PRMEConfig`.
- **Multi-path ordering default.** `PRMEConfig()` now orders the ordinary
  multi-path tier with `balanced` (`packing.multipath_ordering="balanced"`)
  instead of score order. The other retrieval defaults above are unchanged.
  `balanced` was also the ordering in v0.12.0, so an install upgrading from
  that release sees only the scoring, context format and session decay change
  above. Balanced reserves the highest-scored ordinary multi-path candidate and
  then orders the rest by score / full-entry tokens\*\*0.25, so a context
  holds more short records than under score order. With the other defaults
  (the DeepSeek-track variant `prme-rrf-rec-balanced`), it passed the epic #77
  default-change test twice on the DeepSeek answer track at a 3,996-token
  context, each pair against a fresh run of the score-order defaults:
  LongMemEval-S went from 86.8% to 90.0% in both pairs (+3.2 points, 95%
  intervals +0.6 to +6.0 and +0.6 to +5.8), and LoCoMo went from 80.1% to
  80.6% and from 80.6% to 80.4% (+0.5 and -0.2 points, intervals -0.9 to +2.0
  and -1.5 to +1.1). LongMemEval-S multi-session questions gained 7.5 and 11.3
  points; LongMemEval-S knowledge-update questions (73 to 70 of 78 in both
  pairs, #169) and LoCoMo multi-hop questions (-1.8 and -4.3 points) fell, with
  intervals including zero. See `BENCHMARKS.md`. Candidate retrieval and
  scoring, the context budget and the receipt schema version (19) are
  unchanged; receipts record the ordering as before.
  - **Upgrade note:** to keep score order, set
    `PRME_PACKING__MULTIPATH_ORDERING=score`. In code, copy the packing
    configuration with `multipath_ordering="score"`:
    `config.packing.model_copy(update={"multipath_ordering": "score"})`.
- Simulation checkpoints can require memory text in the context the reader
  gets (`SimCheckpoint.context_keywords`, reported as
  `CheckpointResult.context_missing`). Four checks that tested the weighted
  formula's order rather than what reaches the reader now use it: the
  infrastructure question in `consolidation`, the tools question in
  `remention`, and the database and observability questions in
  `surprise_gating`.
- Receipt versions from 4, 6 and 8 on must state their ordering, guidance and
  episode settings instead of taking the current defaults. Stored receipts
  always include them.
- Installs that already set `min_fidelity` to `full`, `prose` or `structured`
  and store records with blank text (for example tool-call-only chat turns) now
  see those records excluded from the auditable and compact contexts instead of
  packed with empty text.
- **Upgrade note:** `python -m prme.api` and `prme.api.server.run_server()`
  now refuse to start on a non-loopback address (such as `0.0.0.0`, including
  inside a container) when neither `PRME_API_USER_KEYS` nor `PRME_API_API_KEY`
  is set. They used to log a warning and serve every request without
  authentication. The command exits with status 2 and `run_server()` raises
  `prme.api.server.UnauthenticatedBindError` (a `ValueError`), before the app,
  the engine or a listener is created. Configure credentials before upgrading
  a network-reachable server. Per-user keys bind each request to its owner, so
  a backend that acts for many users needs the global key. Only literal
  loopback addresses (`127.0.0.0/8`, `::1` and IPv4-mapped loopback) and
  `localhost` (when it resolves only to loopback) count as loopback; other
  host names count as network addresses. A server that only an authenticating
  reverse proxy can reach can pass `--allow-unauthenticated-external-bind`
  (`allow_unauthenticated_external_bind=True`), which starts it with a warning
  and gives every caller operator access. The command no longer accepts
  abbreviated options. Hosting the app under another ASGI server skips the
  check, as the HTTP and deployment guides now explain.
- The development `docker-compose.yml` publishes its PostgreSQL test database
  (fixed `prme_test` credentials) on `127.0.0.1:5432` only, instead of on all
  interfaces. CI connects to `127.0.0.1`. An existing container keeps its old
  mapping until it is recreated: run `docker compose up -d` once. The
  deployment guide now separates this test database from a network-reachable
  one.

## [0.12.0] - 2026-09-22

### Experimental retrieval and evaluation

- Add explicit reranker `score_envelope` and `anchored_score_envelope` policies
  to preserve the scored prefix's original score scale during downstream packing.
  Add opt-in alternate-query `max_signals` merging for existing candidates, with
  exact source-snapshot checks and atomic failure behavior. Both storage engines
  receive these through `PRMEConfig`; ordinary defaults remain unchanged.
- Record neural ordinal assignments in receipt schema 13, with exact score replay
  and distinct execution identities for ranking-profile compatibility. Stored
  schemas 1–12 keep their canonical bytes; older readers cannot read schema 13.
- Retain complete registered LongMemEval-S development results, including negative
  interactions, inactive features, provider failures and failed primary controls.
  Production scored 437/500. The anchor-preserving trial scored 430/500 versus
  its new control's 429/500 (+0.2 points, 95% CI [−1.6,+2.0]); better source
  retention did not establish an answer gain. Ongoing ingestion/reformulation
  trials remain explicitly unfinished, with no partial-arm score or new default.
- Update the archived milestone-checklist test to its moved source path, fixing
  the existing mainline CI failure without weakening its 29 historical assertions.


### Fixed

- Grounded fact objects with one exact currency or bounded measurement unit no
  longer depend on model-authored quantity fields. PRME can derive the verbatim
  value and unit from the grounded object and evidence, while preserving the
  existing exclusions for approximations, ranges, identifiers and unsupported
  notation. A separate user-only path recovers one complete first-person
  conditional quantified action when the model drops the whole claim. Fresh
  outputs recorded `speech_act_v10`. V10 also suppresses conditional fallback
  when an existing validated fact has the same evidence, predicate, polarity
  and quantity identity but quotes a different valid condition substring.
  Saved v9 through v6 outputs keep their bytes and continue to prepare v12
  plans. Registered 20-case runs passed all zero-tolerance gates on both hosted
  DeepSeek and a distinct local Qwen 35B-A3B artifact: 40/40 cases, 24/24
  expected quantity instances, no unexpected quantities and no provider errors.

- Fresh `speech_act_v11` extraction can recover one leading exact measure from
  a user-authored first-person completed action in a bounded verb lexicon. The
  path retains the verbatim measured phrase as the fact object and runs the
  existing evidence, quantity and reference checks. It excludes modals,
  negations, examples, questions, conditions, approximations and ranges. Saved
  v10 through v6 extraction records remain byte-compatible and prepare v12
  plans.

- Quantity aggregation accepts optional `predicate_prefixes` for explicit,
  token-bounded composition of model-authored predicate detail. A normalized
  prefix such as `raised` matches `raised` and `raised_*`, but not
  `fundraised`; no synonyms or embeddings are inferred. Responses distinguish
  this mode with
  `semantic_equivalence="normalized_exact_and_predicate_prefix"`.
  A preregistered end-to-end Qwen 35B-A3B confirmation passed all ten source
  contracts and all aggregation gates, returning the exact `$3,750` total from
  four real charity memories while excluding unrelated, negated, conditional,
  approximate, incompatible-unit and cross-owner controls.

- Python, HTTP and MCP now expose a fail-closed natural-language quantity
  planner/executor for a fixed set of complete, qualifier-free amount and count
  questions. It returns the exact structured query and assumptions alongside
  the result and preserves the question's exact `I` or `we` subject. Unsupported
  qualifiers, negation, future wording, named subjects, actions or units return
  a typed refusal without scanning memory; ordinary retrieval remains unchanged.
  A preregistered confirmation over a frozen end-to-end pack passed five
  executable questions and six refusal controls with zero refusal scans.

- Assertion and quantity aggregation queries now round-trip their serialized
  default empty scope selector. An empty scope list consistently means all
  scopes; duplicate scopes remain invalid.

- A user-authored sentence such as `My final score was 3` no longer disappears
  when the model omits its claim. A narrow deterministic path admits terminal
  exact score, count, rating and level attributes with a source-literal concept
  subject, then applies the normal evidence, quantity and reference checks. It
  excludes assistant messages, examples, unsupported attributes,
  approximations, ranges and multi-number values. This behavior was introduced
  under `speech_act_v8`; fresh v10 outputs retain it, while saved v9 through v6
  outputs retain their original bytes and still prepare v12 plans.

- Quantified claims now remain exact when a provider serializes a decimal as a
  JSON float: PRME discards the float and recovers only a single supported
  decimal token from the grounded source phrase. Surrounding approximation and
  range cues fail closed even when the provider clips them from `source_text`.
  This behavior was introduced under `speech_act_v7`; fresh v10 outputs retain
  it, while saved v9 through v6 outputs keep their original bytes and still prepare
  v12 materialization plans.

- Built-in extraction now preserves literal first-person attempts and
  intentions instead of admitting them under completed or current-state
  predicates, including component relationships inside the attempted action.
  The preregistered confirmation outputs recorded `speech_act_v6` and prepared
  `speech_act_v12` plans. A narrow source/entity recovery retains the target of an explicit
  nonactual clause when the model returns the entity but omits its claim, while
  preserving explicit conditions. Saved v5 outputs remain v11, v4 remain v10,
  v3 remain v9, v2 remain v8, and legacy extraction records recover
  under `temporal_validity_v7`, so restart cannot claim a validator that did
  not run. A preregistered 14-case real-model development confirmation passed
  all 15 safety and utility targets with zero unsafe claims on both a hosted
  DeepSeek profile and a distinct local Qwen 35B-A3B artifact.

- Registered extracted BEAM ingestion now completes both durable raw-source
  materialization and structured extraction before acknowledging a source.
  Source-list timestamps come from the latest cited evidence event, and exact
  sibling passages are capped with `max_per_source=1`. Schema-5 validation
  inspects each registered owner's DuckDB work rows after execution, so a scored
  run with pending raw notes or extractions cannot pass again.

- Built-in extraction now sends source messages literally even when they contain
  Jinja expressions or blocks. Grounding context is task-local and isolated
  across concurrent calls, so Instructor cannot evaluate user code as a prompt
  template or alter the source before inference.

- BEAM schema-4 extracted-run registrations now bind the extraction retry count,
  and adapter schema 3 reports the applied value. A library-default change can
  no longer silently alter an attested extraction run.

- Exact integer and string decimal quantities from Instructor JSON responses now
  survive strict extraction validation. JSON float approximations and malformed
  quantities are still discarded without losing an otherwise grounded fact.
  Missing or ambiguous named references now discard only their affected claim,
  so one omitted entity declaration cannot exhaust retries and lose every
  grounded, reference-closed sibling in the provider response.

- Preserve BEAM source-session boundaries when the upstream request exposes an
  observation timestamp, preventing adjacent-turn expansion from treating a
  complete multi-session history as one session.

- Store-time oscillation dampening now revalidates the complete owner-scoped
  supersedence chain under lock and commits its confidence update with a
  deterministic, checksummed `PENALTY` record in one DuckDB or PostgreSQL
  transaction. Concurrent, restarted, failed, and cancelled attempts cannot
  apply the same penalty twice or leave an unjournaled confidence change.

- Automatic question/answer pairing is now disabled by default. Every
  registered quality benchmark already excluded the in-process heuristic, whose
  extra derived node is not atomically journaled or recovered after restart.
  `enable_qa_pairing=True` remains available for explicit hypothesis testing.

- TTL expiration now revalidates policy under lock and commits archival with a
  deterministic, checksummed `TOMBSTONE_SWEEP` record in one DuckDB or
  PostgreSQL transaction. The record retains complete before/after state and
  RFC-0007 policy fields; concurrent, restarted, failed, and cancelled attempts
  cannot leave an unlogged archive or a tombstone without its transition.

- Unverified organizer alias proposals now publish their deterministic
  `RELATES_TO` edge and a checksummed complete `ALIAS_PROPOSED` record in one
  DuckDB or PostgreSQL transaction. Exact, concurrent and restarted retries
  reuse one pair identity; incompatible current nodes are rejected, and legacy
  random-ID links are reused without fabricating historical journal inputs.

- Built-in embedding configuration now infers registered FastEmbed and OpenAI
  dimensions when omitted, selects a coherent OpenAI model/dimension pair when
  only that provider is chosen, and rejects unknown model dimensions before
  opening an index. Direct `FastEmbedProvider` construction follows the same
  fail-early contract without loading model weights.

- Repeated single-vector archive cycles no longer stall in the native HNSW
  deletion path after roughly 192 operations. PRME now requires USearch 2.26.2,
  whose deletion implementation completes this churn pattern; a subprocess
  regression test keeps a native stall bounded and observable. A preregistered
  AgentMemBench confirmation completed 200/200 archive-retirement cases, with
  every memory visible before archival and absent from retrieval afterward.

### Added

- Added an optional local `ClaimVerifier` that checks explicit declarative claims
  against exact packed passages with a revision-pinned NLI cross-encoder. It
  scores individual evidence first, explores only bounded minimal groups, exposes
  supporting and refuting groups plus every model score and digest, preserves
  typed source/time/lifecycle provenance, and returns `incomplete` without a
  model call for derived exhaustive counts or lists. The verifier is available
  through `prme[verification]`; it remains opt-in while representative claim
  evaluations establish domain thresholds. Its first registered 20-case
  real-model assay matched 17 cases and failed the all-cases gate. It produced
  zero unsafe supports and recovered both unseen two-passage claims, but
  overcalled two neutral passages as refutations. The default verifier now
  requires an explicit correction cue or incompatible concrete value in the
  exact evidence group before accepting a model contradiction; blocked raw
  contradictions remain auditable and return `insufficient`. A registered
  same-cohort confirmation passed all eight targeted gates and changed only the
  two intended false refutations, yielding 19/20 expected statuses with zero
  unsafe supports. This is regression evidence on an observed development set,
  not a held-out accuracy result. A later 48-case previously unexecuted
  development assay then failed its safety gate with two unsafe supports: one
  desire was treated as a completed action and one explicit ownership conflict
  was missed. The failed result is retained. The verifier now blocks entailment
  when evidence has a nonactual speech act absent from the claim, retaining the
  raw score and an explicit limitation, and can surface narrowly matched
  explicit negation with a distinct deterministic decision basis. Claim
  verification remains opt-in. A registered same-cohort safety confirmation
  passed all eight gates: the raw scores were unchanged, exactly the two intended
  statuses changed, and unsafe supports fell from two to zero. Four implicit
  contradictions and two relation-composition cases remained unresolved. A
  follow-up diagnosed that bounded candidate ranking had also reordered evidence
  before NLI; group scoring now preserves caller order after selecting
  candidates. Its registered same-cohort confirmation passed all eight gates,
  recovered all 6/6 minimal groups and changed exactly the two intended
  statuses, reaching 44/48 with zero unsafe supports. A later 44-case
  previously unexecuted guard assay failed: four negative distractors about a
  different relation were treated as refutations or conflicts. The result is
  preserved and blocks promotion of claim verification. Evidence-side negation
  now requires every normalized non-generic claim token in the negated clause
  before either deterministic or model-assisted refutation, and reported
  questions share the direct-question speech-act mode. A registered same-cohort
  confirmation passed all eight gates at 43/44 with identical model scores:
  all four wrong-relation negatives became `insufficient`, the reported question
  became `supported`, and every true refutation and conflict was retained. The
  first external WiCE oracle-retrieval assay then failed precision, recall and
  balanced-accuracy gates across all 358 human-annotated test claims. It accepted
  0/32 fully unsupported claims, but passage-wide speech-act and negation guards
  reduced supported recall to 10.91%. Result schema 3 now localizes those guards
  within multi-sentence passages and rechecks the retained exact text with the
  same model. It records one-based retained sentence numbers, premise digests,
  probabilities and a distinct `localized_model_entailment` basis; schemas 1 and
  2 remain readable. The registered confirmation preserved all eight authored
  gates and improved WiCE supported recall from 10.91% to 30.91% and F1 from
  19.35% to 43.59%, with no fully unsupported accepts. It still failed the fixed
  precision, recall and balanced-accuracy gates, so verification remains opt-in.
  A pinned MIT MiniCheck DeBERTa capacity trial improved WiCE F1 to 65.97% and
  balanced accuracy to 75.01% with no fully unsupported accepts, but failed the
  fixed 90% precision and 60% recall gates. No frozen-score threshold meets both,
  so that model is not integrated as a standalone verifier.
  The registered MIT MiniCheck Flan-T5 trial raised WiCE recall to 72.73%, F1 to
  74.77%, and balanced accuracy to 81.52% with no fully unsupported accepts, but
  failed the 90% precision gate on 24 partially supported compound claims. No
  threshold meets both gates. A preregistered follow-up applied the unchanged
  model to all 958 WiCE human subclaims and required every subclaim to pass.
  Gold decomposition reconstructed 357/358 parent labels, but model errors
  limited parent precision to 76.04% and recall to 66.36%; no frozen threshold
  met both gates. This validates atomic decomposition as an architecture
  boundary on the named cohort while rejecting this verifier for integration.
  A fresh registered 100-case SummEdits trial tested a 675B reasoning model
  with exhaustive atoms and exact proof quotations across ten domains. It
  failed at 75.00% precision, 6.00% recall, 52.00% balanced accuracy and 23.00%
  quote integrity. The checks safely rejected paraphrased or invented proof,
  but the model also decomposed facts from the document instead of only the
  summary. This rejects free-form proof copying and motivates a separated,
  segment-ID protocol. The same-cohort causal v2 split summary-only
  decomposition from fixed-atom document verification. Reference integrity
  rose from 23.00% to 99.00%, supported recall from 6.00% to 72.00%, and
  balanced accuracy from 52.00% to 76.00%. It still failed safety at 78.26%
  precision and a 20.00% false-support rate, chiefly on entity changes and
  antonym swaps. A different-family DeepSeek critic over all 46 primary support
  candidates reduced false support to 8.00% but left precision at 78.95% and
  collapsed recall to 30.00%. A balanced 1,100-claim LLM-AggreFact development
  trial then rejected direct FactCG classification: the best threshold retaining
  60% recall reached 79.44% precision, while 90% precision retained only 28.73%
  recall. A registered FactCG-ranked local-evidence Mistral cascade raised the
  corresponding operating point to 85.12% precision at 63.45% recall, but 90%
  precision still retained only 36.55% recall. Seven structured responses
  remained fail-closed, and the bound test cohort remains untouched. No provider
  verifier is integrated.

- Added an optional, provider-agnostic `AnswerabilityEvaluator` for grounded
  abstention after retrieval. It decomposes compound questions and optional
  draft answers into independent requirements, resolves cited compact refs or
  default auditable UUIDs to exact packed memory IDs, fails closed on missing
  or invented citations, and derives `answerable`, `partial`, `insufficient`,
  or `conflicting` in code.
  Empty bundles avoid a model call; provider failures raise explicitly; every
  result binds the prompt version and query/context/answer hashes, plus separate
  fixed-input and actual-output digests, without changing retrieval receipts or
  memory state. Its first registered 120-assessment BEAM development trial
  failed every promotion gate. A speech-act-aware same-cohort rerun with exact
  evaluator and machine-gate bindings also failed all five gates, including 2
  unsafe full answers and stable actions on only 26/40 questions, so it remains
  explicit and opt-in. A registered frozen-draft follow-up also failed all four
  gates: it fully accepted 5/36 samples from incorrect drafts, fully accepted
  only 13/84 samples from correct drafts, produced 7 citation errors, and was
  action-stable on 22/40 questions. Its systematic failures separate temporal
  task-label disagreement from exhaustive-count verification and block automatic
  enforcement of the current single-call architecture.

- Opt-in direct evidence projection lets extracted claims route retrieval while
  returning the complete active source passage. It replaces bounded exact
  evidence groups only after owner, scope, temporal, validity and epistemic
  checks, records replayable score inheritance, and reserves projected sources
  during packing. Receipt schema version 10 records the full disabled-by-default
  policy while versions 1–9 preserve their canonical bytes and disabled meaning.
  A registered all-top-50 development ablation improved event ordering to 2/2
  but regressed three previously passing questions and reduced overall score to
  11/20, so wholesale projection is explicitly rejected as a default.

- Opt-in evidence augmentation preserves concise derived candidates while
  adding bounded direct sources under the same owner, scope, temporal, validity,
  lifecycle and epistemic checks. Projection and augmentation are mutually
  exclusive. Receipt schema version 11 records augmentation settings and exact
  score inheritance; versions 1–10 retain disabled semantics. A registered
  tuned BEAM ablation improved the accepted result from 13/20 to 14/20 with one
  pass-level win, zero losses and a +0.02916 mean-score delta. A preregistered
  untouched confirmation then scored 14/20 versus the fresh baseline at 15/20,
  with zero wins, one contradiction-resolution loss and a -0.03375 mean delta.
  Unconditional top-10 augmentation remains opt-in and is rejected as a default.
  A new `evidence_augmentation_anchor_policy="non_entity"` mode prevents entity
  name matches from authorizing whole-passage injection and backfills the quota
  from non-entity evidence groups. Receipt schema version 12 records the policy;
  versions 1–11 preserve `all` semantics and their canonical bytes. A registered
  two-conversation diagnostic fixed the observed contradiction regression but
  produced one win, one temporal loss and a -0.03000 combined mean delta, so the
  policy remains opt-in and is not a default candidate. A preregistered
  frozen-context follow-up rejected a stable interpretation of that temporal
  loss: the identical baseline context passed 3/5 repeats, the candidate passed
  4/5, and both mean scores were 0.40. The BEAM integration now includes an
  answer-stability runner that pins both retrieval artifacts and alternates
  repeated answer/judge calls without retrieving again.

- A registered fixed-pack BEAM ablation found that a hard one-node cap per exact
  evidence set held pass accuracy at 13/20 but reduced mean rubric score from
  0.56750 to 0.54833, with one pass-level win and one loss. The failure blocks a
  default cap and motivates the source-preserving projection policy.

- The corrected schema-5 extracted-memory BEAM development run completed at
  13/20 pass (65.0%) with a 0.56750 mean rubric score. Fail-closed validation
  confirmed 188/188 durable raw materializations and 188/188 extractions. Exact
  passage diversity rose from a 15-of-50 median in superseded dev3 to 48-of-50,
  while median retrieval latency fell from 486.0 ms to 198.05 ms. Against the
  accepted raw profile it gained three pass-level questions, lost two and tied
  fifteen. This tuned one-conversation result does not establish a default or
  cross-product leadership.

- The schema-5 extracted BEAM dev4 run is retained as rejected evidence. A
  14,181-character source exhausted four 120-second DeepSeek extraction calls;
  the run was stopped during ingestion and publishes no score. Controlled
  probes on the exact saved source completed with Mistral Large 3 in 44.63
  seconds and DeepSeek V4.1 Flash in 106.42 seconds, identifying hosted latency
  variance and the registered timeout as the failure boundary.

- Python, HTTP, and MCP retrieval now accept optional `max_per_source` and
  `max_per_evidence` result-selection bounds. The first collapses only
  byte-identical passages with the same exact nonempty evidence set. The second
  can cap differently worded nodes that cite the same exact evidence set. Both
  fill the requested count from later groups, record every exclusion, leave
  nodes without evidence untouched, and remain opt-in pending broader answer
  trials.

- The first scored extracted-memory BEAM execution produced 12/20 pass (60.0%)
  with a 0.51875 mean rubric score, but a later pack audit found only 66 of 188
  raw-source materializations complete. The original validator checked all 188
  structured extractions but missed the second durable work stream. The result
  is retained as superseded diagnostic evidence and is no longer accepted as a
  complete extracted-profile quality result.

- Registered BEAM evaluation can now attest scored extracted-memory runs. The
  schema-3 launcher binds and verifies the extraction model and settings before
  ingestion, launches the selected durable extraction profile, and rejects
  adapter-profile or extraction-config drift while preserving earlier raw
  registration semantics.

- The first registered extracted-memory BEAM attempt is retained as rejected
  evidence: DeepSeek V4.1 Flash completed 8 durable extractions but failed the
  complete schema on a longer source after the registered retry budget. The run
  was stopped before retrieval and publishes no score.

- The second registered extracted-memory BEAM attempt is also retained as
  rejected evidence. It completed 11 durable extractions before a stored Jinja
  code example exposed Instructor prompt-context evaluation ahead of the model.
  The run was stopped during ingestion and publishes no score.

- The first fail-closed scored BEAM execution now provides complete answer-quality
  evidence for one 100K raw-memory conversation: 12/20 pass (60.0%) with a
  0.49833 mean rubric score, 94/94 ingested chunks, 20/20 nonempty answers, and
  53/53 complete judge verdicts. Registration binds the source, dataset,
  selection, endpoint, and Ollama manifests before execution. Three precursor
  attempts remain published as rejected evidence, including a completed
  upstream result that the validator rejected for six silent empty answers.
  The accepted cohort identifies abstention and broad summarization as the
  largest measured gaps and does not establish official-scale or cross-product
  leadership.

- Registered BEAM evaluation now supports complete scored runs as well as the
  earlier predict-only workflow. Schema-2 registrations bind distinct local
  answerer and judge models, full Ollama digests, endpoint, selection,
  concurrency, dataset, and runtime source before the first model call. The
  launcher and fail-closed validator reject model or protocol drift while
  preserving schema-1 predict-only artifacts unchanged.

- AgentMemBench retrieval registrations now bind the exact Ollama judge model
  digest, endpoint, and generation/retry controls before execution. Verification
  checks both the saved harness arguments and the live model identity. The
  installed pinned harness disables reasoning for its 32-token JSON decision and
  aborts after malformed output or exhausted retries instead of silently scoring
  judge failures as retrieval misses. The first registered 100-record Qwen 35B
  development run completed at 95/100 recall@5 with a 90% to 99% bootstrap
  interval and 100/100 durable materializations. The preregistered official-size
  confirmation scored 979/1,000 with a 97.0% to 98.7% interval, including 98.8%
  personal-fact and 97.0% task-request recall, with all 1,000 writes durably
  materialized.

- Current-state scoring now recognizes the newest record when it explicitly
  presents itself as an update and applies a configurable, relevance-capped,
  replayable multiplier. A 100-pair AgentMemBench development diagnostic moved
  new-fact-first retrieval from 20% to 100%; the default 1.30 multiplier remains
  provisional. Receipt schema version 9 records configured and applied values,
  while versions 1–8 preserve their canonical bytes and disabled semantics.

- Atomic `ingest_fast_many()` raw-source admission across the asynchronous
  engine, synchronous client, HTTP, MCP, DuckDB, and PostgreSQL. One owner is
  bound to the complete ordered batch, every item is validated before I/O, and
  all immutable events and restart-safe materialization jobs commit together or
  roll back together. An optional owner-scoped request UUID makes exact retries
  return the retained event IDs after restart and rejects changed inputs.
  MemoryAgentBench adapter schema 10 uses this path with a stable request UUID
  and drains the existing bounded materialization queue before publishing a pack.

- Opt-in deterministic two-stage episode routing for sources stored under
  meaningful session boundaries. It uses BM25 to route candidate-backed
  `(scope, session_id)` groups, promotes a bounded local evidence set with
  replayable score provenance, and reserves that evidence during packing. It is
  disabled by default while matched answer trials test the EventQA hypothesis.
  Receipt schema version 8 records the complete policy while versions 1–7 retain
  their canonical bytes and disabled semantics. MemoryAgentBench schema 8 can
  preserve upstream source chunks as distinct registered episode partitions.
  New execution descriptors also hash the episode-routing module alongside the
  other retrieval stages.

- The registered EventQA episode-routing development trial scored 19/20 versus
  BM25 at 20/20 while using 90.48% fewer retrieved-context tokens. Against the
  earlier flat-session PRME arm on the same 20 questions and reader, it recovered
  three of four failures with no paired losses and essentially unchanged context
  size and query time. The one remaining miss contained the strongest routed
  evidence but not the answer verbatim. This supports the technique on the named
  slice. Registered follow-up arms preserved Banking77 at 20/20 and improved
  DetectiveQA from 13/20 to 15/20, with every receipt verified and no context
  expansion. The default remains disabled pending a larger cohort and another
  reader.

- MemoryAgentBench registrations now bind the complete retrieval source map
  reported by execution descriptors. Verification compares every durable
  receipt with that frozen map, rejecting runs whose adapter came from the
  declared revision but whose PRME runtime resolved from another checkout.

- The MemoryAgentBench installer now upgrades its previously generated reader
  configuration block in place. Reinstalling after a new reader contract no
  longer leaves both the old and new validators in `agent.py`; managed markers
  make subsequent installs idempotent, and ambiguous edits still fail closed.

- MemoryAgentBench DetectiveQA trials can register a shared
  `choice-only-v1` reader contract. It resolves the pinned task prompt's conflict
  between an example JSON reasoning object and its option-text exact-match
  reference by requiring one `A. choice text` line in both arms. Registration,
  runtime initialization and verification reject the contract outside
  DetectiveQA, and saved model output remains untouched.

- The matched DetectiveQA development trial completed on 20 questions under
  that contract. PRME and BM25 each scored 13/20, with one paired win and one
  loss. PRME used 90.52% fewer retrieved-context tokens and 89.27% fewer reader
  input tokens. Two PRME responses and three BM25 responses contained a
  reference answer but violated the exact format; saved outputs were scored without
  rewriting. The verified tie completes a first matched development pass across
  all four MemoryAgentBench competency families, with task-specific gaps and no
  general leadership claim.

- A matched MemoryAgentBench Conflict Resolution development trial completed on
  20 questions under the shared `answer-only-v1` contract. PRME scored 1/20 and
  BM25 scored 0/20. Post hoc inspection found reference-answer text in 19 PRME
  contexts and all 20 BM25 contexts, isolating the Qwen 9B reader and task
  interpretation as the dominant failure. A separately preregistered check with
  the installed Qwen 35B A3B reader produced the same 1/20 and 0/20 scores,
  ruling out model size alone as the remedy. Both verified negative results are
  published without treating the single paired win as evidence of useful
  conflict-resolution quality.

- MemoryAgentBench trials can register a shared `answer-only-v1` reader contract
  for strict-answer tasks where a local OpenAI-compatible reader otherwise adds
  explanations. PRME and BM25 receive the same prompt instruction, registrations
  bind it before inference, and saved model output is never parsed or rewritten.

- Real extraction diagnostics can now run the same qualifier and entity-reference
  probes through either Ollama or OpenAI. Checked worker processes forward the
  provider explicitly and record it in the report, enabling provider-diverse
  validation without changing the cases or weakening grounding checks.

- MemoryAgentBench ICL trials may opt into a registered `numeric-label-v1`
  reader contract shared by PRME and BM25. It resolves the pinned harness's
  conflict between a `label: N` prompt and its documented digits-only exact-match
  metric by appending the same explicit instruction to both arms. Retrieval
  queries and raw model outputs remain unchanged, and manifests and captures bind
  the selected contract. The paired comparator now rejects cross-arm contract
  drift and derived retrieval-query drift, and reports the matched contract with
  the other reader settings. The matched BM25 path uses the same terminal ICL
  question extractor as PRME, with both derived queries bound before inference.

- The first corrected matched MemoryAgentBench answer trial completed on 20
  Banking77 development questions: PRME scored 20/20 versus BM25 at 17/20 under
  the official strict metric while using 90.52% fewer retrieved-context tokens.
  Both independent verifiers and the strengthened paired comparator accepted the
  exact artifact chain. The paired interval includes zero, and PRME's 5,897-node
  pack took 467.549 seconds to construct, so this remains bounded development
  evidence and exposes typed bulk ingestion as the next performance target.

- The matched MemoryAgentBench EventQA development trial completed on 20
  questions with independently verified PRME, BM25 and paired artifact chains.
  PRME scored 16/20 versus BM25 at 20/20 while using 90.48% fewer
  retrieved-context tokens. All four PRME misses omitted the reference answer
  from the packed context even though answer-bearing records remained in the
  durable store; flat limit and token-budget increases did not recover them
  consistently. This records episodic context reconstruction as a quality gap
  and preserves the negative result rather than treating compression as success.

- MemoryAgentBench verification now authenticates each claimed durable
  retrieval receipt directly from the completed DuckDB pack, including its
  checksum, owner/request identity, replayable ranking, context hash, packing
  contract, scope, and included count. Reports commit an aggregate receipt hash
  instead of trusting the adapter's persistence flag. Registration and both
  verifiers require their executing module bytes to match the frozen source they
  report.

- MemoryAgentBench source ingestion now preserves blank-line units and
  serial-numbered facts as independent records, splitting only an overlong unit
  at the configured hard limit. Test-time-learning retrieval embeds the terminal
  classification question instead of its repeated reader instructions.
  Versioned manifests, preregistrations, captures, and durable receipt checks
  bind both policies and the exact derived retrieval query.

- Session-context expansion now promotes adjacent turns even when broad vector
  or lexical generation already placed them in the candidate pool. Previously,
  those candidates were skipped before packing, making the default session
  window a no-op on sufficiently complete candidate sets. Inherited scores keep
  their trigger and decay in replayable receipt provenance.
  Built-in DuckDB and PostgreSQL stores fetch exact bounded neighborhoods, so
  older hits in sessions longer than 2,000 nodes are no longer silently missed;
  reused session IDs remain partitioned by exact memory scope.

- Contract-correct LangChain and LlamaIndex chat persistence over the immutable
  event log. Versioned control events make clear, replacement, and deletion
  visible to framework callers without erasing source messages or creating
  retrievable control nodes. The adapters page beyond 1,000 events, isolate exact
  scopes, preserve structured message payloads, and now run against the locked
  current framework releases in a dedicated CI job.
- Automatic adjacent question-answer pairing now includes exact scope in its
  session cache identity. A personal turn followed by a project turn can no
  longer produce a combined cross-scope memory, and chat-history control events
  reset the applicable cache before later messages are stored.

- A registered, fail-closed MemoryAgentBench BM25 control for matched-reader
  trials. It binds the exact prepared sources, formatted BM25 documents,
  questions, answers, harness code, configuration, NumPy and `rank-bm25`
  versions, and ranking source hash before inference. The verifier reconstructs
  every BM25 ranking and rejects missing, stale, reordered, or altered captures.
  Explicit retrieval run IDs isolate scratch outputs, while a fixed registered
  memory timestamp removes wall-clock text from otherwise reproducible indexes.
- Explicit MemoryAgentBench reader reasoning and seed controls shared by the PRME
  and pinned BM25 request paths. The installer validates both settings, forwards
  the task generation limit for non-OpenAI model names, and the PRME adapter binds
  the values into its pack identity and retrieval captures. This prevents a local
  common-reader comparison from silently using different thinking or sampling
  behavior between memory arms. Registrations also bind the dataset, NLTK and
  tiktoken versions, the English Punkt data, and the exact tokenizer table so
  preprocessing dependency drift cannot silently change task chunks. The
  canonical PRME agent name now selects the same upstream RAG question template
  as BM25 while still dispatching to the PRME adapter, removing a reader-prompt
  confound from matched trials.
- Isolated MemoryAgentBench PRME state through a validated experiment slug.
  `prme_run_id` participates in the saved-agent path, pack identity, retrieval
  capture, and verifier so multiple configurations using one reader cannot reuse
  or overwrite each other's memory packs.
- Opt-in compact packed context for short-record workloads. It replaces repeated
  JSON keys and full in-prompt UUIDs with one declared array schema and deterministic
  bundle-local references while retaining type, scope, epistemic state,
  lifecycle, provenance, representation, temporal fields, and complete selected text.
  `MemoryBundle.context_references` and `resolve_context_ref()` map citations back
  to full node IDs. Exact token accounting covers the complete output, and receipt
  schema version 7 records the format while versions 1–6 preserve their canonical
  bytes and historical auditable semantics. The auditable format remains default.
- A registered 149-question LongMemEval-V2 4K-budget development study. PRME
  scored 49/149 versus 10/149 without memory, while mean reader memory context
  fell 83.05% from the earlier 32K arm. The same-cohort PRME score fell from
  80/149 to 49/149, so the result preserves the 4K preset as explicit and makes
  intermediate-budget answer retention the next gate rather than supporting a
  quality-default change.
- Explicit LongMemEval-V2 renderer selection. The adapter accepts and reports
  `context_format`, the full preset declares `auditable`, and the compact preset
  now declares `compact` so future runs cannot confuse a 4K budget with compact
  serialization. The registered comparator reports the effective format and
  rejects a declared format that differs from the saved configuration.
- A pinned MemoryAgentBench adapter for the benchmark's four incremental memory
  competencies. It uses the public PRME client with the product-default 4K
  balanced packer, pins the upstream dataset revision, fences completed source
  ingestion for restart, and saves exact retrieved contexts and request
  identities. Task-qualified state paths prevent cross-suite pack reuse. The
  installer is idempotent, rolls back partial changes, and preserves list-valued
  references across upstream resume. Outcome-free registration hashes every
  prepared source, query, answer, context assignment, configuration and harness
  file; post-run verification requires exact registered inputs, complete metrics,
  bounded recounted contexts, durable receipts, and completed source manifests.
  Matched BM25 registrations bind preprocessing, ranking, and LangChain wrapper
  dependencies; the installer updates the pinned harness to the current
  `BM25Retriever.invoke()` API. A paired comparator accepts only exact
  registrations, verified-complete arms and result hashes, applies each task's
  official accuracy metric, and reports paired uncertainty without raw answers.
  Both arm verifiers accept the harness's native boolean match metrics while
  retaining finite numeric validation for graded metrics.
  This is an evaluation path; task results retain their individual claim bounds.
- Durable, atomic hierarchical summary publication. Daily, weekly and monthly
  excerpts use stable owner/scope/level/period lineages, deterministic request
  generations, checksummed preparation, fenced index staging and transactional
  graph publication. Unchanged runs reuse one active identity; late selected
  sources atomically replace and archive the prior generation; interrupted runs
  recover without re-embedding; concurrent engines converge; and the first
  managed run atomically retires legacy excerpts for the same period. Stable-ID
  paging removes the prior 5,000-node daily and 1,000-summary rollup caps while
  keeping unscoped scans an explicit internal operator action.
- Immutable owner/exact-scope ranking profiles with a two-stage activation gate.
  A profile must bind a positive explicit-feedback proposal to a separately
  positive full-retrieval holdout before it can be persisted and activated.
  Retrieval applies compatible profiles per request, records their identity and
  status in metadata and receipts, and safely falls back on feature or base-score
  drift. Append-only, retry-safe activation, deactivation and rollback work
  across DuckDB and PostgreSQL and are exposed through Python, HTTP and MCP.
  Serialized evaluations revalidate their coverage, aggregates, decisions and
  full-retrieval input identity; the query path reads a compact checksummed
  activation record rather than reparsing the complete evidence report.
- A conservative full-pipeline retrieval holdout evaluator for learned ranking
  proposals. It compares separately executed baseline and candidate receipts,
  requires complete caller-supplied relevant-node sets, verifies identical
  owner, scope, clock, filters, limits, scoring, packing, execution parameters,
  and feature identity outside the declared multipliers, and reports grouped
  recall, NDCG, MRR, bootstrap uncertainty, regressions, and immutable input
  identities. Positive observed-candidate learning alone cannot activate a
  profile.
- A preregistered MELT lifecycle launcher and fail-closed report validator. The
  launcher pins the held-out final profile, five-seed schedule, source commits,
  adapter and upstream file hashes before execution. Validation uses MELT's own
  report loader and independently requires final status, exact protocol and SUT
  identity, five complete runs, all case checkpoints, and retained case I/O.
- Opt-in `prme doctor --verify-extraction` provider diagnostics. It verifies
  endpoint reachability, hosted credentials, and the configured model through
  read-only model metadata APIs without generating or storing content, and
  reports sanitized status categories without response bodies or URLs. It
  explicitly leaves generation quota unverified.
- Explicit half-open validity intervals for direct writes across async/sync
  Python, HTTP, and MCP. New extracted facts start validity at their resolved
  source-effective time, and a source-grounded replacement closes the prior
  interval atomically when doing so cannot invert a legacy interval. Invalid or
  timezone-free intervals fail before source admission; historical derivation
  plans retain their original bytes and behavior.
- Exact temporal assertion state across async/sync Python, HTTP, and MCP. A
  required owner, scope, structured subject/predicate, and timezone aware
  validity instant produce auditable current candidates plus stored history,
  supersedence, contradiction, epistemic, lifecycle, clock, and evidence data.
  The operation preserves differing unresolved values and never selects truth
  from recency; historical knowledge cutoffs retain an explicit non-replay
  boundary.
- Exact decimal quantity aggregation across Python, HTTP, and MCP, with totals,
  minima, maxima, counts, temporal bounds, and source/evidence samples. Every
  group must retain its normalized unit, stored quantities are revalidated at
  read time, incompatible units are never mixed, and scaled-integer addition
  avoids decimal-context rounding.
- Source-grounded decimal quantities for extracted facts. Built-in and custom
  grounding retain a quantity only when its exact decimal, quantified phrase,
  and verbatim unit occur in the claim object and evidence. New derivation plans
  version the metadata policy while historical plans and checksums remain
  unchanged; invalid optional quantities do not discard otherwise valid claims.
- Exact owner-scoped assertion aggregation across Python, HTTP, and MCP. It
  counts every matching structured claim for an unchanged store, groups
  normalized subject/predicate/object/polarity values, retains bounded evidence
  samples and time/epistemic filters, and distinguishes stored-set completeness
  from extraction, semantic, and real-world completeness.
- A fail-closed operational benchmark for p50/p95/p99 retrieval latency at
  declared pack sizes, exact fixed-clock repeatability, and 10,000-query owner
  isolation through public store/retrieve paths, with runtime and execution
  provenance in its artifact.
- HTTP and MCP access to the existing bounded, owner-scoped offline learning
  evaluator. Both remote surfaces return the complete validation, uncertainty,
  coverage, exclusion, and input-identity report without activating weights.
- Owner-required, cursor-based stored-record enumeration over HTTP and MCP.
  `GET /v1/nodes/scan` and `memory_scan_nodes` expose scope, type, and lifecycle
  filters, immutable UUID ordering, explicit `has_more`/`next_cursor` fields,
  and page-level consistency so remote exports and counts do not depend on a
  bounded top-k query.
- A pinned BEAM benchmark service with source-only ingestion boundaries, owner
  isolation, source-time query clocks, restart-safe request idempotence, and
  separate raw and durable-extraction profiles. Its fail-closed validator
  rejects the upstream runner's otherwise silent failed-chunk and empty-model
  completion paths and hashes every accepted artifact. A registered raw
  retrieval launcher additionally binds the clean PRME and upstream revisions,
  exact source files, normalized dataset cache, run identity, profile, and
  complete question selection before ingestion; validation checks the same
  registration, execution, dataset, and adapter identities.
- Structured `store_with_receipt()` results on async and synchronous Python.
  They expose the durable event, exact created node, and materialization status
  in one call while preserving the historical `store()` event-ID return. HTTP
  and MCP store responses now use the same provenance-based receipt path and
  expose direct materialization status.
- An official LongMemEval-V2 adapter for agent-trajectory memory. It preserves
  ordered state and transition semantics, creates compact hierarchical procedure
  traces alongside raw states, bounds downstream context items, returns source
  screenshots, fingerprints portable adapter state, and rejects changed or
  interrupted trajectory inserts. A pinned one-question web-small pipeline
  smoke covers the full 100-trajectory save/load path. Its installer verifies
  the pinned upstream revision, copies the adapter and configuration atomically,
  registers the backend, and safely handles repeat installation. A companion
  launcher durably checkpoints each reader response, preserves exact prompt
  rows across retries, rejects configuration drift, and supports explicit
  `reasoning_effort: none` for Ollama development runs. Schema 3 packs persist a
  stable retrieval reference clock; schema 2 packs derive it without mutation
  and remain read-only, preventing saved-run ranking drift across wall-clock
  dates. Registered paired comparisons now bind the PRME and no-memory arms to
  their declared configuration paths, context budgets, frozen saved-memory
  configuration and pack manifest; a baseline that returns memory context fails
  validation. New schema-2 registered runs also write a launch-time execution
  manifest that binds both arms to clean PRME and pinned upstream revisions and
  hashes the exact launcher, installer, adapter, configuration, and harness;
  changed-source resumes and retroactive attribution fail before generation.
- The first preregistered held-out LongMemEval-V2 web-small answer comparison
  completed all 149 deterministic questions for PRME and the official no-memory
  adapter. With one fixed local Qwen 9B reader, PRME scored 80/149 versus 10/149,
  with 75 paired wins, 5 losses and a +37.58 to +55.70 point bootstrap interval.
  The fail-closed comparator verified both arms, official scores, source inputs,
  reader settings, saved pack/config bindings and the baseline's zero-memory
  contract. The result used a mean 43,195 memory-context tokens and remains a
  named-cohort memory-utility result rather than a competitive leadership claim.
- LongMemEval-V2 registered execution manifests now bind the configuration
  actually selected for each arm, including its launch-time hash, instead of
  attesting only the installed default. The installer atomically supplies both
  the 32K maximum-evidence profile and an explicit 4K product-default compact
  profile. Before opening loaded memory, the launcher also hashes every regular
  file by relative path, size and content and rejects symlinks; the comparator
  requires the complete initial artifact and both per-arm configuration
  identities for new schema-2 studies.
- A matched raw-store versus real `ingest()` evaluation profile for
  LongMemEval source evidence. Extracted runs freeze the local model digest and
  full configuration before generation, require durable extraction completion,
  report source/node materialization coverage, and support an explicit paired
  profile comparison without presenting source lineage as answer accuracy.
- Optional, token-counted context guidance with a non-displacement guarantee.
  The packer includes guidance only when it fits after record selection, exposes
  whether it was included, and preserves it through controlled ablation.
- Exact, nonmutating packed-context ablation and citation-checked presence
  credit. Applications can re-answer after removing one cited memory, preserve
  both context hashes and token accounting, and distinguish load-bearing,
  redundant, misleading, and noncuring outcomes without changing retrieval,
  ranking, or retention.
- Immutable, owner-scoped answer citation records across async/sync Python,
  HTTP, MCP, DuckDB, and PostgreSQL. Citations bind to saved content-bearing
  context, preserve optional answer digests, and use caller UUIDs for exact
  retry safety without changing memories or ranking.
- Durable retry IDs for promotion and archival across sync/async Python, HTTP,
  and MCP. HTTP uses `Idempotency-Key`; conflicting key reuse returns 409.
- Atomic, checksummed explicit corrections across sync/async Python, HTTP, MCP,
  DuckDB, and PostgreSQL. State, deterministic edge, and complete before/after
  audit record commit together; exact retries remain safe after restart.
- A complete tenant-scoped contradiction lifecycle on sync/async Python, HTTP,
  and MCP. Marking and resolution atomically update claims, edges, and audit
  records; exact retries are idempotent across restarts and resolution evicts
  the deprecated claim from derived search indexes.
- Atomic, evidence-aware condition evaluation across Python, HTTP, MCP, DuckDB,
  and PostgreSQL. Retry IDs are durable, state changes retain checksummed
  before/after records, and confirmed conditions receive asserted retrieval
  weight without losing their conditional classification.
- MCP `memory_store` parity for role, session, metadata, confidence, epistemic
  type, and source type, allowing agents to create qualified memories directly.
- Tenant-scoped node provenance on sync/async Python, HTTP, and MCP, including
  owned source events, explicit missing-evidence signals, valid contradiction
  links, and bounded chronological operation pages with opaque cursors.
- Explicit aggregation coverage on Python, HTTP, MCP, durable retrieval records,
  and packed model context. Natural-language counts/lists report candidate,
  selection and context truncation without claiming exhaustive enumeration.
- A reproducible 8K-context Ollama profile for `qwen3.5:35b-a3b`, with two
  successful strict extraction reports and an equal-context 9B comparison.
- Typed claim polarity and exact explicit conditions for built-in LLM
  extraction. Conditional claims retain an auditable state and stay out of
  default retrieval until confirmed.
- `MemoryWorkspace` and lease-scoped `NamespaceMemory` for named local projects,
  with stable pack identity, bounded idle-engine eviction, shared embeddings,
  process ownership and cancellation-safe lease cleanup.
- `MemoryWorkspace.open_postgres()` for named PostgreSQL projects sharing one
  bounded connection pool. Identity checks and a private-only search path protect
  routing; registry and schema creation publish atomically. Hosted grants remain
  separate. pgvector symbols now resolve explicitly even outside `public`.

- Optional `duckdb_threads` / `PRME_DUCKDB_THREADS` control for each open local
  database. The default preserves DuckDB's setting; conflicting concurrent opens
  fail without reconfiguring the active pack.

### Changed

- Supplementary aggregation and entity-focused lexical searches now normalize
  their BM25 scores before composite ranking. Unbounded raw Tantivy scores can
  no longer overwhelm semantic, graph, recency, salience, and confidence
  signals or crowd relevant hybrid candidates out of a bounded result set.
- Packing configuration now rejects negative candidate limits and graph depths
  outside the RFC's 1-3 range. The legacy no-op `chars_per_token` and
  `cross_scope_token_budget` fields remain parseable for receipt compatibility,
  but non-default use warns that exact context tokenization and
  `cross_scope_top_n` are the active controls. Setting `cross_scope_top_n=0`
  now skips the secondary hint search entirely.
- The organizer registry now contains only implemented jobs. The unimplemented,
  unvalidated `centrality_boost` no-op is no longer advertised or run during
  default maintenance; explicit requests fail as an unknown job instead of
  returning a misleading success result.
- Temporal reasoning guidance is now enabled by default when it fits after
  evidence selection. A registered 70-context confirmation improved temporal
  answers from 31/50 to 34/50. Personalization and current-state guidance remain
  experimental behind `packing.context_guidance_mode="all"`; `"off"` restores
  pre-guidance behavior. Receipt schema version 6 records the chosen mode while
  versions 1–5 preserve their canonical bytes and mean guidance was off.
  Elapsed-time questions no longer receive aggregation coverage warnings;
  time totals across records remain aggregation queries.
- `knowledge_at` now exposes a machine-readable `historical_coverage` boundary
  in Python, HTTP, MCP, retrieval receipts, and the token-counted model context.
  It is explicitly an aware ingestion-time cutoff over current indexes; prior
  lifecycle and index state are not replayed. Naive datetimes fail immediately.
- Balanced multi-path context packing is now the default. A complete fixed
  119-question answer trial scored 83 correct with balanced contexts versus 67
  with density. A separately registered 381-question answer confirmation scored
  250 versus 185, with 88 paired wins, 23 losses, and no lower category total.
  Explicit density and score policies remain available, while historical receipts
  retain their original density meaning and checksums.
- Current-state retrieval now recognizes ordinary present-tense state questions,
  while inferred recency reweighting requires explicit update evidence and dated
  temporal queries remain historical. Decisions and instructions receive the
  same default semantic-node boost as facts and preferences.
- Structured extraction now sends a configurable sampling temperature, defaulting
  to zero, and rejects uncertain or contingent future actions mislabeled as
  completed decisions. Explicit known-negative updates can retire the same
  known-positive claim without guessing legacy polarity.
- PostgreSQL now honors the default `vector_exact_search=True`, materializing
  eligible rows before distance ordering to prevent filtered HNSW starvation.
  `False` explicitly permits approximate search. Broad exact queries may cost
  more; new receipts report the requested mode.

- Default organizer passes exclude the legacy global feedback tuner; session
  completion runs promotion only. Explicit scoped `feedback_apply` requests now
  raise `ValueError` before any work. Trusted operators can retain the legacy
  behavior with explicit `organize(jobs=["feedback_apply"])` and no user scope.

### Fixed

- Extraction workers now immediately reclaim an expired, uncontested lease once
  before falling back to scheduled retry. Event-loop starvation after a provider
  response can no longer leave durable work indefinitely in `running` state;
  a lost reclaim race or expired inline recovery also schedules the ordinary
  durable retry, while generation fencing prevents stale publication.
- Forgotten synchronous clients now close before Python shuts down its shared
  thread-pool executor, allowing vector and lexical indexes to flush cleanly at
  normal interpreter exit without late-executor errors.
- Historical assistant and system events are now submitted to extraction models
  as input text while retaining their stored source role for provenance and
  epistemic typing. Role-aware admission guidance prevents local chat providers
  from returning an empty continuation and avoids promoting generic assistant
  recommendations, explanations, and examples into durable claims.
- Extracted source-lineage benchmarks now complete and map the durable raw NOTE
  path before retrieval. Reports distinguish raw source nodes from derived
  semantic coverage, so an intentionally empty extraction remains retrievable
  as episodic evidence instead of disappearing from evaluation accounting.
- Mixed-quality structured responses now retain source-supported claims while
  dropping malformed or unsupported proposals. A response with no admitted
  claims completes with an empty claim set while preserving the raw event and any
  grounded named mentions; missing or ambiguous named references on admitted
  claims still enter bounded validation retries. Condition and uncertainty
  validation is scoped to the cited source sentence and directly following
  qualifiers, preventing unrelated language elsewhere in a paragraph and
  indirect questions such as “see if” from contaminating the claim. Polite
  request language such as “could you help” is also excluded from claim
  modality checks while real possibilities remain hypothetical. Model overreach
  therefore reduces derived recall instead of failing ingestion availability.
- Ollama structured extraction now defaults `reasoning_effort` to `none`, with
  an environment and typed-config override. This prevents thinking traces from
  exhausting the same context window needed for the validated response. Ollama
  also uses constrained JSON output, avoiding rejection of valid JSON that lacks
  a tool-call envelope.
- Built-in extraction now accepts source-grounded literal personal references
  such as `I` and `we` without requiring them to be named entities. Each
  unlisted reference receives one provenance-bound identity within its source
  event and cannot merge across messages; missing named entities and unsupported
  citations remain validation errors. Provider citations that differ only in
  straight or curly quote marks are canonicalized back to the exact source span.
- Recreate a loaded empty USearch index before its first new insertion, avoiding
  a reproducible native crash after deleting the last vector, closing, reopening,
  and storing another memory.

- Make greedy consolidation clustering and source selection stable across
  equivalent histories with different generated UUIDs. Singular relational
  state questions use semantic answer-class relevance so generic subject words
  do not swamp the requested relation.
- Make extractive consolidation publication durable and idempotent. Unchanged
  clusters reuse one summary; changed source snapshots atomically publish a new
  generation with its provenance edges and archive the predecessor. DuckDB
  journals and fences external index staging for restart and multi-engine recovery;
  PostgreSQL serializes concurrent publications through the generation head.
- Keep unresolved personal references local to their source event during new
  ingestion. Historical prepared derivations retain their original replay policy.
- Prevent organizer similarity matches from merging different claim text,
  validity or provenance; semantic entity aliases remain unverified links.
- Initialize PostgreSQL vector columns and indexes against the selected table,
  without treating names in other schemas as an existing local installation.
- Preserve relationship validity and provenance during organizer merges; failed
  copies keep the source active, and deterministic copy IDs make retries converge.
- Publish organizer evidence unions, relationship copies, retirement and a single
  supersedence edge atomically, with a checksummed operation record and scoped,
  idempotent retries. Concurrent PostgreSQL merges lock shared nodes before reads.

## [0.11.0] - 2026-09-11

### Added

- Tenant-scoped organizer execution through `organize(user_id=...)` and
  `prme organize --user-id`, with cross-owner merge guards.

### Benchmark measurement
- A registered exact-context ablation over all 37 development questions with one
  annotated source in the balanced bundle reduced the fixed reader from 29/37 to
  7/37 correct. Twenty-three correct answers became wrong. The audit retained six
  redundant non-flips, seven noncuring reader errors, and one benchmark-reference
  inconsistency rather than converting them into negative memory labels.
- Preserve per-question evaluation failures in reports and retry selection.
- Report scored coverage and whole-benchmark failures; weight summary accuracy by
  measured questions and fail the CLI when any repeated run is incomplete.
- Propagate abstention-provider failures during evaluation instead of treating
  the application's fallback as a measured verdict.

### Fixed

- Report the package version consistently in REST metadata and MCP resources.
- Enforce node ownership for engine operations that accept node IDs.
- Reapply scope, bi-temporal, and epistemic filters after late retrieval stages.
- Serialize organizer SQL through the shared DuckDB connection lock.
- Restore CI coverage for main, optional API/MCP dependencies, simulations,
  and live PostgreSQL tests on supported Python versions.
- Disable redundant Tantivy background reader reloads so a closed lexical
  index cannot recreate metadata lock files during pack cleanup or movement.

- Expand vector searches when tenant, scope, lifecycle, or temporal filters leave
  fewer than the requested number of distinct memory nodes.

### Changed

- Schedule opportunistic organizer passes in the background and drain them on close.
- Pin dateparser language and gate temporal parsing to reduce retrieval overhead.
- Remove dataset observations, answer-revealing prompt examples, and benchmark-only
  query expansion from real-data evaluation.
- Replace stale accuracy headlines with a measurement contract and reviewed roadmap.

## [0.10.0] - 2026-07-15

### Added

- **Deterministic vector search + `prme rebuild`** (issue #45) — Reproducible vector retrieval and full reconstruction of derived indexes (vector, lexical) from the durable event/graph store via `prme rebuild`
- **Multi-query reformulation** (issue #43) — Opt-in retrieval mode that expands a query into multiple reformulations for broader candidate generation

### Changed

- **REST API hardening** (issue #34) — Authentication, loopback-only bind by default, CORS restrictions, and error-message sanitization
- **Encryption-at-rest lifecycle hardening** (issue #37) — Tightened key handling and encrypt/decrypt lifecycle for memory pack files
- **Stored memory neutralized in LLM context** (issue #36) — Retrieved memory is delimited and neutralized before entering the LLM context to prevent prompt injection from stored content
- **Ingestion index write path batched** (issue #39) — Batched writes across the ingestion indexing path
- **Retrieval hot path DB round-trips reduced** — Fewer database round-trips on the retrieval hot path
- **Benchmark measurement rigor** (issue #44) — Pinned judge model, cached verdicts, and multi-run scoring for reproducible benchmark numbers

### Fixed

- **Bedrock provider extraction** (issue #59) — Pass model through to `client.create()` so the Bedrock provider works during extraction
- **Context formatter token budget + dedup** (issue #42) — Enforce the token budget and deduplicate entries in the context formatter
- **Index eviction on supersedence/archival** (issue #41) — Evict superseded/archived content from the lexical and vector indexes
- **Auto-promotion coverage** (issue #40) — Auto-promote now reaches older eligible nodes, not just the newest batch

## [0.4.0] - 2026-03-16

### Added

- **Bi-temporal data model** (issue #21) — `event_time` field distinguishes when something happened vs when system learned about it; `knowledge_at` parameter on retrieve() for point-in-time knowledge snapshots
- **Encryption at rest** (issue #14) — Transparent Fernet (AES-128-CBC + HMAC) encryption of memory pack files; PBKDF2 key derivation; encrypt on close, decrypt on create
- **Deduplication and entity alias resolution** (issue #11) — Organizer jobs for vector-similarity-based duplicate detection (threshold 0.92) and alias resolution (threshold 0.85); merge logic with SUPERSEDES edges
- **Evaluation harness** (issue #16) — Precision@k, recall@k, nDCG@k, MRR metrics; ground truth support in simulation checkpoints; 3 evaluation scenarios (factual, temporal, supersedence)
- **HTTP API layer** (issue #17) — FastAPI REST API with endpoints for store, retrieve, organize, node operations, graph traversal, health, and stats
- **CLI tooling** (issue #15) — `prme` command-line tool for memory inspection: info, nodes, edges, search, chain, organize, stats, export
- **Dual-stream ingestion** (issue #25) — `ingest_fast()` guaranteed sub-50ms path (event store + vector only); materialization queue for deferred graph writes
- **Memory quality self-assessment** (issue #24) — Feedback signal tracking, gradient-free weight auto-tuning, per-namespace scoring profiles, quality metrics
- **Procedural memory** (issue #23) — INSTRUCTION node type for system instructions and procedural knowledge; Priority 0 packing into `system_instructions` section; epistemic inference support
- **Entity snapshot generation** (issue #13) — `generate_entity_snapshot()` produces structured entity state views from graph neighborhood; `snapshot_generation` organizer job; simulation scenario
- **Predictive forgetting / consolidation** (issue #22) — Semantic clustering of episodic memories; summary abstraction creation; redundant memory archival; `consolidate` organizer job
- **TTL-based archival** (issue #12) — `ttl_days` field on memory nodes; per-type default TTL configuration; `tombstone_sweep` organizer job with operation logging; policy-based retention enforcement
- **Summarization pipeline** (issue #10) — Hierarchical daily -> weekly -> monthly summarization; configurable thresholds; time-budget-aware processing; `summarize` organizer job
- **Benchmark suite** (issue #26) — LoCoMo long-conversation benchmark, LongMemEval 5-ability evaluation, custom epistemic benchmark (supersedence correctness, confidence calibration, contradiction detection, belief revision, abstention quality)
- **Hybrid retrieval pipeline v2** — supersedence-aware scoring, lifecycle filtering (SUPERSEDED/ARCHIVED exclusion), query reformulation with LLM-generated alternative queries, session context expansion (top-20 with ±3 adjacent turns)
- **Context formatter** — temporal annotations (days-ago, COMPUTED offsets), chronological sorting for temporal queries, relevance-ranked formatting with date annotations
- **Benchmark infrastructure** — LLM-as-judge with configurable generation model, concurrent evaluation (semaphore-based throttling), resilient structured output for reasoning models

### Fixed

- Integration test fixture (issue #18) — Added `examples/conftest.py` with engine/log fixtures
- DuckDB segfault in concurrent tests (issue #19) — Isolated DuckDB connections per test with `conn_lock` protection
- GeneratedAnswer schema resilient to reasoning models (gpt-5-mini) that embed answers in reasoning field

## [0.3.0] - 2026-03-08

### Added

- `engine.reinforce()` method — bumps `reinforcement_boost` (+0.15, cap 0.5) and `confidence_base` (+0.05, cap 0.95), updates `last_reinforced_at`, appends evidence refs
- Re-mention reinforcement in `store()` — opt-in via `reinforce_similarity_threshold` config; vector-searches for similar existing nodes and reinforces them automatically
- Keyword-based supersedence detection in `store()` — opt-in via `enable_store_supersedence` config; `ContentContradictionDetector` with 10 regex patterns for migration/replacement language
- Oscillation detection for flip-flop supersedence patterns — `OscillationDetector` using Jaccard keyword similarity on supersedence chains; applies confidence penalty (0.1 per cycle, cap 0.3)
- `update_node()` method on `GraphStore` protocol and all implementations (DuckPGQ, PostgreSQL) for field-level node updates
- Ranking assertions in simulation harness (`SimCheckpoint.ranking_assertions`)
- Lifecycle assertions in simulation harness (`SimCheckpoint.lifecycle_assertions`)
- Deterministic rebuild verification (`SimulationRunner.run_deterministic_check()`)
- Surprise-gated storage — opt-in via `enable_surprise_gating` config; `NoveltyScorer` computes novelty of incoming content against existing memory, boosting salience for novel content and penalizing redundant content
- Four new simulation scenarios: `reinforcement`, `remention`, `oscillation`, `surprise_gating`

### Fixed

- `promotion_evidence_count` default aligned with `store()` behavior (default 1, matching the single evidence ref created per node)

### Changed

- `store()` pipeline now has 6 steps: event persistence, graph node creation, vector/lexical indexing, re-mention reinforcement (opt-in), supersedence + oscillation detection (opt-in), surprise gating (opt-in)

## [0.2.0] - 2026-02-27

### Added

- Self-organizing memory system (RFC-0015) with virtual decay, maintenance runner, and organizer jobs
- Simulation harness for validating memory behavior without LLM dependencies
- Decay mechanics: exponential salience/confidence decay with per-type decay profiles
- Organizer jobs: promote, decay_sweep, archive, feedback_apply (plus stubs for future jobs)
- Opportunistic maintenance during retrieve/ingest operations
- Three simulation scenarios: `changing_facts`, `decay_mechanics`, `information_accumulation`

## [0.1.0] - 2026-02-19

### Added

- Append-only event store (DuckDB)
- Graph-based relational model with typed nodes and edges
- Vector index (usearch HNSW) with fastembed embeddings
- Lexical full-text search (Tantivy)
- Hybrid retrieval pipeline with deterministic scoring and context packing
- Epistemic state model with lifecycle transitions and confidence tracking
- LLM-powered ingestion pipeline (OpenAI, Anthropic, Ollama)
- Entity merge and supersedence handling
- Namespace and scope isolation
- Optional PostgreSQL backend
- Terminal chat example with persistent memory
- Quickstart example

[Unreleased]: https://github.com/dwamianm/prism/compare/v0.12.0...HEAD
[0.12.0]: https://github.com/dwamianm/prism/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/dwamianm/prism/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/dwamianm/prism/compare/v0.9.0...v0.10.0
[0.4.0]: https://github.com/dwamianm/prism/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/dwamianm/prism/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/dwamianm/prism/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/dwamianm/prism/releases/tag/v0.1.0
