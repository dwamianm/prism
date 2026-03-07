# RFC-0015: Self-Organizing Memory Execution Model

**Status:** Draft
**Tier:** 3 — Lifecycle
**Version:** 1.0
**Date:** 2026-03-06
**Depends on:** RFC-0000, RFC-0001, RFC-0002, RFC-0003, RFC-0005, RFC-0007, RFC-0008, RFC-0009

---

## 1. Abstract

This RFC specifies the execution model for PRME's self-organizing memory: the concrete mechanism by which decay, promotion, archival, deduplication, summarization, and feedback are applied to the memory graph over time.

RFCs 0007, 0008, and 0009 define *what* must happen to memory objects over their lifecycle — decay functions, reinforcement formulas, feedback signals. They do not specify *how* or *when* these operations execute. RFC-0007 Section 8 assumes "a background process that applies decay on a scheduled basis." This assumption is incompatible with PRME's core design constraint: PRME is an embeddable, portable library, not a server. There is no persistent daemon, no event loop running between calls, and no external scheduler.

This RFC resolves that tension with a three-layer execution model that achieves self-organization without requiring a background process:

1. **Virtual Decay** — Decay is computed at read time, not applied on a schedule. Zero background work.
2. **Opportunistic Maintenance** — Lightweight state mutations piggyback on existing `retrieve()` and `ingest()` calls.
3. **Explicit Organize** — Heavy maintenance is triggered by the host application at natural lifecycle boundaries.

The result is a memory system that is always current, never stale, and fully portable.

---

## 2. Motivation

### 2.1 The Problem

PRME's portable memory pack is a set of files (`memory.duckdb`, `vectors.usearch`, `lexical_index/`). When the host application is not running, nothing runs. There is no daemon to apply decay, no cron job to promote tentative facts, no background thread to deduplicate entities.

This creates a gap between the Tier 3 specifications and the Tier 0 portability requirement:

| RFC | Assumes | Reality |
|---|---|---|
| RFC-0007 §8 | Organizer runs every 6 hours (RAPID/FAST decay) | No process exists between API calls |
| RFC-0007 §6 | Lifecycle transitions trigger at thresholds | Nothing evaluates thresholds unless called |
| RFC-0008 §9 | Reinforcement resets the decay clock | Decay clock is never "ticking" — it's just stored timestamps |
| RFC-0009 §2 | Feedback updates confidence/salience after retrieval | Updates require a write path that may not run |

### 2.2 The Insight

Most of what the organizer does can be reformulated:

- **Decay is a pure function of time.** Given `salience_base`, `decay_profile`, and `last_reinforced_at`, the effective salience at any moment is computable without mutation. There is no need to "apply" decay — it can be *evaluated* at query time.
- **Promotion and archival are threshold checks.** They can run on a bounded subset of nodes during any existing operation.
- **Heavy operations (dedup, summarization) are infrequent.** They can be triggered explicitly rather than scheduled.

The organizer does not need to be a separate process if we split its responsibilities into "things that can be computed lazily" versus "things that need to mutate state."

---

## 3. Layer 1: Virtual Decay

### 3.1 Principle

Decay MUST NOT be applied by mutating stored values on a schedule. Instead, each memory node stores its *base* scores and *decay metadata*. Effective scores are computed at read time using the decay formulas from RFC-0007.

This eliminates the need for any background process for decay. Scores are always current because they are always computed fresh.

### 3.2 Node Schema Additions

Each memory node MUST carry the following fields in addition to its existing `salience` and `confidence` fields:

| Field | Type | Default | Description |
|---|---|---|---|
| `decay_profile` | `DecayProfile` enum | Derived from `epistemic_type` | One of: PERMANENT, SLOW, MEDIUM, FAST, RAPID |
| `last_reinforced_at` | `datetime` | `created_at` | Timestamp of the most recent reinforcement event |
| `reinforcement_boost` | `float` | `0.0` | Cumulative reinforcement boost (capped per RFC-0008 §6) |
| `salience_base` | `float` | Same as initial `salience` | Baseline salience before decay |
| `confidence_base` | `float` | Same as initial `confidence` | Baseline confidence before decay |
| `pinned` | `bool` | `False` | If True, exempt from all automated decay |

The existing `salience` and `confidence` fields on `MemoryNode` become the *effective* values, computed at read time.

### 3.3 Decay Profile Assignment

At node creation, the `decay_profile` is assigned based on the `epistemic_type` using the mapping from RFC-0007 §3:

| Epistemic Type | Default Decay Profile | Lambda | Half-life |
|---|---|---|---|
| OBSERVED | SLOW | 0.005 | ~139 days |
| ASSERTED | MEDIUM | 0.020 | ~35 days |
| INFERRED | FAST | 0.070 | ~10 days |
| HYPOTHETICAL | RAPID | 0.200 | ~3.5 days |
| CONDITIONAL | MEDIUM | 0.020 | ~35 days |
| UNVERIFIED | RAPID | 0.200 | ~3.5 days |

The mapping MUST be configurable via `OrganizerConfig.decay_profile_mapping`.

### 3.4 Virtual Decay Computation

When a node is read from storage (during retrieval, query, or any get operation), its effective scores MUST be computed as follows:

```
t = days_since(last_reinforced_at, now)
lambda = decay_lambda[decay_profile]
mu = lambda * 0.5
rho = 0.10

# Salience (RFC-0007 §3 + §5)
effective_salience = salience_base * exp(-lambda * t)
                   + reinforcement_boost * exp(-rho * t)

# Confidence (RFC-0007 §4)
effective_confidence = confidence_base * exp(-mu * t)
```

**Exemptions:**
- If `pinned == True`: effective values equal base values (no decay).
- If `decay_profile == PERMANENT`: effective values equal base values.
- If `lifecycle_state` is ARCHIVED or DEPRECATED: no decay applied (already terminal or near-terminal).
- Confidence decay for OBSERVED nodes: only applied if `t > 180 days` (per RFC-0007 §4).

### 3.5 Scoring Integration

The retrieval pipeline's scoring stage (RFC-0005) MUST use the virtual effective scores, not the stored base scores. The `RetrievalCandidate` MUST carry the effective values. Score traces MUST record the effective values used.

The existing recency factor in scoring (`exp(-recency_lambda * days)`) is distinct from decay. Recency is a retrieval-time ranking signal. Decay is a lifecycle property of the memory object. Both are applied.

### 3.6 Determinism

Virtual decay is deterministic: given the same node state and the same `now` timestamp, the effective scores are identical. Implementations MUST use a consistent `now` timestamp within a single retrieval operation (set once at the start of the pipeline, reused for all candidates).

---

## 4. Layer 2: Opportunistic Maintenance

### 4.1 Principle

During existing `retrieve()` and `ingest()` calls, the engine SHOULD perform a bounded amount of lightweight state mutation. This piggybacks on operations the host application is already performing — no additional API calls or scheduling required.

Opportunistic maintenance is **bounded** (capped by time and node count), **skippable** (respects a cooldown interval), and **configurable** (can be disabled entirely).

### 4.2 Trigger Conditions

Opportunistic maintenance runs when ALL of the following are true:

1. `OrganizerConfig.opportunistic_enabled` is `True` (default: `True`).
2. At least `OrganizerConfig.opportunistic_cooldown` seconds have elapsed since the last maintenance pass (default: 3600 seconds / 1 hour).
3. The current operation is `retrieve()` or `ingest()` (not `store()`, which is a fast path).

The `last_maintained_at` timestamp is stored in the engine's runtime state (not persisted — it resets on restart, which is intentional: the first operation after a restart triggers maintenance).

### 4.3 Maintenance Jobs

When triggered, the following jobs run sequentially within a single maintenance pass:

#### 4.3.1 Auto-Promotion

Query tentative nodes that meet promotion criteria:

```
SELECT node_id FROM nodes
WHERE lifecycle_state = 'tentative'
  AND created_at < now() - promotion_age_threshold
  AND evidence_ref_count >= promotion_evidence_threshold
LIMIT batch_size
```

Default thresholds `[HYPOTHESIS]`:
- `promotion_age_threshold`: 7 days
- `promotion_evidence_threshold`: 2 evidence refs
- `batch_size`: 50

Nodes meeting both criteria are promoted to STABLE via the existing `promote()` transition. Each promotion is logged as a `PROMOTE` operation with `trigger: "opportunistic_auto"`.

#### 4.3.2 Threshold Archival

Query active nodes whose virtual effective salience has fallen below the archive threshold (RFC-0007 §6):

```
For each candidate node:
  effective_salience = compute_virtual_salience(node, now)
  if effective_salience < 0.10 and effective_confidence < 0.40:
    → transition to DEPRECATED
  elif effective_salience < 0.05:
    → transition to ARCHIVED
```

This evaluates the RFC-0007 §6 thresholds that would otherwise require a background process. Limited to `batch_size` nodes per pass.

#### 4.3.3 Feedback Application

Process any pending feedback signals that were recorded during retrieval (RFC-0009) but not yet applied to confidence/salience base values. This applies the reinforcement and penalty formulas from RFC-0008.

Pending signals are identified by `FEEDBACK_EVENT` operations that have not yet been followed by a corresponding `REINFORCE` or `PENALTY` operation for the same object.

### 4.4 Time Budget

The entire maintenance pass MUST complete within `OrganizerConfig.opportunistic_budget_ms` milliseconds (default: 200ms). If the budget is exhausted mid-pass, remaining work is deferred to the next pass. The engine MUST NOT block user-facing operations for longer than the budget.

### 4.5 Failure Handling

If any maintenance job fails, the failure is logged and the remaining jobs still execute. Maintenance failures MUST NOT propagate to the caller — the `retrieve()` or `ingest()` call MUST still succeed.

---

## 5. Layer 3: Explicit Organize

### 5.1 Principle

Heavy maintenance operations that are too expensive or too complex for opportunistic execution are exposed as an explicit `engine.organize()` method. The host application decides when to call it.

This is the escape hatch for operations that genuinely need dedicated time: full-corpus deduplication, entity alias resolution, summarization passes, and comprehensive archival sweeps.

### 5.2 API

```python
async def organize(
    self,
    *,
    user_id: str | None = None,
    jobs: list[str] | None = None,
    budget_ms: int = 5000,
) -> OrganizeResult
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `user_id` | `str \| None` | `None` | Scope to a single user. None = all users. |
| `jobs` | `list[str] \| None` | `None` | Specific jobs to run. None = all applicable jobs. |
| `budget_ms` | `int` | `5000` | Time budget in milliseconds. |

**Returns:** `OrganizeResult` with per-job summaries.

### 5.3 Available Jobs

| Job name | Description | Depends on |
|---|---|---|
| `promote` | Auto-promote eligible tentative nodes to stable | RFC-0003 |
| `decay_sweep` | Evaluate all active nodes for threshold transitions | RFC-0007 §6 |
| `archive` | Archive nodes below force-archive threshold | RFC-0007 §6 |
| `deduplicate` | Detect and merge duplicate entities and facts | RFC-0001 |
| `alias_resolve` | Resolve entity aliases (e.g., "JS" → "JavaScript") | RFC-0001 |
| `summarize` | Generate summary nodes from event windows | RFC-0001, RFC-0006 |
| `feedback_apply` | Apply all pending feedback signals | RFC-0008, RFC-0009 |
| `centrality_boost` | Recalculate graph centrality salience boost | RFC-0007 §11 |
| `tombstone_sweep` | Enforce retention policies and create tombstones | RFC-0007 §9 |

### 5.4 OrganizeResult

```python
class OrganizeResult(BaseModel):
    jobs_run: list[str]
    jobs_skipped: list[str]
    duration_ms: float
    budget_remaining_ms: float
    per_job: dict[str, JobResult]

class JobResult(BaseModel):
    job: str
    nodes_processed: int
    nodes_modified: int
    errors: int
    duration_ms: float
    details: dict[str, Any]  # Job-specific metrics
```

### 5.5 Recommended Trigger Points

The host application SHOULD call `organize()` at these lifecycle boundaries:

| Trigger | Recommended jobs | Rationale |
|---|---|---|
| Session end | `promote`, `feedback_apply` | Finalize session learnings |
| Application startup | `decay_sweep`, `archive`, `promote` | Catch up after idle period |
| Periodic (if host has a scheduler) | All | Full maintenance pass |
| After bulk import | `deduplicate`, `alias_resolve`, `summarize` | Clean up imported data |

### 5.6 Session End Helper

A convenience method for the common "end of conversation" pattern:

```python
async def end_session(
    self,
    *,
    user_id: str,
    session_id: str | None = None,
) -> OrganizeResult
```

This runs a lightweight organize pass with jobs `["promote", "feedback_apply"]` and a 1-second budget. It is semantically equivalent to calling `organize()` with those parameters.

---

## 6. Configuration

### 6.1 OrganizerConfig

```python
class OrganizerConfig(BaseModel):
    # Layer 2: Opportunistic maintenance
    opportunistic_enabled: bool = True
    opportunistic_cooldown: int = 3600          # seconds between passes
    opportunistic_budget_ms: int = 200          # max time per pass
    opportunistic_batch_size: int = 50          # max nodes per job per pass

    # Layer 3: Explicit organize defaults
    default_organize_budget_ms: int = 5000

    # Auto-promotion thresholds [HYPOTHESIS]
    promotion_age_days: float = 7.0
    promotion_evidence_count: int = 2

    # Decay profile mapping (epistemic_type → DecayProfile)
    decay_profile_mapping: dict[str, str] = {
        "observed": "SLOW",
        "asserted": "MEDIUM",
        "inferred": "FAST",
        "hypothetical": "RAPID",
        "conditional": "MEDIUM",
        "unverified": "RAPID",
    }

    # Archive thresholds (from RFC-0007 §6)
    archive_salience_threshold: float = 0.10
    archive_confidence_threshold: float = 0.40
    force_archive_salience_threshold: float = 0.05
    deprecate_confidence_threshold: float = 0.15
```

### 6.2 Integration with PRMEConfig

`OrganizerConfig` is a nested config within `PRMEConfig`:

```python
class PRMEConfig(BaseSettings):
    # ... existing fields ...
    organizer: OrganizerConfig = OrganizerConfig()
```

Environment variable prefix: `PRME_ORGANIZER__` (e.g., `PRME_ORGANIZER__OPPORTUNISTIC_COOLDOWN=1800`).

---

## 7. DecayProfile Enum

```python
class DecayProfile(str, Enum):
    PERMANENT = "permanent"   # lambda = 0.000, no decay
    SLOW      = "slow"        # lambda = 0.005, half-life ~139 days
    MEDIUM    = "medium"      # lambda = 0.020, half-life ~35 days
    FAST      = "fast"        # lambda = 0.070, half-life ~10 days
    RAPID     = "rapid"       # lambda = 0.200, half-life ~3.5 days
```

The lambda values MUST be configurable via `OrganizerConfig` but the enum values are fixed. Custom decay rates are achieved by overriding the lambda-per-profile mapping, not by adding new profiles.

---

## 8. Interaction with Existing RFCs

### 8.1 RFC-0007 (Decay and Forgetting)

This RFC **supersedes RFC-0007 Section 8** (Decay Scheduling and the Organiser). The decay functions, profiles, and thresholds defined in RFC-0007 Sections 3-7 remain authoritative. The execution model changes from "background process on a schedule" to the three-layer model defined here.

Specifically:
- The "every 6 hours" / "daily" / "weekly" schedule in RFC-0007 §8 is replaced by virtual decay (always current) plus opportunistic and explicit maintenance.
- All `DECAY_APPLIED` operation logging requirements from RFC-0007 §8 apply only to threshold transitions (Layer 2 and Layer 3), not to virtual decay computation (Layer 1). Virtual decay does not generate operations because no state is mutated.

### 8.2 RFC-0008 (Confidence Evolution)

Reinforcement and penalty signals (RFC-0008 §2-4) update `salience_base`, `confidence_base`, and `reinforcement_boost` on the node. They also reset `last_reinforced_at` to the current timestamp. The virtual decay formulas then use these updated base values.

The saturation controls (RFC-0008 §6) apply to the base values, not to the virtual effective values.

### 8.3 RFC-0009 (Feedback Loop)

Feedback signals are recorded as `FEEDBACK_EVENT` operations during retrieval. They are applied to node base values either:
- During opportunistic maintenance (Layer 2, §4.3.3), or
- During explicit organize (Layer 3, `feedback_apply` job), or
- Inline during `ingest()` if the ingestion pipeline detects a correction signal.

The feedback session records (RFC-0009 §6) are unaffected by this RFC.

### 8.4 RFC-0005 (Hybrid Retrieval)

The scoring stage MUST use virtual effective scores. The `recency_factor` in composite scoring is a separate signal from decay — both are applied. The `salience` component in the scoring formula uses `effective_salience`, not `salience_base`.

---

## 9. Portability Implications

### 9.1 Memory Pack Compatibility

The memory pack format gains new columns on the `nodes` table but remains a set of copyable files. No additional files or processes are required.

When a memory pack is opened after an idle period (hours, days, weeks), the virtual decay model automatically reflects the elapsed time — no "catch-up" computation is needed. The first `retrieve()` call returns correctly decayed scores immediately.

### 9.2 Rebuild from Event Log

Virtual decay metadata (`decay_profile`, `last_reinforced_at`, `reinforcement_boost`, `salience_base`, `confidence_base`) is derivable from the event log:
- `decay_profile` is determined by `epistemic_type` at creation time.
- `salience_base` and `confidence_base` are the initial values modified by `REINFORCE` and `PENALTY` operations.
- `last_reinforced_at` is the timestamp of the most recent `REINFORCE` operation (or `created_at` if none).
- `reinforcement_boost` is computable from the sequence of `REINFORCE` operations.

All fields satisfy the deterministic rebuild requirement (RFC-0001 §4.6).

### 9.3 Backend Agnostic

The three-layer model works identically for both DuckDB and PostgreSQL backends. Virtual decay is computed in Python, not in SQL. Opportunistic maintenance uses the same `GraphStore` API regardless of backend. The `organize()` method is backend-agnostic.

---

## 10. Conformance Requirements

`[REQUIRED FOR TIER 3]`

### Layer 1 (Virtual Decay)

1. Implementations MUST store `decay_profile`, `last_reinforced_at`, `reinforcement_boost`, `salience_base`, and `confidence_base` on every memory node.
2. Effective salience and confidence MUST be computed at read time using the formulas in Section 3.4.
3. Pinned nodes and PERMANENT decay profile nodes MUST be exempt from decay.
4. A consistent `now` timestamp MUST be used within a single retrieval operation.
5. The retrieval pipeline MUST use effective (virtual) scores, not stored base scores.

### Layer 2 (Opportunistic Maintenance)

6. Opportunistic maintenance MUST respect the configured time budget.
7. Maintenance failures MUST NOT propagate to the caller.
8. All state transitions performed during maintenance MUST be logged as operations.
9. Opportunistic maintenance MUST be disableable via configuration.

### Layer 3 (Explicit Organize)

10. The `organize()` method MUST be exposed on `MemoryEngine`.
11. All jobs listed in Section 5.3 MUST be supported.
12. `OrganizeResult` MUST provide per-job metrics.
13. The `end_session()` convenience method MUST be provided.

### General

14. Virtual decay metadata MUST be rebuildable from the event log.
15. This RFC supersedes RFC-0007 Section 8. Implementations MUST NOT require a background process for decay.

---

## 11. Benchmark Requirements

Before this RFC progresses to Experimental status, implementers MUST publish:

1. **Virtual decay correctness:** Verify that `effective_salience` at `t = half_life` equals 50% of `salience_base` (within floating-point tolerance) for each decay profile.
2. **Idle period accuracy:** Open a memory pack after 30 days of inactivity. Verify that the first `retrieve()` call returns correctly decayed scores without any prior organize() call.
3. **Opportunistic budget compliance:** Run 100 retrieval operations with opportunistic maintenance enabled. Verify that no single maintenance pass exceeds `opportunistic_budget_ms` by more than 10%.
4. **Organize throughput:** Measure `organize()` wall time for corpora of 1K, 10K, and 100K nodes with all jobs enabled.
5. **Deterministic rebuild:** Given identical event logs, verify that all virtual decay metadata fields are identical after rebuild.
6. **`[HYPOTHESIS]` Retrieval quality:** Compare retrieval precision@5 between a system with no organizing, virtual-decay-only, and full three-layer organizing across 100 sessions. The hypothesis is that each layer incrementally improves precision.

---

*End of RFC-0015*
