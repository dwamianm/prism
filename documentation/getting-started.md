# Getting Started

## Installation

```bash
pip install prme
```

With optional extras:

```bash
pip install prme[mcp]        # MCP server for Claude/Cursor
pip install prme[api]        # HTTP REST API
pip install prme[postgres]   # PostgreSQL backend
```

From source:

```bash
git clone https://github.com/dwamianm/prism.git
cd prism
pip install -e ".[dev]"
```

## Initialize a Memory Directory

```bash
prme init ./my_memories
```

This creates the directory structure and a `.env.example` file. You can also verify it:

```bash
prme doctor ./my_memories
```

## Quickstart

```python
from prme import MemoryClient

with MemoryClient("./my_memories") as client:
    # Store memories (no LLM needed)
    client.store("Alice prefers dark mode in all editors.", user_id="alice")
    client.store("The team decided to use PostgreSQL.", user_id="alice")

    # Retrieve with hybrid scoring
    response = client.retrieve("What are Alice's preferences?", user_id="alice")
    for result in response.results:
        print(f"[{result.composite_score:.3f}] {result.node.content}")
```

`MemoryClient` is synchronous — no `async`/`await` needed. It works in scripts, notebooks, FastAPI apps, and any other context.

## Storing Different Types of Memory

PRME supports 9 node types. Use them to give your memories semantic meaning:

```python
from prme.types import NodeType, Scope

# Facts
client.store(
    "Python 3.12 was released in October 2023.",
    user_id="alice",
    node_type=NodeType.FACT,
)

# Preferences
client.store(
    "Alice prefers Neovim over VS Code.",
    user_id="alice",
    node_type=NodeType.PREFERENCE,
)

# Decisions
client.store(
    "We decided to use FastAPI for the backend.",
    user_id="alice",
    node_type=NodeType.DECISION,
    scope=Scope.PROJECT,
)

# Instructions (behavioral rules)
client.store(
    "Always respond in British English.",
    user_id="alice",
    node_type=NodeType.INSTRUCTION,
)
```

## LLM-Powered Ingestion

With an API key, PRME can automatically extract entities, facts, and relationships from text:

```bash
export OPENAI_API_KEY=sk-...
# or
export ANTHROPIC_API_KEY=sk-ant-...
```

```python
# Single message
client.ingest(
    "I just switched from VS Code to Neovim and I love the modal editing.",
    user_id="alice",
)

# Batch of conversation messages
client.ingest_batch(
    [
        {"role": "user", "content": "I'm starting a new project in Rust."},
        {"role": "assistant", "content": "Great choice! What kind of project?"},
        {"role": "user", "content": "A CLI tool for managing Docker containers."},
    ],
    user_id="alice",
    session_id="session-1",
)
```

## Retrieval

The retrieval pipeline scores candidates across 6 signals: semantic similarity, lexical relevance, graph proximity, recency, salience, and confidence.

```python
response = client.retrieve("What programming languages does Alice use?", user_id="alice")

for result in response.results:
    print(f"[{result.composite_score:.3f}] ({result.node.node_type.value}) {result.node.content}")
```

### Point-in-Time Queries

See what was known at a specific time:

```python
from datetime import datetime

response = client.retrieve(
    "What editor does Alice use?",
    user_id="alice",
    knowledge_at=datetime(2024, 1, 1),
)
```

### Scoped Queries

Filter by scope:

```python
from prme.types import Scope

response = client.retrieve(
    "What decisions have we made?",
    user_id="alice",
    scope=Scope.PROJECT,
)
```

## Memory Organization

PRME automatically maintains memory health through 11 organizer jobs:

```python
result = client.organize(user_id="alice")
print(f"Ran {len(result.jobs_run)} jobs in {result.duration_ms:.0f}ms")
```

Jobs include promotion (tentative to stable), decay, deduplication, summarization, and more. See [Configuration](configuration.md#organizer) for tuning.

## Next Steps

- [Core Concepts](concepts.md) — understand node types, lifecycle states, and the epistemic model
- [MemoryClient API](memory-client.md) — full API reference
- [CLI Reference](cli.md) — inspect and manage memories from the command line
- [MCP Server](mcp-server.md) — use PRME as a memory backend for Claude or Cursor
- [Configuration](configuration.md) — tune scoring weights, retrieval parameters, and organizer behavior
