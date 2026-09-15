# Choosing a memory context packing policy

`PackingConfig.multipath_ordering` controls which ordinary multi-path memories
get space first. The default is `"balanced"`, selected after completed source
retention studies and two complete answer trials. Use `"density"` or `"score"`
explicitly when your workload evaluation favors one of those policies.

```python
from prme import MemoryClient
from prme.client import config_from_directory
from prme.retrieval.config import PackingConfig

config = config_from_directory("./my_memories")
config = config.model_copy(update={"packing": PackingConfig(token_budget=4096)})

with MemoryClient(config=config) as memory:
    memory.store("The pilot can launch only after approval.", user_id="alice")
    response = memory.retrieve("When can the pilot launch?", user_id="alice")
    print(response.bundle.render())
```

The same configuration applies to `MemoryEngine.open(config)` and servers using
that config. Set `PRME_PACKING__MULTIPATH_ORDERING=density` for an explicit
density override. This is an engine configuration, not a new per-request HTTP
or MCP parameter.

| Policy | Ordering within the ordinary multi-path tier |
|---|---|
| `density` | Composite score divided by full-entry token cost. |
| `score` | Composite score. |
| `balanced` | Highest composite score first, then score divided by full-entry token cost to the power 0.25. |

Ties use node ID. Instructions, pinned memories and active tasks keep their
existing higher priority; single-path candidates keep their existing lower tier.
Every entry still passes the same measured whole-context budget and fidelity
checks. Reserving the first position does not guarantee that its full text fits:
it can become a reference or be excluded. References do not contain source text.
Call `response.bundle.render()` for the exact budgeted text; the surrounding
API response and the application's other prompts consume additional tokens.

For stores with many short records, opt into the compact renderer:

```python
config = config.model_copy(update={
    "packing": PackingConfig(token_budget=4096, context_format="compact")
})
```

It emits a declared JSON-array schema and short references such as `m3`, while
retaining type, scope, epistemic state, lifecycle, provenance, representation,
event/validity times, and complete selected representation text. Resolve a model-returned reference
with `response.bundle.resolve_context_ref("m3")`; the full mapping is available as
`response.bundle.context_references`. Embedded delimiters and newlines remain
JSON-escaped data. The default `"auditable"` format keeps self-describing JSON
objects and full node IDs in the model context. Exact token accounting applies to
both formats. Evaluate answer quality before changing a production workload.
The equivalent environment setting is `PRME_PACKING__CONTEXT_FORMAT=compact`.

For workloads that store a source block or bounded dialogue episode under one
`session_id`, opt into deterministic two-stage episode routing:

```python
config = config.model_copy(update={
    "packing": PackingConfig(
        token_budget=4096,
        episode_context_top_k=2,
        episode_context_local_k=8,
        episode_context_score_decay=0.95,
    )
})
```

PRME first uses BM25 over the candidate-backed text of each exact
`(scope, session_id)` group, then reserves a bounded set of locally relevant
records from the selected groups. The route makes no model calls and cannot
cross scopes. It does not fetch an entire session outside the existing candidate
pool. The default `episode_context_top_k=0` keeps it disabled while the
hypothesis is evaluated across workloads. Set session IDs to real episode
boundaries before enabling it; reused, unrelated session IDs reduce precision.

On 119 examined development questions, 4K source
recall increased from 74.85% to 95.91%. On the separately captured, previously
examined 381-question regression partition, it increased from 65.04% to 90.55%,
with two question-level losses and positive category mean changes. Preference
recall improved on average, but one preference question regressed. In a fixed
119-question development answer trial, balanced scored 83/119 versus density at
67/119, with 26 paired wins and 10 losses. A separately registered answer
confirmation on the different 381-question partition scored 250/381 versus
185/381, with 88 paired wins, 23 losses and no lower category total. The
[full regression report](../benchmarks/results/research/2026-09-12/PACKING-REGRESSION-STUDY.md)
[development answer report](../benchmarks/results/research/2026-09-13/BALANCED-QWEN35B-ALL-V2.md),
and [answer confirmation](../benchmarks/results/research/2026-09-13/BALANCED-QWEN35B-REGRESSION.md)
retain all losses and limitations. Both source partitions had been examined and
the trials used one local reader and one calibrated local judge. They support the
default change, but they are not an independent competitive benchmark. Evaluate
high-stakes workloads directly.

Current retrievals produce version 9 receipts with explicit ordering,
context-guidance, context-format, and episode-routing policies and the same
score-replay and execution requirements. Version 9 also records the explicit
current-update scoring policy. Versions 1–7 keep their previous
canonical bytes and checksums and mean episode routing was disabled. Versions
1–6 always mean auditable rendering; versions 1–5 also mean context guidance was
off. Version 5 remains the historical balanced format, and versions 1–4 cannot
claim balanced packing. Versions 1–8 mean current-update scoring was disabled.
Older readers that lack version 9 support cannot consume
new receipts. Score replay reproduces the returned
candidate ranking; it is not a reconstruction of packing or unseen candidates.
Relevance feedback remains linked to the saved context exposure.

Temporal guidance is enabled by default. It adds the question time and explicit
record-relative date instructions only after selection, and only when the whole
output still fits. Set `PackingConfig(context_guidance_mode="off")` for exact
pre-guidance behavior. `"all"` also enables experimental current-state and
personalization guidance; confirmation evidence did not support those prompts
as defaults. The [confirmation report](../benchmarks/results/research/2026-09-14/CONTEXT-GUIDANCE-CONFIRMATION.md)
records all transitions and limitations.
