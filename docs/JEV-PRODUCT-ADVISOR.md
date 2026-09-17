# TypeSafe Jev product-alignment advisor

PRME includes an opt-in advisor for deciding whether two caller-selected
software-product records merit an unverified alias proposal. It uses TypeSafe
Jev's typed Score and Noul responses, pins `jev-1.13.0`, and validates the exact
response schema before returning a result. It does not scan the memory store,
publish graph edges, or merge identities.

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
candidate for review or for PRME's unverified `RELATES_TO` proposal path. It is
not proof of identity. The integration deliberately has no mutation capability,
so an external model response cannot bypass PRME's owner, scope, lifecycle,
provenance, or merge-policy checks.

The client retries HTTP 429, 500, 502, 503, 504, and 529 responses and transport
failures up to the configured limit. It fails closed if the service returns a
different model, answer set, legend, invalid probability distribution, or token
usage. Custom endpoints must use HTTPS. Credentials are excluded from model
representations, hashes, and errors.

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
