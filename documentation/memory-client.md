# MemoryClient API Reference

`MemoryClient` is the recommended Python interface for PRME. It's synchronous, manages its own event loop, and works in scripts, notebooks, FastAPI apps, and any other context.

## Creating a Client

```python
from prme import MemoryClient

# From a directory path (creates if needed)
client = MemoryClient("./my_memories")

# With explicit config
from prme.config import PRMEConfig
config = PRMEConfig(db_path="./custom.duckdb", vector_path="./vecs.usearch", lexical_path="./lex")
client = MemoryClient(config=config)
```

Always use as a context manager or call `close()`:

```python
# Context manager (recommended)
with MemoryClient("./my_memories") as client:
    client.store("hello", user_id="alice")

# Manual lifecycle
client = MemoryClient("./my_memories")
try:
    client.store("hello", user_id="alice")
finally:
    client.close()
```

## store()

Store a memory node across all backends (event store, graph, vector index, lexical index).

```python
def store(
    content: str,
    *,
    user_id: str,
    session_id: str | None = None,
    role: str = "user",
    node_type: NodeType = NodeType.NOTE,
    scope: Scope = Scope.PERSONAL,
    metadata: dict | None = None,
    confidence: float | None = None,
    event_time: datetime | None = None,
) -> str  # returns event UUID
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `content` | `str` | required | Text content to store |
| `user_id` | `str` | required | Owner of this memory |
| `session_id` | `str \| None` | `None` | Group related memories |
| `role` | `str` | `"user"` | Speaker role (user, assistant, system) |
| `node_type` | `NodeType` | `NOTE` | Type of memory node |
| `scope` | `Scope` | `PERSONAL` | Visibility scope |
| `metadata` | `dict \| None` | `None` | Arbitrary key-value metadata |
| `confidence` | `float \| None` | `None` | Override initial confidence (0.0-1.0) |
| `event_time` | `datetime \| None` | `None` | When the event occurred (default: now) |

**Returns:** Event UUID as a string.

**Example:**

```python
from prme.types import NodeType, Scope

event_id = client.store(
    "The deployment target is AWS us-east-1.",
    user_id="alice",
    node_type=NodeType.DECISION,
    scope=Scope.PROJECT,
    metadata={"source": "architecture-review"},
)
```

## retrieve()

Search memories using the 6-signal hybrid retrieval pipeline.

```python
def retrieve(
    query: str,
    *,
    user_id: str,
    scope: Scope | list[Scope] | None = None,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    knowledge_at: datetime | None = None,
    token_budget: int | None = None,
) -> RetrievalResponse
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | `str` | required | Natural language search query |
| `user_id` | `str` | required | User whose memories to search |
| `scope` | `Scope \| list \| None` | `None` | Filter by scope(s) |
| `time_from` | `datetime \| None` | `None` | Only memories created after this time |
| `time_to` | `datetime \| None` | `None` | Only memories created before this time |
| `knowledge_at` | `datetime \| None` | `None` | Point-in-time snapshot (bi-temporal) |
| `token_budget` | `int \| None` | `None` | Max tokens for context packing |

**Returns:** `RetrievalResponse` with:
- `results` — list of `RetrievalCandidate` objects, each with `.node` (MemoryNode) and `.composite_score` (float)
- `bundle` — packed context bundle for LLM consumption
- `metadata` — retrieval timing, candidate counts, backends used

**Example:**

```python
response = client.retrieve("What tech stack are we using?", user_id="alice")

for result in response.results[:5]:
    node = result.node
    print(f"[{result.composite_score:.3f}] {node.node_type.value}: {node.content}")
```

## ingest()

Ingest content through the LLM extraction pipeline. Automatically extracts entities, facts, relationships, preferences, and decisions.

Requires an LLM API key (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or Ollama).

```python
def ingest(
    content: str,
    *,
    user_id: str,
    role: str = "user",
    session_id: str | None = None,
    scope: Scope = Scope.PERSONAL,
) -> str  # returns event UUID
```

**Example:**

```python
event_id = client.ingest(
    "I just finished migrating our auth system from JWT to session tokens.",
    user_id="alice",
    scope=Scope.PROJECT,
)
```

## ingest_batch()

Ingest multiple messages at once. Each message dict must have `role` and `content` keys.

```python
def ingest_batch(
    messages: list[dict],
    *,
    user_id: str,
    session_id: str | None = None,
    scope: Scope = Scope.PERSONAL,
) -> list[str]  # returns list of event UUIDs
```

**Example:**

```python
event_ids = client.ingest_batch(
    [
        {"role": "user", "content": "Let's use Redis for caching."},
        {"role": "assistant", "content": "Good call. I'll note that decision."},
    ],
    user_id="alice",
    session_id="planning-session",
)
```

## get_node()

Get a single node by ID.

```python
def get_node(node_id: str) -> MemoryNode | None
```

**Example:**

```python
node = client.get_node("550e8400-e29b-41d4-a716-446655440000")
if node:
    print(f"{node.node_type.value}: {node.content}")
    print(f"State: {node.lifecycle_state.value}, Confidence: {node.confidence:.2f}")
```

## query_nodes()

Query nodes with filters.

```python
def query_nodes(**kwargs) -> list[MemoryNode]
```

Supported keyword arguments: `user_id`, `node_type`, `lifecycle_state`, `limit`, `offset`.

**Example:**

```python
from prme.types import NodeType

facts = client.query_nodes(user_id="alice", node_type=NodeType.FACT, limit=20)
for fact in facts:
    print(f"[{fact.lifecycle_state.value}] {fact.content}")
```

## organize()

Run organizer maintenance jobs (promotion, decay, deduplication, summarization, etc.).

```python
def organize(
    *,
    user_id: str | None = None,
    jobs: list[str] | None = None,
    budget_ms: int = 5000,
) -> OrganizeResult
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `user_id` | `str \| None` | `None` | Scope jobs to a specific user |
| `jobs` | `list[str] \| None` | `None` | Specific jobs to run (None = all) |
| `budget_ms` | `int` | `5000` | Time budget in milliseconds |

Available jobs: `promote`, `decay_sweep`, `archive`, `deduplicate`, `alias_resolve`, `summarize`, `feedback_apply`, `centrality_boost`, `tombstone_sweep`, `snapshot_generation`, `consolidate`.

**Example:**

```python
# Run all jobs
result = client.organize(user_id="alice")
print(f"Jobs run: {result.jobs_run}")

# Run specific jobs
result = client.organize(jobs=["promote", "deduplicate"], budget_ms=2000)
```

## close()

Shut down the engine and background event loop. Called automatically when using a context manager.

```python
client.close()
```

## Integration Patterns

### FastAPI

```python
from fastapi import FastAPI
from prme import MemoryClient

app = FastAPI()
client = MemoryClient("./memories")

@app.on_event("shutdown")
def shutdown():
    client.close()

@app.post("/remember")
def remember(content: str, user_id: str):
    event_id = client.store(content, user_id=user_id)
    return {"event_id": event_id}

@app.get("/recall")
def recall(query: str, user_id: str):
    response = client.retrieve(query, user_id=user_id)
    return [{"content": r.node.content, "score": r.composite_score} for r in response.results]
```

### LangChain / Agent Framework

```python
from prme import MemoryClient

client = MemoryClient("./agent_memory")

def store_memory(content: str, user_id: str):
    """Tool for the agent to store memories."""
    return client.store(content, user_id=user_id)

def recall_memory(query: str, user_id: str):
    """Tool for the agent to recall relevant memories."""
    response = client.retrieve(query, user_id=user_id)
    return "\n".join(r.node.content for r in response.results[:5])
```

### Jupyter Notebook

```python
from prme import MemoryClient

client = MemoryClient("./notebook_memories")

# Store during analysis
client.store("Customer churn rate is 5.2% this quarter.", user_id="analyst")

# Query later
response = client.retrieve("What is the churn rate?", user_id="analyst")
response.results[0].node.content
# → "Customer churn rate is 5.2% this quarter."

# Don't forget to close
client.close()
```
