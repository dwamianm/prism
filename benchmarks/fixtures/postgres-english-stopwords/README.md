# Frozen English stopwords

Copied unchanged on 2026-09-12 from PostgreSQL's
[REL_17_STABLE English stopword list](https://github.com/postgres/postgres/blob/REL_17_STABLE/src/backend/snowball/stopwords/english.stop).
SHA-256: `b3f772a000465cb76e23adb03b47073c591c156fad8f7af09c8b8e80d6bd8eac`.
The accompanying upstream PostgreSQL license is retained in `LICENSE`.

Used only for an exploratory query-side lexical ablation. It does not change
production indexing or promise complete PostgreSQL/Tantivy tokenizer parity.
