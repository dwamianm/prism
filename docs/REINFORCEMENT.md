# Confirming memories and retrying safely

`reinforce()` applies a caller confirmation to one memory node. It increases the
reinforcement boost by 0.15 and base confidence by 0.05, up to increment caps of
0.5 and 0.95 respectively. Values already above those caps are preserved.
An optional evidence event must exist in the same owner and scope as the node.

Generate a request UUID once and save it with your application's job before the
first call. Reuse that UUID, node and evidence if the call times out, loses its
response, or needs to be retried after reopening the memory pack:

```python
from uuid import uuid4

confirmation_id = str(uuid4())  # Persist with the application job.

await memory.reinforce(
    node_id,
    evidence_id=evidence_event_id,
    user_id="alice",
    request_id=confirmation_id,
)
```

The synchronous client supports the same operation:

```python
from prme import MemoryClient

with MemoryClient("./memories") as memory:
    memory.reinforce(
        node_id,
        evidence_id=evidence_event_id,
        user_id="alice",
        request_id=confirmation_id,
    )
```

A committed retry returns without applying another increment, appending another
reference, changing timestamps or restoring an earlier node state. Concurrent
calls with the same request ID also count once. Reusing the ID for another node
or evidence event raises `ValueError`. A new UUID represents a new confirmation.
Calls that omit `request_id` continue to count separately. Keys are scoped to the
node's owner within the memory namespace; another owner's use of the same UUID
is independent and does not grant access to the original node.

For HTTP, send a UUID `Idempotency-Key` header to
`PUT /v1/nodes/{node_id}/reinforce`. The optional JSON body is
`{"evidence_id": "<event UUID>"}`. Existing calls without a body remain valid.
The authenticated owner is enforced. The response includes current `confidence_base`, `salience_base`,
`reinforcement_boost` and `last_reinforced_at`. These stored bases are separate
from virtual decay during retrieval. The response is the current node, so it may
reflect later changes even when the confirmation itself is a replay. A changed
request using the same key returns 409; malformed UUIDs return 422; unavailable
nodes or evidence return 404.

Both backends commit the node mutation and a checksummed, complete before/after
record together. PostgreSQL serializes the target row; DuckDB retains its native
connection lock through completion. A cancelled DuckDB call may already have
committed, which is why a caller should retry with the same request ID. An aborted
transaction leaves no applied confirmation or reserved key.

New records use version 2 and retain the optional request UUID. Version 1 records
remain readable under their original checksums; they are not retroactively bound
to retry keys. A retained request ID is not a claim that its evidence entails the
memory, nor a measure of independent corroboration. Earlier unjournaled changes
and complete historical graph replay remain outside this guarantee.
