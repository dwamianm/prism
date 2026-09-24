# Framework integrations

PRME ships optional adapters for LangChain and LlamaIndex. Both use the public
retrieval API and keep chat history in PRME's immutable event log.

## LangChain

Install the adapter dependency:

```bash
pip install "prme[langchain]"
```

Use PRME as a retriever or chat history:

```python
from langchain_core.messages import AIMessage, HumanMessage
from prme.integrations.langchain import PRMEChatMessageHistory, PRMERetriever

retriever = PRMERetriever(
    directory="./memory",
    user_id="alice",
    top_k=5,
    token_budget=4096,
)
documents = retriever.invoke("What has Alice decided?")

history = PRMEChatMessageHistory(
    directory="./memory",
    user_id="alice",
    session_id="support-42",
)
history.add_messages([
    HumanMessage(content="Use dark mode."),
    AIMessage(content="I will remember that."),
])
history.clear()
assert history.messages == []

retriever.close()
history.close()
```

The history adapter preserves complete JSON-serializable LangChain messages,
including tool-message identity, content blocks, names, and additional fields.

## LlamaIndex

Install the adapter dependency:

```bash
pip install "prme[llamaindex]"
```

```python
from llama_index.core.llms import ChatMessage
from prme.integrations.llamaindex import PRMEChatStore, PRMERetriever

retriever = PRMERetriever(directory="./memory", user_id="alice", top_k=5)
nodes = retriever.retrieve("What does Alice prefer?")

chat = PRMEChatStore(directory="./memory")
key = "alice:support-42"
chat.set_messages(key, [ChatMessage(role="user", content="Use dark mode.")])
chat.add_message(key, ChatMessage(role="assistant", content="Understood."))
removed = chat.delete_last_message(key)
assert removed is not None

retriever.close()
chat.close()
```

LlamaIndex keys use `"user_id:session_id"`; a key without a colon uses the
session ID `"default"`. `set_messages`, `delete_messages`, `delete_message`, and
`delete_last_message` follow `BaseChatStore` behavior. `get_keys()` reports keys
written through the current adapter instance because PRME does not expose a
cross-owner key enumeration operation.

## Retriever score floor

Both retrievers accept an optional `min_score`, the same inclusive floor as
`MemoryEngine.retrieve()` (for example `PRMERetriever(..., min_score=0.5)`).
With the default weighted scoring it applies to the composite score. Under
opt-in rank fusion (`PRME_SCORING__FUSION=rrf`) it applies to each result's
`semantic_relevance`, which is in the result metadata. If vector search fails or
the embedding model changed without a rebuild, no result has a cosine, so the
floor is skipped instead of returning nothing, and every result's metadata then
carries `min_score_skipped: True` (RFC-0005 Section 7.2).

## Append-only clear and delete

Clear, replacement, and deletion append versioned control events. The adapters
apply those events when reading logical history, while the original messages
remain available through PRME's owner-scoped event and provenance APIs. Control
events do not create graph nodes or enter vector and lexical retrieval. Reads
page through the event stream and stop once the newest applicable clear marker
makes older history irrelevant.

These operations change framework chat history. They do not archive memory
nodes previously derived from a message. When an application intends to remove
a memory from normal retrieval as well, resolve the message event through
`get_event_nodes()` and apply the owner-scoped lifecycle operation explicitly.

History is isolated by owner, session, and exact PRME scope. Call `close()` when
an adapter is no longer needed. Framework async fallbacks execute these sync
methods in a worker thread.
