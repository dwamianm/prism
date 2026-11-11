# Relation routing

`enable_relation_routing` (`PRME_ENABLE_RELATION_ROUTING`), off by default, answers a question that asks for a
kind of relation from the graph by that relation (dwamianm/prism#399). "What are the names of Maya's dependents?"
shares no word with "My husband is Theo and my child is Nico.", and the claims `ingest()` extracted from that
sentence carry open predicates (`is_husband_of`, `is_child_of`). Routing maps both sides to one vocabulary:
"dependents" to `partner_of` and `kin_of` with the role child, and those predicates to the same relations, then
walks from Maya over the claims of those relations.

The code is `src/prme/retrieval/relations.py` (vocabulary, normalizer, concepts, router) and
`src/prme/retrieval/relation_walk.py` (the walk). Nothing calls a model.

## The vocabulary

The sixteen relations of the graph oracle research (`benchmarks/graph_oracle/DESIGN.md`), plus `has` for literal
values, which a route never asks for.

| Relation | Subject, object | Definition |
|---|---|---|
| `located_in` | place or org or event, place | spatial containment |
| `part_of` | thing or org, thing or org | non spatial component |
| `member_of` | person, org | belongs to a team or group |
| `works_at` | person, org | employed by |
| `lives_in` | person, place | resides in |
| `partner_of` | person, person | spouse, partner or fiance (symmetric) |
| `kin_of` | person, person | family; a role says which (sister, child) |
| `friend_of` | person, person | friend |
| `manager_of` | person, person | directs or guides; a role says manager or mentor |
| `owns` | person, thing | owns, keeps, adopted |
| `visits` | person, place | travels to |
| `involved_in` | person, event or thing | takes part |
| `prefers` | person, thing | likes or wants; negative polarity is dislike or avoid |
| `allergic_to` | person, thing | an allergy |
| `uses` | person or org or thing, thing | uses, runs on, chose |
| `decided` | person, thing | chose or rejected |
| `has` | any, literal | an attribute value |

`derive_cues()` derives each relation's cue stems from its name and definition with no question in view, by the
rule of `benchmarks/graph_oracle/gold.py` (a test keeps the two equal).

## Claims: the predicate normalizer

`normalize_predicate()` reads a claim's `metadata.predicate`, in order:

1. a relation name of the vocabulary (`lives_in`, `kin_of`);
2. a whole predicate in the synonym table, function words dropped (`is_married_to`, `resides_in`, `works_for`);
3. a people word: partner words (husband, wife, spouse, fiance) are `partner_of`; family words (child, son,
   sister, mother) are `kin_of` with that role, on the subject when the predicate ends in "of" (`is_child_of`: the
   subject is the child) and on the object otherwise (`has_child`); manager, boss and mentor are `manager_of`;
4. a verb of the word table (`lives`, `adopted`, `trip`, `dislikes`, which is `prefers` with negative polarity);
5. a word whose stem is a derived cue of exactly one relation.

Anything else stays unmapped (`has_dependents`, `diagnosed_with`, `coworker_of`). A claim that states its relation
itself (`metadata.relation` in the vocabulary, on the claim node and its `HAS_FACT` link) is read as typed, and its
predicate is not normalized: typed extraction (`enable_typed_extraction`, `PRME_ENABLE_TYPED_EXTRACTION`) writes the
relation, role and status the extractor stated, and the graph oracle writes the same fields on the link. A relation
outside the vocabulary, and `has` (a literal attribute), are read through the normalizer instead. The extractor's
status is stored for the graph, but the claim's lifecycle, not that label, decides whether a routed question may read
it; the graph oracle's own `superseded` status on a link still retires that claim. With the belief ledger (below) the
status also decides where a claim is placed.

**The role's end on a typed claim.** A typed role qualifies one end of the claim: the prompt asks for the role of the
relation's far end, so by default the role is on the object (`Sam kin_of Priya`, role sister: Priya is the sister).
When the open predicate normalizes to the same relation and role, its words decide instead (`is_sister_of` puts the
sister at the subject). Otherwise the claim's quote decides when it puts the role beside the subject's name and not
beside the object's: "My sister Priya is visiting" with `Priya kin_of I` and the role sister puts the role on Priya,
the subject, which is how a model often writes it. A quote with the role beside both names, or beside neither,
leaves the role on the object.
`python -m benchmarks.diagnostics.relation_coverage <build or pack>` reports how many of a pack's claims map, by
relation, and the unmapped predicates by count.

## Questions: concepts and the router

A concept word stands for the relations of one step. The built-in ones:

| Concept | Relations |
|---|---|
| dependents | `partner_of`, `kin_of` with the role child (child, son, daughter, kid and step forms) |
| family | `partner_of`, `kin_of` |
| spouse | `partner_of` |
| children | `kin_of` with the role child |
| home | `lives_in` |
| job | `works_at` |
| allergies | `allergic_to` |
| pet | `owns` |

`retrieve(concepts=...)` adds or replaces concepts for one request: a word maps to a list of `"relation"` or
`"relation:role"` items, such as `{"beneficiaries": ["partner_of", "kin_of:child"]}`. An unknown relation, an
empty list or a word with a space raises `ValueError`, with the option on or off.

`route_question()` reads the question's words: a people word routes to its relation and role, a concept word to
its relations, and any other word whose stem is a derived cue to the cued relations ("lives", "allergic"),
`prefers` with negative polarity when the question says dislike, avoid or hate. When a people or concept word
belongs to someone ("Dana's sister", "my sister") and the question asks for more ("Where does Dana's sister
live?", "Who is my sister's husband?"), the route is two steps: the possessed words, then the rest. Otherwise it is
one step. A question about
when ("when", "how long", "what year") is never routed, because the graph oracle's blind sets lost temporal
questions to the walk, and a question no word routes is left as it was.

## The walk

The walk starts at the entities the question names (the graph path's seed rule, `graph_seed_words` included) and,
for a first-person question (I, me, my, mine or myself), at the owner's entities: the owner reference, and the
caller's `speaker` or, without one, the entity a user turn marked with the owner's name when user turns gave the
owner one name across the request's scopes (`enable_owner_profile_context` applies that rule scope by scope). At most
16 seeds are walked, the owner's entities first. One SQL statement, the same on DuckDB and PostgreSQL, reads:

1. the claims on each seed, as subject (`HAS_FACT`) or object (`MENTIONS`), newest first, at most 128 per seed,
   whatever their relation (the relation is read afterwards);
2. for a two-step route, the entities those claims reach (at most 64, newest first) and their claims, at most 32
   each;
3. the subjects, objects and `DERIVED_FROM` memories of every claim read.

Superseded, deprecated and archived claims are never read, a claim with negative polarity is kept only for a step
that asks for one, and only the owner's records in the request's scopes are read. A text mention
(`enable_mention_links`, `metadata.basis` `text_mention`) records only that a claim's text names an entity, so that
entity is neither end of the claim. A claim is kept when its relation is one the step accepts and, for a step with a
role, when the far end of the claim holds that role. The walk's candidates are the claims of the last step, the claims
of the first step of a two-step route and the memories a claim is derived from by a `DERIVED_FROM` link, at most 32.
`ingest()` writes no such link (a claim cites its source in `evidence_refs`), so on an `ingest()` store the
candidates are claims; typed graphs such as the graph oracle's gold graphs link claims to their memories.

## Ranking and receipts

Under rank fusion the walk's candidates form a fourth list beside the semantic, lexical and graph lists, ranked by
step (an answer before a claim on the way), then by how many of the entities the last step reached their text names
(a memory that names every answer first), then by semantic similarity. Each adds
`scoring.rrf_route_weight / (rrf_k + rank)` before the scale that gives first place on the semantic and lexical
lists 1.0; the weight is `RRF_ROUTE_WEIGHT`, 2.0, when unset. A candidate the walk reached that retrieval did not
find joins the pool with the `ROUTE` path, and one already in it gains that path. Session, episode and evidence
augmentation context inherit their trigger's score without the route term.

A routed retrieval records `scoring.rrf_route_weight`, each candidate's `route_rank` in its score provenance, and
`execution.features.relation_routing`: the routing version, the route, a concept extension, the seeds walked, the
claims the walk kept and its candidates with their scores. Its scores replay from the receipt. A question no word
routes, a walk that reaches nothing, weighted fusion and a privacy-protected read retrieve exactly as with the option
off, receipts included, and a configured `rrf_route_weight` is recorded only on a retrieval it acted on. A walk that
fails is logged and leaves the ranking as with the option off; the response metadata and the receipt's parameters
record it as `backend_failures.ROUTE`.

The routing version recorded in receipts (`ROUTING_VERSION`) is 2: version 2 added the pet concept and the rule for
the role's end of a typed claim, either of which can change a route or its candidates.

## The belief ledger

`enable_belief_state` (`PRME_ENABLE_BELIEF_STATE`), off by default, reads the claims a routed question reaches as a
small ledger and says which one holds at the question's time (`src/prme/retrieval/beliefs.py`; finding 2.2 B of
`memory_bank/THESIS-2026-11-10-EMBODIED-MEMORY.md`). Routing finds the claims at an address, an entity and a
relation such as Sam and `works_at`, but rank fusion counts them as one more list, so "I work as a nurse at St.
Mary's hospital" can rank above "I start at a pediatric clinic on November 2". The option turns relation routing on,
and `memory_mode="address_first"` (`PRME_MEMORY_MODE`) turns on both, with four other read-time settings
(`documentation/configuration.md`). The mode acts only when a question is asked: it does not turn on typed extraction
or corrections, makes no model call and rewrites no stored memory. Without typed extraction a claim has no typed
status, so the ledger reads it as `current` until a later claim at its address closes it or takes its place (below);
stated facts (`ingest(assertions=...)`) and claims stored with `enable_typed_extraction` carry a status. Typed
extraction and corrections were part of the mode until 2026-11-11 and were taken out after the fast probes and the
mid tier showed losses with them
(`benchmarks/results/research/2026-11-11/ADDRESS-FIRST-MID-TIER.md`).

**Addresses.** The claims of the route's last step are grouped by the entity they were read on, their relation and,
except at a relation that holds one value, their role: Priya the sister and Priya the mother are two addresses, and a
nurse and a pediatric nurse at `works_at` are one. A relation holds one value when a newer value replaces the older
one: `works_at`, `lives_in`, `partner_of`, `located_in` and a literal attribute (`has:` with its predicate's words).
Every other relation holds a set (pets, allergies, relatives, preferences), where a new value adds to the old ones.

**States.** A claim first records its own state: `negated` for negative polarity; `ended` for the status past or
superseded, or a validity end that has passed; `planned` for the status planned with a date still ahead; otherwise
`current` (current, done, a plan whose date has come, and an untyped claim). Then, inside an address, in time order:

- a claim that is `ended` or `negated` closes every `current` claim stated before it that names the same value
  (one object phrase's words contain the other's, so "the greyhound" and "a greyhound" are one value), and at a
  relation that holds one value an `ended` claim closes every claim stated before it, since "I quit" need not name
  the place;
- at a relation that holds one value, the newest claim that still holds is `current` and the older ones `earlier`;
  claims stated at that same time are all `current`.

For a question about now the ledger lists `current` (newest first), `planned` (soonest first), `earlier`, `ended` and
`negated` (newest first). A question that is not about now (`is_current_state_question`) and asks for history
("when", "before", "use to", "every", "how long") reads it in time order. The names the ledger's claims use are
taken out of the question before either check, so "Where is the first aid kit now?" asks about now: "first" there
names the kit. Claims stated at one time are ordered by their text, and by node id only when the text is the same
too, so a rebuild of the same log gives the same ledger: a node id is new on every build, and the claims of one turn
have no recorded order, so the end one of them states does not close another.

**Placement.** The ledger's claims are kept together where the best ranked of them already stands, in ledger
order: the i-th of n takes that claim's score times `1 + (n - i) x 0.001`, with its score provenance and a `belief`
adjustment that names it, so the receipt replays the score. The group gains no rank: the text ranking and the route
list decide how high an address's claims stand, so a list question whose answer is another record ("Which cities
will they visit?") keeps that record where it was, and the ledger only decides the order within the group. Each claim
carries the `BELIEF` path, which packing reserves, and its `belief_state`, which the reader format shows as a tag. A
claim of the ledger the search did not find is added beside the others. Until `BELIEF_VERSION` 2 the claims of a
routed question led the results, which cost LoCoMo evidence in the top 25 on the mid tier.

**The catalog.** A question no word routes ("What is Sam up to these days?") can still be about an address. Its
catalog is the claims on the entities it names (and the owner's, for a first-person question), read with the walk's
statement; a seed must be the claim's subject or object, not a text mention. The claim of the catalog that the
question matches best by meaning names the address, when that claim is among the 10 best semantic matches of the
whole pool (`CATALOG_SEMANTIC_RANK`); otherwise the question is left to the text ranking. Every claim of the catalog
at that address is read as one ledger, and the claims are kept together where the best ranked of them stood, as for
a routed question, so a claim that holds now is never below one that ended, and an address the question was not
about gains no rank. A count or list question is not looked up this way.

**Receipts and what stays the same.** A retrieval that placed a claim records `execution.features.belief_state`:
the ledger version (`BELIEF_VERSION`, 2), the source (`route` or `catalog`) and each claim with its state, relation
and role. Nothing is written and no model is called; the ledger is computed from the stored claims each time, so a
correction, a re-ingest or a rebuild changes the next answer and no stored memory is rewritten. A retrieval that
places no claim, weighted fusion and a privacy-protected read retrieve exactly as with the option off, receipts
included. A catalog walk that fails is logged, leaves the ranking as it was, and is recorded as
`backend_failures.BELIEF`.

## Stated facts (`assertions=`)

A structured source, such as a robot's perception stack or a form, states its facts instead of asking a model to
find them (`src/prme/ingestion/assertions.py`):

```python
await engine.ingest(
    "The red mug is on the kitchen counter.", user_id="robot-1", role="system",
    assertions=[{"subject": "red mug", "subject_type": "product", "relation": "located_in",
                 "object": "kitchen counter", "object_type": "location", "status": "current"}],
)
```

Each assertion becomes the claim typed extraction would make from a sentence saying it: the subject and object
entities, a fact node with its relation, role and status (written whatever `enable_typed_extraction` says), the
`HAS_FACT` and `MENTIONS` links, the grounding record and the derivation plan, so the walk and the belief ledger
read it with no special case. No model is called. The source never joins a model batch, and pronoun repair,
corrections and resolved claim text do not apply to it. `ingest_batch()` takes an `"assertions"` key per message,
and `MemoryClient.ingest()` takes the same argument.

**Validation.** Every assertion is checked before anything is admitted, and a failure raises `ValueError` naming
the assertion (`assertions[1]: ...`; in a batch, `messages[2]: assertions[0]: ...`, and no message of the batch is
admitted):

- `subject`, `subject_type`, `relation` and `object` are required; no other key is accepted.
- `relation` is a relation of the vocabulary above. A literal value uses `has`, with the attribute's name as
  `predicate` and no `object_type`; any other relation needs an `object_type`, and takes no `predicate`.
- `subject_type` and `object_type` are one of person, organization, location, product, concept and event.
- `status`, when given, is one of planned, current, done, past and superseded; `polarity` is positive (the
  default) or negative; `role` is a short lower-case word or phrase.
- A leading article (a, an, the) is dropped from the subject and object names, so "the red mug" and "red mug" are
  one entity.
- The content must mention the subject and the object, with or without the article. When the content is omitted
  (None or blank), a plain sentence per assertion is stored instead ("Red mug is located in kitchen counter.").
- Assertions are refused under a privacy policy, and the metadata key `prme_assertions_v1`, where they are kept, is
  reserved.

**Re-derivation.** The validated assertions are kept in the event's metadata, so the log alone rebuilds them.
`reingest()` derives such a source again from them, never through a model, under settings of its own that leave out
the extraction provider, model and request, so a change of extraction model leaves it current.

**Repeats.** With `enable_claim_merge` (on by default) an exactly repeated assertion merges into one current claim
that cites every observation. Each repeat still writes an event, a raw note and a superseded copy of the claim.

**Write rate.** 4.6 stated facts a second at 400 observations through `ingest_batch()` with the product defaults, on
a laptop on 2026-11-10, about 200 ms each. That time is the materialization path every extracted claim takes (a
lexical index staging per source, two entity lookups that scan the owner's entities, the graph commit, the
embeddings), not the assertions. It is not yet fit for a sensor stream.

## Limits

- The derived cues include ordinary words ("call", "like", "take", "part"), so a question can be routed by a word
  that does not ask for a relation; the walk then adds only claims of that relation on the question's seeds. The
  graph oracle's blind set found derived cues weaker than hand-written ones.
- The normalizer maps what its tables and the derived cues name; a predicate the extractor words otherwise stays
  unmapped and the walk does not see it. Typed extraction (OM-09) is the lasting fix.
- Rank fusion keeps the text lists in charge: a claim that names both dependents but shares no word with the
  question can still rank below a claim that names one of them and matches the question's words.
- The 128 claims read per seed are the newest whatever their relation, so on an entity with many claims an old
  family claim can fall outside the window and the walk reaches nothing.
- Under rank fusion `min_score` is a floor on semantic similarity, so a candidate only the walk found (semantic
  similarity 0) is removed by any positive `min_score`, as a graph-only candidate is.
- The ledger compares values by their words: two object phrases name the same value when one's words contain the
  other's, so "the hospital" and "St. Mary's hospital" are one value, and two different hospitals written as "the
  hospital" are too.
- Which relations hold one value is a fixed list. A person with two jobs at once gets one `current` employer and one
  `earlier`.
- A state is only as good as the extracted status and polarity: a claim typed `current` that the source meant as
  past stays `current` until a later claim closes it.
