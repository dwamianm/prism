# Core Concepts

## Node Types

Every memory is stored as a typed node. The type affects how the memory is scored, decayed, and organized.

| Type | Value | Purpose | Default TTL |
|------|-------|---------|-------------|
| `ENTITY` | `"entity"` | People, places, organizations | None |
| `EVENT` | `"event"` | Things that happened | 365 days |
| `FACT` | `"fact"` | Statements of truth | None |
| `DECISION` | `"decision"` | Choices made with context | 180 days |
| `PREFERENCE` | `"preference"` | User likes/dislikes | None |
| `TASK` | `"task"` | Action items | 90 days |
| `SUMMARY` | `"summary"` | Rollup of other nodes | 365 days |
| `NOTE` | `"note"` | General-purpose (default) | 90 days |
| `INSTRUCTION` | `"instruction"` | Learned behavioral rules | None |

```python
from prme.types import NodeType

client.store("Always use UTC timestamps.", user_id="u1", node_type=NodeType.INSTRUCTION)
```

## Lifecycle States

Every node progresses through lifecycle states. Transitions are forward-only.

```
TENTATIVE ──> STABLE ──> SUPERSEDED ──> ARCHIVED
    │            │            │
    │            ├──> CONTESTED ──> DEPRECATED ──> ARCHIVED
    │            │
    └────────────┴──> ARCHIVED
```

| State | Meaning |
|-------|---------|
| `TENTATIVE` | Newly created, not yet confirmed |
| `STABLE` | Confirmed through age or reinforcement |
| `CONTESTED` | Contradicted by newer information |
| `SUPERSEDED` | Replaced by a newer version |
| `DEPRECATED` | Confirmed incorrect |
| `ARCHIVED` | Terminal state, excluded from retrieval |

The organizer automatically promotes `TENTATIVE` nodes to `STABLE` after 7 days (configurable) with at least 1 evidence reference.

## Scopes

Scopes control visibility and isolation of memories.

| Scope | Value | Purpose |
|-------|-------|---------|
| `PERSONAL` | `"personal"` | Single user's memories |
| `PROJECT` | `"project"` | Shared across a project |
| `ORGANISATION` | `"organisation"` | Cross-project organizational facts |
| `AGENT` | `"agent"` | Private to the AI agent |
| `SYSTEM` | `"system"` | System-generated content |
| `SANDBOX` | `"sandbox"` | Temporary, supports hard delete |

```python
from prme.types import Scope

# Personal memory
client.store("I prefer dark mode.", user_id="alice", scope=Scope.PERSONAL)

# Shared project decision
client.store("API uses REST, not GraphQL.", user_id="alice", scope=Scope.PROJECT)
```

## Epistemic Model

PRME tracks the epistemic status of each memory — how it was learned and how certain it is.

### Epistemic Types

| Type | Weight | Meaning |
|------|--------|---------|
| `OBSERVED` | 1.0 | Evidence-based |
| `ASSERTED` | 0.9 | User stated directly |
| `INFERRED` | 0.7 | Derived by logic |
| `CONDITIONAL` | 0.5 | Depends on other state |
| `UNVERIFIED` | 0.5 | Not yet confirmed |
| `HYPOTHETICAL` | 0.3 | Speculative |
| `DEPRECATED` | 0.1 | Confirmed wrong |

In `DEFAULT` retrieval mode, `HYPOTHETICAL` and `DEPRECATED` nodes are excluded. Use `EXPLICIT` mode to include them.

### Source Types

| Type | Meaning |
|------|---------|
| `USER_STATED` | User told us directly |
| `USER_DEMONSTRATED` | Inferred from user behavior |
| `SYSTEM_INFERRED` | System-derived conclusion |
| `EXTERNAL_DOCUMENT` | From an external source |
| `TOOL_OUTPUT` | From a tool or API |

### Confidence and Salience

Every node carries two scores:

- **Confidence** (0.0 - 1.0): How certain we are this is correct. Boosted by reinforcement, decayed over time.
- **Salience** (0.0 - 1.0): How important/relevant this is right now. Decays based on the node's `DecayProfile`.

### Decay Profiles

| Profile | Lambda | Half-life |
|---------|--------|-----------|
| `PERMANENT` | 0.000 | Never decays |
| `SLOW` | 0.005 | ~139 days |
| `MEDIUM` | 0.020 | ~35 days |
| `FAST` | 0.070 | ~10 days |
| `RAPID` | 0.200 | ~3.5 days |

## Edge Types

Nodes are connected by typed edges in the graph:

| Type | Meaning |
|------|---------|
| `RELATES_TO` | General relationship |
| `SUPERSEDES` | New version of old node |
| `DERIVED_FROM` | Created from another node |
| `MENTIONS` | References an entity |
| `PART_OF` | Component relationship |
| `CAUSED_BY` | Causal link |
| `SUPPORTS` | Evidence for another node |
| `CONTRADICTS` | Conflicts with another node |
| `HAS_FACT` | Entity owns a fact |

## Retrieval Pipeline

The retrieval pipeline runs 6 stages:

1. **Query Analysis** — classifies intent (semantic, factual, entity_lookup, temporal, relational), extracts entities and temporal signals
2. **Candidate Generation** — gathers candidates from vector search (k=250), lexical search (k=250), graph neighborhood (150), and pinned nodes
3. **Candidate Merging** — deduplicates by node ID, tracks discovery paths
4. **Epistemic Filtering** — removes HYPOTHETICAL/DEPRECATED in DEFAULT mode, filters by lifecycle state
5. **Scoring** — computes composite score from 6 additive signals + epistemic multiplier + temporal boost
6. **Context Packing** — greedy bin-packing into token budget with representation levels

### Scoring Formula

```
score = semantic * 0.25
      + lexical  * 0.20
      + graph    * 0.20
      + recency  * 0.10
      + salience * 0.10
      + confidence * 0.15

score *= epistemic_weight  (type-based multiplier)
score += temporal_boost    (0.15 for temporal queries with date proximity)
```

All weights are configurable via `ScoringWeights`. See [Configuration](configuration.md#scoring-weights).

## Supersedence

When a fact changes, PRME doesn't delete the old version. Instead, it creates a new node and marks the old one as `SUPERSEDED`:

```python
# Old fact exists: "Alice uses VS Code"
# New fact arrives: "Alice switched to Neovim"
# → Old node gets lifecycle_state=SUPERSEDED, superseded_by=new_node_id
# → Retrieval returns only the latest version by default
```

This preserves the full history. Use `include_superseded=True` to see the chain, or the CLI:

```bash
prme chain ./memory.duckdb <node-id>
```
