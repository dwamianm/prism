# Entity profiles

`consolidate_knowledge()` creates a searchable summary from source nodes that
mention an entity. It preserves whole source excerpts, dates and provenance
within a token budget. The entity association is marked as inferred: a profile
quotes what was recorded, including uncertainty and conditions.

```python
from prme import MemoryClient, Scope, StaleProfileError

with MemoryClient("./project_memory") as client:
    client.store(
        "Aurora deploys from main only after approval.",
        user_id="alice", scope=Scope.PROJECT,
    )
    client.store(
        "Aurora keeps nightly backups for 30 days.",
        user_id="alice", scope=Scope.PROJECT,
    )

    def rebuild() -> int:
        return client.consolidate_knowledge(
            user_id="alice", scope=Scope.PROJECT,
            entity_names=["Aurora"], max_profile_tokens=1000,
        )

    try:
        created = rebuild()
    except StaleProfileError:
        # A source or another rebuild changed during preparation.
        # This fresh call rereads the current sources; retry at most once here.
        created = rebuild()

    response = client.retrieve(
        "Aurora deployment and backup policies",
        user_id="alice", scope=Scope.PROJECT, include_cross_scope=False,
    )
    print(response.bundle.render())
```

`MemoryEngine` exposes the same arguments as an async method. No extraction LLM
is required; the configured embedding provider indexes the profile.

The method returns the number of published profiles. It requires at least two
eligible source nodes per name. Earlier generated profiles never count as new
source evidence. Names use literal, case-insensitive word boundaries; automatic
name discovery is heuristic, so supply known names when available. Whole excerpts
that exceed the remaining budget are skipped. If none fits, no replacement is
published. The budget includes labels, dates and provenance, measured with the
configured packing tokenizer.

Every profile stays within one owner and scope. Omitting `scope` processes each
scope separately. `Scope.PROJECT` is a scope category, not a named project ID;
use separate memory packs to isolate different projects for the same owner.
Profile construction scans the active scope in pages, including older sources
beyond the newest 5,000 nodes.

Each replacement becomes visible atomically after index preparation. Until it
commits, the previous profile stays active. Embedding and storage failures raise
an exception. A lost commit acknowledgement can still mean a replacement was
published; inspect current SUMMARY nodes with `query_nodes(user_id=...,
node_type=NodeType.SUMMARY, scope=...)` when reconciling that outcome. Rebuilding
creates a fresh profile identity and retires prior profiles for that name.

A multi-entity call contains separate publications; earlier names may succeed
before a later one fails. Profiles without enough qualifying sources are retired
on an explicit rebuild. Interrupted local index staging is retained until an
explicit index rebuild; there is no automatic profile retry queue. This API is
separate from the organizer's `consolidate` job.
