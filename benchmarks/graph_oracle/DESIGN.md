# The ideal memory graph: design for the graph oracle

Part of dwamianm/prism#351. This is the design the oracle probe measures. It
works backwards: write down the graph a good extractor would hand us, check
that a good traversal answers the questions from it, and only then ask what
extraction has to produce. Nothing here changes a default.

## What goes wrong today

`ingest()` writes `HAS_FACT` and `MENTIONS` edges. Both join a record to an
entity it names. Nothing says how two entities relate, so "Alex plans to visit
Japan" and "Tokyo is a city in Japan" sit in unconnected clusters, and the
memory "We chose Tokyo, Kyoto, Kanazawa, and Osaka" names neither Alex nor
Japan. `store()` writes no entity and no edge at all. Ranking by text alone
puts that memory 12th to 30th for "which cities will Alex visit in Japan?".

## Node kinds

| Kind | Holds | Example |
|---|---|---|
| `person` | a human, the owner included | Alex Rivera, Jordan |
| `place` | a region, country, city, address | Japan, Tokyo, Denver |
| `org` | a company, team, school, group | Northwind, platform team |
| `event` | something with a date or an outcome | the production cutover, a race, a trip |
| `thing` | an item, animal, tool, food, language, plan item | Juniper, Postgres, JR Pass, shellfish |
| memory | the stored text (an existing node: note, fact, turn) | "We chose Tokyo, ..." |

Every entity has an id, a kind, an optional `subtype` (city, country, dog,
language, company, team, diet; a plain noun that a question can name), and its
names, including the short forms the text uses ("Alex" for "Alex Rivera").
The subtype is what lets "which cities" or "which language" pick targets
without a per question rule. A trip is not a node: "Alex plans to visit Japan"
is a claim between Alex and Japan, which keeps the chain Alex to Japan to
Tokyo three nodes long, not five. An `event` is kept only when it has its own
properties (a cutover with a date, a race with a venue).

## Claims, and how they attach to memories

A claim is one statement: subject, relation, object, and these attributes:

- `polarity`: `positive` or `negative` ("dropped Hiroshima", "dislikes Java").
- `status`: `planned`, `current`, `done`, `past` or `superseded`.
- `time`: free text or ISO dates, as the memory states them.
- `role`: a short qualifier ("data analyst", "learning", "reviewer").
- `evidence`: the memories that state it. Evidence is the provenance: a claim
  without evidence is allowed only when `inferred` (see below).
- `inferred`: true when the claim is world knowledge, not something a memory
  says. Only `located_in` between well known places is allowed to be inferred.
  An inferred claim never counts as evidence and never gets a quote.
- `supersedes`: the id of the claim this one replaces.

The object is an entity, or a literal value for an attribute claim (`has`:
budget "8,500 USD", row count "2.3 billion", a date).

**Stored shape (the default): direct edges.** An entity to entity claim is one
edge from subject to object. It carries the relation, polarity, status, role,
time, inferred flag and the evidence memory ids in its metadata. A memory also
has `mentions` edges to each entity it names. A literal claim is a small
claim node (type fact) on a `has_fact` edge from the subject, with a
`derived_from` edge to its memory.

**The alternative: claim nodes.** Every claim is a node with `has_fact` from
the subject, `mentions` to the object and `derived_from` to each memory. It
is richer (a claim can itself be the target of a correction, a challenge or a
quote) but costs two hops per relation step, so a hop limit of k reaches only
k/2 steps. The oracle measures both shapes (the ablations, on by default).

The existing `EdgeType` values are enough: `relates_to` carries the relation
label in metadata (the alias code already does this), `supersedes` marks a
correction, `mentions`, `has_fact` and `derived_from` keep their meaning. No
new edge type or column is needed to hold the graph. A product implementation
may prefer a typed column for the relation label so that traversal can filter
in SQL.

## The relation vocabulary

Sixteen relations plus `has` for literals. They come from what the three
chats and the 75 memory store need; each is directional and has a definition.
Intent is not a relation: `visits` with status `planned` reads "plans to
visit", with status `done` "visited", and with polarity `negative` and status
`planned` "decided against visiting".

| Relation | Subject, object | Definition |
|---|---|---|
| `located_in` | place or org or event, place | spatial containment: Tokyo in Japan, a firm in Brooklyn |
| `part_of` | thing or org, thing or org | non spatial component: a team of a company, a dashboard of a project |
| `member_of` | person, org | belongs to a team or group |
| `works_at` | person, org | employed by; `role` holds the title |
| `lives_in` | person, place | resides in |
| `partner_of` | person, person | spouse, partner or fiance (symmetric) |
| `kin_of` | person, person | family; `role` says which (sister, mother, niece) |
| `friend_of` | person, person | friend; `role` may say running partner |
| `manager_of` | person, person | directs or guides; `role` says manager or mentor |
| `owns` | person, thing | owns, keeps, adopted |
| `visits` | person, place | travels to; `status` holds the intent |
| `involved_in` | person, event or thing | takes part; `role` says leads, presents, signed up, reviewer, on call |
| `prefers` | person, thing | likes or wants; negative polarity is dislike or avoid |
| `allergic_to` | person, thing | an allergy |
| `uses` | person or org or thing, thing | uses, runs on, chose; `role` may say learning |
| `decided` | person, thing | chose or rejected; polarity carries which |
| `has` | any, literal | an attribute value with `attr` as its key |

`located_in` between places is the only relation a claim may carry as
`inferred`. Claims such as "Tokyo is in Japan" are what turns two disconnected
clusters into one, so the oracle also measures the graph without them.

## Corrections and time

A later memory that changes a claim does not edit it. The new claim gets
`supersedes` pointing at the old one, the old one gets status `superseded`
and keeps its evidence, so history is still answerable ("what was the old
date?"). A job change ends the old `works_at` claim as `past` with a new
`works_at` claim as `current`. Queries default to claims that are not
`superseded`, and `past` claims rank below `current` ones. A plan that came
true moves from `planned` to `done` the same way.

## The query shape

A question compiles to:

1. **Seeds.** The entities the question names, found by name or short name,
   whatever the case. A question word that is a subtype ("cities", "dog",
   "language") adds a target type, not a seed.
2. **Relation path.** One to three steps, each a relation, a direction
   (out, in, any) and filters on polarity, status and role. "Which cities will
   Alex visit?" is `[visits, out, positive, planned]`. "Where does Dana's
   sister live?" is `[kin_of, role sister][lives_in]`.
3. **Target.** The kind or subtype of the entities the path should end on
   (`city`, `person`), or a literal attribute key (`date`, `budget`).
4. **Answer memories.** The evidence of the last step's claims first, then
   the memories that mention the most targets. A list question is answered
   by one memory that names every target.

The oracle follows this exactly from gold seeds and a gold path. The generic
strategy builds the same thing without a gold path: seeds from the question
text, relations followed to a hop limit with hop decay and a weight per
relation, subtype words as a target bonus, and relation words (a small lexicon
of cue words for each relation) that restrict the walk to the cued relations
and containment. The ablations show the relation words are the largest lever:
choosing the relation is the query compiler's job.

## Combining graph evidence with vector similarity

The graph is a second opinion, not a replacement: text similarity decides
which memories are about the question, the graph decides which of those are
about the right entities and relations. The final ranking is reciprocal rank
fusion of the existing list (words and vectors, as `retrieve()` ranks them)
and a graph list, with a weight on the graph list. A memory the graph does
not reach has no graph rank and keeps its text score, so a question that
names no entity is unchanged. The owner entity touches nearly every memory, so
its weight is damped (the hub rule); without damping, "Alex" would lift every
memory equally and the list would carry no information.

The graph list scores a memory in two ways, added together:

- `mention`: the sum of the entity scores of the entities it names. This is
  what answers a list question: the cities memory names four reached cities.
- `evidence`: the sum of the scores of the claims it evidences. A claim
  scores the sum of its two endpoints' scores times the relation weight, so a
  claim between two reached entities beats a claim that hangs off one.

An entity's score is, per seed, the best product of relation weights and hop
decay along any path up to the hop limit, summed over seeds, so an entity
reached from two seeds scores about twice as much (Tokyo, from Alex and from
Japan).

## What extraction has to produce

For each memory: the entities it names with kind, subtype and names; the claims
it states with relation from the vocabulary, polarity, status, role and time;
a `located_in` claim for each pair of well known places in the same memory or
chat; and, when a memory changes an earlier claim, a `supersedes` link. The
fidelity measures are entity recall and precision, claim (relation) recall and
precision against the gold graph, and polarity and status accuracy on the
matched claims. `benchmarks.graph_oracle` reports all of them for any graph
that follows the gold file format, so a later extraction run can be scored
without new tooling.
