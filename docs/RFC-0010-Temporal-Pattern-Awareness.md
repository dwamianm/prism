# RFC-0010: RMS Temporal Pattern Awareness

**Status:** Draft
**Tier:** 4 — Advanced Capabilities
**Version:** 1.0
**Date:** 2026-02-19
**Depends on:** RFC-0000 through RFC-0009

---

## 1. Abstract

This RFC specifies temporal pattern awareness: the capacity of the memory system to detect recurring temporal structures in memory events and use that awareness to adjust retrieval salience. It covers recurrence detection, dormancy detection, and temporal salience modulation.

**Scope boundary:** This RFC covers detection of temporal patterns in memory access and content. It does NOT cover prediction of future user behaviour or intent (which is a research problem outside the current scope of the suite). `[HYPOTHESIS — prediction claims are explicitly deferred pending experimental validation]`

---

## 2. Motivation

Consider a user who reviews project status every Monday, discusses budget in the last week of each quarter, and has daily stand-up notes. A memory system unaware of these patterns will surface budget memory equally in February and in September-end. A temporally aware system can modulate salience to favour content whose temporal context is currently active.

This is useful. It is also bounded in scope — the goal is not to predict the future, but to recognise patterns that are already in the historical record and use them to make better retrieval decisions in the present.

---

## 3. Temporal Pattern Types

The system MAY detect the following temporal pattern types. None are required to be detected; each is independently enabled per namespace via configuration.

| PatternType | Description | Example |
|---|---|---|
| `DAILY` | Recurs approximately every 24 hours. | Morning stand-up notes. |
| `WEEKLY` | Recurs approximately every 7 days. | Weekly reviews on Mondays. |
| `MONTHLY` | Recurs approximately every 30 days. | Monthly billing cycles. |
| `QUARTERLY` | Recurs approximately every 90 days. | Quarterly planning sessions. |
| `ANNUAL` | Recurs approximately every 365 days. | Annual reviews or renewals. |
| `IRREGULAR_RECURRING` | Recurs but not on a fixed schedule. | Detected via clustering on access timestamps. |
| `DORMANT` | Previously frequent, now absent for longer than expected. | A project that has gone quiet. |
| `BURST` | Sudden increase in frequency not matching prior pattern. | Elevated activity on an issue. |

---

## 4. Pattern Detection Method

**Recurrence detection** is performed by the organiser during its weekly pass (RFC-0007, Section 8).

For each memory object with at least 5 access events, the organiser computes:

```
access_timestamps = sorted list of INJECTION_EVENT timestamps for this object
inter_access_intervals = [t[i+1] - t[i] for i in range(len(access_timestamps) - 1)]
mean_interval = mean(inter_access_intervals)
std_interval = std(inter_access_intervals)
coefficient_of_variation = std_interval / mean_interval
```

If `coefficient_of_variation < 0.30`, the object is considered to have a `REGULAR` access pattern. The pattern period is classified by `mean_interval` against the pattern type thresholds.

If `coefficient_of_variation >= 0.30`, the pattern is `IRREGULAR_RECURRING` if `mean_interval < 45 days` and the object has been accessed at least 5 times. Otherwise, no pattern is detected.

**Minimum sample requirement:** Pattern detection MUST NOT be performed on objects with fewer than 5 access events. Claiming a pattern exists from 2 or 3 data points is statistically unjustified.

**Dormancy detection:** If the last access event for a regularly-recurring object is older than `2 × mean_interval`, the object is classified as `DORMANT`.

**Burst detection:** If access frequency in the past 3 days exceeds the 90th percentile of the object's historical 3-day access count, a `BURST` pattern is noted. Burst detection MUST be treated as an observation, not a conclusion — it may indicate genuine increased relevance or may be noise.

---

## 5. Temporal Salience Modulation

Detected patterns are used to modulate retrieval salience. Modulation is additive to the base salience from RFC-0007.

```
if current_temporal_context matches object's detected pattern:
    salience_temporal_boost = pattern_boost_weight × match_confidence
else:
    salience_temporal_boost = 0.0

salience_effective_with_temporal = salience_effective (RFC-0007) + salience_temporal_boost
```

**`pattern_boost_weight`:** Default 0.10. This caps the maximum temporal contribution at 10% of the salience scale. This is deliberately conservative — temporal patterns are a weak signal and MUST NOT dominate the retrieval ranking.

**`match_confidence`:** Computed based on the strength of the pattern (inverse of coefficient_of_variation) and how well the current time matches the pattern's expected recurrence window.

**`[HYPOTHESIS]`** — The 0.10 cap and the matching algorithm require empirical tuning. These values are starting points based on design reasoning, not measurement.

---

## 6. Temporal Pattern Storage

Detected patterns are stored as structured metadata on memory objects. Patterns are NOT separate memory objects.

```json
"temporal_pattern": {
  "pattern_type": "WEEKLY",
  "mean_interval_days": 7.2,
  "std_interval_days": 1.1,
  "coefficient_of_variation": 0.15,
  "sample_count": 12,
  "first_detected_at": "<ISO8601>",
  "last_updated_at": "<ISO8601>",
  "pattern_version": "temporal_v1",
  "confidence": 0.78,
  "next_expected_window_start": null,
  "next_expected_window_end": null
}
```

**`next_expected_window_start` and `next_expected_window_end` are deliberately set to null by default.** Prediction of future occurrence windows is a research-grade feature and MUST NOT be enabled by default. Implementations MAY expose this as an experimental opt-in feature, but MUST clearly mark it as `[EXPERIMENTAL]` and MUST NOT include predictions in production retrieval scoring without explicit user consent.

---

## 7. Temporal Context at Retrieval Time

The retrieval pipeline (RFC-0005) receives the current timestamp as part of every query. The temporal pattern awareness layer computes, for each candidate:

```
time_since_last_access = now - last_access_timestamp
expected_interval = object.temporal_pattern.mean_interval_days (if pattern detected)

temporal_match_score = max(0.0, 1.0 - |time_since_last_access - expected_interval| / expected_interval)
```

`temporal_match_score` ranges from 0.0 (no temporal match) to 1.0 (perfect temporal match). It is multiplied by `pattern_boost_weight` to produce `salience_temporal_boost`.

If no pattern is detected, `temporal_match_score = 0.0` and `salience_temporal_boost = 0.0`.

---

## 8. Dormancy Surfacing

When the organiser detects that a previously active pattern has become DORMANT, it MAY create a SUMMARY memory object noting the dormancy:

```
type: SUMMARY
value: "Project Orion budget planning was regularly discussed (weekly) but has been dormant for 22 days."
epistemic_type: OBSERVED
source_type: SYSTEM_INFERRED
salience: 0.40   -- Modest salience; this is an observation, not a priority.
```

Dormancy summaries are informational. They MUST NOT be treated as alerts or triggers for user notification unless the implementation provides an explicit notification layer that the user has opted into.

---

## 9. Limitations and Constraints

**What this RFC does NOT include:**

- Prediction of future access times as a retrieval signal (deferred to a future experimental RFC).
- Correlation of temporal patterns across different users (a privacy violation).
- Temporal pattern learning from fewer than 5 data points.
- Use of day-of-week or time-of-day signals beyond interval analysis (these are available in the timestamp data but the benefit of fine-grained temporal matching has not been validated).

**Known failure modes:**

- Burst detection will produce false positives during normal periods of intensive work (a week of intensive project work will look like a BURST even if it is entirely normal).
- DORMANT classification may incorrectly flag intentional project pauses as concerning.
- The minimum 5-sample requirement means new objects have no temporal boost for their first several weeks of use.

---

## 10. Conformance Requirements

`[REQUIRED FOR TIER 4 — Temporal Pattern Awareness]`

- Pattern detection MUST NOT be performed on objects with fewer than 5 access events.
- The coefficient_of_variation threshold for REGULAR pattern classification MUST be 0.30 or configurable per namespace.
- Temporal salience boost MUST NOT exceed 0.10 of the full salience scale without explicit operator override.
- Predictions (`next_expected_window`) MUST be null by default.
- Burst detection MUST be logged as an observation, not a state change.
- All pattern metadata MUST reference the `pattern_version` policy used to compute it.

---

## 11. Benchmark Requirement

Before this RFC progresses to Experimental status, implementers MUST publish:

- Pattern detection precision/recall on a synthetic dataset of 500 objects with known regular and irregular access patterns (target: ≥80% precision for DAILY, WEEKLY, MONTHLY patterns with ≥10 samples).
- False positive rate for BURST detection on a control dataset of steady-frequency access.
- `[HYPOTHESIS]` — Retrieval improvement: measure precision@5 on a temporally-patterned workload with and without temporal salience modulation.

---

*End of RFC-0010*
