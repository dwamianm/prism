# PV-04 / #323 accepted implementation plan

Implementation released by the orchestrator on 2026-10-20 against merged #320 base `50d22d4e365b52ce78300fe2d2e21d6f753b3a78`. This reuses the approved preparation below. Statements below describing preparation-only status are historical; this release authorizes local implementation. Final integrated validation and publication await actual #321/#322 contracts and source reconciliation. No new acceptance criteria or cohort is added.

Dedicated worktree: `privacy-323-source-separation`; branch `issue-323-source-separation`; PostgreSQL database `privacy_engine_323`. No live or paid calls, long benchmarks, deployment, source-schema changes, key custody, migration or erasure implementation. Root owns review, push and PR release. Zero validation repair cycles at implementation start.

## Original acceptance criteria and validation (verbatim)

- [ ] Sealed mode indexes/returns only admitted memory and permitted metadata.
      Raw notes, `evidence_quote` and longer supporting passages are not copied
      into ordinary claims, graph responses or packed context.
- [ ] Statement rendering retains necessary conditions, negation, effective time
      and provenance links; a diagnostic source is never promoted into truth.
- [ ] Ordinary event/history, receipt, graph, aggregate, Python/HTTP/MCP and
      framework reads return an availability status or denied response for sealed
      evidence, not a bypass around the retriever.
- [ ] Queries needing source-only text disclose that limitation. No fallback
      decrypt, raw-text search, model-assisted vault access or broad source cache.
- [ ] Direct `store()` applies the same sanitizer before admitting caller text.
      No promise that sealing sources hides an intentionally permitted sensitive
      fact. Evidence checks use the labeled sanitized source representation.
- [ ] Unsupported framework/raw-history features fail explicitly in sealed mode
      rather than silently switching the store back to standard mode.

Validation: the applicable access/leakage fixtures, a mock key provider that fails
the test if ordinary sealed retrieval attempts any evidence decrypt, and fixed
qualifier/correction fixtures. Share saved results with [PV-11](https://github.com/dwamianm/prism/issues/327); no live answer model.

## Definition of done and fixed scope

Done for #323 means all six criteria above have real public/backend evidence,
including successful admitted-memory operations. The eight cases whose frozen
owners include PV-04 are AP04, AC01, AC04, AC06, AC07, AC08, CL02 and CL07.
Each gets DuckDB and PostgreSQL result records, with directory/single-file layout
subchecks recorded inside the DuckDB result. The capability adapter checks consume
AC02, AC03 and AC05 from #321; they do not transfer crypto/custody ownership.

The full CL07 expected result also requires covered erasure, owned by #324/#325.
Record the source/index/staging assertions owned here and the erasure subcheck
as an explicit dependency until that work is actually run. Do not label the whole
CL07 case passed because this ticket's part passed. Likewise #324 owns AC06's
historical recovery assertions. This does not weaken any of #323's six criteria.
The combined whole-case results and separate 24-query parity/24-case sealed
behavior sets belong to #327. Do not run or invent another broad quality cohort.

The finite fixture matrix below, affected compatibility checks, required final
project checks and root-scheduled reviews are the complete validation plan. Once
they pass, finish the authorized commit/push/PR steps when root releases delivery.
No extra quality round or default flip is authorized. Protected behavior stays
explicitly opt-in. No new live/paid model calls or long benchmarks.

After two unsuccessful repair-and-validation cycles for the same behavior, stop
that strategy, preserve both failed logs and describe the remaining criterion and
a bounded alternative to root. Switching files, agents or context does not reset
the count. Do not add acceptance criteria from exploratory findings. Report
measured cases, commands and elapsed time; no account-quota or exact-cost claim.

## Root decisions and upstream handoffs

Root confirmed that #323 owns actual metadata-only public provenance/history/read
projection, including `OperationAuditRecord.payload`. An empty protected
`Event.content` alone does not satisfy AC1/AC3. #324 remains the journal content,
historical codec and recovery lifecycle owner.

Root also clarified provider fakes: use fixed synthetic responses at external
extraction, embedding, temporal or answerability provider boundaries where a
successful supported route is required. Keep the actual policy, routing,
grounding, packing, authorization and backend code real. Transport-denial tests
are additional failure cases, never substitutes for success. Such evidence is
interface/privacy evidence, not answer quality.

The following are required integration seams, not permission to expand another
ticket. Names must be reconciled with merged code, rather than copied from these
provisional descriptions. They were sent to root and the available #320 owner.

| Owner | Minimal required contract | Why it is required |
| --- | --- | --- |
| #320 | Immutable saved policy plus actual namespace/project, owner, exact scope, opaque event/source/representation IDs; trusted source/representation classification independent of caller metadata; authoritative disclosure/publication guards. | AC1/AC3/AC4: reading a record through another API or after a default change cannot reinterpret its admitted/source class. Protected receipt/source associations must remain resolvable without treating mutable public metadata as authority. |
| #320 | The existing synchronous preparation hook must expose the trusted sanitized source and private representation checksum until grounding and admitted projection complete, before plan save or embedding. Separate evidence/work slots and empty protected Event envelopes stay its implementation. | AC1/AC2/AC5: `pipeline.py:1412-1444` currently copies a supporting passage into graph content and `evidence_quote`. Projecting after plan persistence is too late. |
| #321 | Metadata-only authorized inspection of policy/slot references and readiness, without private-key lookup, unlock or unwrap; explicit runtime and diagnostic selected-ID reads with final authority/audit checks. | AC3/AC4 and AC01/AC04/AC06: ordinary sealed reads must return availability while the diagnostic private provider is entirely absent or wired to raise on any call. |
| #322 | Sanitized input and generated representations retain their trusted roles: source used for grounding versus proposed admitted fields. Preserve source identity/private checksum, issued-token provenance and validated qualifier/quantity results. | AC1/AC2/AC5 and CL02: a grounding quote may contain an unrelated source-only detail without authorizing its promotion into an ordinary claim. No generic semantic source-only detector is assumed. |
| #324 | Consume the admitted-only node/edge projection and opaque refs in executable plans. Its protected receipt and journal family exposes a closed content-free manifest/availability; old bytes stay intact. | Public projections here must not decode arbitrary historical payloads into ordinary evidence. #323 can project current legacy record shapes safely before #324 merges; it must not depend on #324 to close current ordinary read leaks. |

#324 proposed a separate protected receipt family/discriminant preserving
`RetrievalReceipt` v1-v22, with numeric ranking/candidate identity and explicit
availability/replay limits. It also owns changing alias review's duplicated
`proposal_payload` journal shape. Reuse those typed manifests after their merge;
do not create a competing persistent receipt/alias codec in #323.

Source/representation checksums remain private diagnostic provenance. A new
admitted statement may have its own versioned integrity identity; do not return
an original predictable source hash under another name. Current availability
observations never change immutable source identity or old checksums.

## Root-cause assumptions challenged

1. **Retrieval-only filtering is insufficient.** Engine event/history getters,
   direct node scans, provenance journals, extraction results, graph explorer
   neighbors, receipt APIs, alias inboxes and framework deserialization bypass
   packing. Filtering only rendered context leaves full nodes and metadata in
   `RetrievalResponse.results` and bundle candidates as well.
2. **Sanitized source is not automatically admitted memory.** Detection removes
   configured prohibited values; it does not classify every contextual secret.
   The engine must deliberately build a closed admitted representation from
   validated claim fields, rather than reuse the whole sanitized paragraph.
3. **Dropping `evidence_quote` alone damages correctness.** Current quantities
   revalidate against that field or `node.content`; condition/negation/time and
   attribution must survive statement projection. All necessary qualifiers are
   explicitly admitted with the statement or the statement is rejected.
4. **Existing `_claim_statement` is a useful formatter, not a privacy boundary.**
   It handles subject/predicate/object, negative polarity and a condition, but
   does not itself prove complete qualifiers, temporal intent or attribution.
   Extend/reuse its deterministic formatting after validated protected projection;
   do not call its output a semantic entailment proof.
5. **Source availability is an observation, not a key test.** Owner authorization
   precedes erasure, expiry, capture, classification, profile permission, key and
   payload readiness. An expired sealed source reports expired to an authorized
   metadata reader without probing the evidence key. Missing/foreign records use
   the same unavailable response.
6. **Successful memory must remain possible.** Directly admitted allowed
   sensitive facts, structured facts with qualifiers, exact quantities and
   ordinary framework retrieval must work while evidence stays sealed. Universal
   rejection of protected operations is not a passing implementation.

## Concrete read/copy/output trace

Line references are at the investigated revision; reconcile only affected seams
after the prerequisites merge. This is a direct trace, not runtime success.

| Path / copies | Concrete existing reader or copy and planned treatment |
| --- | --- |
| Claim rendering, C04/C05/C06 | `ingestion/pipeline.py:1412-1479` uses expanded quote or full event body as claim content, saves `evidence_quote`, condition, quantity `source_text`, and structured values. `:1480` resolution can carry original/replacement snippets from `ingestion/resolution.py:241-327,499-508`. Protected projection must run in `_prepare_plan`/`_populate` before `PlanningIndexes.prepare` (`planning.py:185-217`) captures nodes and calls `encode_texts`. Relationship claims enter the same loop at `pipeline.py:1370-1392`; entities, suggested scope, temporal_ref, speaker and resolution metadata need an explicit admitted-field decision too. |
| Direct store and binding copies, C04/C05/C07 | Reuse #320/#322 intake. `models/value_bindings.py:149-196` requires exact presentation membership in source and retrieval text; `:221-246` exposes only values whose presentation survives packing. Both presentation and lookup are intentionally admitted operational values when explicitly supplied; do not falsely label allowed direct-store values source-only. Evidence-only/generated unknown binding fields cannot acquire admitted status. |
| Primary retrieval, C04/C11/C14/C24/C25 | `retrieval/candidates.py:114-350,477-640` obtains entity/neighborhood, pinned, vector and lexical candidates, then hydrates full nodes. `pipeline.py:523,624` adds aggregation/entity lexical nodes outside the common hydrator, and alternate queries repeat hydration at `:1354`. A request-scoped admitted read view must cover every branch before reranking, temporal sends, result assembly or packing. |
| Expansion, C04/C24/C25 | `session_context.py:126-146` calls either native `get_session_neighbors` or fallback `query_nodes`. `evidence_context.py:70-106` resolves evidence refs by fetching source-ID nodes; projection/augmentation then replace/add whole source nodes. `episode_context.py:71-131` ranks the already supplied candidate content. In sealed/no-evidence mode all use admitted nodes; unavailable source refs add a limitation without opening the vault. Standard may use a separately authorized runtime source resolver for selected refs. |
| Packing and returned objects, C15/C25 | `packing.py:129-164` renders full/prose/structured content; `:1008-1021` marks conditional/epistemic state. `MemoryBundle.sections` and `rendered_context` both exist (`retrieval/models.py:439`); protecting the string alone is insufficient. Project candidate nodes/metadata before packing, retain provenance/qualifiers, and put a budgeted fixed source-availability notice in context and response metadata. |
| Temporal and answerability, C24 | `retrieval/temporal_relations.py` operates on the bundle; `answerability.py:427-490` uses exact bundle context/references and never reads storage itself. Test successful synthetic provider dispatch/return and provider failure with actual components. No engine/provider receives a diagnostic capability. External copies of a previously returned bundle remain the recipient's responsibility. |
| Exact state and aggregates, C05/C25 | `retrieval/temporal.py:89-105,137-211` reads every lifecycle state through engine scans, returns node content/evidence refs and contradiction links, and reads edges directly. `aggregation.py:237` uses structured claim values; `:390-417` revalidates exact quantities from evidence_quote or node content and `:374-378` returns quantity source_text. Keep admitted exact phrase/unit, condition, polarity and effective clocks; no diagnostic quote fallback. Projection must preserve complete paging and honest stored-set coverage. |
| Entity snapshots, C40 | `retrieval/snapshots.py:89-154` directly reads a node, neighborhood and edges, then joins content; the single-entity API has no owner parameter today. Add a protected scoped read route/owner parameter while preserving ordinary legacy calls. Bulk snapshots at `:164-209` must pass the same owner and scope. Snapshot output is admitted-only and uses #322 output guards. |
| Public graph reads, C25 | `engine.py:2537-2559` get_node, `:2682-2742` query/scan/iter and `:2851-2862` event derivations return full models, including retired nodes when requested. Apply saved-policy classification, scoped projection and disclosure fence; never trust a supplied metadata label. Filtering a page must not terminate iteration early or skip later admissible rows. |
| Event/extraction/history, C01/C08/C25 | `engine.py:2826-2850,2932-2944` returns Event/ExtractionRecord directly. `client.py:982-1015` forwards those results; history reconstructs source metadata. Protected ordinary returns a typed envelope/availability, with no source body, source hash, quote, raw extraction or original serialized message. Event derivation/status calls still work from opaque IDs and public processing counts. |
| Provenance and alias history, C16-C21/C25 | `engine.py:2864-2930` combines sources, raw operation pages and contradiction edges. `storage/provenance.py:43-66` decodes arbitrary payload; `models/provenance.py:14-25` returns `payload: Any`. `list_alias_proposals` (`engine.py:2651`) returns complete proposal/review objects; `storage/alias_review.py:56-75` includes duplicated proposal_payload. Public protected views allow only typed operation IDs/kinds/clocks/approved transitions and admitted pair/edge data; hide diagnostic payloads/hashes behind availability, preserving stored bytes for #324. |
| Receipts and related reads, C15/C23/C25 | `engine.py:3423-3467` forwards full receipts/relevance/citations; `models/relevance.py:18-77` includes query, candidate content hashes, context hash and execution. Protected inspection exposes a closed metadata manifest, availability and replay limits. Relevance/citation membership uses the internal saved candidate manifest; ordinary inspection never reconstructs context or decrypts sources. Treat raw historical evaluation/report features that lack the closed contract as explicitly unsupported in protected mode, not a route to full raw receipts. |
| Deprecated raw search | `engine.py:2455-2493` directly returns vector/lexical backend results. Protected mode must either project admitted result IDs through the same view or reject this deprecated raw feature with a fixed supported-path notice; ordinary `retrieve` must still succeed. It cannot silently call a raw source index. |
| HTTP/MCP, C26/C27 | `_node_to_response` (`api/routes.py:175-202`) includes all metadata. Event/extraction routes at `:324-380`, receipt at `:1154`, provenance/state/aggregate routes and retrieval candidates need protected model unions/projections. MCP `memory_get_event` (`server.py:1369`) promises original source today; `:1388` exposes saved extraction; `:1701` resource_node bypasses neither owner nor privacy. Update tool descriptions and tools/resources together. No ordinary tool mints or accepts diagnostic private authority. |
| Frameworks, C28/C29 | `visible_chat_events` (`integrations/_chat_history.py:17-57`) pages raw history; `serialized_chat_message` (`:87`) reads nested metadata. LangChain `.messages` (`langchain.py:211-241`) falls back to event.content. LlamaIndex `_message_from_event` (`llamaindex.py:309-324`) does the same; delete methods (`:259-300`) return removed messages. Refuse unsupported lossless reads/set/clear/delete before mutation or return explicit availability. Normal retrievers at `langchain.py:121-167` and `llamaindex.py:110-154` must return usable admitted text plus the source limitation. |
| Explorer, C30 | `web/server.py:53-90` checks the selected node through engine, then reads neighbors/edges directly from `_graph_store`, including retired ones. Route those through the common scoped projection. `web/static/app.js:375-397` renders `provenance.evidence_events[].content`; adapt it to protected availability states with no diagnostic unlock. Existing no-store middleware stays. |
| CLI, C25/C35 | `cli.py:256-455` nodes/node/edges/chain include full metadata, and edge/chain commands call raw graph methods. Use common ordinary views, require an explicit owner for protected inspection, preserve legacy operator behavior when privacy is unset. Search uses retrieval. #321's separate explicit local diagnostic action consumes selected grants; no ordinary command unlocks. Complete export/migration remains #326; at #323 release unsafe protected export must remain explicitly unavailable, never raw fallback. |
| Namespace boundary | `workspace.py:41-114` inherits/forwards public methods with lease fencing; it does not forward private storage. Use actual namespace identity and per-operation read context, not global state. New public methods must remain within lease lifetime. No workspace schema or registry redesign is needed. |
| Reopen/rebuild boundary, C37 | `engine.py:3201-3300` DuckDB rebuild reads direct SQL and embeds node content; single-file lexical startup rebuild follows `LEXICAL_DOCUMENT_SQL`. #323 establishes admitted-only newly persisted nodes/index input. #324 owns source/plan recovery hardening and #325 erasure fencing; identify old/unclassified data explicitly and never advertise migrated protection from a flag. PostgreSQL rebuild_indexes is unsupported today, so use actual restart/pgvector/index inspection there instead of pretending it ran. |

Relevant history inspected includes `6ce01efa` (plural first-person statement),
`d48769a0` (speaker grounding), `f05b0ff0` (claim sentence text), `2beab2c4`
(grounded quantity aggregation), `e0c1710e` (explorer), and the newer graph/owner
retrieval options. Preserve their ordinary defaults and historical record bytes.

## Bounded implementation design after release

1. **Closed source/read models and one request context.** Define a versioned
   SourceAvailability model with the exact eight RFC states and approved opaque
   identifiers/policy revision only. Add distinct protected Event, extraction,
   provenance, operation and receipt inspection envelopes; never mutate an old
   canonical record into a new-looking original. Ordinary legacy responses stay
   unchanged when privacy is unset. Public node/edge/state/aggregate results use
   safely projected admitted values with an availability sidecar where their
   source references are exposed. Protected metadata fields are closed and
   cannot be supplied by callers as authority. Bind each read to owner, actual
   namespace/scope and the saved policy; deny absent/foreign identities uniformly.
   No-owner protected public reads fail explicitly. Low-level trusted operator
   storage remains outside a hostile-same-process isolation claim.

2. **Admitted statement projection before all graph/index effects.** Add a
   deterministic protected renderer over grounded structured facts/relationships.
   Reuse exact speaker, negative-polarity, condition and temporal validators and
   existing formatting where valid. Keep subject/predicate/object, necessary
   condition, epistemic type, polarity, source attribution, effective/valid time,
   necessary exact quantity and opaque provenance links. Reject incomplete or
   unsupported qualifiers; do not fall back to a paragraph, claim sentence or
   free generated resolved_text. Strip evidence_quote, supporting passage,
   resolution original/replacement snippets, unknown fields and diagnostic-only
   metadata before planned node snapshots and embeddings. Entity/profile/summary
   inputs must be admitted values, with #322 generated-output admission still
   mandatory. This is origin/schema classification, not arbitrary semantic secret
   detection or a new entailment model.

3. **Keep exact quantity behavior without source decrypt.** The already
   source-validated exact quantity phrase is explicitly admitted only if it
   occurs in the admitted object and rendered statement, and the full original
   sanitized supporting passage passed the existing approximation/range checks.
   At read time, validate `object_decimal_v1` against the admitted statement and
   retained phrase/unit/value, never a hidden quote. Test omission/tampering and
   approximation controls. Prefer this existing validator path over introducing
   another proof format. If a representation cannot preserve those requirements,
   reject that quantity/claim according to the existing optional-quantity rules;
   do not silently erase all quantities or mark an approximate value exact.
   Legacy rows retain their existing quote validation and checksums.

4. **Admitted read view and explicit source resolver.** Use a request-scoped
   graph-read adapter/helper for get/batch-get/query/scan/neighborhood/edges/session
   neighbors and native fallback. Pass it to all protected candidate/expansion
   paths and snapshot consumers. It must preserve pagination and actual backend
   ownership/lifecycle filtering, and fail closed on unknown protected extension
   fields. Scope/availability resolution happens before provider sends and final
   output; fence again after awaited work. Sealed/no-evidence source resolution
   returns availability without touching an evidence provider. Standard may use
   the explicit runtime grant for the selected currently permitted sources;
   neither grant nor plaintext enters a global cache or ordinary model tools.

5. **Read projection and explicit reveal.** Event/history/extraction/provenance,
   alias inbox, receipt, exact-state/aggregate and value-binding outputs share the
   same projection and authorization. Existing raw journal data remains private
   to the journal reader; ordinary views expose content-free history descriptors
   and admitted values only. Add/consume a Python `inspect_source` and explicit
   selected-source read operation through #321's grant API, with a synchronous
   wrapper and the separate local diagnostic CLI action. Diagnostic reveal uses
   finite selected IDs, exact boundary/purpose, audit success and a final fence,
   returns labeled sanitized evidence, and never promotes it into graph truth.
   HTTP/MCP ordinary readers expose availability only; hosted diagnostic sessions
   and broker/authentication belong to the hosted ticket, not a new ordinary
   engine unlock endpoint. The actual names follow the merged #320/#321 API.

6. **Explicit source limitation with usable results.** Include a fixed,
   non-source-bearing notice in response metadata and the budgeted packed
   context when sources are sealed/not captured/unavailable. Do not invent a
   semantic classifier that knows every source-only question; the general
   limitation is truthful for all protected source-restricted bundles. Report
   it even with zero candidates. Keep existing epistemic/correction/time markers
   and `knowledge_at` current-graph limitation. Successful answerability/temporal
   fixtures operate on the admitted bundle; they have no vault route.

7. **Adapter siblings and unsupported features.** Forward protected types through
   sync Python, HTTP, MCP tools/resources, framework retrievers, explorer and CLI.
   Add a scoped public graph inspection method for explorer/CLI rather than
   exposing raw `_graph_store`. Unsupported lossless chat-history and binary/raw
   blob features reject before reads or control writes. Framework clear/delete
   remains logical history management in legacy mode and never claims erasure.
   Strict unknown history/receipt extensions deny or return a fixed unavailable
   envelope until classified; primary admitted retrieval/state/count/quantity
   operations must continue to work.

8. **Inventory, documentation and handoff.** Record new transient read envelopes,
   admitted metadata/rendering version and availability refs in the inventory.
   Save case/layout/code/library/fixture versions, actual commands/logs and
   failures for #327. Document allowed facts remain sensitive, ordinary default
   compatibility, source unavailability, permitted standard source use, explicit
   diagnostics and limitations. Do not promise physical erasure, anonymity,
   same-process isolation, exact replay or full product protection.

## Planned file scope

These are the bounded possible edits after prerequisite merges; omit unnecessary
ones rather than create changes for the list. Adding another implementation file
requires naming the concrete affected AC and seam to root first. No backend
schema, new custody implementation, sanitizer package, migration or erasure
implementation is authorized by this plan.

New runtime files:
- `src/prme/models/privacy_reads.py`: closed availability and protected inspection models.
- `src/prme/privacy/reads.py`: read context, projections and selected source access orchestration.
- `src/prme/privacy/read_graph.py`: admitted graph/query view with complete paging.
- `src/prme/ingestion/admitted_statements.py`: protected statement/metadata projection.

Existing runtime files:
- `src/prme/__init__.py`
- `src/prme/privacy/__init__.py` (from #320)
- `src/prme/privacy/cli.py` (from #321, reveal consumption only)
- `src/prme/storage/engine.py`
- `src/prme/storage/provenance.py` (public projection only, no journal rewrite)
- `src/prme/client.py`
- `src/prme/ingestion/pipeline.py`
- `src/prme/ingestion/planning.py`
- `src/prme/retrieval/pipeline.py`
- `src/prme/retrieval/candidates.py`
- `src/prme/retrieval/evidence_context.py`
- `src/prme/retrieval/session_context.py`
- `src/prme/retrieval/snapshots.py`
- `src/prme/retrieval/aggregation.py`
- `src/prme/retrieval/temporal.py`
- `src/prme/retrieval/models.py`
- `src/prme/retrieval/packing.py`
- `src/prme/api/models.py`
- `src/prme/api/routes.py`
- `src/prme/mcp/server.py`
- `src/prme/integrations/_chat_history.py`
- `src/prme/integrations/langchain.py`
- `src/prme/integrations/llamaindex.py`
- `src/prme/cli.py`
- `web/server.py`
- `web/static/app.js`

Tests and artifacts:
- `tests/fixtures/privacy/source-separation-v1.json` (fixed synthetic rows, before execution).
- `tests/privacy_source_support.py` (spy/marker helpers; reuse #320/#321/#322 support).
- `tests/test_privacy_source_separation.py` (statements, actual plans/indexes, quantities).
- `tests/test_privacy_source_reads.py` (all ordinary readers, history/receipt availability).
- `tests/test_privacy_source_routing.py` (real expansion, provider boundaries, limitation).
- `tests/test_privacy_source_interfaces.py` (public adapters and explicit reveal wiring).
- `tests/typing/public_api.py`
- `web/tests/test_explorer.py`
- `docs/SEALED-MEMORY.md`
- `docs/plans/2026-10-evidence-privacy/PV-04-plan.md`
- `docs/plans/2026-10-evidence-privacy/PV-04-results.json`
- `docs/plans/2026-10-evidence-privacy/content-copy-inventory.json`
- `docs/reviews/issue-323-source-separation-review.md` (root-scheduled review evidence).

## AC-to-validation mapping

| Original AC | Finite acceptance evidence |
| --- | --- |
| 1 admitted-only index/return | AC01, AC06, AC07, CL02 and the #323 subchecks of CL07. Real source envelope contains a distinct source-only marker, actual graph/edge/plan/index/provider/public-output scans do not, admitted control is retrievable. Include retired nodes, staged/abandoned index inputs, reopen and both DuckDB layouts. |
| 2 complete statements, diagnostic never truth | AP04 plus fixed Q01-Q08 below; graph/state/context and exact quantity totals agree with validated conditions, polarity, attribution and clocks. Diagnostic reveal changes neither graph truth nor indexes/ordinary response. |
| 3 all ordinary read siblings | AC01/AC04/AC08 with the explicit surface inventory; receipt/provenance/alias payloads and source hashes remain absent. Missing/foreign IDs indistinguishable and strict protected envelopes reject unknown content extensions. |
| 4 source-only limitation, no fallback | AC06 with every enabled route, decrypt trap and successful fixed external provider responses; unavailability in both rendered context and response metadata, including no-result path. Standard explicitly authorized source success is a control. |
| 5 same sanitizer/direct store | AP04/CL02/AC07 integrate actual merged #322; store source, optional retrieval text, names/metadata and bindings receive the same sanitization and source identity checks. A permitted sensitive direct fact still appears, an unrelated sealed source-only detail does not. |
| 6 explicit framework unsupported behavior | AC08: LangChain messages/set/clear and LlamaIndex get/set/delete/delete_last paths do not deserialize protected source or write controls before rejecting; profile remains unchanged. Both framework retrievers successfully return admitted text. |

## Finite validation plan

No command or scenario in this section has run. Root must provide the isolated
locked environment and dedicated PostgreSQL database after implementation release.
Do not create a shared environment, key store or DB during preparation.

1. Freeze one synthetic fixture file with separate admitted marker, source-only
   marker and sanitizer-prohibited marker, exact opaque identities and fixed
   clocks. Use actual #322 local sanitation and #321 crypto with test-owned keys;
   synthetic external extraction/embedding/provider responses only. Fakes cannot
   replace internal routing, backend writes, policy/fence decisions or projection.
2. Run the eight PV-04-owned scenario rows on each backend, with applicable
   DuckDB directory and single-file subchecks. Consume saved #321 AC02/03/05
   evidence and execute only the added adapter wiring on the final #323 revision.
   There are eight owned result slots per backend plus three capability wiring
   slots per backend, not a new 52-case suite. CL07/AC06 shared historical/erasure
   subchecks remain explicitly pending until their owners' evidence exists.
3. Add eight fixed qualifier/correction rows below to those scenario fixtures;
   they are AC2 regression subchecks, not new quality benchmark cases or extra
   counted scenario successes. Freeze text/expected structured values before
   running. Reuse existing exact fixtures where indicated.
4. Use a #321 evidence-private-provider trap that fails on *any* ordinary sealed
   lookup/unwrap, not merely a decrypt counter on the happy path. Correctly
   purpose-bound processing capability tests are separate and do not authorize
   archive access. Inspect actual bytes/logical storage after success, failure,
   restart and the bounded staged interruption; then explicitly unlock a
   selected source in the diagnostic test to prove that retained evidence exists.
5. Target new privacy modules and affected existing tests during development.
   On the final revision run the required full suite once with real PostgreSQL,
   `ruff check src/ tests/`, scoped formatting, strict public API mypy, wheel
   build and isolated installed-wheel import/version/py.typed smoke, and the two
   existing CI simulation configurations. Run framework selections only if the
   full run did not execute them with installed extras. Run `web/tests/test_explorer.py`
   explicitly because it is outside `tests/`. Run the frozen contract structural
   validator after inventory updates. Passing checks repeat only for affected
   changes or a concrete unresolved concern.
6. For the explorer's changed availability rendering, use agent-browser against
   task-owned synthetic state for desktop/mobile selection, retired neighbor and
   source-button flows. Verify the displayed availability and browser responses
   contain no source-only marker. Do not deploy. Save this small UI check with
   AC01; no new screenshot/design/audit project is included.
7. Save command, actual exit status, elapsed time, code/library/fixture hashes,
   backend/layout and failure log paths. A skip or unavailable PostgreSQL is not
   parity. Preserve unrelated failing evidence and distinguish it from this
   ticket's criteria. Root schedules required independent reviews of the exact
   final snapshot; do not spawn reviewers or duplicate their passing test runs.

Exact final commands, after root supplies the environment, follow current CI:

```text
uv run --no-sync python -m pytest tests/ -q
uv run --no-sync python -m pytest web/tests/test_explorer.py -q
uv run --no-sync ruff check src/ tests/
uv run --no-sync mypy --follow-imports=silent --ignore-missing-imports --strict tests/typing/public_api.py
uv build
uv run --no-sync python -m scripts.run_simulations
PRME_SCORING__FUSION=weighted PRME_PACKING__CONTEXT_FORMAT=auditable PRME_PACKING__MULTIPATH_ORDERING=balanced uv run --no-sync python -m scripts.run_simulations
uv run --no-sync python docs/plans/2026-10-evidence-privacy/validate-contract.py
```

Full pytest uses only root's task-specific `PRME_TEST_DATABASE_URL`; preserve the
existing Python 3.11-3.13 CI backend matrix. Installed-wheel smoke uses a separate
temporary environment and never overwrites another worktree's editable install.
The structural validator establishes references/coverage, not privacy correctness.

Existing focused compatibility evidence includes
`test_extraction_grounding.py`, `test_relationship_claims.py`,
`test_claim_sentence_text.py`, `test_claim_statement_fold.py`,
`test_speaker_grounding.py`, `test_temporal_validity.py`,
`test_quantity_aggregation.py`, `test_assertion_aggregation.py`,
`test_node_provenance.py`, `test_event_provenance_api.py`,
`test_evidence_context.py`, `test_session_context.py`, `test_episode_context.py`,
`test_temporal_relation_pipeline.py`, `test_packing_receipt_compatibility.py`,
`test_value_bindings.py`, API/MCP identity and integration tests, and existing
direct-store/rebuild/workspace checks. Use failures/changed seams to select
development reruns; the required final suite is the complete regression pass.
Do not regenerate receipt-v1 through receipt-v22 fixtures or old checksums to
make a changed protected format look compatible.

### Fixed qualifier/correction rows

| ID | Frozen intent before execution |
| --- | --- |
| Q01 | A named speaker's negated conditional preference at a stated effective time, with an unrelated source-only sentence in the same paragraph. The admitted statement preserves all four qualifiers and opaque provenance; full paragraph never enters graph or context. This is AP04. |
| Q02 | A trailing condition in a following sentence and a repeated quote with differing qualifications. Reuse `test_short_real_quote_cannot_remove_trailing_conditions`, `test_following_condition_sentence_still_qualifies_claim` and `test_repeated_quote_keeps_both_distinct_qualifications`; admit a complete supported form or reject, never flatten it. |
| Q03 | Explicit positive-to-negative correction and supersedence using the existing v14 correction fixtures: preserve the old retired admitted claim, new negative claim, effective validity and evidence IDs; diagnostic text must not reactivate the old value as truth. |
| Q04 | Future-effective correction with a half-open validity interval, queried before and at its boundary. Compare exact state and ordinary retrieval without turning ingestion time into effective time. Legacy half-open validity fixtures remain unchanged. |
| Q05 | An attempted/intended action with a valid speech-act predicate and a deliberately collapsed completed-state extraction. Reuse the paired attempt preservation/rejection fixtures in `test_extraction_grounding.py` and intention fixture in `test_speaker_grounding.py`. |
| Q06 | Hypothetical/conditional relationship versus a real observed relationship; preserve epistemic filtering and source attribution. Use the existing relationship qualification fixtures; diagnostic access and opaque source linkage do not make an unverified relationship an authoritative edge. |
| Q07 | One exact admitted decimal/unit phrase, an approximation/range control, and a tampered quantity object. Real aggregate totals/samples retain exact decimal/unit semantics without evidence_quote or unwrap; read-time tampering cannot bypass existing validation. |
| Q08 | Scoped first-person attribution and a multi-source correction: correct named/owner-bound source succeeds, ambiguous or foreign source reference fails. The source IDs remain provenance, while resolution original/replacement snippets and unrelated source passage do not become ordinary metadata. |

### Case-specific validation, success controls and ownership

| Case | Concrete validation and positive control |
| --- | --- |
| AP04 | Run Q01/Q02 through actual sanitized admission and protected rendering; authorized diagnostic shows the complete labeled sanitized minimal source, ordinary graph/index/context shows only the complete admitted statement and provenance. Too-small excerpt remains #320 AP05's explicit rejection. |
| AC01 | Enumerate async/sync get_event/get_events/get_extraction/status/get_event_nodes, get/query/scan/iter nodes, provenance and alias inbox, state, assertion/quantity aggregation, retrieval, receipt/relevance/citations; HTTP/MCP tools/resources; explorer retired neighbors and CLI node/edge/chain/search. A successful admitted node/claim remains visible in every applicable representation. Source-only body, quote, resolution snippets, raw journal payload and source hashes never appear. |
| AC02 (shared #321) | Through the real selected-source Python/CLI wrapper, grant two exact IDs and show only their currently available sanitized evidence. Verify successful authorization/outcome audit contains only IDs/codes, and reveal does not write graph/index/ordinary cache. Reuse crypto test evidence; no hosted session claim. |
| AC03 (shared #321) | Missing/expired/wrong-purpose/overbroad/unselected capability denies before private lookup/disclosure; valid selected control works. Ordinary MCP/HTTP inventories contain no diagnostic unlock tool. |
| AC04 | Interleave two actual project/namespace identities, owners and scopes on both backends. Replace each boundary and source ID independently; compare absent and foreign ordinary/API/MCP outputs byte-for-byte where transport permits. Authorized matching scope succeeds; no request field itself is a grant. |
| AC05 (shared #321) | Fail required audit authorization, remove test key version, and corrupt a test envelope. Actual reveal denies with fixed status/code and no plaintext exception; healthy key/audit control succeeds. Ordinary sealed reads still return admitted memory without probing the key. |
| AC06 | Source-only quotation request with session and episode expansion, augmentation, projection (mutually exclusive separate config subchecks), temporal reasoning, answerability and reformulation. Use actual components with fixed synthetic external responses plus denial controls. All sealed source decrypt calls are zero, transmitted context contains admitted text only, limitation survives packing/repacking and zero-result response. A separate standard request with exact runtime capability successfully receives available sanitized source, then denies at exact expiry. Shared journal-recovery assertion awaits #324. |
| AC07 | Deliberately admit an allowed sensitive synthetic fact under a scoped allow policy and verify actual vector/lexical retrieval returns it. Keep another marker only in evidence. Report admitted fact as sensitive usable data, not as anonymity or a leak failure. Direct store and extraction paths both get this control. |
| AC08 | Actual LangChain/LlamaIndex history reads, replacement, clear and delete paths reject/return availability before raw deserialization/control writes; profile snapshot is unchanged. Framework retrievers return admitted Documents/TextNodes and a source limitation. No fallback to serialized message metadata or event.content. |
| CL02 | Scan actual node/edge metadata, planned embeddings/lexical content, returned candidates, packed context and visible bindings. Long quote and resolution snippets stay source-only; admitted object/necessary condition/quantity phrase and explicitly sanitized direct binding remain usable. Caller/provider attempts to supply trusted source-role keys or unknown generated binding fields reject. |
| CL07 (shared #324/#325) | Inject one interruption before/after actual vector/lexical staging and native snapshot save, retry one work item and abandon another. Inspect SQL staging, USearch/Tantivy/pgvector logical inputs and native snapshots; no sealed source was indexed or saved there, admitted control survives supported reopen/rebuild. Keep the full frozen erasure subcheck pending until #325 executes it; plaintext absence does not prove physical removal. |

### Copy scans and deliberately excluded claims

Use the full C01-C42 inventory as the coverage checklist, recording relevant
copy, protected treatment, inspector and owner. Core #323 scans cover C01-C09,
C11-C15, C24-C30, C32, C37 and C40. For C16-C23, inspect the actual *public*
projection here and hand historical stored-payload obligations to #324; do not
call an empty public response a journal purge. C10/C31/C34 include fixed error,
log and task-owned temporary/index artifacts at the exercised boundaries. C35/C36
export/migration custody remain #326; C41 custody and C42 erasure remain their
owners. No unsupported future copy is marked passed by an empty scan.

Scan distinct synthetic markers in decoded logical state and outgoing payloads,
not just database file bytes. Inspect embedding provider inputs and native index
logical documents; numerical vectors cannot be certified by substring absence.
Encrypted evidence is expected to retain the source-only marker; inspect it only
under the explicit test diagnostic grant to establish successful capture. Removed
sanitizer originals must not reappear anywhere, including authorized decoded
evidence. Approved admitted values and necessary qualifiers are not forbidden
source-only markers. Computed hashes of predictable source-only probes are
checked for leakage, but never stored as production public transformation reports.

No generic confidentiality classifier, heap-zeroization, anonymity, malicious
same-process isolation, physical-media wiping, external-provider purge, deployment
WAL/backup deletion, exact historical replay, new import feature or hosted broker
is part of #323. New protected APIs stay experimental until the full privacy and
migration gates pass. The current CLI has no general import command; do not
invent one to pad adapter coverage.

## Readiness and remaining integration checks

The bounded plan is ready to reconcile against the actual merged prerequisites.
There is no unresolved product choice requiring a user prompt in this preparation.
The external wait is root's merge/release of #320/#321/#322, not permission to code
against their preparation documents. Verify the specific trusted-role, metadata
inspection and before-plan hooks above after rebase. If absent, report that seam
and its violated AC before expanding scope or writing a parallel mechanism.

#324's future typed journal manifests must fit these public read models; their
absence cannot postpone closing current read leaks. Whole historical content
lifecycle and erasure remain pending and must be recorded honestly in shared
scenario evidence. Real implementation tests, backend/runtime claims and final
review all remain `not_run`.

## Frozen scenario reference

The following table is appended directly from the reviewed manifest, preserving
its titles, setup and expected outcomes. Original owner arrays and copy IDs are
retained in the authoritative JSON. All rows remain `not_run`.

| ID | Frozen title | Frozen setup | Frozen expected outcome |
| --- | --- | --- | --- |
| AP04 | Minimal evidence preserves qualifiers | Use a source containing a conditional negated preference, named speaker and effective time with enough excerpt space. | Minimal sanitized evidence and admitted statement preserve condition, negation, attribution and time; the quote is labeled sanitized and linked to its own source ID. |
| AC01 | Every ordinary sealed read path | Store admitted fact plus source-only marker in sealed evidence; enumerate ordinary read adapters and retired graph inspection. | Python/HTTP/MCP event/extraction/history/receipt/node/provenance/state/aggregate/retrieval and explorer return allowed memory plus availability or denial, never marker or raw diagnostic metadata. |
| AC02 | Selected authorized diagnostic read | Use an explicit scoped unexpired capability for two selected records with working key and audit sink. | Only selected currently available evidence is returned, labeled sanitized; successful authorization/outcome audit contains no source or key. |
| AC03 | Missing expired or overbroad capability | Attempt no capability, expired capability, unselected record and wrong purpose. | All deny before decrypt/disclosure and produce content-free attempts; no ordinary tool mints a diagnostic capability. |
| AC04 | Cross-owner project and scope substitution | Substitute owner/scope/project and compare missing versus foreign record requests. | No disclosure or existence oracle; authenticated ownership and envelope context both bind the selected record. |
| AC05 | Audit or key unavailable | Fail audit append, remove key version or corrupt envelope on a permitted diagnostic request. | Fail closed with fixed reason/status and no plaintext fallback or exception-body source. Key loss is visible to authorized inspection. |
| AC06 | No source fallback through retrieval components | Ask a quotation/source-only query with session expansion, augmentation, temporal reasoning and answerability enabled; decrypt provider raises if called. | Sealed ordinary processing makes zero evidence decrypt calls and never transmits source-only marker; reports source unavailability. Standard source access is separately authorized while available. |
| AC07 | Permitted admitted facts remain sensitive | Deliberately admit an allowed sensitive synthetic fact and keep an unrelated source-only marker in sealed evidence. | Normal authorized retrieval returns admitted fact, not marker; report does not call admitted data anonymous or count it as a source-only leak. |
| AC08 | Framework raw history cannot change profile | Request lossless chat history/clear/delete through LangChain and LlamaIndex for a sealed source. | Unsupported raw-history operation rejects or returns explicit availability; never returns sealed removed messages or changes the store profile. |
| CL02 | Claim metadata and value binding copies | Extract claims with longer evidence_quote, references, structured objects and presentation/lookup metadata. | Only admitted sanitized claim fields enter ordinary graph/index/output; evidence-only passages and lookups cannot smuggle source-only text. |
| CL07 | Indexes snapshots and abandoned stages | Interrupt vector/lexical staging and native snapshot save; retry and abandon one work item. | No sealed source indexed or left in prepared/abandoned content. Inspect all live SQL/native/Tantivy/pgvector copies, then verify covered erasure; physical residue limitations remain explicit. |

Frozen artifact checksums:
- `privacy-scenarios.json` SHA-256: `e85223747c3f4464c98c306d38b54065504a6f063eb6c56bacc3c7cbd89e4d12`
- `content-copy-inventory.json` SHA-256: `40c0925819a37e19bd1624dae80e3edee8aa17dc0aa36058b0d41a1a7ea2b5a8`
- `sanitizer-contract.json` SHA-256: `ec1f5aa375269b33df33ead3c4b0c763a278a8887b3c250f47d19a1718664f7d`

Preparation accounting: six original AC bullets preserved; eight PV-04-owned
scenario IDs mapped, plus three #321-owned capability adapter cases; zero runtime
executions, zero repair cycles, zero model calls. Stop here until root releases
implementation.


## 2026-10-20 fixed-main retrieval handoff (PR339/PR340; preparation only)

This appendix records only the affected retrieval diff from
`976821c6b07e99775845f5ebfeeeb0fb6454be39` to
`4f23c01e53cc534c78f72421c0f62867ca1f8e98`. The latter merges PR340 head
`8c4b411674cbda83c10738b1633f9dff66fa40d4` after PR339 merge
`0ab2b06e021268a83a3eddc4e5cfcb2e562021f8`. Inspection used fixed Git objects,
not the shared root's working files. The original investigation revision and
contract hashes above remain unchanged. All source anchors below are relative
to `src/prme/` at `4f23c01e53cc534c78f72421c0f62867ca1f8e98`.

**Decision:** retain the accepted admitted graph view, closed public projections,
source resolver and denial-sentinel design. The diff adds a concrete edge-read
and exception seam within the already planned `retrieval/candidates.py` edits;
it does not require another runtime file, another feature, a default change or
an expanded validation cohort. The 43 possible files remain the upper bound of
the accepted plan. In particular, inspecting `retrieval/config.py`,
`retrieval/scoring.py` and `retrieval/episode_context.py` does not add them to
#323's edit list. Their behavior is consumed and preserved as described below.

### Exact affected hooks and ownership

| Fixed source hook | Effect on the existing plan and original AC |
| --- | --- |
| `retrieval/candidates.py:184-245`, especially `:212-226`; caller `:278-358` | `_seed_reach` adds a direct `graph_store.get_edges(node_ids=list(expandable))` read. Its known nodes include all seed/neighborhood nodes before the final candidate cap at `:343`, not only returned candidates. The #323 request-scoped view must authorize and project seed discovery, depth-aware neighborhoods, every expandable edge and both endpoints before this walk. A raw foreign/source-only edge must not affect even a numeric rank. Endpoint membership in this unscoped method is not itself an authority check. Preserve the existing fewer-than-two-seeds no-read path, three-hop bound and 5,000 expandable-node cap. This is the existing AC1/AC3/AC4 guarded-read obligation (AC01/AC04/AC06/CL02), not a new graph algorithm. |
| `retrieval/candidates.py:344-356` and outer `:653-704` | The new broad `except Exception` logs a traceback and retains graph candidates with `backend_error`; the outer `gather(return_exceptions=True)` also maps/logs exceptions and falls back. Re-raise the shared typed privacy/fence denial before either branch classifies or logs it. Protected fallback logging must be content-free. Preserve genuine backend-error degradation over already authorized candidates and the fixed `neighborhood_limit` result; do not convert an authority/sanitizer failure into apparent success or disable all successful graph routes. Root assigns physical changes to these guards, typed-denial propagation and protected fallback logging solely to #323. #322 supplies the sanitizer/provider contracts and its existing SN11 integration assertions when #323 is enabled, with no concurrent edits to this file. This enforces AC1/AC3/AC4 and the existing log-copy obligation. |
| `retrieval/candidates.py:168-181,210-218,234`; `models/owner_reference.py:12`; `ingestion/owner_name.py:35,42-44` | The walk uses trusted `owner_name` marker truthiness and fixed `identity_status=owner_reference` as owner traversal barriers, and `relation=alias` plus `identity_verified` to exclude unverified alias proposals. The existing internal admitted projection must preserve the approved structural facts needed for these decisions without admitting arbitrary metadata. The existing owner marker contains an event ID and fixed speaker/self-introduction basis; it is not a new source-text field. Public projections still follow the closed schema. Blanket metadata stripping would silently change traversal and supported behavior. #320/#322 own producing authorized/sanitized metadata and group links; #323 consumes those contracts. This refines AC1/AC2/AC3 and CL02. |
| `retrieval/models.py:382-386,447-469`; `retrieval/scoring.py:588-612,684-686` | The reach tuple is transient with `exclude=True`. The numeric graph rank requires at least two seeds, a direct link from one, semantic top ten membership and a non-entity candidate. No new source/provider read exists in scoring. Preserve the numeric tuple/rank and `inherited_score` replay behavior for admitted candidates; do not serialize the transient walk state, erase legitimate scores while projecting, or treat a score as source-access authority. No scoring implementation rewrite is needed for the read-view design. |
| `retrieval/session_context.py:181-184`, `retrieval/episode_context.py:149-152`, `retrieval/evidence_context.py:169-180,285-293` | Session, episode and evidence augmentation inherit a trigger's score with the seed-count graph term removed. Evidence projection keeps the full score because the source replaces claims. Apply source permission before existing source-node resolution/projection; the ranking distinction grants no access to a sealed source. Keep separate mutually exclusive projection/augmentation controls and successful admitted session/episode results. A separately authorized available standard source remains the positive projection/augmentation control. This is the existing AC4/AC06 routing obligation, with no new mode or cohort. |
| `retrieval/pipeline.py:545-556,1176-1178,1248`; `retrieval/models.py:721-730`; `retrieval/config.py:31-39,412-414,458-498` | The primary candidate call enables the walk from effective scoring settings. The operation payload and returned metadata add only the optional closed failure code `graph_seed_reach` (`backend_error` or `neighborhood_limit`), not the transient tuple. Keep this safe diagnostic separate from source availability in #323's public operation/metadata projection. Preserve the enabled switch, effective graph weight, config identity and numeric saved rank/provenance where required by #324's protected receipt manifest. The receipt model itself is unchanged by this diff and still supports legacy versions 1-22; do not add a new legacy receipt version or regenerate their bytes/checksums. The new operation key does not justify exposing its raw payload, query, source/candidate hashes or execution strings. This refines AC3/AC01 public provenance/receipt projection. |

The primary pipeline passes the new ranking flag; existing alternate-query
calls retain their existing default behavior. Every alternate read remains
inside the admitted view, but #323 must not silently change which algorithm
those calls select. Source-only reads still return the existing availability or
explicit unsupported response; a typed operational privacy denial remains a
denial through every fallback. Ordinary permitted admitted reads must work.

PR339 also changes `enable_owner_group_links` to a product default. Per root's
ownership ruling, its raw-source LRU remains explicitly unsupported for protected
execution; do not make that cache a #323 source fallback or add a group-extraction
implementation. Root/#320 owns main-merge compatibility and the newly identified
post-plan member-admission guard; #322 owns the changed ingestion/sanitizer hooks.
The #323 internal edge view may consume only their admitted trusted links and
approved opaque provenance. No feature/default flip is authorized here. A missing
upstream guard is an AC1/AC3 dependency gap to report, not permission to bypass it.

### Reconcile the same finite validation rows after implementation release

`retrieval/config.py:100-108` now enables seed-count ranking in product defaults,
while the field's historical default remains false (`:330-332`). The changed
`tests/conftest.py` autouse fixture removes this alpha scoring default for most
tests. Therefore the already planned successful AC01/AC04/AC06 and CL02 routing
subchecks must explicitly enable `fusion=rrf, rrf_graph_seed_count=True`; relying
on fixture defaults would never exercise the added read. This is a configuration
subcheck within the existing rows, not an extra counted scenario. Preserve the
product default and do not edit shared test defaults to make privacy checks pass.

Use the same frozen synthetic data's admitted entities/claim to supply two
matching entity seeds, an admitted non-entity directly linked to at least one,
and a deterministic external embedding result placing it in the semantic top
ten. Exercise real backend nodes/edges, actual candidate generation and scoring,
and the request-scoped view. The supported result must retain usable admitted
content and its real rank/provenance, with zero evidence lookup/unwrap in sealed
mode. Existing source-only/foreign marker substitutions must neither join the
walk nor change admitted results through an unauthorized edge. Preserve the
owner barrier and unverified-alias exclusion using trusted structural metadata.

Keep the already planned failure controls attached to these same rows: an actual
privacy/fence denial must escape both catch layers without raw error/log output;
a genuine backend fault may retain only already admitted graph candidates with
the fixed failure code and no invented graph rank. This supplements existing
successful provider dispatch/return evidence, not a blanket rejection strategy.
If an off-control is built from a populated scoring dump, remove its auto-filled
`rrf_graph_weight` as well as disabling the switch (`config.py:353-356`), or use
an explicitly constructed compatible off configuration. Otherwise that control
selects the old hop-count graph list and does not mean what its label says.

AC06 keeps its existing mutually exclusive session/episode/evidence expansion
subchecks and authorized standard-source control, now observing the correct
score inheritance. AC01's public operation/receipt checks keep the safe fixed
failure enum and required numeric manifest while excluding raw payloads. CL02
still checks actual planned/indexed/returned node and edge metadata; no raw
source marker or caller-supplied structural authority becomes admitted. Existing
required compatibility tests cover score/receipt semantics on the final revision;
this appendix adds no standalone evaluation or benchmark run.

All six original ACs, the eight PV-04-owned cases (AP04, AC01, AC04, AC06, AC07,
AC08, CL02, CL07), shared AC02/AC03/AC05 boundaries and Q01-Q08 remain unchanged.
The exact admitted-quantity validator remains the existing `object_decimal_v1`
path, with no parallel proof format. Shared AC06 recovery and CL07 erasure
subchecks remain pending their real #324/#325 integration; no full row may be
reported passed from this source inspection. The current root release/merge
sequence remains authoritative, with #321 custody and #322 sanitation dependencies
unchanged. This is ready for reconciliation after that release, not permission
to implement against preparation documents.

Appendix accounting: fixed-object read-only inspection and this ignored-document
append only; no source/test/worktree/branch/state edits, runtime tests, validation
repair cycles, model calls, cloud activity, commits or pushes. All runtime
success and integration evidence remains `not_run`.

## Implementation freeze, 2026-10-20

The source fixture was frozen before execution: `source-separation-v1.json` SHA-256 `8e5094399c848c66d9830ce3128f91ef25ba7889f4fd093792546fc50d7d0a09`. The eight qualifier rows are component subchecks of the accepted AP04/AC2 criteria, not additional counted acceptance cases. Full original multi-source, grounding/correction and backend assertions remain pending actual integration. Root approved the narrow shared `privacy/admission.py` delegate needed for AC3/AC4; #322 owns reconciliation of that edit.

Root approved `src/prme/retrieval/temporal_relations.py` solely to propagate typed PrivacyDenied through its two existing fallback catches (original AC4). Existing unrelated provider failures keep their fallback. Projection component check: initial 13 passed/1 failed because the test Event omitted required role; corrected cycle1 passed all14. Both logs are preserved under the assigned runroot. The missing pytest environment setup was installed only in this worktree using the lockfile. This is component evidence, not the eight-case backend acceptance matrix.

Root additionally approved `retrieval/temporal_relation_providers.py` for the
existing per-send guard and generated-output admission contract, and
`retrieval/reranker.py` for the local inference guard (original AC4/AC06).
Algorithms, defaults and legacy behavior remain unchanged. The shared native
SDK generator and content-free provider logging window remain owned by #322.
These changes reuse the already frozen positive routing and compatibility cases.

Deliberate dependency adoption: #322 checkpoints
`572e1d079f93e64bb1b7332282b88569c4982b3b` and
`8446fbb5cb29303331940a327aed2dac50253ecd` are local
`987c43e3` and `a6feadc8`; their shared admission changes were preserved.
They are prerequisite source, not independent #323 completion. The scoped
compatibility run (graph seeds, provenance, API provenance, both frameworks,
temporal pipeline and projection components) passed 95 tests with 13 skips.
The skipped checks are not backend acceptance evidence.

The next deliberate #322 source adoption is `9ce3af6c3b1c5d3081bc9bb4561a10678956dc1a`
as local `b419be6b`, after the PV-04 checkpoint `999ff393`. This brings the actual
pre-index projection hook, direct-node projection, conservative source-time
refusal, and shared guarded generator/logging window. Scoped legacy snapshot,
temporal provider and reformulation checks passed 48 tests. Public consumer
type checking passed after the protected response unions were declared.

Root confirmed the concrete local current-ledger authority is already owned by
#325 (accepted preparation-325 lines 92-100); hosted authority belongs to #280.
#320 supplied the contract. Any test using `FakeAuthority` remains component
integration evidence even when storage, sanitizer and cryptography are real.
The corresponding original authority/fence gates remain pending actual #325
integration. No alternative authority implementation is added to #323.

This is an intermediate implementation handoff to free the ticket slot for
required review. The actual two-backend PV-04 acceptance matrix, explicit
standard runtime source lease, protected profile/summary publication, backend
reopen/staging checks and final mandatory suite remain unfinished. AC06 recovery
and CL07 erasure remain pending #324/#325. No acceptance case or issue closure
is inferred from the passing component and compatibility checks.


## Reconciled read integration checkpoint, 2026-10-20

This appendix supersedes the intermediate implementation status above without
changing the original six criteria, frozen scenario IDs or Q01-Q08 bytes. The
source is frozen for required review and final checks, not issue closure.

### Deliberate source reconciliation

PV-03 prerequisites were adopted in dependency order: `4e0b3c9c` as local
`d0bacbb1`, `3c96baf8` as `a0898ffb`, `d179fd35` as `548b413f`, then the reviewed
expiry/grant-lease repair `a63e78a7b64810a4b127a00e9102732e393272aa` as
`7cc34172`. The first incomplete cherry-pick was aborted before that deliberate
chain. Both sanitizer and crypto exports remain lazy, both optional dependency
sets and pinned versions remain, and the public read delegate is retained.
PV-13 runtime/tests are the previously adopted `9ce3af6c`; `002fbbe` adds evidence
clarifications rather than runtime changes. Imported prerequisites are not
independent PV-04 delivery.

The final selected-source route decrypts only exact selected sources, then holds
PV-03's grant-only metadata lease through downstream work and return. Every
actual effect retains its effect-specific policy guard and post-wait deadline
checks. The rejected outer-policy-lock approach and PV-03's reproduced lock and
expiry failures remain preserved in that owner's repair evidence. The bounded
second approach passes the two-source, transmission denial, expiry and policy
activation component controls here. No authority clone or bypass was added.

The explorer factory and framework constructors forward explicit runtime and
embedding capabilities. Query encoding preserves the configured index identity
and asymmetric query-encoder requirement. No defaults or ranking algorithm
changed. The existing session fallback path was confirmed to swallow a privacy
denial and log its traceback. Root approved the existing planned path's narrow
PrivacyDenied propagation and protected content-free logging fix. It belongs to
the same generic-catch root-cause group as the temporal provider catch; each
confirmed site passed its first source-repair validation. No unresolved behavior
received a third fix-and-validation cycle.

### Actual component evidence and limits

The three source-read test modules contain 12 test functions with DuckDB
(directory) and PostgreSQL executions, 24 component executions. They use actual
storage, sanitizer, AES/RSA ciphertext, routing, grounding and packing. Only
external model responses, the OS key adapter and the explicitly named fixture
PolicyAuthority are replaced. These are not whole product acceptance passes.

- AP04/CL02: Q01-Q03 and Q05-Q08 exercise actual extraction, grounding, projected
  metadata, supported quantity aggregation, exact state reads and a successful
  entity snapshot. Named speaker, source event_time, conditions and negation are
  preserved. Q04 is conservatively refused before embedding. That refusal is
  not the planned positive future-effective before/at-boundary result; this
  specific original obligation remains pending. No future-date retention pass
  is claimed from event_time retention.
- AC01/AC04/AC06: ordinary event, graph, provenance, state, aggregate and retrieval
  responses are admitted-only; the evidence-open trap is never invoked. Actual
  two-seed graph traversal retains its numeric rank and excludes foreign or
  unclassified neighbors. Session and episode expansion, ordinary evidence
  projection/augmentation, standalone answerability over the admitted bundle,
  guarded native query reformulation and actual temporal HTTP dispatch pass.
  Query provider failure/retry, generated credential denial, effect-specific
  policy denial and asymmetric query encoding refusal are checked. Zero-result
  and zero-token responses still carry source limitation metadata.
- AC02/AC03/AC05 adapters and AC06: two selected standard sources use the finite
  grant with successful projection and augmentation; the unselected third source
  stays excluded. Diagnostic reads are labeled sanitized, cannot drive retrieval,
  and missing/unselected capabilities deny. Actual crypto/audit failure cases
  remain PV-03 evidence; these adapter positives do not replace it.
- AC07/AC08: intentionally allowed sensitive admitted memory remains usable.
  Public LangChain and LlamaIndex constructors execute actual MemoryClient
  retrieval; lossless raw history and destructive history controls explicitly
  refuse without changing the protected profile. Actual HTTP and MCP dispatch
  returns admitted memory and closed availability, with equal foreign/missing
  denial responses.
- CL07: interrupted pre-publication derivation, actual retry, an abandoned staged
  preparation, directory native snapshot interruption, file/logical SQL copy
  inspection and actual reopen retrieval pass on the tested layouts. This is
  not erasure or historical crash-recovery certification. Single-file DuckDB
  read/staging subchecks have not been run by PV-04 and remain pending.
- Explorer AC01: the task-owned actual app factory opened synthetic protected
  state. Desktop selection, mobile retired-neighbor navigation and both source
  buttons show sealed availability. Browser API responses exclude the source
  marker; the mobile viewport has no horizontal overflow. The local browser and
  server were closed. The policy/OS fixtures make this a component UI check.

Required affected adapter/framework/temporal/explorer compatibility: 60 passed
in 23.58s. The expansion run contained 30 passing checks (two protected plus 28
legacy) and two initially failing augmentation configuration assertions; those
specific assertions passed on the corrected existing top-three configuration
(2 passed, 10 deselected in 5.47s). All failures are retained. Earlier unchanged
14 projection, 95 compatibility/13 skips and 48 provider/snapshot results remain
historical evidence, not new backend passes. Full Ruff, strict public mypy and
the contract structural validator pass after dependency-import and moved-anchor
reconciliation. Final full pytest/build/wheel/simulations are still pending on
the genuine exact-head validation clone; no worktree checkout assertion was
weakened. Root schedules required reviews.

### Frozen email oracle adjudication

The original fixture from `717f1cf2`, SHA-256
`8e5094399c848c66d9830ce3128f91ef25ba7889f4fd093792546fc50d7d0a09`, retains
`privacy-fixture@example.test` unchanged. The original selected-source removal
assertion failed on both backends in `323-selected-positive-unmasked.log`.
The read-only `323-source-sanitizer-probe.py`/`.log` confirms the admitted source
retains it. PV-13 checked installed Presidio 2.2.364 and its pinned offline suffix
snapshot: `.test` is outside the email-v1 recognizer grammar. The original
removal assertion remains failed evidence, not a sanitizer success.

Root approved returning to the accepted plan's oracle: compare selected evidence
byte-for-byte with the actual admitted sanitized representation and assert
absence of values actually removed. The same case reuses frozen sanitizer-v1
`email-01` (`sample.person@example.com`) for a supported removal positive, while
asserting that the `.test` miss remains visible. The corrected selected-source
case passes on both backends (`323-selected-supported-oracle.log`, then the
extended augmentation case `323-selected-augmentation-cycle1.log`). No fixture
bytes, detector labels, thresholds, pinned recognizers or sanitizer runtime were
changed. This correction does not claim universal detection.

### Preserved failures and pending delivery

All complete logs are in the assigned runroot. The results artifact indexes
failed assertions, source repairs, setup issues and stronger oracle assumptions
separately. In particular: the snapshot fixture initially chose the first entity
instead of Alex; the framework fixture initially used an invalid UUID session;
the graph query initially supplied one recognized seed; the mobile text probe
initially compared unnormalized casing/line breaks. These are retained fixture
failures, not hidden product passes. The actual temporal/session denial failures
and original `.test` removal failures remain distinct.

Pending original gates: actual PV-06 local current-ledger/fence/erasure authority;
Q04 positive future-effective before/at-boundary behavior; single-file PV-04
layout subchecks; PV-05 protected profile/summary publication, durable receipts
and AC06 historical recovery; CL07 covered erasure; final mandatory checks and
independent review, actual merged-dependency reconciliation, push and PR.
Protected owner-group and reingestion routes remain explicit refusals. No live
model calls, long benchmarks, new cohort, publish, deploy or issue closure.


## Approved Q04 and single-file repair checkpoint, 2026-10-20

Root reviewed the concrete Q04 proposal after checkpoint `b0e64283` and released
only the original AC2 repair in `ingestion/grounding.py`, `privacy/admission.py`
and the existing resolver in `ingestion/pipeline.py`, followed by the already
planned single-file layout subchecks. No new cohort or temporal classifier was
added. The preceding pending Q04 and single-file statuses are historical.

The private qualifier gate now returns a checked copy of the extraction. It
requires a unique exact source-present temporal_ref directly preceded by the
existing starting/beginning cue, with no other recognized temporal cue outside
that reference. The coordinator supplies the trusted source event_time or event
timestamp. Only this supported forward-start path requests future resolution.
The existing resolver retains its past default and shared parser lock; forward
resolution uses the same dateparser to require complete day/month parts, reject
conflicting numeric date orders, and verify that the cited passage has exactly
one matching parsed temporal span. Missing refs, borrowed cues, incomplete or
ambiguous dates and competing source dates fail closed. The validated UTC ref
then follows the existing resolved_date/event_time/valid_from materialization
path. Source bytes and the caller's extraction remain unchanged. No projection,
legacy schema, old plan bytes, detector or product defaults were changed.

Frozen Q04 is now an actual positive through sanitizer, crypto, ingestion,
storage and ordinary reads. With its frozen Oct 19, 2026 source clock, the source
"Starting October 21, Alex uses TeaDB." resolves to 2026-10-21T00:00:00+00:00.
The stored claim retains that event_time, valid_from, resolved_date and opaque
source reference. Exact state is unknown one microsecond before the boundary
and single at it. Ordinary retrieval with an explicit time_to boundary excludes
the claim before and includes it at the boundary; the source limitation remains.
This does not claim that reference_time alone is an effective-time filter.
The prior missing-ref refusal is retained as a negative control. The old blanket
refusal is no longer used as proof of positive effective-time retention.

Exact preserved evidence in the runroot:

- `323-q04-positive-before-repair.log`: 2 failed, 6 deselected in 3.10s; the
  original protected date gate prevented actual positive Q04 ingestion.
- `323-q04-repair-cycle1.log`: 55 passed, 6 deselected in 13.49s. This covers both
  backend qualifier cases, existing temporal validity/half-open boundaries,
  historical ingestion and saved recovery behavior, shared parser locking and
  existing projection tests. The first source repair resolved the positive gap.
- `323-q04-competing-span-before-repair.log`: 1 failed, 13 deselected in 1.02s.
  The required ambiguity control found that the original cue regex alone did
  not catch an additional numeric source date. This failure is preserved; the
  bounded fix reuses the existing parser's span search under the same lock.
- `323-q04-single-file-final-targeted.log`: 18 passed, 35 deselected in 26.94s.
  It covers the Q04 controls and actual Q04 backend paths, shared-parser checks,
  plus all 12 existing source-read/routing/adapter test functions in the
  single-file DuckDB layout. The existing CL07 interruption also covers its
  actual vector_snapshot row, copy inspection and reopen. The suite now contains
  36 layout executions of the same 12 functions: directory DuckDB, single-file
  DuckDB and PostgreSQL. This does not add scenario IDs or certify erasure.

The exact targeted command was
`pytest tests/test_privacy_source_reads.py tests/test_privacy_source_routing.py tests/test_privacy_source_interfaces.py tests/test_privacy_source_separation.py tests/test_shared_temporal_parser.py -q -k 'single_file or Q04 or AP04 or mixed_parser or mixed_parsing'`,
using the assigned worktree environment and PostgreSQL database. Scoped Ruff,
strict public mypy, diff whitespace and refreshed contract anchors are checked
on this repair. Unrelated passing checks were reused; final full-suite, build,
wheel and simulations were not started before root receives this handoff.

Repair accounting remains one successful source-repair validation for positive
Q04 and one successful bounded ambiguity repair; no unresolved behavior received
two unsuccessful repair cycles. Previous .test detector/oracle failures and all
other evidence remain unchanged. Actual PV-06 policy/fence/erasure acceptance,
PV-05 protected publication/receipts/recovery, full CL07 erasure, final mandatory
checks, independent review and publication remain pending. The new checkpoint
is suitable for deliberate adoption by the #324 branch stacked on `b0e64283`.

## Reviewed dependency composition, 2026-10-20

Root authorized composing reviewed #321 head
`d8b75a078195aabc89c6a41a4ab506e6399ba1a3`, including actual merged #322
ancestry, into the frozen Q04 head `01cd5a4d`. This is a normal two-parent
merge so the reviewed dependency ancestry remains available. No new behavior,
acceptance criterion, scenario or broad test rerun belongs to this step.

The twelve additive conflict dispositions are:

- Preserve the complete approved upstream #321 review record and SANITIZATION.md
  evidence, rather than the older copies inherited by this branch.
- Preserve approved PV-13 plan/results, including explicit FakeAuthority and
  product-case qualifications. Add only a separate Q04 integration qualification
  that points to the existing PV-04 evidence; original SN outcomes stay intact.
- Take upstream pyproject.toml and uv.lock byte-for-byte. Their previous local
  difference was optional-extra order only, with no version or package change.
- Take upstream privacy/__init__.py byte-for-byte, retaining both crypto and
  sanitizer lazy exports. The top-level #323 read exports remain intact.
- Retain the checked #323 grounding, admission delegate and Q04 tests. Admission
  keeps all reviewed writer guards, original snapshots, sanitized source binding
  and final admitted-projection hooks. Preserve both existing public consumer
  type functions and the #323 read signatures.
- Retain all PV-03/PV-13 inventory values and all 45 copy IDs, plus the intended
  PV-04 annotations and source anchors. Restore upstream C41 field order.

`323-dependency-composition-conflicts.log` and `.patch` preserve the initial
merge conflicts. `323-composition-source-audit.json` checks 685 prior tracked
source/test/web files: the only byte change is the reviewed lazy-export module.
All Q04/read, sanitizer, crypto/custody, storage and test bodies retain the
passing checkpoint bytes. Twenty-two dependency paths, including the reviewed
review record, dependency manifests and all existing privacy modules except
the deliberately extended admission coordinator, match upstream exactly. Every
existing non-anchor inventory value is preserved. The dependency-relative scope
is 39 files after removing five inherited-only differences.

`323-composition-types.log`, `323-composition-ruff.log` and
`323-composition-contract.log` pass strict public mypy, changed-export Ruff and
the structural contract. `323-composition-exports-2.log` passes all 20 privacy
exports, seven #323 read exports and actual admission/read/pipeline imports.
The first import probe referred to a nonexistent class name; its ImportError
is preserved in `323-composition-exports.log`. Correcting that probe to the
existing ReadContext required no product change. Previous runtime evidence is
reused; no detector inference or broad behavioral rerun was added.

Root will receive the exact merge head/tree and both parent-relative diffs in
the current handoff before scheduling final reviews and the required full suite.
Actual #325 authority/erasure and #324 durable publication/recovery remain
pending; this composition is not issue closure or publication authorization.

## Required review repairs authorized after 41e2cad5

Root read the immutable CORE and SECURITY reports and approved only the following
original-criterion repairs, with a later specific DATA classification finding:

- CORE-323-01 (AC2): preserve admitted effective-time qualifiers in both protected
  snapshot text paths and their generated-source input. Reuse existing reader
  time formatting; unprotected text/bytes and frozen Q04 source remain unchanged.
- SEC-323-1 (AC1/AC3): prevent ordinary A/B reads from returning A after its memory
  deadline passes during B's later authorization await. Reuse ReadContext and
  its final disclosure guard, checking all contributing deadlines after waits
  and guard exits. No lock spans a nested graph/provider operation or a streaming
  consumer yield. Selected evidence retains the existing grant-only lease.
- DATA-323-01 missing classification (AC3/AC6): an owner or
  unscoped read with privacy-marked events/nodes but no admission row must fail
  closed, using the existing metadata-only protected-range detector. No migration,
  row repair, policy authority or cache is introduced; legacy positives remain.

Finite validation first reproduces each issue on actual public paths with the
existing frozen Q04 and backend/layout fixtures. The A/B regression uses the
existing authority fixture with its real nonreentrant lock, a live positive,
deadline-minus/at controls and an awaited B authorization. These are additional
subchecks of existing AC01/AC03 and Q04, not new source/scenario cohorts. Then
run one bounded source correction and affected tests, existing legacy snapshots,
and selected-source lease/effect controls. Preserve each failed log and immutable
review. Each behavior starts its first review-repair cycle; previous Q04 source
resolution/ambiguity successes remain distinct and do not certify snapshot text.
Actual #325 authority/scoped-revocation/erasure remains pending.

Root interrupted the known-incomplete head41e full suite: 679 passed, 11 skipped,
exit 2 at 129.81 seconds. This is not a passing full suite. Root-owned packaging,
Ruff and wheel checks at41e remain saved under validation-323. No broad/full
checks will be repeated by this worker; root schedules final validation and
affected review after the cohesive repair handoff.

### Review repair implementation and evidence

All three findings have a first successful bounded source repair. CORE-323-01
uses one protected snapshot-node formatter and the existing reader time label
for summary_text, render_snapshot_text and generated-source inputs. Legacy
snapshot bytes retain the content-only path. No temporal parser or frozen Q04
source was changed in this repair.

SEC-323-1 uses ReadContext.disclose for completed graph/public/snapshot results,
with all contributor lifetime checks after awaited authorization and after guard
release. The existing scoped policy guards are acquired only after the result is
assembled. No policy lock wraps nested graph/provider work, and iter_nodes checks
each item before yielding without holding a lock across the caller's suspension.
Selected evidence still uses its existing grant-only lease and effect-specific
guards. The component tests do not establish actual #325 scoped revocation.

DATA-323-01 reuses require_legacy_range and its existing marker-aware SQL before
choosing raw owner or unscoped paths. The SQL accepts an unscoped range without
reading content. Missing admissions and malformed event-only/node-only markers
deny; a separate legacy owner remains readable. Only disposable test state loses
its admission row to reproduce an interrupted enrollment; product code performs
no data rewrite, migration or cleanup. The narrowly added storage/privacy.py
path is this metadata classifier extension, within root's specific authorization.

Preserved reproductions and first repair results, all in the runroot:

- `323-review-core-security-reproduction.log`: three actual CORE Q04 snapshot
  failures and three setup-only policy-identity failures. Both protected text
  outputs visibly omitted the date; the original source/clock fixture is intact.
- `323-review-security-data-reproduction.log`: six setup failures because the
  probe used event IDs as node IDs. These are not target-behavior reproductions.
- `323-review-security-data-public-reproduction.log`: six actual public-path
  failures in 6.96s: three SEC deadline violations and three DATA orphan-marker
  bypasses. Together with CORE, these are nine actual failed executions.
- `323-review-repair-cycle1.log`: 9 passed, 9 deselected in 14.45s. All three
  findings across directory DuckDB, single-file DuckDB and PostgreSQL. A/B checks
  include live positives, deadline-minus/at boundaries, awaited B guard entry/exit,
  graph/scan/query/history/provenance siblings and iterator resumption. DATA checks
  cover owner and unscoped siblings plus malformed event-only/node-only markers.
- `323-review-repair-read-compatibility.log`: 9 passed, 27 deselected in 15.34s;
  existing ordinary reads and selected-source/denial routes on all three layouts.
- `323-review-repair-snapshot-compatibility.log`: all 23 existing legacy snapshot
  tests passed in 1.31s.
- `323-review-repair-ruff.log`, `323-review-repair-types.log`,
  `323-review-repair-contract.log`: scoped Ruff, strict public types and structural
  contract pass; seven moved anchors were updated without changing mappings.
  Diff whitespace passes.

The initial compatibility selector accidentally matched every test name and was
stopped after four passes at 10.78s. Its interrupted exit-2 log remains
`323-review-repair-compatibility.log`; it is not a completed check. The two
explicit targeted commands above are the completed compatibility evidence.

No behavior received an unsuccessful source-repair cycle. Setup corrections are
identified separately, not hidden or relabeled as product failures. The initial
CORE/SECURITY/DATA reports remain immutable. Earlier date resolution/ambiguity
results and the .test detector/oracle failure remain separate historical evidence.
The two new regression functions are subchecks of existing AC01/AC03, not new
source cohorts or acceptance IDs. Full suite and affected/adversarial reviews
remain root-owned; #324 durable publication/recovery and #325 actual authority/
erasure remain original pending integration obligations.

## Upstream windowed-extraction composition

Root authorized a normal merge of exact main
`b5fa5be5294dbf28e63536fce4836dad8644561e` (PR352) into the approved
`eb4576463e5f00f1e8849c539ec627c9ed4bf484` checkpoint. All four required
#323 reviews approved that checkpoint; their evidence remains unchanged. This
composition preserves upstream session/owner windows, reingest fences and
settings, plus the existing protected routing, sanitizer and Q04 grounding.
No acceptance criterion, source fixture, default or feature is added.

The finite composition checks are the affected extraction-window and reingest
tests plus the existing real protected Q04 ingest/read and applicable protected
ingestion/provider checks. The actual private grounding call is in
PrivacyCoordinator.extraction_plan; protected queued work routes through resume
before the legacy _extract_or_load method. Upstream's moved _extract_turn must
not bypass or replace that protected route. Concrete merge conflicts, if any,
will be recorded and resolved only at these seams. Contract anchors and public
types are checked if the moved source affects them.

Root verified all ten required PR352 CI checks passed on head1f462591, merged at
20:13:38Z. Byte-identical upstream-only config-audit, fast-cycle and PostgreSQL
event-store paths reuse that evidence rather than repeat passing checks. The
root-owned full suite on eb457646 was intentionally interrupted after 1,074
passes and 12 skips because main advanced; it is not a passing full suite or a
repair failure. Its evidence remains in
`validation-323-review-repair/final-interruption-upstream352.json`. Root will run
the final combined suite once after this frozen composition and affected review.
The original #324 and #325 integration gates remain pending.

The normal merge completed without conflicts. Sixteen of nineteen upstream
files are byte-identical to main. The composed files are configuration
documentation (the existing privacy-key section), pipeline.py (the unchanged
Q04 resolver alongside the upstream extraction refactor) and engine.py (upstream
constructor options and docstrings alongside existing protected public routes).
No runtime correction was required. The actual protected coordinator, grounding,
read, projection, snapshot, crypto and sanitizer files retain their approved
hashes. Twenty-three source anchors moved; only their line numbers changed,
using unchanged-line mappings from eb457646.

Completed composition evidence in the runroot:

- `323-upstream352-window-reingest-final.log`: 103 passed, 69 deselected in
  40.87s. The existing extraction-window/reingest tests and applicable privacy
  reingest fences ran against DuckDB and PostgreSQL.
- `323-upstream352-protected-ingest.log`: 8 passed in 19.78s. The existing
  AP04/CL02 Q04 and qualifier/snapshot case plus CL07 staging/retry/reopen ran
  across all three layouts; the two existing native provider guard/plan-boundary
  checks used an external MockTransport. This remains component authority
  evidence; it does not certify #325 or the whole CL07 erasure/recovery case.
- `323-upstream352-public-types.log`, `323-upstream352-ruff.log` and
  `323-upstream352-contract.log`: strict public types, lint on the composed
  runtime files and structural contract pass. Diff whitespace passes.

The first test invocation included unavailable `--no-cov` and exited before
collection; its command/log remain `323-upstream352-window-reingest.json` and
`.log`. The corrected invocation changed only that command argument. The initial
stale-anchor report is preserved as `323-upstream352-contract-before-anchors.log`.
Neither is a runtime failure or an unsuccessful source-repair cycle. All earlier
failed evidence, approved reviews and pending full-suite/dependency gates remain
separate and unchanged. No new cohort, live call or broad full run was added.

## Legacy chat-history fixture compatibility

At pushed checkpoint `4f1deb7784d8ebb67688fcd0f3db9b74ee810d3f`, CI run
39495180277, integration Python 3.13 job 118878294206, failed the three existing
pagination/clear/version tests because their _PagedEvents double lacks the
MemoryClient.require_legacy_history method. The production helper deliberately
calls that method unconditionally under original AC3/AC6. The immutable initial
log is `323-ci-integration313-initial.log`: 3 failed, 45 passed in 22.19s.

Root authorized one bounded test-only repair: explicitly authorize the fixture's
legacy owner, record that authorization and assert it precedes every page read.
Retain all pagination, latest-clear and unknown-version assertions. Production
guards and interfaces remain unchanged; no optional guard bypass is introduced.
This is the first source/test repair cycle for this compatibility behavior.

The finite validation plan is the complete existing test_integration_chat_history,
test_integrations_langchain and test_integrations_llamaindex files, plus Ruff on
the changed test. No public types change, new cohort or full-suite run is needed
from this worker. Root released the assigned environment after interrupting the
known-obsolete full suite at 1,966 passed and 46 skipped, exit 2 at 361.39s; that is
not a completed pass. The root-owned interruption and full output remain saved
in `final-interruption-ci-fixture.json` and its validation log. Root owns affected
review, push/CI and final full validation. Original #324/#325 gates stay pending.

The first bounded repair passed all 48 tests in the three integration files in
9.61s on this worktree's Python 3.11.6. Exact command/output and the initial CI
log checksum are saved in `323-ci-fixture-repair-cycle1.log` and `.json`.
`323-ci-fixture-ruff.log` records the changed test's passing Ruff check. Every
production file and existing test assertion is preserved. The fake requires
exactly one authorization for alice before any event page and the three tests
assert the recorded owner, including the unknown-control-version refusal.
There are zero unsuccessful repair cycles for this behavior. Python 3.13 CI
and the final complete suite remain root-owned pending checks, not inferred
from this local Python 3.11 pass.

## Full-suite classification and bounded second DATA repair

CI run 39496148208, Python 3.13 DuckDB job 118881583879, completed with 9 failed,
6,229 passed, 1,268 skipped and 2 xfailed. The immutable log is
`323-ci-duckdb313-fullsuite-initial.log` (SHA256
89e61f9a60bf429f6b96bc370b8a63c14dd30f8582f6b3cf05c657059e9407cf).
The AP06 failures reach the old public extraction-is-None assertion after
processing/evidence removal has passed. The bounded test update will additionally
assert the native extraction store has no record while asserting the closed
public unavailable/not_captured view required by original AC3. Existing payload
removal checks remain unchanged; this is AP06's first test-contract correction.

The seven other CI failures expose a real compatibility regression: the first
DATA-323-01 repair added owner/global legacy-range checks even for an exact legacy
object whose metadata already establishes its independent boundary. Its focused
orphan tests passed, but that repair strategy has now failed the full suite.
This is the second DATA/classifier repair cycle, not a reset counter. Root
authorized reusing records_for_objects for exact calls at both disclosure edges,
retaining range checks for broad/ambiguous calls, and checking every returned
legacy event-node identity before its raw result. Protected routing and final
deadline fences remain unchanged. No new authority, cache or migration is added.

Finite validation reuses the three failed test functions across their original
layouts, the existing DATA orphan and final-deadline cases, then the existing
affected read/routing compatibility cases. The orphan fixture additionally checks
its same already-marked event/node identities through exact public calls. No
frozen source or cohort changes. After two unsuccessful DATA repair cycles, stop
that strategy and report instead of starting a third.

Separately, the local full suite at source54aa261 terminated with SIGABRT (-6)
after 1162.61s in engine.close at the DuckDB connection close, called by the
existing AC06 selected-deadline/effect-denial test. Its log hash is
84f4ca9314f9350740b94ca63bd0a84060a24ada256e40807ae38bfb9e13e921.
The 17 F markers are not final failure details or pass totals. Preserve the crash
separately; root released the assigned DB only after confirming termination.
Inspect and reproduce the same AC06 teardown path before any further full suite;
no generic close rewrite or broad test run is authorized. Root owns affected
review, publication and final validation. The #324/#325 gates remain pending.

### Second DATA cycle stopped; approval required for an alternative

The finite validation did not complete. `323-data-repair-cycle2.log` records
nine passes before a single SIGINT, exit 2 after 149.31s (150.35s wrapper time).
The planned 22 executions are not a passing batch. The process held the database
for the existing directory/scope reinforcement fixture when it stalled;
`323-data-repair-cycle2-stall.json` preserves the PID, exact command and live
file-descriptor evidence. PID 42570 is now absent. Ruff and the structural
contract passed, but neither certifies the runtime correction or the added
orphan assertions that the interrupted batch did not reach.

Static inspection identifies a likely lock conflict: the exact protected event
read creates a ReadContext from all owner records, including an unrelated scope.
ReadContext.guard then enters one policy guard per scope while holding the prior
guard. The existing FakeAuthority has one nonreentrant lock. The interrupt stack
only identifies asyncio.runners.py:123; no captured coroutine stack proves the
precise wait site. Preserve that distinction in the handoff.

The first DATA classification repair passed focused orphan checks but regressed
mixed legacy calls in the full suite. This second correction restored the first
nine executions but stalled the existing mixed-scope call. The same availability
requirement remains unresolved after two unsuccessful cycles. All current
runtime/test changes stay uncommitted and uncertified. Root instructed this
worker to stop and will request the user's approval before a third strategy.
There is no new implementation, validation run or relaxed assertion after that
instruction. The AP06 native-store assertion and public unavailable view change
remain a separate first test-contract correction; the interrupted aggregate
does not certify the complete batch or whole AP06 requirement.

The proposed bounded alternative is to seed exact-object disclosure contexts
with the authoritative contributing records already resolved for those objects,
and let existing node/event projection add and recheck actual dependencies.
The named seams are ReadContext.create/project_public_result in privacy/reads.py
and _ordinary_graph/get_event_nodes in storage/engine.py. Exact identity
resolution in storage/privacy.py stays authoritative. Broad or ambiguous calls
retain marker-aware classification; all final contributor deadlines, selected
grant leases and effect-specific guards remain intact. No authority/cache or
lock replacement is proposed. Reuse the same five test functions, including
their original scope/owner boundaries and final-deadline checks. This proposal
does not certify general multi-scope authority composition and is not authorized
for implementation yet.

The separate native-close investigation remains read-only while approval is
pending. `323-native-close-readonly.json` establishes that MemoryEngine.close is
byte-identical in upstream b5fa5be5, validated54aa261, committedc42af439 and the
current working source. That does not establish a baseline defect or exclude an
introduced caller/resource-lifetime problem. No crash reproduction or close
repair was run. Preserve the local SIGABRT independently from CI's nine failures
and this interrupted targeted batch. Root owns subsequent review/full validation
and publication; original #324/#325 gates remain open.
