# Memory explorer

A standalone, read-only browser interface for PRME memory entries and their
stored graph connections. All interface code and its small API adapter live in
this folder. It uses native browser modules and SVG; no Node installation,
frontend build, CDN, or external assets are needed. The Inter font is bundled in
`static/fonts/` under the SIL Open Font License (`static/fonts/OFL.txt`).

From the **repository root**:

```bash
uv sync --extra api
uv run python -m web.server
```

Open **http://127.0.0.1:8080** and enter the owner ID used when storing memories.
If per-user authentication is configured, enter the API key; the owner ID can be
omitted. Operator keys still require an owner ID. Credentials are held only in
the current page's memory, cleared on disconnect/reload, and never put in URLs or
browser storage.

The server loads the normal `PRME_` configuration and `.env`, including PostgreSQL
when `PRME_DATABASE_URL` is set. It also reads `PRME_CHAT_DATA_DIR`, the pack
folder used by `examples/chat.py`. When that is set, the explorer opens
`memory.duckdb`, `vectors.usearch` and `lexical_index` inside it, so it shows the
same memories as the chat example. The folder must already exist; the server
stops with an error instead of creating an empty pack at a mistyped path. Without
`PRME_CHAT_DATA_DIR` (or the paths below), PRME's defaults create a new pack in the
current directory.

To open a particular local pack, set its paths. Each one set this way wins over
`PRME_CHAT_DATA_DIR`:

```bash
PRME_DB_PATH=/absolute/path/to/pack/memory.duckdb \
PRME_VECTOR_PATH=/absolute/path/to/pack/vectors.usearch \
PRME_LEXICAL_PATH=/absolute/path/to/pack/lexical_index \
uv run python -m web.server --port 8080
```

### Demo pack

`web/demo.py` builds a small connected pack for owner `abc_123`: one person
across nine sessions, their employers, cleaners, manager and partner, a job
change, a rescheduled visit and two repeated claims. Each message goes through
the real `ingest()` pipeline; only the model's extraction output is scripted, so
the pack is deterministic and needs no model or network. It shows what PRME
builds from correct extraction output and does not measure extraction quality.
The messages name their subject, because first-person references (`I`, `my`)
stay local to one message by design (`docs/ENTITY-IDENTITY.md`).

```bash
uv run python -m web.demo ./my_memories_demo
PRME_CHAT_DATA_DIR=./my_memories_demo uv run python -m web.server
```

Stop another process using the same DuckDB pack before opening it here. This
starts a normal PRME engine: initialization/recovery can write to the pack even
though the interface only issues GET requests. Use a copy to examine a pack
without affecting the original. The normal HTTP API is also served, including
its authenticated write endpoints. This is an inspection UI, not a read-only
database or server. Existing authentication and external-bind protections apply;
`--host` defaults to loopback and external binds require API credentials.

## Explore

- Browse records, including retired memories, with type, lifecycle, and scope
  filters. Load more uses the API's stable UUID cursor. Text search is literal
  over **loaded entries** (content and ID), not semantic retrieval.
- Select an entry for its full content, stored confidence/salience, source
  evidence, timestamps, metadata, and full JSON record.
- An extracted claim shows its subject → predicate → object above its text,
  in the list, the graph and the relationship list. Its text is the source
  passage, which every claim from one message can share, so the claim is what
  tells them apart. The details add the claim's polarity, kind, temporal
  intent, the value it replaces, and the words fact text resolution rewrote.
- Follow incoming and outgoing connections by clicking graph nodes or the
  equivalent keyboard-accessible relationship list. Zoom, reset, and pan the
  map. On small screens, entries, graph, and details stack vertically.
- Inspect each edge's direction, type, confidence, validity, provenance event ID,
  and metadata. Unverified aliases are excluded by default; enabling them shows
  dashed proposal edges and explicit labels. Displaying a link does not verify
  its claim or accept an alias proposal.

The graph shows one hop, with **12 relationships per page** (six on small screens), within the selected
entry's owner and exact scope. Click a neighbor to make it the center. It includes
closed edges and retired neighbors; it is the current stored graph, not an
as-of-time replay. Edges between two neighbors are shown when either becomes the
center. Parallel edges and self-links retain their identities in the relationship
list. Independent pages can change during concurrent writes; refresh after changes.

## API adapter and checks

`GET /v1/explorer/nodes/{id}/connections` accepts `user_id` (or a bound identity),
`after_id` (edge UUID), `limit` (1–100), and `include_proposals` (default false).
It returns the center `node`, neighboring `nodes`, `edges`, `has_more`, and
`next_cursor`. Owner and scope checks happen before pagination, including checks
of both edge endpoints. Foreign and missing centers return 404. No hidden edge
counts are exposed. The adapter uses both backends' existing graph primitives;
those primitives currently load all incident edges before response pagination,
so very high-degree nodes can still be expensive.

```bash
uv run --extra api pytest web/tests -q
node --check web/static/app.js
node --check web/static/graph.js
```

The integration suite uses temporary stores and mock embeddings. PostgreSQL
cases run when `PRME_TEST_DATABASE_URL` is configured, following the main suite.
