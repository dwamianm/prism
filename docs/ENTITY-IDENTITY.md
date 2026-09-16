# Entity identity and conservative merges

A memory owner is the account storing a message. A message role identifies its
source, and an entity identifies the subject of a claim. These are separate:
a user can paste a colleague's email without adopting the colleague's preferences.

Extracted English personal references such as `I`, `we` and `they` remain local
to their source event. They carry `identity_status="unresolved_reference"` and
`reference_event_id` metadata. Repeating preparation for that same event may
reuse its reference; another event, absent provenance, or an older globally
merged pronoun node cannot establish the same referent. Explicit non-personal
meanings such as `US`/country and `IT`/technology retain named-entity behavior.
This is a conservative English guard, not general coreference resolution. It
does not disambiguate several quoted speakers inside one event or equal names
belonging to different people.

Built-in extraction keeps only claims whose named references resolve within the
same response, apart from those event-local personal references. A missing or
ambiguous reference discards its claim while preserving grounded, closed
siblings; it never creates or selects an identity to repair model output.

Fresh built-in extractions record `speech_act_v3`; derivations prepared from
those records identify qualifier-aware, quantity-preserving,
speech-act-preserving, and source-effective validity rules as `speech_act_v9`.
Saved `speech_act_v2` records prepare missing plans under `speech_act_v8`.
Legacy `source_passage_v1` extraction records prepare missing plans under
`temporal_validity_v7`, so recovery never claims validation that did not run.
Prepared plans using `speech_act_v8`, `temporal_validity_v7`, `grounded_quantities_v6`, `claim_qualifiers_v5`,
`event_local_references_v4`, and earlier policies remain valid immutable replay
inputs.
Previously saved plans preserve their policy, node IDs and checksums and replay
unchanged. The default for an omitted historical policy remains the previous
value. Existing incorrectly merged identities are not automatically split; doing
so requires an explicit provenance-based correction.

Organizer duplicate and alias candidates must agree on owner, scope, memory
type, source type, epistemic classification, session, event time, validity end,
retention settings, pinning and metadata. This includes entity types and claim
subject/predicate metadata. Unresolved personal references cannot become
canonical identities through organizer merging or alias links.

For non-entity duplicate copies, automatic merging additionally requires exactly
equal content and validity start. Two separately admitted observations remain
distinct even when their text matches. Case changes in ordinary claim text are
not assumed equivalent. Named entities retain stripped, case-insensitive name
matching within the compatible metadata/provenance boundary. This policy favors
retaining an uncertain duplicate over erasing an episode or qualifier.

Vector similarity still proposes candidates; it does not authorize replacing
different text with a canonical claim. `organize(jobs=["deduplicate"])` reports
both candidates and actual merges, plus unapplied pairs and its merge-policy
version. A candidate count greater than zero with no merged nodes can be correct.
Compatible exact copies can still merge and combine their evidence references.

Organizer duplicate and alias merges now publish canonical evidence, copied
relationships, retirement and one supersedence edge in a single backend
transaction. They revalidate both nodes after entering that transaction.
PostgreSQL locks shared nodes in UUID order before reading evidence, so
overlapping merges cannot overwrite each other's evidence union. A conflicting
DuckDB transaction can fail safely and be retried against current state.

Copied relationships retain validity, provenance, confidence, metadata and
assertion time. Their deterministic IDs also recognize exact partial copies left
by older versions. A conflicting existing copy aborts the new transaction.
Each committed merge records complete before/after node values, original and
published relationships, and a checksum-protected `ORGANIZER_MERGED` operation.
The operation identity is stable for the unordered node pair and merge kind.
Non-finite numeric metadata is rejected before commit rather than silently
changed to JSON null in the journal. Existing compact JSON records retain their
original bytes, checksums and retry identities.
Retries return the committed identity without reactivating later-retired nodes.

External index eviction runs after graph commit and remains repairable by
compaction. Retired graph state governs candidate admission if eviction fails.
Cancellation or a lost acknowledgment may follow a committed transaction;
callers must not interpret cancellation as proof of rollback. This adds complete
records for new organizer merges, not replay of every historical manual or
organizer mutation.

Alias application rechecks the actual names. A compatible known abbreviation,
case variant or exact normalized name can merge at the configured confidence
threshold. A high semantic score by itself only creates a `RELATES_TO` proposal
marked `identity_verified=False`, preserving both entities. Caller-supplied
candidate labels do not bypass these checks. A proposed alias is not a verified
same-person relationship.

New proposals publish their relationship and a checksum-protected
`ALIAS_PROPOSED` record in one backend transaction. Their operation and edge IDs
are stable for the unordered pair, so repeated or concurrent organizer passes
do not create duplicate links. The record retains both complete node inputs and
the exact edge. Existing random-ID proposal edges are recognized and reused but
not backfilled with invented creation history.

These guards protect source identity and merge behavior. They do not establish
that extraction predicates are entailed, that a source is truthful, or that a
reader correctly attributes every quotation. The PersonaMem reader diagnostic
still exposes attribution failures on raw histories; it is not an evaluation
of these new ingestion/organizer guards.
