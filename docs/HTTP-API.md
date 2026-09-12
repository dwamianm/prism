# HTTP source fidelity and recovery

The running server publishes its complete schema at `/openapi.json` and interactive
reference at `/docs`. Configure per-user bearer credentials as described in the
[README](../README.md#http-api). An authenticated owner can omit `user_id`; selecting
another owner returns 403. Source/node lookups belonging to another user return 404.

## Store supplied memory

`POST /v1/store` accepts the Python `store()` fields: `content`, `role`, `user_id`,
`session_id`, `node_type`, `scope`, `metadata`, `epistemic_type`, `source_type`,
`confidence`, `event_time` and `ttl_days`.

```json
{
  "content": "The telescope recorded the observation successfully.",
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

Materialization processing accepts `{"budget_ms": 5000}` and returns `processed`,
`pending` and `failed` counts. With zero budget it only inspects counts. The budget
is checked between operations; a started operation can exceed it. It processes
one bounded batch for the authenticated owner, computes missing embeddings when
needed, and never invokes LLM extraction. Repeat passes for remaining work after
resolving persistent failures. Operator-mode callers must specify `user_id`.

Malformed UUIDs and invalid request fields produce HTTP 422 with structured
validation details. Valid unknown or foreign identities return 404.

## Save relevance judgments for future evaluated learning

Retrieval `metrics` include `request_id` and `receipt_persisted`. If the latter is
true, `GET /v1/retrievals/{request_id}` reads the saved candidate score traces,
content hashes, configuration and context membership through the authenticated
owner. A logging failure does not fail retrieval; it sets the flag to false.
Legacy requests without a receipt return 404.

New receipts use `schema_version: 2`. Their `score_provenance` map contains applied
weights, base features and ordered neural/session adjustments for each returned
candidate; `ranking_policy` records the actual sorting rule. Python's
`RetrievalReceipt.model_validate_json(...)` and `replay_ranking()` validate and
replay this saved ranking without the current graph. Version 1 receipt JSON and
checksums remain unchanged and still support labels, but cannot replay scores.
These snapshots cover returned candidates only, not unseen retrieval candidates.

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
