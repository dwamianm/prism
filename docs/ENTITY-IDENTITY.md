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

New prepared derivations identify these rules as `event_local_references_v4`.
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

Alias application rechecks the actual names. A compatible known abbreviation,
case variant or exact normalized name can merge at the configured confidence
threshold. A high semantic score by itself only creates a `RELATES_TO` proposal
marked `identity_verified=False`, preserving both entities. Caller-supplied
candidate labels do not bypass these checks. A proposed alias is not a verified
same-person relationship.

These guards protect source identity and merge behavior. They do not establish
that extraction predicates are entailed, that a source is truthful, or that a
reader correctly attributes every quotation. The PersonaMem reader diagnostic
still exposes attribution failures on raw histories; it is not an evaluation
of these new ingestion/organizer guards.
