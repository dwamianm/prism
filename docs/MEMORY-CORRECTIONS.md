# Explicit memory corrections

Store the correction as a new source, then explicitly replace the older node.
`store()` returns an **event ID**; `supersede()` takes **node IDs**. Use
`get_event_nodes()` to find the nodes derived from a source.

```python
from prme import MemoryClient

with MemoryClient("./memory") as memory:
    old_event = memory.store("My current city is Boston.", user_id="alice")
    new_event = memory.store("I moved to Chicago.", user_id="alice")
    old_node = memory.get_event_nodes(old_event, user_id="alice")[0]
    new_node = memory.get_event_nodes(new_event, user_id="alice")[0]

    memory.supersede(
        str(old_node.id),
        str(new_node.id),
        evidence_id=new_event,
        user_id="alice",
        actor_id="alice",
    )

    assert memory.get_node(str(old_node.id), user_id="alice") is None
    history = memory.get_node(
        str(old_node.id), user_id="alice", include_superseded=True
    )
    assert history.superseded_by == new_node.id
    assert memory.get_event(old_event, user_id="alice").content == old_node.content
```

With `MemoryEngine`, await the same methods. Both nodes must belong to the same
owner and scope; supply `user_id` to bind the operation to your caller. Omitted
`user_id` retains trusted operator access and does not bypass the same-owner and
same-scope requirement between nodes.

The old node becomes superseded and a `SUPERSEDES` relationship points from the
new node to the old node. Source events and the old content remain available.
Default retrieval excludes the retired node. The state, deterministic edge, and
a checksummed `SUPERSEDENCE_APPLIED` record with complete before/after snapshots
commit together. Index eviction follows commit and failed cleanup is repairable.

Optional `evidence_id` must identify an existing event in the same owner and
scope. Missing, malformed, foreign-owner and foreign-scope references all raise
`ValueError("Evidence event is not available in the node owner and scope")`.
The transition publishes no partial changes. Omitting evidence remains allowed
for an explicit caller decision. Presence and ownership checks do not prove
that the evidence logically supports the correction.

The graph backend applies the same evidence checks to `supersede_many`,
`contradict` and `resolve_contradiction`. An invalid entry aborts an entire
replacement batch. These checks do not validate every arbitrary `create_edge`
or low-level graph update.

An exact retry with the same old node, replacement, evidence, and actor is a
durable no-op, including after restart. A retry that changes the actor or
evidence is rejected instead of being mistaken for the original correction.
The operation ID is derived from the ordered node pair, so callers do not need
to manage a separate request key. HTTP exposes `POST /v1/supersedences`; MCP
exposes `memory_supersede`. New explicit corrections are fully journaled, but
legacy operations and other historical mutations still prevent a claim of
complete graph replay.
