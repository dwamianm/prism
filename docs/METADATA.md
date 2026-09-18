# Portable metadata and legacy journal values

Metadata on new events and direct-store nodes must contain finite,
JSON-serializable values. `NaN`, positive/negative infinity and unsupported
objects raise `ValueError("Metadata must contain finite JSON-serializable values")`
before the event or its recovery work is written. DuckDB and PostgreSQL apply
the same check. Use an explicit JSON null when missing data is what you mean:

```python
memory.store(
    "The measurement was unavailable.",
    user_id="alice",
    metadata={"latency_ms": None, "source": "probe"},
)
```

Admission makes a JSON snapshot before waiting for its storage lock or
connection. Mutating the caller's nested metadata during that wait cannot change
the admitted source. JSON normalization still applies: tuples become arrays,
for example. This is an event-admission contract; it does not validate arbitrary
low-level graph or table mutations.

Grounded extracted quantities use a portable metadata object:

```json
{
  "quantity": {
    "value": "12.50",
    "unit": "$",
    "source_text": "$12.50",
    "grounding": "object_decimal_v1"
  }
}
```

The decimal stays a string so event, graph, DuckDB, PostgreSQL JSONB, and replay
paths preserve its exact value without binary floating-point conversion. The
unit and source text are verbatim evidence, with only surrounding whitespace
removed. If provider JSON encodes the value as a binary float, the float is
discarded and the exact decimal may be reconstructed only from one supported
token in `source_text`; ordinary object and evidence grounding must still pass.
Approximation and range cues in the surrounding evidence reject a clipped
exact-looking phrase. When quantity fields are absent or invalid, one verbatim
currency or unit may be recognized from a bounded physical, data and count-unit
lexicon in the grounded object. The unit is not normalized and an unlisted noun
is not inferred as a measure. No unit, currency, plural, or locale conversion
is implied. New extraction records use `speech_act_v11`, and their materialization
plans use `speech_act_v12`; v11 also admits one leading exact measure from a
bounded, user-authored first-person completed action after the same evidence and
quantity checks. Saved v10 through v6 records remain v12, v5 remain v11, v4 remain
v10, v3 remain v9, and v2 remain v8. The quantity representation is unchanged from
`grounded_quantities_v6`, and older records, plans, and checksums remain
unchanged. A missing policy in a legacy extraction record means
`source_passage_v1`, and recovery prepares a missing plan under
`temporal_validity_v7`. Version 12 retains v7 temporal validity and rejects a
first-person attempt or intention when its extracted predicate erases the
non-completed speech act, including a relationship between components named
inside that attempted action.

JSON object keys must remain unambiguous after normalization. A nested Python
mapping such as `{1: "first", "1": "second"}` would otherwise serialize both
keys as `"1"`, silently losing one value when read. Admission rejects collisions
at any nesting depth with `ValueError("Metadata object keys collide after JSON
serialization")`, before writing an event or work. Unambiguous key conversions
still work; the same key in separate objects is not a collision. Already lost
values in older records cannot be reconstructed by this check.

Older DuckDB packs could admit non-finite metadata. Direct storage could convert
such values to null in the initial node snapshot while retaining them in the
event; raw ingestion could retain them in both. New validation does not rewrite
existing source events or claim to recover values already converted to null.

Lifecycle, reinforcement, organizer-merge and consolidation-retirement journals can preserve special
floats from existing nodes. Their checksummed raw record uses
`prme-special-floats-v1` only when needed: the encoded value has explicit null
placeholders, with typed paths recording `nan`, `positive_infinity` or
`negative_infinity`. These paths are outside the typed record root, so ordinary
metadata dictionaries and literal strings such as `"NaN"` cannot collide with
tags. The entire representation is valid JSON, including inside PostgreSQL JSONB.
Decoders verify paths and reconstruct values without silently normalizing them.

Finite records retain their previous serialized bytes. Existing checksums are
verified on retained raw bytes; they are never recalculated from reconstructed
values. The special-float encoding preserves numeric kinds, not platform-specific
NaN payload bits. Older readers do not understand the new special-float wrapper;
use the current journal readers for these records. This encoding does not add a
complete historical replay engine.
