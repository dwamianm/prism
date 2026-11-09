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
it; the graph oracle's own `superseded` status on a link still retires that claim.
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
