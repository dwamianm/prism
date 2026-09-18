# Lexical query exploration

The frozen three-policy experiment completed all 119 development questions with
zero errors and native exit zero. The [plan](lexical-query-dev-plan.json) predates
execution, and the [completion report](lexical-query-dev-completion-c47d666.json)
verifies the exact runner, corpus, cohort, runtime versions, stopword fixture and
parameters. Five authored checks cover isolation, literal operator text, empty
queries, stopword-only titles, annotation exclusion and visible adapter failures.

The investigation began with development preference case `d6233ab6`: generic
conversational wording ranked a sewing note above the relevant high-school
memory. The earlier hybrid ranking placed one labelled source at 43; lexical
alone placed it at 76. That observation motivated testing query preprocessing.
It does not establish that all preference failures have the same cause.

All policies share freshly built, unchanged `en_stem` indexes, the same raw
source text and scope filters, a deterministic source-ID tie policy and a
100-result limit. Literal policies use optional distinct stemmed terms. The
stopword policy additionally applies a fixed PostgreSQL English list at query
time, falling back to literal terms when the entire query is stopwords. Source
content and qualifiers are never rewritten. No model, hybrid score or product
context is evaluated; the shared whole-turn packer isolates lexical ranking.

| Budget | Parser recall | Literal recall | Literal + stopwords recall | Stopword change | Paired 95% interval |
|---|---:|---:|---:|---:|---:|
| 2,048 | 86.70% | 85.82% | 84.58% | −2.12 pp | −5.41 to +0.88 pp |
| 4,096 | 89.55% | 89.25% | 91.96% | +2.41 pp | −1.32 to +6.29 pp |
| 8,192 | 92.69% | 92.98% | 94.44% | +1.75 pp | −0.44 to +4.53 pp |

These means cover 114 labelled questions. At 4K, preference recall rose from
78.57% to 100% across seven questions (two wins, no losses). Assistant evidence
fell from 100% to 88.89% across nine questions (one loss), and three multi-session
questions lost evidence. The overall 4K result has eight wins and four losses.
At 2K, overall recall declined, with four wins and six losses. Every overall
stopword interval includes zero. This is exploratory data with shared histories,
not an independent accuracy result or evidence to enable this policy globally.

**Decision:** retain current production query behavior. The stopword variant is
a candidate for a full-hybrid experiment, including both packing policies and
all categories; its improved preference subset must not replace the overall
regression checks. The failed 381-question packing gate remains unchanged.

There is also a backend semantic difference to resolve deliberately: the current
Tantivy parser uses optional terms, whereas `PgLexicalIndex` uses PostgreSQL's
`plainto_tsquery`, which joins remaining terms with AND. A long natural-language
question can therefore have no PostgreSQL lexical candidates unless every term
occurs in one document. This experiment covers Tantivy, not PostgreSQL query
semantics. The separate PostgreSQL candidate-limit fix does not change that query
policy.

Primary references: [Tantivy's default tokenizers](https://docs.rs/tantivy/latest/tantivy/tokenizer/),
[PostgreSQL text-search query semantics](https://www.postgresql.org/docs/17/textsearch-controls.html),
and the [frozen stopword source](https://github.com/postgres/postgres/blob/REL_17_STABLE/src/backend/snowball/stopwords/english.stop).
The copied stopword fixture retains its upstream license and exact content hash.
