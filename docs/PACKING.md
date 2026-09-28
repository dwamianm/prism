# Choosing a memory context packing policy

`PackingConfig.multipath_ordering` controls which ordinary multi-path memories
get space first. The default configuration (`PRMEConfig().packing`) uses
`"balanced"`, with the one-line `"reader"` context format and rank fusion
scoring (see [Default retrieval settings](#default-retrieval-settings) below).
`"balanced"` was also the default while every record carried a JSON envelope,
and it is the field's own default. `"score"` was the default with the reader
format for a short time on 2026-09-25, until balanced order passed the
default-change test against it. Use `"score"` or `"density"` explicitly when
your workload evaluation favors one of those policies.

```python
from prme import MemoryClient
from prme.client import config_from_directory

config = config_from_directory("./my_memories")
config = config.model_copy(update={
    "packing": config.packing.model_copy(update={"token_budget": 4096})
})

with MemoryClient(config=config) as memory:
    memory.store("The pilot can launch only after approval.", user_id="alice")
    response = memory.retrieve("When can the pilot launch?", user_id="alice")
    print(response.bundle.render())
```

The same configuration applies to `MemoryEngine.open(config)` and servers using
that config. Copying `config.packing` keeps the other defaults. A
`PackingConfig` built from scratch starts from the class's own historical
defaults instead (`auditable`, `balanced` and no rank fusion session decay),
which stored receipts and configurations rely on. Set
`PRME_PACKING__MULTIPATH_ORDERING=density` or `=score` for an explicit density
or score override.
This is an engine configuration, not a new per-request HTTP or MCP parameter.

| Policy | Ordering within the ordinary multi-path tier |
|---|---|
| `density` | Composite score divided by full-entry token cost. |
| `score` | Composite score. |
| `balanced` | Highest composite score first, then score divided by full-entry token cost to the power 0.25. |

Ties use node ID. Under rank fusion, `ScoringWeights.rrf_tie_break`
(RFC-0005 Section 7.2), which the default configuration sets, makes equal fused scores differ by event time, newest
first, so only memories with the same time or the same inherited score still
fall back to node ID. Instructions, pinned memories and active tasks keep their
existing higher priority; single-path candidates keep their existing lower tier,
except for session neighbors under the opt-in
[session context packing](#session-context-neighbors) below.
Every entry still passes the same measured whole-context budget and fidelity
checks. Reserving the first position does not guarantee that its full text fits:
it can become a reference or be excluded. References do not contain source text;
a text-bearing `min_fidelity`, described below, keeps them out of the context.
Call `response.bundle.render()` for the exact budgeted text; the surrounding
API response and the application's other prompts consume additional tokens.

For stores with many short records, opt into the compact renderer:

```python
config = config.model_copy(update={
    "packing": config.packing.model_copy(update={"context_format": "compact"})
})
```

It emits a declared JSON-array schema and short references such as `m3`, while
retaining type, scope, epistemic state, lifecycle, provenance, representation,
event/validity times, and complete selected representation text. Resolve a model-returned reference
with `response.bundle.resolve_context_ref("m3")`; the full mapping is available as
`response.bundle.context_references`. Embedded delimiters and newlines remain
JSON-escaped data. The `"auditable"` format keeps self-describing JSON
objects and full node IDs in the model context. Exact token accounting applies to
both formats. Evaluate answer quality before changing a production workload.
The equivalent environment setting is `PRME_PACKING__CONTEXT_FORMAT=compact`.

All three context formats escape U+0085 (next line), U+2028 (line separator)
and U+2029 (paragraph separator), in addition to JSON's ordinary escapes. A
stored record cannot introduce another record or section by containing a line
separator; decoding the JSON restores its exact text. Ordinary multilingual
characters remain readable. Escaped bytes count toward the complete context
token budget and can change which records fit near the limit.

Compatibility for issue #108: new auditable and compact contexts containing
these three characters differ from earlier rendered bytes and `context_sha256`
values. Repacking those inputs with the corrected renderer is not a byte-exact
replay of the old context. Persisted receipt bytes/checksums and stored source
text are unchanged; reader output and inputs without these characters retain
their rendering. No retrieval or format default changes.

The default reader format gives the answering model the memory text instead of
the audit envelope. To choose it explicitly:

```python
config = config.model_copy(update={
    "packing": config.packing.model_copy(update={"context_format": "reader"})
})
```

A header line explains the layout, section labels such as `[stable_facts]`
stay, and each record becomes one line starting with `- `:

```text
- [2023-05-28 18:12] "I've been looking at apartments on Zillow."
- "(7:55 pm on 9 June, 2023) Caroline: I went to the support group yesterday."
- [valid 2020-01-01 to 2021-01-01] [hypothetical] "Alice may live in Boston."
- [2023-06-09 20:02] "Melanie": "That sounds wonderful. How did it go?"
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
- A record stored with a `speaker` shows that name as a quoted string and a
  colon before its text, as in the last line. It is left out when the text
  already begins with the name and a colon, after an optional leading group in
  parentheses or brackets such as a date, as in the second line. Only when a
  packed line shows a speaker does the header describe it and say that speaker
  names, like record text, are source data; contexts without speakers keep
  their exact bytes. The auditable format adds a `"speaker"` key to records
  stored with one, and the compact format's fixed fields do not include it.
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
`response.bundle.sections` and in the retrieval receipt. `reader` is the
default, and `auditable` and `compact` remain available. The equivalent
environment setting is `PRME_PACKING__CONTEXT_FORMAT=reader`.

With the reader format, score order and `balanced` trade LoCoMo evidence against
LongMemEval-S evidence. On the
[offline evidence gate](../BENCHMARKS.md#offline-evidence-gate-the-first-gate-for-retrieval-changes)
at 4K, measured on 2026-09-24 while `balanced` and the weighted score were the
defaults, score order packed all annotated evidence for more LoCoMo questions
and for fewer LongMemEval-S questions, whose evidence mostly sits in short user
turns next to long assistant replies. Under the weighted score LoCoMo gained 8.8
percentage points and LongMemEval-S lost 3.2. Under rank fusion
(`ScoringWeights.fusion="rrf"`) with
`PackingConfig.session_context_rank_fusion_score_decay=0.6`, LoCoMo gained only
1.0 and LongMemEval-S lost 7.9. The DeepSeek answer pairs that made the reader
format the default used score order; a balanced version was then answered as a
variant of its own and passed the default-change test twice, so the defaults
use `balanced` (see [Default retrieval settings](#default-retrieval-settings)).
The [reader ordering record](../benchmarks/results/research/2026-09-24/READER-PACKING-ORDER-GATE-V1.md)
has both budgets, the rank fusion recency settings and every category.

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
    "packing": config.packing.model_copy(update={"min_fidelity": "full"})
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
    "packing": config.packing.model_copy(update={
        "episode_context_top_k": 2,
        "episode_context_local_k": 8,
        "episode_context_score_decay": 0.95,
    })
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
    "packing": config.packing.model_copy(update={
        "evidence_projection_top_k": 50,
        "evidence_projection_max_sources": 1,
        "evidence_projection_score_decay": 1.0,
    })
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
    "packing": config.packing.model_copy(update={
        "evidence_augmentation_top_k": 10,
        "evidence_augmentation_max_sources": 1,
        "evidence_augmentation_score_decay": 0.99,
        "evidence_augmentation_anchor_policy": "non_entity",
    })
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

The earlier `balanced` default was chosen on the following evidence, measured
while records were rendered as JSON. On 119 examined development questions, 4K source
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

## Session context neighbors

Session expansion adds the turns around each of the top results
(`session_context_window` turns on each side of the top `session_context_top_k`).
By default a turn that only expansion found has one path, and a turn another
search found keeps its own path count, so packing puts these neighbors behind
every multi-path candidate. With a broad candidate pool most turns are
multi-path: in an exact replay of the 2026-09-23 LoCoMo retrievals, none of the
neighbors that only expansion found was packed. The opt-in
`session_context_packing` setting changes that (issue #86):

```python
config = config.model_copy(update={
    "packing": config.packing.model_copy(update={"session_context_packing": "adjacent"})
})
```

- `"trigger_tier"` counts session expansion as a path when it joins a candidate
  another search found, and gives a turn that expansion added the multi-path
  tier when the result that brought it in (its trigger) is in that tier or
  above. The neighbor then competes on the score it inherited.
- `"adjacent"` packs exactly the same records and places each packed neighbor
  beside its packed trigger in session order, so a question and its answer read
  together in the context.

Each neighbor belongs to the highest-ranked trigger whose window holds it; a
neighbor that is itself one of the top results keeps its own place. Results and
bundles show that link as `session_context_link` only when the setting is on.
The setting needs session expansion: with `session_context_window=0` it changes
nothing and receipts do not record it. The equivalent environment setting is
`PRME_PACKING__SESSION_CONTEXT_PACKING=adjacent`; remove the variable to turn
it off.

On the offline evidence gate at 4K, against the current defaults, both values
packed all annotated evidence for more questions and lost no category: LoCoMo
1,290 to 1,304 of 1,536 (+0.9 points, 95% interval +0.1 to +1.8; single-hop
+1.5, +0.7 to +2.5) and LongMemEval-S 445 to 447 of 470 (+0.4, +0.0 to +1.1).
The packed records that session expansion reached went from 45.9% to 52.8% of
LoCoMo records and from 54.7% to 55.6% of LongMemEval-S records, and the LoCoMo
records found by no other path from 9 to 3,232 over the 1,540 questions.
`"adjacent"` reorders almost every context without changing which records it
holds. Packing a trigger's neighbors right after the trigger, ahead of the
remaining multi-path records, was measured and rejected: the whole window lost
5.5 points of LoCoMo and 15.7 of LongMemEval-S evidence, and only the turn
after and the turn before still lost 8.5 points of LongMemEval-S evidence
(multi-session -16.5), because long assistant turns used the budget. The
setting stays off until the epic #77 paired answer run supports it. The gate
comparisons for
[`trigger_tier`](../benchmarks/results/research/2026-09-25/session-context-packing-trigger-tier-gate-comparison.md)
and [`adjacent`](../benchmarks/results/research/2026-09-25/session-context-packing-adjacent-gate-comparison.md)
list every category.

## Default retrieval settings

`PRMEConfig()` retrieves with these settings, which changed after v0.12.0
(see the changelog):

| Setting | Default | Default in v0.12.0 |
|---|---|---|
| `scoring.fusion` (`PRME_SCORING__FUSION`) | `rrf`, with `rrf_k=60` | `weighted` |
| `scoring.rrf_recency_boost` (`PRME_SCORING__RRF_RECENCY_BOOST`) | `0.25` | unset (rank fusion did not exist as a default) |
| `scoring.rrf_tie_break` (`PRME_SCORING__RRF_TIE_BREAK`) | `event_time` | unset |
| `packing.context_format` (`PRME_PACKING__CONTEXT_FORMAT`) | `reader` | `auditable` |
| `packing.multipath_ordering` (`PRME_PACKING__MULTIPATH_ORDERING`) | `balanced` | `balanced` (`score` between the two steps described below) |
| `packing.session_context_rank_fusion_score_decay` (`PRME_PACKING__SESSION_CONTEXT_RANK_FUSION_SCORE_DECAY`) | `0.6` | None (0.85, the weighted decay) |

The settings changed in two steps on 2026-09-25, and each step passed the
project's default-change test twice on the DeepSeek answer track. The first
step made rank fusion with its recency settings, the reader format, score order
and the 0.6 session decay the defaults. The second replaced score order with
`balanced`. The context budget and session expansion did not change.

The first step's combination passed the test on the DeepSeek answer track
(`deepseek-v4.1-flash:cloud` as reader and judge through Ollama 0.34.3, a
3,996-token context, the registered 2026-09-23 prompts). Each pair answered the
new settings and a fresh run of the previous defaults interleaved question by
question over the same questions:

| Pair | LoCoMo (1,540 questions) | LongMemEval-S (500 questions) |
|---|---|---|
| First | 65.7% to 80.9%, +15.2 points (95% interval +13.0 to +17.4) | 85.8% to 86.8%, +1.0 (-1.6 to +3.6) |
| Confirmation | 65.8% to 81.6%, +15.7 (+13.5 to +17.9) | 86.4% to 86.6%, +0.2 (-2.6 to +3.0) |

These are DeepSeek-track numbers and are not comparable with the GPT-5.4
results; [BENCHMARKS.md](../BENCHMARKS.md) has the categories and receipts.
LongMemEval-S knowledge-update questions went from 73 to 71 of 78 in both
pairs (-2.6 points, intervals including zero); #169 tracks recency on those
questions.

The second step changed only the ordering. On the offline evidence gate,
`balanced` order with the same reader format and rank fusion settings packed
all LongMemEval-S evidence for more questions and all LoCoMo evidence for fewer
(#81). Its answer pairs, each against a fresh run of the first step's
defaults under the same conditions, gained on LongMemEval-S and showed no
LoCoMo difference:

| Pair | LoCoMo (1,540 questions) | LongMemEval-S (500 questions) |
|---|---|---|
| First | 80.1% to 80.6%, +0.5 points (95% interval -0.9 to +2.0) | 86.8% to 90.0%, +3.2 (+0.6 to +6.0) |
| Confirmation | 80.6% to 80.4%, -0.2 (-1.5 to +1.1) | 86.8% to 90.0%, +3.2 (+0.6 to +5.8) |

LongMemEval-S multi-session questions gained the most (+7.5 and +11.3 points,
both intervals excluding zero). LongMemEval-S knowledge-update questions went
from 73 to 70 of 78 in both pairs (-3.8 points, intervals including zero;
#169). LoCoMo multi-hop questions fell by 1.8 and 4.3 points. In the
confirmation the conversation-level interval excludes zero (-9.2 to -0.6), but
the question-level one does not (-9.2 to +0.4), so the category's interval,
which spans both, includes zero. Set `PRME_PACKING__MULTIPATH_ORDERING=score`
to keep the first step's score order.

What else changes with these defaults:

- The new defaults live in `PRMEConfig` (`PRMEConfig().scoring` and
  `PRMEConfig().packing`), and environment variables, `.env` entries and
  secrets that set only some `PRME_SCORING__*` or `PRME_PACKING__*` values
  keep them. `ScoringWeights()` and `PackingConfig()` built in code keep their
  historical defaults (the weighted formula without the rank fusion settings;
  `auditable`, `balanced` and no rank fusion session decay), because stored
  receipts and configurations omit some of those values and must keep their
  meaning. To change one setting and keep the new defaults, copy from the
  configuration: `config.packing.model_copy(update={...})`.
- Rank fusion does not use the additive weights, graph proximity, salience or
  confidence, and uses recency only through the current-state recency boost,
  so salience decay, reinforcement and confidence changes no longer move a
  result's rank. A candidate found only through the graph or a pin scores 0,
  though pins are still packed first. `min_score` is compared with each
  result's semantic cosine (`semantic_relevance`). Non-neutral request
  `ranking_multipliers` are rejected (HTTP 422, MCP error), learned ranking
  profiles report `rank_fusion_scoring` and are not applied, and the
  `feedback_apply` organizer job reports `not_applicable` and keeps its signals.
- The reader format does not print a record's source type or ID, so the same
  statement stored by the user and by the assistant reads alike; the bundle
  sections and the receipt keep both.
- Answerability checks and `verify_bundle()` cite records through the context,
  so they raise `ValueError` for a reader bundle without citations. Set
  `PRME_PACKING__CONTEXT_CITATIONS=true` (which adds `[m3]` references to the
  context) or use the `auditable` format for those callers.
- Retrieval receipts are written as version 19 by default.

To go back to the v0.12.0 defaults, set all three:

```bash
PRME_SCORING__FUSION=weighted
PRME_PACKING__CONTEXT_FORMAT=auditable
PRME_PACKING__MULTIPATH_ORDERING=balanced
```

or, in code:

```python
from prme.retrieval.config import PackingConfig, ScoringWeights

config = config.model_copy(update={
    "scoring": ScoringWeights(),
    "packing": PackingConfig(),
})
```

`ScoringWeights()` and `PackingConfig()` are the v0.12.0 defaults. `balanced`
ordering is the default again, so the third setting only makes it explicit. A
weighted
fusion set through the environment drops the rank fusion recency boost and
tie-break, and the rank fusion session decay stays 0.6, but weighted scoring
never applies it and its receipts omit it. With these three settings the
offline evidence gate reproduces every saved 2026-09-23 LoCoMo and
LongMemEval-S context byte for byte.

Retrievals with the default settings produce version 19 receipts (rank
fusion with its recency boost, tie-break and session decay, described below). Weighted retrievals in the
auditable or compact format produce version 12 receipts with explicit ordering,
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

Retrievals scored with rank fusion (`ScoringWeights.fusion="rrf"`, the
`PRMEConfig` default, RFC-0005 Section 7.2) emit version 16 in every context
format. Version 16 records
`scoring.fusion` and `scoring.rrf_k`, formula version 2 score provenance with
saved ranks and factors, each candidate's `semantic_relevance` (the semantic cosine
that `min_score` is compared with under rank fusion), and everything versions 13
and 14 admit. Version 15 receipts, saved before `semantic_relevance` existed, keep
their bytes and checksums and cannot claim it. Versions 1 to 14 cannot claim
rank fusion; weighted scoring omits both settings, so their stored bytes and
checksums remain unchanged. Older readers without version 16 support cannot
consume these rank-fusion receipts. A rank fusion retrieval with
`session_context_rank_fusion_score_decay` set (0.6 by default) emits version
17, which also records that decay; every session decay in its score provenance
must equal it. Versions 1 to 16 cannot record it, and a value of None is
omitted from the packing settings. A stored receipt from before version 17
reads a missing value as None rather than as the current default, so no earlier
receipt changes. Weighted retrievals never apply the decay, so their receipts
omit it too. A rank fusion retrieval that skipped a positive `min_score`
because the vector path failed and no candidate had a cosine emits version 18,
which records `min_score_skipped: true`, requires every candidate's
`semantic_relevance` to be 0, and also records the rank fusion session decay
when it is set. Versions 1 to 17 cannot record a skipped floor. A rank fusion
retrieval with `scoring.rrf_recency_boost` or `scoring.rrf_tie_break` set (the
defaults set both) emits version 19, which records them in its scoring
settings and saved factors, requires every score provenance to use the same
values, and also admits the version 17 and 18 features, so default retrievals
write version 19. Versions 1 to 18 cannot record either setting, and unset
settings are omitted, so no earlier receipt changes. A weighted retrieval with
`scoring.recency_time="event_time"` emits version 20 in any context format,
which records the setting and requires every score provenance to use it.
Versions 1 to 19 cannot record it, and an unset value is omitted. A retrieval
with `packing.session_context_packing` set and session expansion on emits
version 21 under either formula, which records the setting and admits the
features of versions 12 to 20 for its formula. Versions 1 to 20 cannot record
it; an unset value, or one set while session expansion is off, is omitted.

Temporal guidance is enabled by default. It adds the question time and explicit
record-relative date instructions only after selection, and only when the whole
output still fits. Set `context_guidance_mode="off"` for exact
pre-guidance behavior. `"all"` also enables experimental current-state and
personalization guidance; confirmation evidence did not support those prompts
as defaults. The [confirmation report](../benchmarks/results/research/2026-09-14/CONTEXT-GUIDANCE-CONFIRMATION.md)
records all transitions and limitations.
