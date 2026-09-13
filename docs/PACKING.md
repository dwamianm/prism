# Choosing a memory context packing policy

`PackingConfig.multipath_ordering` controls which ordinary multi-path memories
get space first. The default remains `"density"`. Use `"balanced"` explicitly to
try the policy that improved whole-source retention in the completed development
and regression studies.

```python
from prme import MemoryClient
from prme.client import config_from_directory
from prme.retrieval.config import PackingConfig

config = config_from_directory("./my_memories")
config = config.model_copy(update={
    "packing": PackingConfig(multipath_ordering="balanced", token_budget=4096),
})

with MemoryClient(config=config) as memory:
    memory.store("The pilot can launch only after approval.", user_id="alice")
    response = memory.retrieve("When can the pilot launch?", user_id="alice")
    print(response.bundle.render())
```

The same configuration applies to `MemoryEngine.open(config)` and servers using
that config. Environment configuration is
`PRME_PACKING__MULTIPATH_ORDERING=balanced`. This is an engine configuration,
not a new per-request HTTP or MCP parameter.

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

`balanced` is experimental. On 119 examined development questions, 4K source
recall increased from 74.85% to 95.91%. On the separately captured, previously
examined 381-question regression partition, it increased from 65.04% to 90.55%,
with two question-level losses and positive category mean changes. Preference
recall improved on average, but one preference question regressed. These are
source-retention results, not answer accuracy or an unseen holdout. The
[full regression report](../benchmarks/results/research/2026-09-12/PACKING-REGRESSION-STUDY.md)
retains all losses and uncertainty. Evaluate your own workloads before adopting
it; no production default has changed.

Balanced retrievals produce version 5 receipts with an explicit policy and the
same score-replay and execution requirements. Density/score retrievals continue
to produce version 4 receipts. Versions 1–4 keep their previous canonical bytes
and checksums, and cannot claim the new balanced policy. Older readers that lack
version 5 support cannot consume balanced receipts. Score replay reproduces the
returned candidate ranking; it is not a reconstruction of packing or unseen
candidates. Relevance feedback remains linked to the saved context exposure.
