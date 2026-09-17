# TypeSafe Jev product-alignment advisor

PRME includes an opt-in advisor for deciding whether two caller-selected
software-product records merit an unverified alias proposal. It uses TypeSafe
Jev's typed Score and Noul responses, pins `jev-1.13.0`, and validates the exact
response schema before returning a result. It does not scan the memory store,
select candidate pairs, or merge identities. An explicit node-pair workflow can
publish positive advice as an unverified graph proposal.

Set `JEV_API_KEY` in the environment or in the current directory's `.env` file,
then compare a pair:

```python
import asyncio

from prme.integrations.typesafe import advise_product_alignment


async def main() -> None:
    advice = await advise_product_alignment(
        {
            "name": "Adobe Acrobat Standard 7.0 Windows",
            "manufacturer": "Adobe",
            "price": "299.00",
        },
        {
            "name": "Adobe Acrobat 7 Standard for Windows",
            "manufacturer": "Adobe Systems",
            "price": "289.99",
        },
    )
    if advice.proposal_recommended:
        print("queue this pair for identity review", advice.assessment_sha256)


asyncio.run(main())
```

Reuse one client for a batch so connections are pooled:

```python
from prme.integrations.typesafe import JevProductAdvisor

async with JevProductAdvisor() as advisor:
    first = await advisor.compare(left_product, right_product)
    second = await advisor.compare(other_left, other_right)
```

To bind a positive assessment to existing PRME product entities, pass the exact
node IDs and product records:

```python
proposal = await memory.propose_product_alignment(
    left_node_id,
    right_node_id,
    {
        "name": "Adobe Acrobat Standard 7.0 Windows",
        "manufacturer": "Adobe",
        "price": "299.00",
    },
    {
        "name": "Adobe Acrobat 7 Standard for Windows",
        "manufacturer": "Adobe Systems",
        "price": "289.99",
    },
    user_id="alice",
)
if proposal.proposal_published:
    print(proposal.proposal_edge_id, proposal.proposal_applied)
```

`MemoryClient.propose_product_alignment()` provides the same workflow without
`await`. The standalone async function remains available from
`prme.integrations.typesafe` for dependency injection and testing.

Both nodes must be active `ENTITY` nodes owned by the supplied user, share the
same scope and compatible provenance metadata, carry
`metadata={"entity_type": "product"}`, and have content exactly matching the
corresponding product name. The caller still selects the pair; this operation
does not scan the store.

`ProductEntity` accepts only `name`, `manufacturer`, and `price`. Name is
required; the other fields may be empty. Fields remain strings because the
confirmed protocol did not normalize currencies, infer units, or establish
numeric equivalence. Jev receives all three fields. Do not send records that
your data policy prohibits an external service from processing.

## Result and audit boundary

`JevProductAlignment` includes:

- the pinned model, protocol, question, and non-secret configuration hashes;
- separate hashes for both inputs and the complete request;
- the three-level relationship score and complete probability distribution;
- field-level probabilities for same name, same manufacturer, and compatible
  price;
- token use, attempts, elapsed time, and a deterministic assessment hash; and
- `proposal_recommended` plus `automatic_merge_authorized=False`.

The frozen proposal rule is `link_state.score >= 1.5`. A positive result is a
candidate for review. `propose_product_alignment()` can atomically publish it as
an unverified `RELATES_TO` edge and a checksummed `ALIAS_PROPOSED` version-2
record. The record retains both node snapshots, both complete product inputs,
the complete typed assessment, and its hashes. Publication revalidates owner,
scope, active lifecycle, metadata compatibility, and the exact assessed node
checksums while holding the backend transaction. Both entity nodes remain
active. A negative result returns without graph or journal mutation.

The unordered node pair has one durable proposal identity. A matching
assessment-request retry returns the first committed assessment and edge without
rewriting history. A preexisting legacy or version-1 proposal cannot be relabeled
as Jev-backed; the evidence-aware call fails explicitly. External assessment
data cannot bypass PRME's provenance or merge-policy checks.

The client retries HTTP 429, 500, 502, 503, 504, and 529 responses and transport
failures up to the configured limit. It fails closed if the service returns a
different model, answer set, legend, invalid probability distribution, or token
usage. Custom endpoints must use HTTPS. Credentials are excluded from model
representations, hashes, errors, edge metadata, and journal records. The product
records and complete Jev response are stored locally in the version-2 journal,
so applications should apply their data policy to both the external request and
the local audit record.

## Evidence

The protocol was frozen after a balanced 400-pair development trial, then run
unchanged on 400 previously untouched validation/test pairs from the DeepMatcher
Structured Amazon-Google dataset.

| Method | Precision | Recall | Accuracy | False-positive rate |
|---|---:|---:|---:|---:|
| Exact normalized name | 83.33% | 2.50% | 51.00% | 0.50% |
| Jev score >= 1.5 | 96.30% | 78.00% | 87.50% | 3.00% |

All 400 held-out responses were schema-valid. Median latency was 0.178 seconds
and p95 was 0.330 seconds with six concurrent requests. The development cohort
was directionally consistent at 94.38% precision, 75.50% recall, and 85.50%
accuracy.

These results validate proposal advice for candidate software-product pairs.
They do not validate candidate generation, automatic merging, people,
organizations, places, or arbitrary entity metadata. The full preregistration,
runner, immutable results, checksums, and reports are under
`benchmarks/results/research/2026-09-17/` and `benchmarks/integrations/`.

See TypeSafe's [introduction](https://docs.typesafe.ai/introduction), [API
reference](https://docs.typesafe.ai/api), and [entity-alignment
cookbook](https://docs.typesafe.ai/cookbooks/entity_alignment) for provider
concepts. PRME's exact pinned protocol and its evidence boundary take precedence
over mutable provider examples.
