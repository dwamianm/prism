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

## Corrections extracted from messages

Fresh `speech_act_v14` ingestion plans additionally recognize a grounded
`temporal_intent="update"` with negative polarity, even when the extractor omitted
`replaces_object`. The denied object becomes its target. It retires only known
positive, observed/asserted claims under the same resolved subject, owner, scope,
source type and memory type. Optional conditions prevent automatic retirement.
The prior interval closes at the new source-effective time; an earlier-effective
historical correction cannot retire a later-effective claim. Publication retains
all inputs, replacements and outputs in the atomic derivation journal.

Positive updates still require a named, source-supported `replaces_object`.
Matching preserves the existing predicate equivalence classes, plus one bounded
transition: `switched_to` may replace positive `uses`/`use` FACT claims. It never
replaces arbitrary `has`, `owns` or `likes` claims just because their objects match.
An exact object match or a shorter contiguous whole-word phrase of one to three
words inside a maximum four-word alphabetic object can identify the target.
Thus `pump` can identify `insulin pump`, while numeric values and partial-word
matches are not shortened. Every word in a shortened target must have at least
three characters. If matching finds more than one distinct prior object, it
retires none; it does not choose the most recent or highest-ranked pump.
This is a bounded lexical rule, not general semantic equivalence.

For first-person messages, the caller must establish a shared referent. A named
speaker works with `enable_speaker_references`. When the author is the memory
owner and no display name should be bound, explicitly declare that **per message**:

```python
from prme import NodeType, SourceType

await engine.ingest("I use an insulin pump.", user_id="sam",
                    first_person_owner=True, wait_for_extraction=True)
await engine.ingest("I don't use an insulin pump anymore.", user_id="sam",
                    first_person_owner=True, wait_for_extraction=True)
response = await engine.retrieve(
    "What treatment do I use?", user_id="sam",
    exclude_node_types={NodeType.NOTE, NodeType.ENTITY},
    source_types={SourceType.USER_STATED},
)
```

`first_person_owner` defaults to false and accepts only a boolean. It requires a
user/human role and cannot be combined with a named speaker. Only singular
`I`, `me`, `my`, `mine` and `myself` bind; plural and other personal references
remain event-local. Keep it false for pasted letters or other quoted speakers.
The declaration spans the whole message; split mixed authorship into separate
sources before declaring it. A stable owner identity is separate for each owner
and scope and never retroactively merges old event-local or named identities.
Python async/sync ingest, per-message batch dictionaries, HTTP `/v1/ingest` and
MCP `memory_ingest` expose the declaration. The reserved source metadata key is
`prme_first_person_owner_v1`; pass the argument rather than injecting the key.

Raw source notes and immutable events remain available. A note can contain
several claims, including an unrelated diagnosis that must survive a treatment
correction. Use the explicit NOTE filter to leave those sources out of retrieval.
A surviving claim's evidence sentence can still mention a retired sibling claim:
filtering notes does not rewrite that sentence or prove every phrase in it is
current. Inspect structured claim metadata/lifecycle and `get_assertion_state`
when selecting current state. Ambiguous or unsupported corrections need an
explicit `supersede` decision rather than automatic retirement.

Saved extractions through v13 and every saved plan retain their recorded rules,
IDs and checksums. Missing plans for v13 records still prepare v13. No historical
claims, pronoun identities or notes are automatically migrated. Older releases
cannot read v14 records/plans; upgrade all readers of a shared store first.
