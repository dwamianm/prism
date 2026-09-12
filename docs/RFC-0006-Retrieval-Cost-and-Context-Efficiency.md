# RFC-0006: RMS Retrieval Cost and Context Efficiency

**Status:** Draft
**Tier:** 2 — Retrieval
**Version:** 1.0
**Date:** 2026-02-19
**Depends on:** RFC-0000, RFC-0001, RFC-0005

---

## 1. Abstract

This RFC specifies context packing: the process of selecting which scored memory objects to include in a Memory Bundle given a fixed token budget, and how each object's token cost is estimated and balanced against its retrieval value.

Packing must be evaluated for source retention and downstream task quality under
an actual token budget. Signal-to-Token Ratio (STR) was the initial heuristic;
the composite score is not a calibrated additive utility, so dividing it by
token cost does not establish that the resulting context is better.

**Evidence update, 2026-09-12:** a complete 119-question development comparison
found that score ordering within the multi-path tier retained more labelled
source evidence than density ordering at 2K, 4K and 8K. At the default 4K budget,
recall rose from 75.51% to 93.49%, with 31 wins and no losses among 114 labelled
questions. There were five losses at 2K, including a negative preference-category
mean. The density default remains pending a frozen test-partition confirmation;
see the [study and limitations](../benchmarks/results/packing/2026-09-12/CONFIRMATION.md).
This supersedes the earlier rationale treating STR as established superior utility.

**Configurable policy:** `PackingConfig.multipath_ordering` accepts `"density"`
(default) or `"score"`. It changes only the ordering of the multi-path tier; ties
still use node ID and all representations obey the same measured budget. The
public implementation reproduces all 714 frozen development contexts and source
measurements across both arms and three budgets. This is implementation parity,
not new evidence or completion of the separate confirmation study. New retrieval
receipts record this policy in schema version 4; legacy receipts preserve their
original bytes and implicit density semantics.

**Implemented contract, 2026-09-12:** the product packer now counts its complete
rendered context with a named tiktoken encoding. `MemoryBundle.render()` returns
that exact text; `tokens_used` includes headers, separators, and metadata.
`overhead_tokens` is an additional caller reservation. Content-bearing
representations retain whole source text; character slicing is not a valid
PROSE or STRUCTURED representation. Missing space permits explicit references
or exclusion, including for pins. No always-include rule can exceed the budget.
The separate LLM formatter enforces the same whole-output rule when a budget
is supplied and supports a caller-provided tokenizer counter.

Aware timestamps are rendered in UTC before token counting or date-relative
annotations. Equivalent instants must produce identical context and token costs
regardless of their returned timezone offset. Local engine connections select
UTC explicitly: DuckDB otherwise uses the host timezone for TIMESTAMPTZ output
([timestamp semantics](https://duckdb.org/docs/current/sql/data_types/timestamp)).
This changes presentation, not the stored instant. Legacy naive in-memory dates
remain nominal; formatting must not silently attach the host timezone to them.

Every packed representation retains the node's stored `source_type` alongside
its epistemic status. This distinguishes user-stated evidence from inferred,
external-document and tool-output provenance; it is not verification of speaker
identity or truth. Metadata costs count against the same context budget.

The alternate `format_for_llm()` renderer retains provenance, epistemic state
and memory lifecycle in its body, profile and conflict sections. It removes
repeated node identities, not matching text prefixes: distinct records can
describe different episodes, or carry different qualifications, despite sharing
text. A profile-rendered record is excluded from the body by its identity.
Chronological markers describe recording order; they do not assert supersedence
or establish truth. Generated guidance must preserve unresolved contradictions
and must not supply unsupported relationships or counts as examples to follow.
The additional annotations are included in whole-output budget enforcement.

---

## 2. The Signal-to-Token Ratio (STR)

STR is defined as:

```
STR(obj) = composite_score(obj) / token_cost(obj)
```

Where:
- `composite_score` is the retrieval score from RFC-0005, Stage 5. Range: [0, 1].
- `token_cost` is the estimated token count for the object's representation in the bundle (Section 3).

STR represents how much retrieval value the object delivers per token consumed. Objects with high STR are preferred over low-STR objects when the context budget is tight.

STR is used as a tiebreaker within priority tiers (RFC-0005, Stage 6), not as a replacement for composite score.

---

## 3. Token Cost Estimation

Token cost estimation is `[BEST-EFFORT]`. The following methods are listed in order of accuracy.

**Method 1 — Exact tokenisation (preferred):** Run the target LLM's tokeniser over the object's serialised representation. Use the result directly.

**Method 2 — Character-based approximation:** Use `ceil(char_count / chars_per_token)` where `chars_per_token` is configurable per language and representation type.

| Language / content type | Default chars_per_token |
|---|---|
| English prose | 4.2 |
| JSON / structured data | 3.5 |
| Code | 3.0 |
| Chinese, Japanese, Korean | 1.5 |
| Arabic, Hebrew | 2.0 |

These values are `[HYPOTHESIS]`. The correct value is tokeniser and language dependent. Implementations MUST allow per-namespace override of these defaults and SHOULD validate against the actual tokeniser output for any production deployment.

**Method 3 — Pre-computed stored estimate:** Store `token_cost_estimate` on the memory object at creation time using Method 2. Use this as a fast approximation. Update it during organiser passes.

Implementations MUST use Method 1 for context budget enforcement if the budget is within 15% of full. Method 2 or 3 may be used for early-stage ranking to avoid the cost of full tokenisation on every candidate.

---

## 4. Object Representation Formats

Each memory object may be represented at multiple levels of detail. The appropriate level is chosen by the context packer based on the available budget.

| Representation | Content | Approximate token cost |
|---|---|---|
| `REFERENCE` | `[Object type: ID]` — a pointer only. Used when an object's existence matters but content does not fit. | 5–10 tokens |
| `KEY_VALUE` | Type, value, confidence, date. Single line. | 15–30 tokens |
| `STRUCTURED` | Full object fields as formatted key-value pairs. | 30–80 tokens |
| `PROSE` | LLM-generated natural language summary of the object. | 40–120 tokens |
| `FULL` | Raw JSON of the full memory object including all metadata. | 80–300 tokens |

**Representation selection policy:**

```
if token_budget > 50% remaining:
    use STRUCTURED for top-ranked objects
    use KEY_VALUE for lower-ranked objects
elif token_budget > 20% remaining:
    use KEY_VALUE for all objects
    use REFERENCE for low-ranked objects
else:
    use REFERENCE for all objects except pinned (which use KEY_VALUE)
```

Implementations MAY use PROSE representation for summaries (SUMMARY type objects). PROSE MUST NOT be generated on-the-fly during retrieval unless the implementation pre-generates prose summaries during organiser passes.

---

## 5. Context Packing Algorithm

Context packing is a greedy bin-packing problem. The following algorithm MUST be implemented.

**Inputs:**
- `ranked_objects`: List of memory objects sorted by composite score (descending).
- `context_budget`: Integer token limit for the memory bundle.
- `overhead_tokens`: Reserved tokens for bundle structure/formatting. Default: 100.

**Algorithm:**

```
available = context_budget - overhead_tokens
included = []
excluded = []

# Priority 1: Always include pinned objects and active tasks
for obj in ranked_objects where obj.salience == 1.0 or obj.type == TASK:
    cost = token_cost(obj, STRUCTURED)
    if cost <= available:
        include(obj, STRUCTURED)
        available -= cost
    else:
        cost_kv = token_cost(obj, KEY_VALUE)
        if cost_kv <= available:
            include(obj, KEY_VALUE)
            available -= cost_kv
        else:
            include(obj, REFERENCE)
            available -= token_cost(obj, REFERENCE)

# Priority 2: Multi-path objects (path_count >= 2) by STR descending
multi_path = [obj for obj in ranked_objects if obj.path_count >= 2 and obj not in included]
multi_path.sort(by=STR, descending=True)
for obj in multi_path:
    representation = select_representation(available, obj)
    cost = token_cost(obj, representation)
    if cost <= available:
        include(obj, representation)
        available -= cost
    else:
        if token_cost(obj, REFERENCE) <= available:
            include(obj, REFERENCE)
            available -= token_cost(obj, REFERENCE)
        else:
            exclude(obj, reason="BUDGET_EXHAUSTED")

# Priority 3: Remaining objects by composite score descending
remaining = [obj for obj in ranked_objects if obj not in included and obj not in excluded]
remaining.sort(by=composite_score, descending=True)
for obj in remaining:
    representation = select_representation(available, obj)
    cost = token_cost(obj, representation)
    if cost <= available:
        include(obj, representation)
        available -= cost
    else:
        exclude(obj, reason="BUDGET_EXHAUSTED")
        if available < token_cost(smallest_representable_object, REFERENCE):
            break  # No point continuing — budget is exhausted
```

The packing algorithm is deterministic. Given the same input list and budget, it MUST produce the same bundle.

---

## 6. Bundle Structure

The assembled bundle MUST follow this structure within the LLM context:

```
<memory>
  <namespace id="..." name="...">
    <entities>
      [Entity snapshots — canonical name, type, key facts]
    </entities>
    <facts>
      [High-confidence FACT objects — value, confidence, source_type, date]
    </facts>
    <decisions>
      [Recent DECISION objects — value, context, date]
    </decisions>
    <tasks>
      [ACTIVE TASK objects — value, status, created_at]
    </tasks>
    <context>
      [Other included objects not fitting above categories]
    </context>
    <references>
      [REFERENCE-level objects — pointer only]
    </references>
  </namespace>
  <metadata>
    <tokens_used>1840</tokens_used>
    <context_budget>4096</tokens_budget>
    <conflicts>[List of object_ids with conflict_flag=true]</conflicts>
    <excluded_count>23</excluded_count>
  </metadata>
</memory>
```

The XML-like structure is for illustration. Implementations MAY use JSON, Markdown, or other serialisation formats. The section grouping MUST be preserved regardless of format.

**Conflicts MUST be surfaced.** If the bundle contains two objects with a `CONTRADICTS` edge between them, both must be included in the `<context>` section with a `<!-- CONFLICT -->` annotation, and listed in `<metadata><conflicts>`. The LLM MUST be aware of the conflict; it MUST NOT silently receive both as if they were consistent facts.

---

## 7. Budget Overflow and Degradation

When the context budget is insufficient to include all ACTIVE tasks and pinned objects:

1. First, downgrade pinned objects to KEY_VALUE or REFERENCE representation.
2. If still over budget, include only the highest-STR pinned objects and all ACTIVE tasks.
3. If still over budget, log a `BUDGET_OVERFLOW` warning. Include all ACTIVE tasks as REFERENCE only. Do not include pinned objects.
4. Never exceed the budget. Truncation of a memory object mid-representation is NOT permitted.

**Fail-safe behaviour:** If the context budget is zero or negative (an error state), return an empty bundle with a `BUDGET_ERROR` flag rather than an error response. An empty memory bundle is always a valid degraded state.

---

## 8. STR-Based Memory Quality Metrics

The STR metric, aggregated over a session, provides a quality signal for the memory system:

```
session_STR_mean = mean(STR(obj) for obj in session_included_objects)
session_STR_p10  = 10th percentile STR for included objects
budget_efficiency = tokens_used_by_included / total_tokens_available
budget_waste = tokens_used_by_REFERENCE_objects / total_tokens_available
```

These metrics SHOULD be tracked per session and surfaced to the feedback loop (RFC-0009). A declining `session_STR_mean` over time suggests that memory quality is degrading (increasingly verbose or irrelevant objects are being included). An increasing `budget_waste` suggests that too many objects are being included at REFERENCE level only and the budget allocation needs review.

---

## 9. Conformance Requirements

`[REQUIRED FOR TIER 2]`

- Token cost estimation MUST use Method 1 when within 15% of budget limit.
- All four representation formats MUST be supported.
- The context packing algorithm in Section 5 MUST be implemented in priority order.
- Conflicts MUST be surfaced in the bundle with explicit annotations.
- The context budget MUST NEVER be exceeded.
- An empty bundle MUST be returned as a valid response when the budget is zero.
- STR-based metrics in Section 8 MUST be computed and available to the feedback loop.

---

## 10. Benchmark Requirement

Before this RFC progresses to Experimental status, implementers MUST publish:

- Token cost estimation accuracy: error rate between estimated and actual token counts for the target tokeniser across 1000 objects of mixed types and languages.
- Budget adherence test: verify that the packing algorithm never exceeds the budget across 10,000 simulated retrieval requests.
- Context quality benchmark: measure downstream LLM task accuracy with and without STR-based representation selection. `[HYPOTHESIS — STR-based packing improves task accuracy compared to full-representation packing at equivalent budget]`

---

*End of RFC-0006*


## Rendering clarification (2026-09-12)

Packed JSON records label graph lifecycle as `memory_lifecycle`, not the ambiguous
`state`. This describes the memory record's verification/maintenance status, not
whether the reported event or decision was provisional. `epistemic` and the full
source wording remain present. A local reader diagnostic turned an unqualified
reported decision into a "tentative" decision when the generic key was used;
renaming the key preserved both firm and actually provisional source wording in
that small diagnostic. This is not a broad answer-accuracy claim. Public
`MemoryNode.lifecycle_state` and bundle candidate metadata are unchanged. The
complete rendered output, including metadata keys, remains token-counted.
