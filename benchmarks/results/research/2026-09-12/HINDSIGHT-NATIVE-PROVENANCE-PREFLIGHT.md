# Native provenance adapter preflight

This operational preflight follows a failed return-validation gate in the
normalized development run at `f5a2a69`. No dataset quality metrics or generated
dataset answers have been inspected. The strict Hindsight worker continues to
record full failure coverage; its PRME companion was interrupted with native
exit 130 after the comparison became ineligible. Those outputs are not eligible
for the common-reader study and will not be merged into a replacement run.

In `case-0046`, five returned units have `metadata=null`. Their retained unit
identities, source-document identities and exact text substrings match admitted
sources. Their native `context` still carries `source role: user` or
`source role: assistant`, and `mentioned_at` still carries the supplied timestamp.
Requiring every custom metadata key in every returned unit is therefore too
strict for measuring the actual native return, and rendering only those keys
would omit provenance the product actually returned.

The pinned upstream retrieval model explicitly permits absent metadata
(`engine/search/types.py`, `RetrievalResult.metadata`). The semantic seed SQL in
`engine/search/link_expansion_retrieval.py` omits metadata, and public response
assembly uses `result_dict.get("metadata")`. This is consistent with the observed
omission, but the exact execution path has not been traced. The finding is not
evidence of source deletion or lost role/date information.

The new explicit `allow_missing_metadata` policy permits absent custom metadata
keys and records each missing key per returned unit. Present conflicting values,
invalid metadata types, foreign or duplicate unit IDs, altered source text and
failed document readback still fail. The default stays strict so earlier plans
retain their original semantics. No original-source metadata fills a missing
returned field.

The new `native_fields_v2` adapter renders only returned `id`, `document_id`,
`text`, `context`, `occurred_start`, `occurred_end` and `mentioned_at`. It preserves
all values, including nulls, and greedily fits whole records under complete
serialized 2K/4K/8K limits. No source-document substitution, truncation, heuristic
role parsing or inferred timestamp is added. Earlier `metadata_v1` rendering
remains reproducible as its own protocol.

Run three existing authored cases plus the single operational failure case,
using fresh stores and the same pinned products, embeddings and numeric runtime.
The diagnostic input includes no gold labels. Its separate analysis references
contain empty evidence sets and no scored answers; all source metrics are null.
This preflight can validate the adapter path, not comparative quality. Register
a complete replacement dataset protocol only after reviewing operational failure
coverage and completing this preflight. Retain all earlier failures.
