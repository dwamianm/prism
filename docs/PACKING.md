# Choosing a memory context packing policy

`PackingConfig.multipath_ordering` controls which ordinary multi-path memories
get space first. The default is `"balanced"`, selected after completed source
retention studies and a full-cohort answer trial. Use `"density"` or `"score"`
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

On 119 examined development questions, 4K source
recall increased from 74.85% to 95.91%. On the separately captured, previously
examined 381-question regression partition, it increased from 65.04% to 90.55%,
with two question-level losses and positive category mean changes. Preference
recall improved on average, but one preference question regressed. In a fixed
119-question answer trial, balanced scored 83/119 versus density at 67/119,
with 26 paired wins, 10 losses and no lower category total. The
[full regression report](../benchmarks/results/research/2026-09-12/PACKING-REGRESSION-STUDY.md)
and [answer report](../benchmarks/results/research/2026-09-13/BALANCED-QWEN35B-ALL-V2.md)
retain all losses and limitations. The answer cohort had been examined and used
one local reader and one calibrated local judge; it supports the default change,
not a competitive leadership claim. Evaluate high-stakes workloads directly.

Balanced retrievals produce version 5 receipts with an explicit policy and the
same score-replay and execution requirements. Density/score retrievals continue
to produce version 4 receipts. Versions 1–4 keep their previous canonical bytes
and checksums, and cannot claim the new balanced policy. Older readers that lack
version 5 support cannot consume balanced receipts. Score replay reproduces the
returned candidate ranking; it is not a reconstruction of packing or unseen
candidates. Relevance feedback remains linked to the saved context exposure.
