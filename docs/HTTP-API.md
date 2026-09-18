# HTTP source fidelity and recovery

The running server publishes its complete schema at `/openapi.json` and interactive
reference at `/docs`. Configure per-user bearer credentials as described in the
[README](../README.md#http-api). An authenticated owner can omit `user_id`; selecting
another owner returns 403. Source/node lookups belonging to another user return 404.

## Review identity proposals

`GET /v1/alias-proposals?status=pending` lists the authenticated owner's
decoded unverified alias proposals. Optional `scope`, `status`, and `limit`
filters apply after owner isolation. Each item includes the complete proposal
journal and its accepted or rejected review when present.

`POST /v1/alias-proposals/{proposal_operation_id}/review` accepts one decision:

```json
{
  "decision": "accepted",
  "reviewer_id": "human:catalog-owner",
  "reason": "The source catalog confirms one licensed product."
}
```

Acceptance creates a verified traversable alias link while leaving both entity
nodes active. Rejection requires a reason and creates no link. Identical retries
return the first decision; conflicting decisions and stale assessed nodes return
HTTP 409. The API never turns Jev advice into an automatic merge.

## Store supplied memory

`POST /v1/store` accepts the Python `store()` fields: `content`, `retrieval_content`, `role`, `user_id`,
`session_id`, `node_type`, `scope`, `metadata`, `epistemic_type`, `source_type`,
`confidence`, `event_time` and `ttl_days`.

```json
{
  "content": "The telescope recorded the observation successfully.",
  "retrieval_content": "Telescope observation: success.",
  "role": "tool",
  "source_type": "tool_output",
  "epistemic_type": "observed",
  "session_id": "observation-42",
  "scope": "project",
  "event_time": "2024-03-10T07:30:00Z",
  "confidence": 0.81,
  "ttl_days": null,
  "metadata": {"instrument": "cobalt"}
}
```

`event_time` requires a timezone offset. `confidence` must be finite and between
zero and one. Omitting `ttl_days` uses the configured default for the node type;
JSON `null` disables TTL, and a nonnegative integer overrides it. TTL is measured
from node creation, not the historical event time. Omitted source/epistemic fields
use the engine's inference rules. Source classification describes provenance;
it does not independently establish truth.

When `retrieval_content` is present, PRME retains `content` verbatim in the
immutable event and uses the compact value for the graph node, vector and lexical
indexes, retrieval results and packed model context. Omit it for the historical
one-string behavior. The projection is recovered from the checksummed direct
store journal and never regenerated after restart.

The response contains the immutable source `event_id`, its `node_id` when available,
and `processing_status`. A completed status means the direct node and indexes
were materialized. It is separate from the assertion's epistemic classification
and memory lifecycle. An index outage can leave a saved node with pending work;
the response exposes that state.

Node reads retain session, event time, validity dates and TTL. Retrieval result
items retain source classification, scope, session, event/validity dates and
`evidence_refs`. Resolve those event UUIDs with `GET /v1/events/{event_id}` to read
the original source, or `/nodes` below that event to inspect its derived nodes.

## Extract memory from a message

`POST /v1/ingest` accepts `content`, `role`, `user_id`, `session_id`, `metadata`,
`scope`, `event_time` and `wait_for_extraction`. Extraction is asynchronous by default. Set
`wait_for_extraction: true` to wait for the configured extraction pipeline.
Inspect saved extraction work with `GET /v1/events/{event_id}/extraction-status`.
For historical messages, provide `event_time` with a timezone offset, such as
`2024-03-10T01:30:00-06:00`. A phrase such as “yesterday” is resolved relative to
that source time. The immutable ingestion timestamp still records when PRME
received the message. Omit `event_time` to use ingestion time as the relative-date
anchor. This does not change previously saved extraction plans or infer an
unknown source date.

Both write endpoints reject unknown fields with HTTP 422. Earlier HTTP versions
silently discarded extra fields; clients relying on that behavior must correct
their payloads. The former `namespace` field was never applied and is now rejected.
Scope and authenticated owner controls retain their documented meanings; metadata
and session labels are not additional authorization boundaries.

## Aggregate grounded quantities

`POST /v1/quantities/aggregate` accepts an owner and a structured
`QuantityAggregationQuery`. It performs a complete owner-scoped scan for an
unchanged store, revalidates every decimal against its claim and evidence, and
never converts units. Optional `predicate_prefixes` match only the normalized
predicate itself or an underscore-delimited suffix.

`POST /v1/quantities/aggregate-text` accepts `{"user_id": "alice",
"question": "How much did I raise?"}`. This separate convenience route supports
only a fixed set of complete, qualifier-free amount/count shapes. Its response
always exposes the structured plan and assumptions and preserves the exact `I`
or `we` subject. Unsupported wording returns
`plan.status="unsupported"` and `aggregation=null` without scanning memory.
Ordinary retrieval never auto-routes to either operation.

## Recover an accepted request

If graph materialization or blocking extraction fails after source admission, the
server returns HTTP 503 with a receipt such as:

```json
{
  "detail": "Source saved; processing did not complete. Inspect its status and retry the saved work.",
  "event_id": "11111111-1111-1111-1111-111111111111",
  "reason_code": "TimeoutError",
  "accepted": true
}
```

The reason is a sanitized category; provider messages are not returned. Inspect
the referenced source and work status instead of resubmitting the content, which
would create another event. A receipt-read failure can occur after processing
already completed, so check status before deciding whether work needs retrying.
A general service-unavailable response without `accepted: true` is not this receipt.
Network failures can still leave admission uncertain; these routes do not yet
support client idempotency keys.

| Work | Inspect | Process or retry |
|---|---|---|
| Supplied node or raw-source indexes | `GET /v1/events/{event_id}/processing-status` | `POST /v1/materializations/process` |
| LLM extraction and its derived artifacts | `GET /v1/events/{event_id}/extraction-status` | `POST /v1/events/{event_id}/retry-extraction`, then `POST /v1/extractions/process` |

For raw imports, `POST /v1/ingest/fast` accepts `{"user_id": "...", "items":
[...]}`. Each item can set `content`, `role`, `session_id`, `scope`, `metadata`,
and a timezone-aware `event_time`. The resolved owner applies to the complete
ordered list, which is validated before I/O and committed with its repair jobs
as one transaction. The response returns `event_ids` in input order and an
`accepted` count. A UUID `Idempotency-Key` header binds the exact ordered inputs
to the resolved owner. Retrying it returns the original IDs after restart;
changed inputs return `409`. Call the materialization processor until `pending`
is zero; do not resubmit IDs whose admission already succeeded.

Materialization processing accepts `{"budget_ms": 5000}` and returns `processed`,
`pending` and `failed` counts. With zero budget it only inspects counts. The budget
is checked between operations; a started operation can exceed it. It processes
one bounded batch for the authenticated owner, computes missing embeddings when
needed, and never invokes LLM extraction. Repeat passes for remaining work after
resolving persistent failures. Operator-mode callers must specify `user_id`.

Malformed UUIDs and invalid request fields produce HTTP 422 with structured
validation details. Valid unknown or foreign identities return 404.

## Correct an outdated claim

Store the corrected source first, resolve both event IDs to node IDs, then call
`POST /v1/supersedences`:

```json
{
  "old_node_id": "11111111-1111-4111-8111-111111111111",
  "new_node_id": "22222222-2222-4222-8222-222222222222",
  "evidence_id": "33333333-3333-4333-8333-333333333333"
}
```

The response returns the superseded old node followed by the active replacement.
All nodes and optional evidence must belong to the authenticated owner and one
scope. The state, edge, and checksummed audit record commit atomically. Sending
the exact body again is safe after a timeout or restart; changing its evidence
after publication returns 422.

`PUT /v1/nodes/{node_id}/promote` and
`PUT /v1/nodes/{node_id}/archive` accept a UUID `Idempotency-Key` header. Reuse
the same key after an ambiguous response; a matching retry returns the current
node and a key reused for different lifecycle inputs returns 409. Without the
header, repeated transitions keep the strict state-machine error behavior.

## Save relevance judgments for future evaluated learning

Retrieval `metrics` include `request_id` and `receipt_persisted`. If the latter is
true, `GET /v1/retrievals/{request_id}` reads the saved candidate score traces,
content hashes, configuration and context membership through the authenticated
owner. A logging failure does not fail retrieval; it sets the flag to false.
Legacy requests without a receipt return 404.

`POST /v1/retrieve` accepts `max_per_source` as an optional positive integer.
Candidates with the same exact nonempty evidence set and byte-identical content
share that cap; later source groups fill the requested `limit`. Equal text from
distinct events remains distinct. The response metrics report the applied value,
and excluded candidates use reason `source_limit`.

`max_per_evidence` is the broader optional bound. Candidates with the same exact
nonempty evidence set share this cap even when their text differs. Nodes without
evidence are not grouped. Response metrics and receipts retain the applied value,
and excluded candidates use reason `evidence_limit`.

Current pipeline receipts use schema version 12 and explicitly retain packing
ordering, context guidance, context format, episode-routing settings, and the
configured current-update multiplier. Version 9 score provenance records any
applied `current_update` operation and its exact coefficient. Version 10 retains
the evidence-projection policy and replayable `evidence_projection` operations;
versions 1–9 mean projection was disabled.
Version 11 retains the mutually exclusive evidence-augmentation policy and its
replayable score operations; versions 1–10 mean augmentation was disabled.
Version 12 records the augmentation anchor policy; versions 1–11 mean `all`.
Historical schema versions 4 and 5 introduced density/score and balanced
ordering; version 6 introduced guidance, and version 7 introduced context format.
Their `score_provenance` map contains applied
weights, base features and ordered neural/session adjustments for each returned
candidate; `ranking_policy` records the actual sorting rule. Python's
`RetrievalReceipt.model_validate_json(...)` and `replay_ranking()` validate and
replay this saved ranking without the current graph. Version 1 and 2 receipt JSON
and checksums remain unchanged and still support labels; version 1 cannot replay scores.
These snapshots cover returned candidates only, not unseen retrieval candidates.
Versions 1–7 mean episode routing was disabled and omit its three configuration
fields when serialized. Versions 1–8 mean the separate current-update multiplier
was disabled and omit it from configured and applied scoring weights.
Versions 3 and later also include `execution.parameters` and `execution.features`, recording
request filters/adjustments and reported model/environment identity. These fields
do not establish that a remote model is pinned. The explicit ranking-adjustment
trial argument is currently available through Python; HTTP reads its saved receipts.

`POST /v1/relevance` accepts this body after a user explicitly judges a result:

```json
{
  "feedback_id": "11111111-1111-4111-8111-111111111111",
  "request_id": "22222222-2222-4222-8222-222222222222",
  "labels": {"33333333-3333-4333-8333-333333333333": true},
  "surface": "results",
  "method": "explicit_user"
}
```

Use actual IDs from retrieval. Reusing `feedback_id` with the same judgment returns
the original record and timestamp. Conflicting reuse or an unknown candidate
returns 400. A missing or foreign receipt returns 404. Owner mismatches return
403. Non-boolean labels, empty labels and malformed UUIDs return 422. If the
caller omits `feedback_id`, the server generates one; callers needing safe retries
after a lost response should choose it before sending.

`surface` is `results` (returned candidates) or `context` (included bundle entries).
Positive context labels require source content; reference-only entries do not
qualify. `method` is `explicit_user` or `structured_evaluation`. These are supplied
classifications, not verified identities. Unlabelled candidates are not negatives.

Read a record with `GET /v1/relevance/{feedback_id}` or list with
`GET /v1/relevance?limit=100&after_id=...`. Pages sort by feedback UUID, not creation
time; a concurrent insert can precede the cursor. Records retain the original
exposure even after graph changes. Collection does not modify facts, change
weights, or establish learned quality. The legacy global feedback tuner does not
consume these records; evaluated scoped learning remains pending in RFC-0017.

## Record answer-time memory citations

`POST /v1/answer-citations` records which content-bearing entries from one saved
retrieval context supported an application answer:

```json
{
  "citation_id": "44444444-4444-4444-8444-444444444444",
  "request_id": "22222222-2222-4222-8222-222222222222",
  "answer_id": "assistant-message-42",
  "cited_node_ids": ["33333333-3333-4333-8333-333333333333"],
  "answer_sha256": "1f3c2b1a00000000000000000000000000000000000000000000000000000000",
  "method": "application_verified"
}
```

`answer_id` is the caller's stable answer reference. The optional digest binds
the record to answer bytes without storing answer text. `method` is
`model_reported`, `application_verified`, or `human_verified`. These names report
how the citation set was obtained; the service does not independently inspect the
answer. An empty `cited_node_ids` list explicitly records an answer with no memory
citations and remains distinct from missing telemetry.

Every cited node must be a content-bearing entry in the saved context. A result
that was omitted during packing, a reference-only entry, an unknown candidate,
or a foreign receipt is rejected. Reusing `citation_id` with identical input
returns the original record and timestamp; changed input returns 409. Missing or
foreign receipts return 404, and owner mismatches return 403.

Read one record with `GET /v1/answer-citations/{citation_id}` or page owned
records with `GET /v1/answer-citations?limit=100&after_id=...`. Records bind to
the receipt and rendered-context checksums, and remain valid if a cited memory is
later changed or archived. Citation telemetry does not mutate memories or ranking
and does not establish that an uncited memory has zero causal value.

## Confirming a memory

`PUT /v1/nodes/{node_id}/reinforce` accepts an optional UUID `Idempotency-Key`
header and optional body `{"evidence_id": "<event UUID>"}`. Keep the same key,
node and evidence for retries; changing the request under a used key returns 409.
A key is scoped to the node's owner within its memory namespace. The response
contains the current node, including any changes made since the original call.
Without a key, each call is a separate confirmation. See
[confirmation and retry semantics](REINFORCEMENT.md).
