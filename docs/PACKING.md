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
it can become a reference or be excluded. References do not contain source text;
a text-bearing `min_fidelity`, described below, keeps them out of the context.
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

To give the answering model the memory text instead of the audit envelope, opt
into the reader format:

```python
config = config.model_copy(update={
    "packing": PackingConfig(token_budget=4096, context_format="reader")
})
```

A header line explains the layout, section labels such as `[stable_facts]`
stay, and each record becomes one line starting with `- `:

```text
- [2023-05-28 18:12] "I've been looking at apartments on Zillow."
- "(7:55 pm on 9 June, 2023) Caroline: I went to the support group yesterday."
- [valid 2020-01-01 to 2021-01-01] [hypothetical] "Alice may live in Boston."
```

- The bracketed time is the record's `event_time` in UTC, with the time of day
  when it is not midnight. It is left out when the text already begins with that
  date, as in the second line. `ingest()` uses the ingestion time as the event
  time of extracted facts when the source has none, and that time is shown.
- A validity range appears only for a closed window a caller supplied through
  `store()`. `valid_from` defaults to the time PRME admitted the record, and PRME
  does not record whether a caller set it, so an open-ended `valid_from` is never
  shown. Windows closed by write-time supersedence are not shown either, because
  their bounds can fall back to admission times. `created_at` is never shown.
- Tags appear for every state except the defaults: lifecycle `tentative` and
  `stable`, epistemic `asserted` and `observed`. That covers `contested`,
  `superseded`, `deprecated`, `archived`, `inferred`, `hypothetical`,
  `unverified`, and `conditional` with its condition state. Default retrieval
  already excludes most superseded, hypothetical and unconfirmed conditional
  records.
- The text is the complete stored text as a JSON string, so newlines, quotes
  and Unicode line separators stay escaped and a record cannot span lines or
  pose as a tag. The renderer prints no node IDs, type, scope, source type or
  representation; text written by other components, such as organizer summaries,
  is printed as stored.
- Only the `full` (or equal `prose`) text is packed. A record that fits only as
  a `structured`, `key_value` or `reference` fallback, or whose text is blank,
  is excluded instead.

Set `context_citations=True` (`PRME_PACKING__CONTEXT_CITATIONS=true`) to prefix
each line with a bundle-local reference such as `[m3]` and fill
`response.bundle.context_references` (MCP returns it with `include_context`).
Answerability checks and `verify_bundle()` cite through these references, so
they raise `ValueError` for a reader bundle packed without them. The setting is
rejected with the other formats, because compact records always carry
references and auditable records carry full node IDs. Context ablation keeps
the bundle's format and references.

The complete record, including its ID and every metadata field, stays in
`response.bundle.sections` and in the retrieval receipt. `auditable` remains
the default and `compact` remains available. The equivalent environment setting
is `PRME_PACKING__CONTEXT_FORMAT=reader`.

When a record does not fit whole, the packer tries lower representation levels
down to `PackingConfig.min_fidelity`. The default floor is `reference`, and the
two lowest levels carry no memory text: `key_value` renders
`id: <uuid>, type: fact, confidence: <value>` and `reference` renders
`fact:<uuid>`. In the auditable and compact formats each such entry still costs
a full metadata envelope and gives the answering model nothing to read. To keep
these text-free fallbacks out of the context in every format, set a
text-bearing floor:

```python
config = config.model_copy(update={
    "packing": PackingConfig(token_budget=4096, min_fidelity="full")
})
```

With `full`, `prose` or `structured` as the floor, a record that fits only
without its text, or whose stored text is blank, is excluded instead. It is
listed in `response.bundle.excluded_ids` and appears in the retrieval receipt
as a candidate that was not in the context, so a caller that needs the node IDs
still has them outside the prompt. Neither `prose` nor `structured` is ever
shorter than `full`, so the three floors pack the same records; `full` does the
least work. Aggregation coverage does not count a blank record's exclusion as a
token-budget limit. The floor governs the packed context only
(`response.bundle` and MCP `context`); `response.results` still lists every
retrieved record. The equivalent environment setting is
`PRME_PACKING__MIN_FIDELITY=full`, and `retrieve()`, HTTP and MCP accept
`min_fidelity` per request, which replaces the configured floor for that
request. The reader format always applies this rule. The default stays
`reference` until a paired answer run supports changing it (epic #77).

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

To use extracted claims for routing while returning complete direct evidence,
enable bounded evidence projection:

```python
config = config.model_copy(update={
    "packing": PackingConfig(
        token_budget=4096,
        evidence_projection_top_k=50,
        evidence_projection_max_sources=1,
        evidence_projection_score_decay=1.0,
    )
})
```

For each selected exact evidence group, PRME replaces derived siblings with an
active source node whose own ID is cited by the group. Owner, scope, temporal,
validity, lifecycle and epistemic checks all apply. The source inherits the
strongest group score through replayable provenance and receives bounded packing
priority. A group without an eligible direct source is unchanged. The default
`evidence_projection_top_k=0` keeps this experimental policy disabled. A frozen
BEAM development ablation showed that evidence diversity without source
preservation caused a pass-level regression. A second trial found that replacing
all top-50 groups improved event ordering to 2/2 but caused three pass-level
losses. Wholesale projection is therefore rejected as a default.

Use augmentation when both the concise claim and direct wording are needed:

```python
config = config.model_copy(update={
    "packing": PackingConfig(
        token_budget=4096,
        evidence_augmentation_top_k=10,
        evidence_augmentation_max_sources=1,
        evidence_augmentation_score_decay=0.99,
        evidence_augmentation_anchor_policy="non_entity",
    )
})
```

Augmentation applies the same owner, scope, time, validity and epistemic checks,
but adds sources beside derived candidates. Projection and augmentation cannot
both be enabled. A registered tuned BEAM ablation at `top_k=10` improved the
accepted result from 13/20 to 14/20 with one pass-level win, no losses and a
+0.02916 mean-score delta. A separately preregistered untouched confirmation
scored 14/20 versus its fresh baseline at 15/20, with no wins, one
contradiction-resolution loss and a -0.03375 mean delta. Unconditional top-10
augmentation remains disabled by default. It can add useful direct wording, but
callers must evaluate it for their query distribution rather than assume the
development gain generalizes.

`evidence_augmentation_anchor_policy="non_entity"` addresses one measured
failure mode without classifying the query. Entity nodes are useful search
routes, but an entity-name match does not establish that its full source passage
supports the queried state. This policy skips entity anchors and lets later
non-entity evidence groups fill the configured quota. It remains experimental
after a paired two-conversation diagnostic fixed the observed contradiction
regression but produced one win, one temporal loss and a -0.03000 combined mean
delta. Use it as an explicit safety control, not as an assumed quality gain.

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

Ordinary retrievals produce version 12 receipts with explicit ordering,
context-guidance, context-format, and episode-routing policies and the same
score-replay and execution requirements. Version 9 records the explicit
current-update scoring policy, version 10 records evidence projection, and
version 11 records evidence augmentation; version 12 records its anchor
policy. Versions 1–11 mean the anchor policy was `all` and omit that field.
Versions 1–7 keep their previous
canonical bytes and checksums and mean episode routing was disabled. Versions
1–6 always mean auditable rendering; versions 1–5 also mean context guidance was
off. Version 5 remains the historical balanced format, and versions 1–4 cannot
claim balanced packing. Versions 1–8 mean current-update scoring was disabled.
Versions 1–9 mean evidence projection was disabled. Older readers that lack
version 12 support cannot consume
new receipts. Score replay reproduces the returned
candidate ranking; it is not a reconstruction of packing or unseen candidates.
Relevance feedback remains linked to the saved context exposure.

Explicit [reranker envelope policies](EXPERIMENTAL-RETRIEVAL-POLICIES.md) emit
version 13 when neural rank assignments occur. Each assignment follows the raw
neural blend and retains the original prefix score scale for downstream packing
and session expansion. Versions 1–12 reject that operation; their stored
canonical JSON and checksums remain unchanged. Version 13 does not change the
packer or make score assignments into probabilities. Older readers without
version 13 support cannot consume these opt-in receipts.

Reader-format retrievals emit version 14, which records
`packing.context_citations` explicitly and also admits the version 13 rank
assignments. Versions 1–13 cannot claim the reader format, omit the citation
setting from their canonical JSON, and mean it was off, so their stored bytes
and checksums remain unchanged. Every version must state each packing setting
it introduced: ordering from version 4, guidance from 6, format from 7 and
episode routing from 8, instead of taking a later default. Other formats keep
emitting versions 12 and 13. Older readers without
version 14 support cannot consume reader receipts.

Retrievals scored with opt-in rank fusion (`ScoringWeights.fusion="rrf"`,
RFC-0005 Section 7.2) emit version 15 in every context format. Version 15 records
`scoring.fusion` and `scoring.rrf_k`, formula version 2 score provenance with
saved ranks and factors, and everything versions 13 and 14 admit. Versions 1 to
14 cannot claim rank fusion; weighted scoring omits both settings, so their
stored bytes and checksums remain unchanged. Older readers without version 15
support cannot consume rank-fusion receipts.

Temporal guidance is enabled by default. It adds the question time and explicit
record-relative date instructions only after selection, and only when the whole
output still fits. Set `PackingConfig(context_guidance_mode="off")` for exact
pre-guidance behavior. `"all"` also enables experimental current-state and
personalization guidance; confirmation evidence did not support those prompts
as defaults. The [confirmation report](../benchmarks/results/research/2026-09-14/CONTEXT-GUIDANCE-CONFIRMATION.md)
records all transitions and limitations.
