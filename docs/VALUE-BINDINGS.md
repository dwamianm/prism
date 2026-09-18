# Typed presentation and lookup values

Agents often need two exact forms of the same value. A confirmed memory may
need to preserve `Salt Lake City(Utah)` in a user-facing answer while a search
tool accepts only `Salt Lake City`. A prose instruction cannot reliably keep
those roles separate across tool calls. PRME therefore supports opt-in typed
value bindings on direct stores.

```python
from prme import MemoryClient, MemoryValueBinding

with MemoryClient("./memory") as memory:
    memory.store(
        '{"plan":"Current City: Salt Lake City(Utah)"}',
        retrieval_content="Confirmed plan\nCurrent City: Salt Lake City(Utah)",
        value_bindings=[
            MemoryValueBinding(
                reference="current-city-1",
                kind="city",
                presentation="Salt Lake City(Utah)",
                lookup="Salt Lake City",
            )
        ],
        user_id="alice",
    )

    response = memory.retrieve("Which city?", user_id="alice")
    print(response.bundle.render_value_bindings())

    resolution = response.bundle.resolve_tool_arguments(
        {"city": "Salt Lake City(Utah)"}
    )
    assert resolution.arguments == {"city": "Salt Lake City"}
    assert resolution.binding_uses[0].operation == "replaced"
```

`presentation` is the exact source-backed form intended for output. `lookup` is
caller-supplied operational data for a tool or database. `kind` and `reference`
are application-defined stable labels. PRME does not infer the mapping.

Admission requires every presentation value to occur byte-for-byte in both the
immutable source and the text indexed for retrieval. The bindings are copied
into the event and direct node metadata before storage work begins, so restart
recovery retains the same values. The reserved metadata key is
`prme_value_bindings_v1`; pass `value_bindings` instead of writing that key
directly.

Only bindings whose presentation value survived context packing are exposed by
`bundle.value_bindings()`. A reference-only or key/value memory cannot silently
influence a tool call. `resolve_tool_arguments()` copies a finite JSON argument
object and replaces only complete string values that exactly equal a visible
presentation form. It never performs substring, fuzzy, case-folded, or model-
generated rewriting. The result lists JSON-pointer paths, source node IDs and
binding references for every substitution. `binding_uses` also identifies an
argument that already equals an unambiguous visible lookup form, using the
`already_lookup` operation without changing it. This lets a tool adapter carry
the source-backed presentation form into result rendering without exposing all
lookup values in model context. Ambiguous reverse lookup forms are omitted, and
conflicting presentation mappings fail instead of choosing one.

The HTTP `POST /v1/store` body and MCP `memory_store` accept the same
`value_bindings` array. HTTP retrieval returns visible bindings in the top-level
`value_bindings` field. MCP `memory_retrieve(include_context=true)` returns them
beside the exact packed context. Cross-language clients can apply the same exact
complete-value rule. Python callers can use the audited resolver directly.

Bindings do not rewrite generated answers, validate a tool's schema, prove that
the lookup form is correct, or turn caller metadata into a factual claim. They
provide an explicit execution boundary with source provenance. Applications
should still validate the resolved arguments against the target tool schema and
retain the returned replacement and binding-use audit when the action matters.
If an application adds presentation guidance to a tool result, it should use
only the `binding_uses` from that exact call and keep the annotation separate
from the tool's data.
