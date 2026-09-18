# Evidence-bound temporal relations

Questions such as “How long passed between these events?” need more than
topical retrieval. A reader must align the question to the right events,
interpret each event's date, and perform arithmetic. PRME can now do that as an
opt-in post-packing stage while retaining exact evidence and a fixed token
budget.

```python
from prme import MemoryEngine, PRMEConfig, TemporalRelationConfig

config = PRMEConfig(
    temporal_relation=TemporalRelationConfig(enabled=True),
)

async with MemoryEngine.open(config) as engine:
    response = await engine.retrieve(
        "How long passed between finishing and hanging the frame?",
        user_id="alice",
        reference_time=question_time,
    )

outcome = response.metadata.temporal_relation
if outcome and outcome.status == "accepted":
    print(outcome.value, outcome.evidence_ids)
```

Set `JEV_API_KEY` or `TYPESAFE_API_KEY` for the independent gate. The confirmed
resolver runs through Ollama, whose default endpoint is
`http://127.0.0.1:11434` and whose pinned tag is
`deepseek-v4.1-flash:cloud`. Server processes can enable the same configuration
with:

```shell
export PRME_TEMPORAL_RELATION__ENABLED=true
```

The feature is disabled by default. Disabled and non-temporal retrievals make
no resolver or Jev calls. Enabling it sends the temporal question and already
packed record representations to the configured Ollama resolver, then sends
the proposed operand alignments and exact evidence quotes to TypeSafe Jev. It
does not send unrelated memories outside the packed bundle.

## Execution boundary

The stage runs after ordinary candidate generation, ranking, selection, and
context packing:

1. The resolver selects the minimum operands, cites packed node IDs, and copies
   exact temporal phrases. Its schema has no answer or arithmetic field.
2. PRME rejects unknown IDs, repeated records, non-verbatim quotes, unsupported
   date precision, unlicensed use of `event_time`, mixed duration units, future
   elapsed-since values, and same-date chronological ties.
3. PRME computes supported date differences, order, absolute dates, explicit
   duration sums, and schedules in code. Decimal duration arithmetic is exact.
4. Jev evaluates whether every quote dates the named event at the proposed
   expression. Every operand must meet the fixed `0.85` threshold.
5. PRME repacks only the records in the original bundle. Cited records keep
   their selected representation. The derived guidance must fit inside the
   same token budget; only uncited records may be displaced.

The inserted block labels the result as inferred, identifies every evidence
node, and asks the downstream reader to verify the interpretation. It is query
context only. PRME does not persist it as a fact, mutate graph state, or treat a
model probability as truth.

## Outcomes and failure behavior

`response.metadata.temporal_relation` is present only when the enabled stage
runs. Its `status` is one of:

| Status | Meaning |
| --- | --- |
| `accepted` | Local validation, the semantic gate, and bounded repacking passed. |
| `unsupported` | The resolver found no safe operation and cited no operands. |
| `validation_rejected` | At least one citation or computation failed a local invariant. |
| `gate_rejected` | One or more operand alignments scored below the configured threshold. |
| `packing_rejected` | The relation and its cited records could not fit safely. |
| `provider_error` | The resolver or gate failed, timed out, changed identity, or returned an invalid response. |
| `empty_context` | Retrieval packed no records, so no provider call was useful. |

The default `failure_policy="fallback"` returns the original bundle byte for
byte on any provider failure and records only the provider stage and exception
type. Set `failure_policy="raise"` when an application requires the enrichment
to succeed. Rejections also return the original bundle. Credentials and raw
exception messages never enter retrieval metadata or receipt hashes.

Accepted and rejected executions retain the provider/model names, Ollama model
digest and version, request and response hashes, attempts, schema-repair count,
token usage, gate probabilities, threshold, evidence IDs, context hashes,
dropped uncited IDs, and elapsed time. The same structure is stored in the
owner-scoped retrieval receipt and exposed under HTTP/MCP retrieval `metrics`.
`confirmation_protocol_aligned` means the declared endpoints, models, model
digest, prompts, options, repair count, and threshold match the registered
confirmation. It does not promise that an opaque hosted service is bitwise
reproducible.

## Evidence and limits

The frozen 104-question held-out LongMemEval-S confirmation compared the
ordinary PRME context with this composition using the same reader and judge.
The control answered 50/104 correctly and the temporal-relation arm answered
60/104, with 10 paired wins, no losses, and 94 ties. All five abstention
controls remained correct. The accepted 22-question subset improved from 11 to
21 correct. See the
[registered confirmation report](../benchmarks/results/research/2026-09-18/LONGMEMEVAL-S-TEMPORAL-RELATION-CONFIRMATION-V2.md)
for frozen inputs, identities, failure recovery, alignment audit, and study
limitations.

That result supports this exact composition on the tested cohort. It does not
establish universal temporal accuracy, provider availability, or superiority
over other memory products. A changed resolver model, digest, endpoint, gate,
or threshold remains usable when explicitly configured, but
`confirmation_protocol_aligned` becomes false. Evaluate changed configurations
on the intended workload before relying on their output.
