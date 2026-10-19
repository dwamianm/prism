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

`enable_speaker_references` (on by default since 2026-10-06) provides a named
exception. When a turn names its speaker (`ingest(speaker=...)`), a
first-person singular reference in its claims (`I`, `me`, `my`, `mine`,
`myself`) binds to an entity with that speaker's name and type `person`, the
same node that other mentions of the name use. The claim keeps the literal
pronoun as its subject or object, and its `speaker_reference` metadata names
the speaker and which of the two it bound. A speaker's claims then connect
across messages, so a repeated claim can merge (#209) and a named update can
supersede the value it replaces. Turns without a speaker or an explicit owner declaration, and plural,
second-person and third-person references, stay local to their message. It
became a default together with `enable_claim_sentence_text` and
`packing.fold_repeated_text`, after the three passed the default-change rule
on the DeepSeek answer track over `ingest()` packs (`BENCHMARKS.md`, "Default
change: claim sentences, speaker references and folding"). Only the caller
knows that the named speaker, and not someone the turn quotes, is who says
`I`, so set `PRME_ENABLE_SPEAKER_REFERENCES=false` when a speaker's turns
quote others in the first person.

With `enable_ingest_claim_fixes` (default `true` since 2026-10-19 in the alpha channel; `false` restores
0.16.0a2), a user turn that gives no
speaker but says who it is ("Hi, I am Alex Rivera", "I'm Alex", "My name is Alex
Rivera", "Call me Alex") binds the same way when the extraction lists that name
as a person; the claim's `speaker_reference` metadata then has
`"basis": "self_introduction"`. That binding is local to the turn that names
itself, unless `enable_owner_name_carry` (below) is on, as it is by default: later turns of the
session still keep their own `I`, a caller's `speaker`
or `first_person_owner` wins, and a possessive ("I am Jordan's partner"), a name
the extraction did not list as a person, and a turn that introduces two
different people bind nothing. The default rests on the fast gate by the owner's directive of 2026-10-19; the
full benchmark audit and the default-change rule on the `ingest()` track are pending.

With `enable_owner_name_carry` (default `true` since 2026-10-19 in the alpha channel; `false` restores
0.16.0a2; it needs
`enable_speaker_references`), the name outlives the turn that gave it. A `user`
turn is the memory's owner by definition, so the person entity that a user turn
creates under the owner's name (the caller's `speaker`, or the introduction above)
carries `owner_name` metadata, written once with the node. A later user turn of
the same owner and scope that names nobody ("I prefer trains to flights") binds its
`I`, `me` and `my` to the newest such entity, so the person is linked to all of
their claims and no `I` entity is made for the message; the claim's
`speaker_reference` metadata then has `"basis": "owner_name"`. It never applies to
`participant`, `assistant`, `tool` or `system` turns; a caller's `speaker` or
`first_person_owner` on the turn wins; a later name replaces an earlier one for the
turns after it and earlier claims keep theirs; and a name no user turn gave is never
invented. The extraction request, the number of model calls and the claim wording
are unchanged: a statement written for a duplicate sentence still says "the
speaker", because a name in every such statement makes a question that names the
owner match all of them. Only an entity that the turn creates is marked, so a person
an earlier message already named is not, and an owner who goes back to an
earlier name (A, then B, then A again) keeps B, because A's entity is not marked again. A failed lookup leaves the old
behavior. The owner's entity lives in one scope, so a name given in one scope is
not carried to another.

With `enable_owner_group_links` (default `true` since 2026-10-19 in the alpha channel; `false` restores
0.16.0a3; it needs `enable_owner_name_carry`), a claim that a `user` turn states about `we`, `us` or `our`
is linked to the people it speaks for, while its subject stays the `we` entity local to its message and its
text keeps the literal word (prism-monorepo issue 275). We always includes the speaker, so the claim gains a
`MENTIONS` link marked `metadata.basis` `group_member` to the owner's named entity (the newest entity a user
turn marked with the owner's name, or the one under the turn's own `speaker`). It is linked to the other
members only on evidence: when the session's user turns with the same speaker, up to and including the claim's
turn in the order they were ingested, name exactly one group with the owner ("My partner Jordan and I", "Jordan,
Sam and I", "me and Jordan"), the claim is also linked to each named person, and that link records the turn
that named the group in `named_in`. A member must be a person entity a turn already stored, matched by its full
name or the last words of it, and only one person may carry that name. When the session names two or more
different groups, a member without a name ("my wife and I"), or a name no single person entity has, the claim is
linked to the owner only; no member is invented, and names count as written with a capital letter. The reading
is a conservative English rule, not coreference resolution: "and I" counts only after members that start a
clause, so "I got the offer from Brightpath and I accepted it" names no group. Participant, assistant, tool and
system turns keep their own `we`, as do `first_person_owner` turns. With `enable_claim_merge`, the same `we`
claim repeated in two user turns of one session merges into one current record, as a named speaker's repeated
`I` claim does: the earlier copy is found through its link to the owner. A claim repeated in another session
stays separate, because a session may speak for another group. The statements built for a duplicate sentence
keep "the speaker's group" and never name the members. The links are made after the derivation commits,
outside the plan, so saved plans keep their bytes; memories ingested before the option was on are not linked
or merged. The default rests on the fast tiers by the owner's directive of 2026-10-19; the full benchmark
audit and the default-change rule on the `ingest()` track are pending.

`ingest(first_person_owner=True)` provides a separate per-message declaration
for user/human sources with no named speaker. Under fresh v14 plans, singular
first-person references bind to one deterministic `owner_reference` entity per
owner and scope. Its content remains `I`; it binds no display name. Claims keep
their literal references and record `owner_reference` with the owner and bound
fields. The reserved source metadata key is `prme_first_person_owner_v1`.
This is caller-declared authorship, not automatic coreference. Leave it false
for pasted first-person letters; split mixed authorship into separate messages.
Old unresolved identities and named speaker entities are not merged into this
identity. See [the correction API](MEMORY-CORRECTIONS.md#corrections-extracted-from-messages).

`enable_speaker_grounding` (default false) makes the same assumption during
grounding. Grounding requires a claim's subject and object to occur in its
quoted sentences, and an extractor writes the speaker into a claim as their
name ("Dana, lives in, Denver" for "I live in Denver") or as `I` ("Rachel, is
sister of, I" for "my sister Rachel"), so grounding discards a name absent from its evidence. Fresh extraction also
accepts an unnamed first-person singular subject (`I`, `me`, `my`, `mine`,
`myself`) when any of those forms occurs in its cited sentence. For example,
`I / name / Sam` is supported by "My name is Sam", and a preference
`I / wants_to_be_called / Sam` by "Call me Sam". This binds no personal name
and keeps the reference event-local. It does not infer a missing first-person
subject in "keep answers short", bind plural references, or establish that
pasted first-person text belongs to the memory owner. With the
option, in a turn that names its speaker, the speaker's own first-person
singular reference (`I`, `me`, `my`, `mine`, `myself`) counts as a mention of
a claim value that is the speaker's name or one of those references. That
applies in the provider's check, the pipeline's second check and the sentence
choice of `enable_claim_sentence_text`. The model is asked the same question either way.
A first-person attempt or intention ("I'm trying to set up ESLint") still
needs a predicate that keeps it when the claim names the speaker, and a claim
naming the speaker covers the speaker's `I` clause, so recovery adds no
duplicate for it. Plural references are unchanged.

Built-in extraction keeps only claims whose named references resolve within the
same response, apart from those event-local personal references. A missing or
ambiguous reference discards its claim while preserving grounded, closed
siblings; it never creates or selects an identity to repair model output.

Fresh extractions and plans record `speech_act_v14`. V14 preserves v13
unnamed singular-form matching and earlier qualifier, quantity, speech-act and
source-effective validity rules, and adds explicit owner references and bounded
correction matching. Saved v13 records still prepare v13 plans. Saved v11 and v12 extractions keep their
v12 plan behavior; saved v12 plans retain literal-only unnamed matching.
Saved v10 through v6 records also prepare v12, v5 records prepare v11, v4 prepare v10, v3
prepare v9, and v2 prepare v8. A v12 extraction record also prepares v12.
Legacy `source_passage_v1` extraction records prepare missing plans under
`temporal_validity_v7`, so recovery never claims validation that did not run.
Prepared plans using `speech_act_v11`, `speech_act_v10`, `speech_act_v9`, `speech_act_v8`, `temporal_validity_v7`, `grounded_quantities_v6`, `claim_qualifiers_v5`,
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
the exact edge. Version-2 records can additionally retain a complete external
assessment bound to exact node checksums. Repeated matching requests return the
first committed assessment; older proposals are never relabeled with evidence
they did not originally contain. Existing random-ID proposal edges are
recognized and reused but not backfilled with invented creation history.
Unverified proposals remain queryable as edges but are excluded from ordinary
neighborhood and shortest-path traversal unless a caller explicitly opts in.

An owner can list these records through the alias-proposal inbox and make one
explicit accepted or rejected decision. The decision transaction retains the
complete original proposal payload and reviewer input in a checksummed record.
Acceptance rechecks the exact proposed node snapshots and publishes a separate
verified alias edge; it leaves both nodes active and does not perform an
organizer merge. Rejection publishes no traversable edge. Matching retries reuse
the deterministic review record, while conflicting decisions fail. This turns a
model or similarity signal into a reviewable identity link without treating it
as permission to delete or supersede either entity.

These guards protect source identity and merge behavior. They do not establish
that extraction predicates are entailed, that a source is truthful, or that a
reader correctly attributes every quotation. The PersonaMem reader diagnostic
still exposes attribution failures on raw histories; it is not an evaluation
of these new ingestion/organizer guards.
