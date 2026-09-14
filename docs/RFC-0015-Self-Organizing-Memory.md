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

Request-triggered passes inherit the requesting `user_id`, including pending
materialization, promotion, and archival. Cooldowns are independent per tenant,
with at most one background pass in flight. The in-memory cooldown table retains
up to 4,096 recently maintained users; eviction permits an earlier next pass,
never a change of scope. An explicit unscoped runner call is operator maintenance.

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

PRME schedules the pass in the background so the caller does not await it.
`OrganizerConfig.opportunistic_budget_ms` (default 200ms) is a cooperative budget:
the runner checks its deadline before each job query and node mutation, and gives
pending materialization only the remaining time. Once exhausted, it defers further
work. A database or index write already in flight may finish after the deadline;
it is not cancelled midway through a durable operation. This supersedes the
original hard wall-clock guarantee, which synchronous backend operations cannot
provide safely. Node-count bounds remain in effect.

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
| `feedback_apply` | Legacy global tuner; explicit unscoped operator selection only | RFC-0008, RFC-0009 |
| `centrality_boost` | Proposed graph centrality salience experiment; not registered | RFC-0007 §11 |
| `tombstone_sweep` | Enforce retention policies and create tombstones | RFC-0007 §9 |
| `snapshot_generation` | Generate entity snapshots for active entities | RFC-0006 |
| `consolidate` | Cluster similar memories into summary abstractions | RFC-0006 |
| `index_compaction` | Evict vector/lexical entries for inactive nodes | RFC-0002 |

Index compaction preserves prepared vector identities with no graph node yet
(RFC-0016). Their durable staging claims distinguish them from ordinary orphaned
index entries. This includes derivations, entity profiles, and extractive
consolidation publications. Published inactive nodes remain eligible for eviction.
Automatic collection of abandoned consolidation identities is not yet exposed;
an offline rebuild collects them.

Consolidation currently produces an extractive excerpt of up to three sources,
not a lossless abstraction of the entire cluster. Clusters and their summaries
must retain one user and scope. Excerpts preserve complete text, source identity,
episode dates, validity windows, and epistemic labels. Automatic retirement
requires recorded coverage matching the current source and an unchanged active
summary. Omitted, changed, pinned, recent, high-confidence, or other-namespace
sources remain active. Legacy summaries without coverage metadata cannot
authorize retirement. Similarity alone is not evidence that details are redundant.

Greedy cluster discovery orders sources by owner, scope, source time and content,
with UUID only as a final exact-duplicate tie-breaker. Centroid and excerpt-source
ties use the same source order after confidence. Equivalent histories therefore
do not change clustering merely because ingestion generated different UUIDs.

Summary creation uses a checksummed `ConsolidationPublication`. A request hash
covers every source snapshot, selected-source order, exact rendered content,
scores, policy version, and embedding identity. The owner, scope, and sorted
source identities form its lineage key. An unchanged active request reuses one
summary. A changed source under the same lineage creates a new generation; new
node and `DERIVED_FROM` edges, predecessor archival, generation advancement, and
the complete `CONSOLIDATION_PUBLISHED` operation commit atomically. PostgreSQL
writes pgvector in that transaction. DuckDB first journals
`CONSOLIDATION_PREPARED`, reserves the artifact identity, and stages the exact
vector and lexical document under a changing database fence. A restart reuses
that saved numerical input without re-embedding. Independent engines either
return the same committed identity or retry a transient staging conflict.

The lineage is intentionally exact for a fixed source-identity set. If clustering
adds or removes a source, it forms a new lineage; the earlier summary remains a
separate generated view until normal lifecycle maintenance archives it. This
avoids guessing that two changing similarity clusters represent the same concept.

`forget_consolidated()` rechecks each source and its summary inside a backend
transaction. Coverage, summary content, owner/scope, active state, source event
time, pinning, age, confidence and retained evidence must still match the
retirement policy. The source transition, supersedence edge and checksummed
`CONSOLIDATION_RETIRED` before/after record commit together. The record includes
the summary snapshot and policy clock, using the lossless metadata snapshot
encoding. An ineligible or already retired source is a no-op. Storage failures
propagate; there is no fallback that archives a source after supersedence fails.
External index eviction follows commit and remains repairable.

PostgreSQL locks both endpoints in UUID order. DuckDB makes a real `updated_at`
write on both endpoints and rolls it back when retirement is ineligible; a no-op
assignment can be optimized away. Local schema initialization removes
the `idx_nodes_lifecycle` ART index: changing that indexed field could replace
a row and bypass a concurrent column claim. Owner, type and scope indexes remain.
Raw external SQL, custom mutable-column indexes and historical unjournaled
retirements are outside this guarantee.

Duplicate and alias discovery partitions exact/string matches by owner and scope.
Semantic searches request the same scope and verify each returned node against
the durable graph before proposing a pair. Both apply functions recheck owner
and scope before transferring evidence, redirecting edges, superseding a node,
or linking aliases. This also applies to unscoped operator runs and manually
supplied candidate lists. The same owner's PERSONAL and PROJECT memories must
remain independent even when their text or entity names match. These checks do
not implement the broader namespace grant hierarchy in RFC-0004.

Automatic merges additionally preserve memory/entity type, source and epistemic
classification, session, event time, validity end, retention/pinning and complete
metadata. Unresolved personal references are excluded from canonical identity
merges and alias links. Non-entity duplicate copies require exact content and the
same validity start; two separately admitted observations are not interchangeable.
Similarity remains a proposal signal, not proof of equivalence. Duplicate jobs
report `pairs_not_applied` alongside candidates/merges and the policy version.
Known compatible name variants can merge; purely semantic alias candidates only
create unverified `RELATES_TO` links, even above the former merge threshold.
Apply functions recheck actual names and values rather than trusting candidate
labels. These rules supersede automatic similarity-only deduplication and alias
merging. See [the operational contract](ENTITY-IDENTITY.md) for limitations and
historical-data behavior.

The separate `consolidate_knowledge()` convenience API also processes one scope
at a time. Its optional `scope` argument is available on the async engine and
sync client; omission visits each scope separately. Profile graph nodes and
lexical entries inherit the source scope. Earlier generated entity profiles
are excluded from source matching so they cannot perpetuate mixed-scope content
or recursively include themselves. On an explicit rebuild, existing profile
names are reconsidered even if source counts fall below automatic discovery's
threshold. Profiles without two eligible same-scope sources are archived and
evicted; underlying source nodes and events remain intact. This handles obsolete
legacy derived profiles in the scopes actually processed, without moving source
memories between namespaces. It does not repair prior duplicate evidence writes.

Profile format version 2 uses complete literal entity-name boundaries, preserving
possessives without matching `Ann` inside `Joanna`. Distinct source identities
remain distinct even when their text or first 80 characters match. It includes
whole source excerpts with source IDs, event/recording dates, validity and
original epistemic/provenance labels. The generated association is INFERRED /
SYSTEM_INFERRED, with confidence capped by the configured inferred matrix value
and the least-confident included source. It uses the inferred FAST decay profile;
it does not upgrade quoted conditional or hypothetical statements into observed
facts. Event evidence references and included source-node identities are retained.

`max_profile_tokens`, exposed by both the async engine and sync client, is now
an exact limit under the configured packing tokenizer,
including the complete profile header, separators and source metadata. Oversized
sources are skipped intact so smaller later sources can fit; omitted sources
remain active. Metadata records included/available source counts, encoding and
tokens. If no complete source fits, no new profile is published. Names and token
limits are validated before storage access. Existing profiles take this format
on explicit rebuild; prior artifacts are not silently rewritten. Name matching
is still a heuristic association.

Source collection uses scoped pages ordered by immutable node ID, rather than a
newest-5,000 cutoff. Explicit entity names retain only matching source nodes in
memory; automatic discovery still examines the full active scope. An interrupted
scan fails before any profile publication or retirement. Source snapshots are
rechecked at publication; the paginated scan itself is not a global database
snapshot of concurrent writes.

Each replacement now prepares an embedding before publishing graph state. Local
DuckDB stages the exact numerical vector and commits the lexical document first;
PostgreSQL writes pgvector and generated text search in its graph transaction.
`ProfilePublication` is a separate primitive from assertion supersedence. Only
active inferred entity profiles can be published; only same-owner, same-scope,
same-entity profiles can be retired. It checks exact source/prior-node snapshots,
the complete active prior-profile set and a database publication generation.
New profile creation, predecessor archival, generation advancement and an
immutable `PROFILE_PUBLISHED` operation commit together. The operation retains
the complete plan (including numerical embedding) as a JSON string plus checksum.
Replay returns the committed identity without reactivating subsequently archived
profiles. Local index eviction of predecessors happens after commit.

Failures now propagate instead of incrementing a success count. Source changes
and concurrent rebuilds raise public `StaleProfileError`; a fresh explicit call
rebuilds the plan. Cancellation or lost acknowledgement never compensates by
deleting a possibly committed view. A call spanning several entities/scopes
still consists of separate publications, and retiring unsupported old profiles
uses the existing archive path.

Profile preparation is now journaled as a checksummed `PROFILE_PREPARED` operation
before external staging. Its fixed node identity, complete inputs and numerical
embedding survive restart. Matching requests reuse the saved plan; a different
explicit request atomically abandons pending predecessors and records
`PROFILE_PREPARATION_REPLACED`. Prepared identities reserve the same global
artifact namespace used by derivations. Managed staging holds a changing work-row
epoch against replacement and publication for the duration of the native write;
a no-op SQL update is insufficient on supported DuckDB builds.

`profile_jobs`, `resume_profile` and `process_profiles` expose scoped inspection
and explicit recovery through both Python clients. Successful graph publication
and completed work state commit together. Recovery never repeats model inference;
changed dependencies fail visibly. A cooperative budget applies between jobs,
and failed attempts move behind unattempted work. Missing work and reservation
rows are restored from validated immutable operations at startup. Corrupt journal
records remain unregistered with identity-only diagnostics and prevent retired
index collection; legacy ownership collisions remain ambiguous. Reconstructed
work resets operational attempt diagnostics, which are not immutable history.

Local CLI equivalents require `--user-id`: `profile-jobs`, `process-profiles`,
`resume-profile`, `discard-profile` and `collect-profile-staging`. Batch operations
accept a scope, limit and cooperative budget. JSON results remain on stdout;
diagnostics use stderr. Failures and blocked collection return nonzero status.
An explicit local file cannot be redirected by an ambient database URL. The
[profile guide](ENTITY-PROFILES.md) describes exact results and exit behavior.

`discard_profile` explicitly abandons owned unpublished work and appends an
immutable `PROFILE_PREPARATION_DISCARDED` receipt. It is idempotent, cannot retire
completed publications, and shares the changing work epoch with native staging.
Startup reconstruction validates discard receipts before restoring abandoned work.

`collect_profile_staging` explicitly reclaims uniquely owned abandoned inputs.
It validates the journal, graph absence and exact native contents while holding
a work-epoch transaction around each native deletion. Lexical deletion precedes
vector removal; an immutable `PROFILE_STAGE_COLLECTED` receipt follows both.
Cancellation, native failures and process exit retain discoverable work, even if
both deletions completed before acknowledgement. Failed attempts move behind
unattempted work. Unknown ownership blocks reclamation; mismatched entries are
retained. PostgreSQL acknowledges eligible abandoned preparations without graph
mutation, because it has no external pre-publication indexes. Source nodes,
immutable receipts and reservations remain intact. There is no automatic profile
scheduler or implicit collection of unpublished profiles by ordinary compaction. Publication completion describes the graph commit; predecessor
index eviction occurs afterward. An interrupted call must not be described as
completed without checking its saved work state.

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
| Session end | `promote` | Promote eligible memories without changing ranking weights |
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

This runs a lightweight organize pass with jobs `["promote"]` and a 1-second budget. It is semantically equivalent to calling `organize()` with those parameters.

Duplicate and alias merge application uses a backend transaction covering the
canonical evidence union, complete relationship copies, source retirement and
one supersedence edge. Admission is rechecked on current values inside the
transaction. PostgreSQL takes ordered node locks before reading evidence;
DuckDB transactions retain the connection lock until native work finishes.
Conflicting DuckDB writes can fail safely and be retried.

`ORGANIZER_MERGED` records complete before/after node values, original and
published relationships, kind, score and a versioned identity for the unordered
pair. The record is a checksummed JSON string inside the operation payload,
preserving numeric bytes through PostgreSQL JSONB. A repeat verifies the record
and returns its original identity without rewriting graph state. Deterministic
copy IDs also recognize exact partial transfers from older versions; conflicting
copies abort publication. Unverified alias links do not retire nodes and remain
separate from this merge operation.

External index eviction follows commit. Compaction repairs failures, while the
durable retired lifecycle excludes stale index candidates. Cancellation and lost
acknowledgments do not imply rollback. Full historical graph reconstruction still
requires records for other organizer/manual mutations; this operation does not
retroactively invent those inputs.

Single-node `promote`, `archive` and `deprecate` now validate the current state
and commit the update with a version 1 `LIFECYCLE_CHANGED` record in one backend
transaction. The checksummed record retains complete before/after nodes and
the action under `lifecycle_transitions_v1`. PostgreSQL locks the target row
before reading it, preventing stale promotion from restoring an archived node.
DuckDB retains its connection lock until native work finishes, including when
the caller is cancelled. Both backends implement contested-to-deprecated
transitions. Existing organizer summary logs remain separate from this atomic
per-node record.

Invalid or repeated terminal transitions still raise `ValueError`; there is no
caller-supplied idempotency key for these actions. After an ambiguous outcome,
inspect the current node with retired states included. Index eviction follows
archival and remains repairable by compaction. These records cover the named
transition methods, not arbitrary low-level `update_node` calls or older
unjournaled mutations. Full historical replay remains incomplete.

`ALL_JOBS` lists available jobs. `DEFAULT_JOBS` excludes the legacy global
`feedback_apply` tuner. Default `organize()` calls use `DEFAULT_JOBS`, with or
without a user scope. Explicit scoped requests containing `feedback_apply`
raise `ValueError` before any job or pending-work drain runs. Pending anonymous
signals remain untouched. A trusted operator can explicitly call
`organize(jobs=["feedback_apply"])` without a scope to retain legacy behavior.
That operation affects every user of the engine and is not scoped learning.

`centrality_boost` is not registered. RFC-0007 §11 labels its in-degree formula
as a hypothesis, and the repository has no benchmark evidence supporting a
retrieval or retention benefit. Returning a successful no-op misrepresents
maintenance coverage and makes default runs harder to audit. A future
implementation must first define drift-free persistence and pass a controlled
quality and retention study before joining `ALL_JOBS`.

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

The originally proposed feedback lifecycle below is not the current implementation.
Owner-scoped receipts and relevance records are described in RFC-0017; they do
not automatically change nodes or weights. The separate legacy memory-only
tuner requires explicit unscoped operator selection. The following remains a
design proposal:

Feedback signals would be recorded as `FEEDBACK_EVENT` operations during retrieval and applied to node base values either:
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

This is the rebuild requirement, not a statement that historical mutations are
fully replayable. New explicit `reinforce()` calls validate ownership and evidence,
read current values, apply increments and append a versioned `REINFORCE` operation
in one backend transaction. Its checksummed record retains complete before/after
nodes and the optional evidence event. Recorded outputs are read back from the
database so stored numeric precision is preserved. PostgreSQL locks the target
row before reading; DuckDB holds the connection lock through native completion.
Concurrent successful calls accumulate. An aborted transaction publishes neither
the node change nor the operation.

The current `additive_caps_v1` policy preserves the existing +0.15 boost / +0.05
confidence increments, 0.5 / 0.95 increment caps and above-cap values. It does not
prove that cited evidence semantically supports the claim, re-evaluate conditions,
apply every proposed RFC-0008 saturation rule, or deduplicate separate calls.
An optional owner-scoped `request_id` UUID binds a confirmation to its node and
evidence. Same-request retries reuse the version 2 journal record, including after
restart; changed requests using that key fail without mutation. Calls without a
key or with a new UUID remain separate signals. Version 1 records retain their
original checksum semantics and are not retroactively keyed. See
[the confirmation guide](REINFORCEMENT.md) for sync, async and HTTP use.
Older unjournaled reinforcement and other historical organizer/manual mutations
cannot be reconstructed from these new records. Full historical replay remains
incomplete.

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

## Hierarchical source excerpts in PRME

Daily, weekly, and monthly summaries retain full selected source content with
source IDs, epistemic/source labels, and temporal qualifiers. They are marked
INFERRED, not promoted to newly observed facts. Their stable lineage is keyed by
user, scope, level, and period; summaries retain that namespace. The immutable
request hash covers every selected source snapshot, rendered content, scores,
policy, and embedding identity. Calendar windows use UTC event time (falling
back to creation time), including when rolling historical daily summaries into
weeks and months.

These are selected excerpts, not exhaustive or semantically compressed accounts.
Sources omitted by the per-summary item limit remain intact. Nested excerpts can
be large; the context packer must skip oversized entries rather than truncate
their qualifications. Unchanged requests reuse one summary identity. A changed
selected source publishes a deterministic new generation while archiving the
active predecessor in the same graph transaction. Summary creation, provenance
edges, generation advancement, predecessor archival, and the complete
`CONSOLIDATION_PUBLISHED` record commit together. DuckDB first persists
`CONSOLIDATION_PREPARED`, then stages the exact vector and lexical document under
the consolidation fence; PostgreSQL writes pgvector inside publication. A retry
reuses saved embedding work, and concurrent engines converge on one active
identity. The first managed publication includes active legacy
`source-excerpts-v1` nodes for the same bucket in its atomic predecessor set.
Retired external-index entries are evicted after commit and remain repairable by
compaction.

*End of RFC-0015*
