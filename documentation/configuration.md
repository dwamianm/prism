# Configuration Reference

PRME uses [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) for configuration. All settings can be set via:

1. Constructor arguments
2. Environment variables (with `PRME_` prefix)
3. `.env` files

Nested settings use double underscores: `PRME_EMBEDDING__PROVIDER=openai`.

## Quick Setup

Minimal `.env` for LLM-powered ingestion:

```bash
# Required for ingest() — pick one provider
OPENAI_API_KEY=sk-...
# or
ANTHROPIC_API_KEY=sk-ant-...

# Optional: extraction model
PRME_EXTRACTION__PROVIDER=openai
PRME_EXTRACTION__MODEL=gpt-4o-mini
```

For store-only usage (no LLM), no configuration is needed.

## Explicit evidence policy

`PRMEConfig(privacy_policy=PrivacyPolicy(...))` selects an opt-in evidence policy;
the default is `None`. The caller can further restrict it with a text write's
`policy` argument. A policy requires opaque project/owner IDs, an explicit memory
retention choice, and finite evidence retention unless unlimited retention is
explicitly permitted by both policy and deployment.

Protected admission requires an injected `PrivacyRuntime` with an authoritative
policy service, sanitizer, payload protector and guarded providers. Missing
capabilities reject before new store creation. Built-in providers are not yet
implicitly eligible for protected operation. This component does not turn on a
production protected profile or change existing defaults. The current alpha
enables owner-group links, which protected profiles do not support: use
`PRMEConfig(privacy_policy=policy, enable_owner_group_links=False)`, or set
`PRME_ENABLE_OWNER_GROUP_LINKS=false`. Leaving it enabled returns the fixed
`privacy_configuration_invalid` error before protected admission effects. See
[evidence policies](../docs/PRIVACY-POLICIES.md) for valid combinations,
synchronous preparation, provider inspection and remaining integration limits.

## Storage Paths

| Variable | Default | Description |
|----------|---------|-------------|
| `PRME_DB_PATH` | `./memory.duckdb` | DuckDB database file |
| `PRME_VECTOR_PATH` | `./vectors.usearch` | usearch HNSW index file |
| `PRME_LEXICAL_PATH` | `./lexical_index` | Tantivy full-text index directory |
| `PRME_PACK_PATH` | `None` | Opt-in single-file pack, conventionally a `.prme` file: one DuckDB database that also holds the vector snapshot and the pack manifest, with the lexical index rebuilt in memory on open. When set, `PRME_DB_PATH`, `PRME_VECTOR_PATH` and `PRME_LEXICAL_PATH` are ignored. It cannot be combined with `PRME_DATABASE_URL`, and an empty value means unset. See [single-file packs](../docs/INTEGRATION.md#single-file-packs) |
| `PRME_DATABASE_URL` | `None` | PostgreSQL URL (overrides DuckDB) |

Without `PRME_PACK_PATH`, a local pack is the directory layout of the three
paths above, which is the default.

## Embedding

| Variable | Default | Description |
|----------|---------|-------------|
| `PRME_EMBEDDING__PROVIDER` | `fastembed` | `fastembed` (local) or `openai` |
| `PRME_EMBEDDING__MODEL_NAME` | `BAAI/bge-small-en-v1.5` | Embedding model |
| `PRME_EMBEDDING__DIMENSION` | `384` | Embedding dimension |
| `PRME_EMBEDDING__API_KEY` | `None` | API key for OpenAI embeddings |
| `PRME_EMBEDDING__THREADS` | `None` | ONNX Runtime threads for the `fastembed` model. `None` keeps ONNX Runtime's default of one thread per core the machine shows, which does not follow a container's CPU limit; set it to the CPUs the process may use. Other providers ignore it |
| `PRME_EMBEDDING__QUERY_PREFIX` | `None` | Text put before every search question the `fastembed` model embeds, for a model trained with a query instruction (BGE and mxbai: `Represent this sentence for searching relevant passages: `; nomic: `search_query: `). `None` adds nothing. Changing it changes every question's vector and the version recorded with each stored vector, so an existing store needs its vectors rebuilt |
| `PRME_EMBEDDING__ADDITIONAL_MODELS` | empty | Fastembed models, besides the active `PRME_EMBEDDING__MODEL_NAME`, whose vectors every store writes as well (dual write), so a new model's index can be built while the store keeps serving the active one. A list, a JSON list or a comma-separated string; it needs `PRME_ENABLE_MULTI_MODEL_EMBEDDINGS`, cannot name the active model, and the models are embedded without the prefixes above. Searches read only the active model's index. Empty it is left out of the configuration dump (dwamianm/prism#368; [switching models](../docs/EMBEDDING-MODELS.md)) |
| `PRME_EMBEDDING__DOCUMENT_PREFIX` | `None` | Text put before every stored text the `fastembed` model embeds (nomic: `search_document: `). `None` adds nothing. Changing it changes every stored vector, so an existing store needs its vectors rebuilt |

The default `fastembed` provider runs locally with no API key needed. It downloads the model on first use (~130MB). `PRME_EMBEDDING__THREADS` changes how many threads compute each vector, not the model or the version recorded with each vector; see [resource control](../docs/LOCAL-RESOURCES.md) before changing it for a store whose vectors already exist.

## Extraction (LLM)

| Variable | Default | Description |
|----------|---------|-------------|
| `PRME_EXTRACTION__PROVIDER` | `openai` | `openai`, `anthropic`, or `ollama` |
| `PRME_EXTRACTION__MODEL` | `gpt-4o-mini` | Model name |
| `PRME_EXTRACTION__MAX_RETRIES` | `3` | Retry count for API failures |
| `PRME_EXTRACTION__TIMEOUT` | `30.0` | Timeout in seconds |
| `PRME_EXTRACTION__BATCH_TIMEOUT` | `180.0` | Timeout in seconds for one batched extraction call, which returns the claims of every turn it holds |
| `PRME_EXTRACTION__API_KEY` | `None` | Credential; defaults to the provider's own (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) |
| `PRME_EXTRACTION__BASE_URL` | `None` | Endpoint; defaults to the provider's own (`OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL`, or a local Ollama) |

The extraction provider is used by `ingest()` and `ingest_batch()`, and by `retrieve()` only when `PRME_ENABLE_QUERY_REFORMULATION=true`: query reformulation calls the same provider, model, endpoint and credential, with the same timeout. The `store()` method does not call an LLM.

## Visual memory (RFC-0018)

Off by default. `observe()` and `prme observe` describe photos and videos with a
local vision model and store the descriptions as events; nothing here changes a
pack that never observes.

| Variable | Default | Description |
|----------|---------|-------------|
| `PRME_VISION__ENABLED` | `false` | Allow `observe()`; `prme observe` turns it on for its own run |
| `PRME_VISION__PROVIDER` | `ollama` | Vision provider (the only one in this release) |
| `PRME_VISION__MODEL` | `qwen3.5:9b` | Vision model, recorded on every observation |
| `PRME_VISION__BASE_URL` | `None` | Endpoint; defaults to `OLLAMA_HOST` or the local server |
| `PRME_VISION__TIMEOUT` | `180.0` | Seconds per description call |
| `PRME_VISION__EXTRACT` | `true` | Admit descriptions with `ingest()` and extraction by the configured extraction provider (which may be a hosted API); `false` uses `store()` |
| `PRME_VISION__EXTRACTION_RETRY_DELAYS` | `[5.0, 15.0, 60.0]` | Waits before each retry of a failed extraction |
| `PRME_VISION__FPS` | `1.0` | Frames sampled per second of video |
| `PRME_VISION__FRAME_WIDTH` | `1280` | Decoded frame width in pixels |
| `PRME_VISION__CHANGE_THRESHOLD` | `0.55` | Pixel-change score that starts a keyframe |
| `PRME_VISION__MAX_GAP_SECONDS` | `30.0` | A keyframe at least this often (heartbeat) |
| `PRME_VISION__REPEAT_SIMILARITY` | `0.93` | Description cosine similarity, with the same quoted text, that drops a repeat |
| `PRME_VISION__THUMBNAIL_WIDTH` | `320` | Thumbnail width in pixels |
| `PRME_VISION__KEEP_THUMBNAILS` | `true` | Keep a thumbnail per observation in the pack's `blobs` table |

Video needs `ffmpeg` on the path; images do not. A HEIC, HEIF or AVIF photo
needs the `pillow-heif` package. `observe()` needs a local DuckDB pack; the
PostgreSQL backend is not supported in this release.

## Extracted claims (`ingest()`)

These settings change what `ingest()` stores from an extraction. They do not affect `store()`, and records stored before a change keep their text.

| Variable | Default | Description |
|----------|---------|-------------|
| `PRME_ENABLE_CLAIM_MERGE` | `true` | Merge an exactly repeated extracted claim into one current record: the new fact keeps the earlier copies' evidence and supersedes them (issue #209). Quantities, conditional claims, unresolved relative dates and claims that are not observed or asserted stay separate. `false` stores one fact per extraction |
| `PRME_ENABLE_CLAIM_SENTENCE_TEXT` | `true` | Store a claim's own sentences as its text: the shortest run of whole sentences of its passage that mentions its subject and object, with a following sentence that qualifies it (only if, unless, however). The paragraph stays its `evidence_quote`, so a qualifier elsewhere in the paragraph stays in the evidence but not in the text. `false` gives every claim from a one-paragraph message the whole message as its text. Default since 2026-10-06; provisional |
| `PRME_ENABLE_SPEAKER_REFERENCES` | `true` | In a turn that names its speaker (`ingest(speaker=...)`), bind I, me, my, mine and myself in its claims to that speaker's entity, so the speaker's claims connect across messages and a repeated or replaced claim can merge with or supersede the earlier one. Plural, second- and third-person references stay local to their message. Only the caller knows that the named speaker, and not someone the turn quotes, is who says I, so set it to `false` when a speaker's turns quote others in the first person. Default since 2026-10-06; provisional. See [entity identity](../docs/ENTITY-IDENTITY.md) |
| `PRME_ENABLE_MENTION_LINKS` | `true` | Link each claim and the raw source note to every entity its extraction lists whose name occurs in the text (a `MENTIONS` edge with `metadata.basis` `text_mention`), so the claims and notes that name an entity are its graph neighbors (issue #259). A name that two listed entities share, and a literal I or we, are not linked. Memories ingested before it was on are not linked. On since 2026-10-19 in the alpha channel (0.16.0a3), on the fast gate by the owner's directive; `false` restores the links of 0.16.0a2. The full benchmark audit and the default-change rule's answer runs are pending. Use it with `PRME_PACKING__GRAPH_SEED_WORDS` |
| `PRME_ENABLE_SPEAKER_GROUNDING` | `false` | Let a named speaker's own I, me and my count as a mention of the speaker when grounding checks a claim's subject and object, so a claim such as "Dana, lives in, Denver" for "I live in Denver" is kept. It completes the graph, but the extra claims cost evidence on the offline evidence gate, so it stays off. Provisional |
| `PRME_ENABLE_INGEST_CLAIM_FIXES` | `true` | Three changes to what a turn's claims say (prism-monorepo issue 258): a claim the model writes with I where the sentences say we keeps the sentences' own word, a user turn that says who it is ("I am Alex Rivera") binds its I, me and my to that person when the extraction lists them, and a claim whose sentences are already another claim's text from the same message gets a one-sentence statement of its own. On since 2026-10-19 in the alpha channel (0.16.0a3), on the fast gate together with `PRME_PACKING__FOLDED_RECORDS_KEEP_RANK`, which keeps the third change from lowering the rank of an answer; `false` restores the claims of 0.16.0a2. The full benchmark audit and the default-change rule's answer runs are pending. Provisional |
| `PRME_ENABLE_OWNER_NAME_CARRY` | `true` | A `user` turn is the memory's owner by definition. The person entity that a user turn creates under the owner's name (the caller's `speaker`, or "I am Alex Rivera" with `PRME_ENABLE_INGEST_CLAIM_FIXES`) is marked with `owner_name`, and a later user turn of the same owner and scope that names nobody ("I prefer trains to flights") binds its I, me and my to the newest such entity, so the person is linked to all of their claims. Needs `PRME_ENABLE_SPEAKER_REFERENCES`. No model call, and the extraction request is unchanged. A memory built before it was on has no marked entity. On since 2026-10-19 in the alpha channel (0.16.0a3), on the fast gate by the owner's directive; `false` gives each later turn its own I entity again. The full benchmark audit and the default-change rule's answer runs are pending. Provisional |
| `PRME_ENABLE_OWNER_GROUP_LINKS` | `true` | Link a claim that a `user` turn states about we, us or our to the people it speaks for (prism-monorepo issues 259, 260 and 275). The claim keeps its group subject and its literal we, because a we claim is not always true of one person alone, and a graph walk from the owner's entity never reached it. Each extra link is a `MENTIONS` edge with `metadata.basis` `group_member` from the claim: to the entity that carries the name a user turn gave the owner (`PRME_ENABLE_OWNER_NAME_CARRY`), under the turn's own `speaker` when the caller named one, because we always includes the speaker; and to each person the session names with the owner, when the owner's turns of the session up to the claim's turn name exactly one group ("My partner Jordan and I", "Jordan and I", "me and Jordan") and each member is a person entity already stored (that link records the naming turn in `named_in`). Two different groups, a member without a name ("my wife and I") or a name no single person entity has link the owner only. Names count as written with a capital letter. With `PRME_ENABLE_CLAIM_MERGE`, the same we claim repeated in two user turns of one session merges into one current record. Made after the derivation commits, outside the plan; no model call and no extraction request change; never for a `first_person_owner` turn, never when no user turn named the owner. Memories ingested before it was on are not linked or merged. Needs `PRME_ENABLE_OWNER_NAME_CARRY`. On since 2026-10-19 in the alpha channel, on the fast tiers by the owner's directive; `false` restores the claims and links of 0.16.0a3. The full benchmark audit and the default-change rule's answer runs are pending. Provisional |
| `PRME_ENABLE_MULTI_MODEL_EMBEDDINGS` | `false` | Keep vectors by node, model name and model version, one index per model, so a store can switch embedding models (dwamianm/prism#368): `PRME_EMBEDDING__ADDITIONAL_MODELS` are written too, `reembed()` (`POST /v1/reembed`, `memory_reembed`, `prme reembed`) builds a model's vectors for the stored memories, and setting `PRME_EMBEDDING__MODEL_NAME` to a model whose index is complete makes it the active one. An existing store's vectors are adopted as its first model's index, with no re-embedding. A store whose vectors were all made by another model refuses writes with an error that names both models and reports `embedding_incomplete` in retrieval metadata. Off, storage, behavior and this dump are what they were. Not available for single-file packs. See [switching embedding models](../docs/EMBEDDING-MODELS.md) |
| `PRME_ENABLE_REINGEST` | `false` | Let the engine re-derive an owner's stored memories (`reingest()`, `POST /v1/reingest`, `memory_reingest`, `prme reingest`): the batched extraction `ingest()` runs, over the owner's sources in event order, replaces the claims derived from each with new ones and never changes the source events or raw notes. It calls the extraction model, so an engine that did not turn it on refuses the call (prism-monorepo issue 278) |
| `PRME_ENABLE_WINDOWED_EXTRACTION` | `false` | Show the extractor the preceding turns of the same session, so it can read what the new turn refers to (issue #91). Only the new turn is extracted and grounded. Needs a `session_id`; provisional |
| `PRME_ENABLE_OWNER_WINDOW_EXTRACTION` | `false` | Give a turn with no `session_id` a window too: the owner's preceding memories in the same scope, in the order they were stored, up to `PRME_EXTRACTION_WINDOW_TURNS` of them. `store()` carries no `session_id`, so without this a plainly stored memory such as "They chose Tokyo and Kyoto." is extracted with nothing before it. Only has an effect with `PRME_ENABLE_WINDOWED_EXTRACTION`, which also turns batched extraction off, so `ingest()` and `reingest()` send each memory as its own request. Provisional |
| `PRME_EXTRACTION_WINDOW_TURNS` | `4` | How many preceding turns the window shows, from 0 to 32; 0 turns the window off. Each turn adds prompt tokens to every extraction. Provisional |
| `PRME_ENABLE_PRONOUN_REPAIR` | `true` | Ask a second time who a pronoun means, only for a source whose first extraction left a claim on one: a third person pronoun (they, them, their, he, she, it and the like) as the claim's subject or object. A first person pronoun is never repaired: `I` is bound by the speaker rules, and `we`, `us` and `our` include the speaker and a group no memory states (the owner group links connect them). The request shows the owner's preceding memories (up to `PRME_EXTRACTION_WINDOW_TURNS`, in the order stored, in the same scope; the turns before it in its session when it has one) and only the claim's own sentences, and the model returns the names it can ground. A claim is repaired only when each name is a proper name written in its sentence, the message, the speaker line or a memory the request showed (a name need not occur in the evidence quote, which stays the claim's evidence, verbatim); its text becomes its own sentence with the names written in, a field that resolves to one name attaches to that person's entity, a field that resolves to several (they) stays on the pronoun and each name is listed as an entity, and no other claim of the message changes. `metadata.resolved_from_context` records the memories whose text wrote the names (`event_ids`) and, for each repaired field, the pronoun and the names that replaced it (`fields`); `metadata.grounding_method` is `pronoun_repair_v1`. Works in `ingest()`, `store(extract=True)` and `reingest()`, with batched extraction on or off, and needs nothing else on. It costs one more request for each source with such a claim; its request and window are part of the re-derivation settings, so `reingest()` reads sources derived without it as stale. On by default since 2026-10-22 in the alpha channel, by the maintainer's decision: the alpha pass rule's defect probe condition (7 of 7) was not met, because the extraction model used in production scores 6 of 7 with the option off; no mid tier or full benchmark was run on it; one memory in the fixtures is repaired (the cities claim, 23rd to 1st). `false` restores the first extraction exactly. Provisional |
| `PRME_EXTRACTION_BATCH_TARGET_TOKENS` | `2048` | Extract several turns of a session in one model call (issue #254): each call takes a session's queued turns while their text stays within this many tokens; a larger turn, or one without a `session_id`, goes alone. Each turn's claims are still checked against that turn alone and saved as its own record. A turn ingested without waiting is extracted in a batch 30 seconds later, or sooner by a waiting `ingest()`, `ingest_batch()`, `process_extractions()` or the engine's close, which waits for the queued batches up to five minutes. A batched call has its own timeout, `PRME_EXTRACTION__BATCH_TIMEOUT`. Cannot be combined with the extraction window or fact text resolution. `0` makes one call per turn. The default since v0.15.0, chosen on the offline evidence gate (95% fewer extraction calls on LoCoMo) without an answer run; see [batched extraction](../docs/INTEGRATION.md#batched-extraction) |
| `PRME_ENABLE_FACT_TEXT_RESOLUTION` | `false` | Ask the extractor for fact text that stands alone: a pronoun for a named person becomes the name and a relative date the date it means (issue #91). A deterministic check keeps that text only when those replacements are its sole changes; otherwise the fact keeps its source passage. Names from earlier turns need `PRME_ENABLE_WINDOWED_EXTRACTION`. Provisional |

## Scoring Weights

These control the hybrid retrieval scoring formula. The six additive weights must sum to 1.0. The default fusion is `rrf`, which ranks by semantic and lexical rank and does not use the additive weights; set `PRME_SCORING__FUSION=weighted` to score with them. Setting any other `PRME_SCORING__*` variable keeps `rrf` and its recency boost and tie-break unless `PRME_SCORING__FUSION` is set too.

| Variable | Default | Signal |
|----------|---------|--------|
| `PRME_SCORING__W_SEMANTIC` | `0.25` | Vector cosine similarity |
| `PRME_SCORING__W_LEXICAL` | `0.20` | BM25 full-text score |
| `PRME_SCORING__W_GRAPH` | `0.20` | Graph proximity |
| `PRME_SCORING__W_RECENCY` | `0.10` | Time decay: exp(-lambda * days) |
| `PRME_SCORING__W_SALIENCE` | `0.10` | Virtual salience with decay |
| `PRME_SCORING__W_CONFIDENCE` | `0.15` | Virtual confidence with decay |
| `PRME_SCORING__W_EPISTEMIC` | `0.05` | Epistemic type multiplier |
| `PRME_SCORING__W_PATHS` | `0.00` | Path count tiebreaker |
| `PRME_SCORING__RECENCY_LAMBDA` | `0.02` | Decay rate for recency |
| `PRME_SCORING__TEMPORAL_BOOST` | `0.15` | Bonus for temporal queries |
| `PRME_SCORING__FUSION` | `rrf` | `rrf`: reciprocal rank fusion of semantic and lexical ranks, which ignores the additive weights (RFC-0005 Section 7.2), or `weighted`: the sum above. A `ScoringWeights` object built in code defaults to `weighted`; `PRMEConfig().scoring` is `rrf`. Under `rrf` a result's score is rank-based, so a request's `min_score` is compared with its `semantic_relevance`, the semantic cosine similarity, instead; a floor tuned on weighted scores does not carry over, and a low-cosine exact keyword match is filtered out when a floor is set. If vector search fails or detects an embedding model mismatch and no result has a cosine, the floor is skipped and the response metadata sets `min_score_skipped` |
| `PRME_SCORING__RRF_K` | `60` | Rank constant for `rrf`; ignored, with a warning, under `PRME_SCORING__FUSION=weighted` |
| `PRME_SCORING__RRF_RECENCY_BOOST` | `0.25` | `rrf` only: on current-state questions, multiplies each fused score by `1 + boost x recency` relative to the pool's largest value, where recency is the weighted formula's current-state recency (exp decay from the newest candidate, doubled for update wording), so the newer of two conflicting memories can rank first. Above 0 and at most 4. 0.25 passed the simulations with no significant change on the offline evidence gate; 1.0 lost LoCoMo evidence (RFC-0005 Section 7.2); provisional. Dropped when `PRME_SCORING__FUSION=weighted` is set; a `ScoringWeights` object built in code leaves it unset |
| `PRME_SCORING__RRF_TIE_BREAK` | `event_time` | `rrf` only: `event_time` orders equal fused scores newest first instead of by path count and node ID. Dropped when `PRME_SCORING__FUSION=weighted` is set; a `ScoringWeights` object built in code leaves it unset |
| `PRME_SCORING__RRF_GRAPH_WEIGHT` | unset (`0.5`, the seed count list's, while `PRME_SCORING__RRF_GRAPH_SEED_COUNT` is on, as it is by default) | `rrf` only, above 0 and at most 4 (prism-monorepo issues 259 and 260). Adds the graph path as a third ranked list: a candidate the graph search returned ranks by hop count (first for one hop, second for two, third for three) and gains `weight / (rrf_k + rank)` before the scale that gives first place on both other lists 1.0, so a candidate first on all three scores `1 + weight / 2`. The term goes through the same epistemic and node-type factors as the rest of the score. A candidate only the graph returned scores above 0. Unset, graph proximity is ignored. Not gated, and not recommended: on the fast cycle it moves two chat-v1 answers out of the top 3 at `0.25` and one at `0.15` or `0.1`, and on the mid tier it lowers LoCoMo evidence in the top 25 by 5.4 points (`BENCHMARKS.md`, "Graph fusion"). Dropped, with a warning, under `PRME_SCORING__FUSION=weighted`. With `PRME_SCORING__RRF_GRAPH_SEED_COUNT=true` it is the weight of that list instead, `0.5` when unset |
| `PRME_SCORING__RRF_GRAPH_SEED_COUNT` | `true` | `rrf` only (prism-monorepo issue 276). Ranks the graph list by how many of the entities the question names reach a record, instead of by hop count. A record joins only when two or more seeds reach it within `PRME_PACKING__GRAPH_MAX_HOPS`, one of them by a direct link, along links that never pass through the owner's entity (an entity a user turn named as the owner, or the owner reference), because nearly every claim of the owner is one hop from it, and when the semantic search ranks it in its top 10, so the list reorders records that already match the question; the list ranks more seeds first, then fewer total hops, and an entity record never joins. Its weight is `PRME_SCORING__RRF_GRAPH_WEIGHT`, `0.5` when that is unset. A record that session, episode or evidence augmentation context pulls in inherits its trigger's score without the graph term; evidence projection keeps it. Limits: only a record among the graph search's kept candidates (`PRME_PACKING__GRAPH_MAX_CANDIDATES`) can join, and a first-person question ("which cities will we visit in Japan?") has one seed at most, so the list does nothing for it. Cost: a question that names two or more entities reads the links around them once more, so the cost grows with their neighborhood: about 60 ms per retrieval (median) on a 400-turn LoCoMo conversation, about 0.1 s at 2,000 turns and 0.5 s at 10,000 on synthetic DuckDB packs. The walk is skipped when more than 5,000 nodes would be read, and the response metadata then reports `graph_seed_reach: neighborhood_limit` (or `backend_error` when the read failed; the graph candidates are kept). On since 2026-10-19 in the alpha channel, on the fast tiers by the owner's directive; `false` restores the scoring of 0.16.0a5. Turning it off in a dump of settings it filled (`PRMEConfig().scoring.model_dump()`) leaves `rrf_graph_weight` at `0.5`, which turns on the hop count list; drop the weight too. If you set `PRME_SCORING__RRF_GRAPH_WEIGHT` for the hop count list of 0.16.0a4, set this to `false` to keep it. On the fast tiers it keeps every defect probe and chat probe count, passes the fast cycle and leaves the offline evidence gate and the mid tier unchanged; with the group links of `PRME_ENABLE_OWNER_GROUP_LINKS` it puts the Japan cities claim 4th for "which cities will alex visit in japan?" (6th without it) (`BENCHMARKS.md`, "Graph seed count"). The full benchmark audit and the default-change rule's answer runs are pending. Dropped, with a warning, under `PRME_SCORING__FUSION=weighted` |
| `PRME_SCORING__RECENCY_TIME` | unset | `weighted` only: `event_time` dates every memory by when it was stated (its event time, else when it was stored; never when it was last updated, and never its validity start) and measures recency from that against the request's reference time. Unset, questions that are not about the current state measure it from when the memory was last updated or created, so history imported with a past event time and retrieved at a past reference time gets full recency everywhere. Current-state questions keep measuring back from the newest candidate. Memories without an event time, including entity, consolidation and profile nodes, count from when they were stored. On the offline evidence gate it lost LoCoMo evidence, so it stays off (RFC-0005 Section 7). Needs `PRME_SCORING__FUSION=weighted`; rank fusion's recency boost and tie-break already use this clock, and rank fusion drops the setting with a warning. Turning it on or off makes a learned ranking profile built under the other setting inapplicable |

## Context Packing

The defaults below are `PRMEConfig`'s, and environment variables that set only
some of them keep the others. A `PackingConfig` built in code starts from its
own field defaults instead (`auditable`, `balanced`, no rank fusion session
decay and no folding), which stored receipts and configurations rely on.

| Variable | Default | Description |
|----------|---------|-------------|
| `PRME_PACKING__TOKEN_BUDGET` | `4096` | Max tokens in packed context |
| `PRME_PACKING__OVERHEAD_TOKENS` | `100` | Reserved for formatting overhead |
| `PRME_PACKING__CHARS_PER_TOKEN` | `4.2` | Estimated chars per token |
| `PRME_PACKING__CONTEXT_FORMAT` | `reader` | `reader` (one plain line per record), `auditable` (one JSON object per record) or `compact`. Answerability and claim verification need `PRME_PACKING__CONTEXT_CITATIONS=true` with `reader` |
| `PRME_PACKING__MULTIPATH_ORDERING` | `balanced` | Order within the multi-path tier: `balanced`, `score` (briefly the default with the reader format) or `density` |
| `PRME_PACKING__CONTEXT_CITATIONS` | `false` | Reader format only: add `[m3]` references and fill `context_references` |
| `PRME_PACKING__FOLD_REPEATED_TEXT` | `true` | Reader format only: pack each source text once. A record is left out when a packed record from one of its source events already shows its text, unless its line shows another date, tag or speaker, and its budget goes to other records; the auditable and compact formats ignore the setting. Default since 2026-10-06. Receipts that record it use schema version 22, and with `false` default retrievals write version 19. See [folding repeated text](../docs/PACKING.md#folding-repeated-text); provisional |
| `PRME_PACKING__FOLDED_RECORDS_KEEP_RANK` | `true` | A record that stands in for another keeps the better rank of the two (prism-monorepo issues 258 and 259). `retrieve()` folds each claim statement that `PRME_ENABLE_INGEST_CLAIM_FIXES` builds into the claim of the same evidence that holds its object, which takes the better score, and in the reader format a record that folds earlier packed records takes the place of the earliest one. Memories without statements are unchanged. On since 2026-10-19 in the alpha channel (0.16.0a3), on the fast gate by the owner's directive; `false` restores the ranking of 0.16.0a2. Provisional |
| `PRME_PACKING__MIN_FIDELITY` | `reference` | Lowest representation a record may fall back to. `full`, `prose` or `structured` keeps the text-free `key_value` and `reference` fallbacks, and blank records, out of every context format |
| `PRME_PACKING__VECTOR_K` | `500` | Vector search candidates. Count and list questions multiply it by `PRME_PACKING__AGGREGATION_K_MULTIPLIER`, up to `PRME_PACKING__AGGREGATION_K_MAX`. At 500 the saved benchmark packs return nearly every stored turn; the offline evidence gate reports each channel's recall at smaller limits (issue #87) |
| `PRME_PACKING__LEXICAL_K` | `500` | BM25 search candidates, widened the same way. The entity-name and aggregation keyword scans add hits beyond this limit |
| `PRME_PACKING__GRAPH_MAX_CANDIDATES` | `150` | Graph search candidates, widened the same way |
| `PRME_PACKING__GRAPH_MAX_HOPS` | `3` | Max graph traversal depth |
| `PRME_PACKING__GRAPH_SEED_WORDS` | `true` | Seed graph search from the words of the question whatever their case (issue #259). Off, only capitalized names and quoted strings seed it. On, a word of three or more letters that is not a common function word also seeds it when it is a whole word of an entity's name, so `alex` finds Alex Rivera. On since 2026-10-19 in the alpha channel (0.16.0a3), on the fast gate by the owner's directive; `false` restores the seeds of 0.16.0a2. A `PackingConfig` built in code keeps `false`. Provisional |
| `PRME_PACKING__SESSION_CONTEXT_WINDOW` | `3` | Adjacent turns to include |
| `PRME_PACKING__SESSION_CONTEXT_TOP_K` | `20` | Top results for session expansion |
| `PRME_PACKING__SESSION_CONTEXT_SCORE_DECAY` | `0.85` | Fraction of the triggering result's score that an adjacent turn inherits |
| `PRME_PACKING__SESSION_CONTEXT_RANK_FUSION_SCORE_DECAY` | `0.6` | Fraction for results scored with `PRME_SCORING__FUSION=rrf`, between 0 (exclusive) and 1, in place of the decay above; weighted scoring never uses it. Fused scores are compressed, so at 0.85 adjacent turns can crowd primary evidence out of the context. The offline evidence gate favored 0.6 (RFC-0005 Section 7.2); provisional |
| `PRME_PACKING__SESSION_CONTEXT_SKIP_ENTITIES` | `true` | Keep entity records out of session context expansion (prism-monorepo issues 259 and 260). `ingest()` gives an entity the `session_id` of the turn that names it, so a window of adjacent records holds the entities created beside a claim, and each takes the full decay of the claim's score; a chat that names five places puts five entities, which never answer a question, above its notes and claims. On, an entity neither starts an expansion (`PRME_PACKING__SESSION_CONTEXT_TOP_K` counts the other records) nor receives an inherited score; the window still counts its place in the session. Passes the fast gate, the fast cycle and the offline evidence gate unchanged, and on the mid tier raises LoCoMo evidence in the top 25 by 2.1 points (48 wins, no loss). On since 2026-10-19 in the alpha channel (0.16.0a4), on the fast tiers and the mid tier by the owner's directive; `false` restores the expansion of 0.16.0a3, and a `PackingConfig` built in code keeps `false`. Provisional |
| `PRME_PACKING__SESSION_CONTEXT_PACKING` | unset | Opt-in (issue #86). `trigger_tier` counts session expansion as a path on a turn another search found and gives a turn it added the multi-path tier of the match that brought it in; `adjacent` packs the same records and places each neighbor beside that match in session order. Needs session expansion. Receipts that record it use schema version 21; remove the variable (an empty value is invalid) to turn it off. Provisional |
| `PRME_PACKING__AGGREGATION_K_MULTIPLIER` | `3.0` | Multiplier applied to `VECTOR_K`, `LEXICAL_K` and `GRAPH_MAX_CANDIDATES` for count and list questions |
| `PRME_PACKING__AGGREGATION_K_MAX` | `2000` | Cap on each widened limit |

## Organizer

| Variable | Default | Description |
|----------|---------|-------------|
| `PRME_ORGANIZER__OPPORTUNISTIC_ENABLED` | `true` | Run maintenance during retrieve/ingest |
| `PRME_ORGANIZER__OPPORTUNISTIC_COOLDOWN` | `3600` | Seconds between opportunistic runs |
| `PRME_ORGANIZER__OPPORTUNISTIC_BUDGET_MS` | `200` | Budget for opportunistic runs |
| `PRME_ORGANIZER__DEFAULT_ORGANIZE_BUDGET_MS` | `5000` | Default budget for explicit organize() |
| `PRME_ORGANIZER__PROMOTION_AGE_DAYS` | `7.0` | Days before auto-promotion |
| `PRME_ORGANIZER__PROMOTION_EVIDENCE_COUNT` | `1` | Min evidence refs for promotion |
| `PRME_ORGANIZER__ARCHIVE_SALIENCE_THRESHOLD` | `0.10` | Salience below this → archive candidate |
| `PRME_ORGANIZER__ARCHIVE_CONFIDENCE_THRESHOLD` | `0.40` | Confidence below this → archive candidate |
| `PRME_ORGANIZER__DEDUP_SIMILARITY_THRESHOLD` | `0.92` | Vector similarity for dedup |
| `PRME_ORGANIZER__ALIAS_SIMILARITY_THRESHOLD` | `0.85` | Similarity for entity alias detection |
| `PRME_ORGANIZER__CONSOLIDATION_MIN_CLUSTER_SIZE` | `3` | Min cluster size for consolidation |
| `PRME_ORGANIZER__CONSOLIDATION_SIMILARITY_THRESHOLD` | `0.80` | Similarity for consolidation |

## Optional Features

| Variable | Default | Description |
|----------|---------|-------------|
| `PRME_LEXICAL_TERM_WEIGHTS` | `true` | PostgreSQL word search only; the DuckDB backend ignores it. Weights each query word by how rare it is among the owner's searched memories (ln(N / df)), and with more than one query word keeps a memory as a word-search candidate only when the words it holds would be held together by chance by at most 0.2% of those memories, or 2 memories if that is more. The owner's name or a common verb no longer lifts every memory, and candidates rank by the weight of the words they hold. `false` restores the any-term search of 0.16.0a1. Default since 2026-10-19, in the alpha channel, chosen on the fast cycle and the offline evidence gate; the full benchmark audit is pending. Provisional |
| `PRME_ENABLE_STORE_SUPERSEDENCE` | `false` | Auto-detect supersedence on store |
| `PRME_REINFORCE_SIMILARITY_THRESHOLD` | `None` | Auto-reinforce similar existing nodes |
| `PRME_ENABLE_SURPRISE_GATING` | `false` | Novelty-based salience adjustment |
| `PRME_ENABLE_RERANKER` | `false` | Rerank the top scored candidates with a local cross-encoder after scoring. Needs `pip install prme[reranker]` and the model files. Experimental: it has not improved answer scores so far, and on the offline evidence gate no cross-encoder rank-order variant beat rank fusion alone on both benchmarks at both 4K and 8K (issue #88) |
| `PRME_RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Hugging Face cross-encoder the reranker loads |
| `PRME_RERANKER_TOP_K` | `100` | How many of the top scored candidates the reranker scores; the rest keep their order |
| `PRME_RERANKER_POLICY` | `legacy` | `score_envelope` gives the reranked candidates their original scores in the new order, so later stages see the same score scale; `anchored_score_envelope` also keeps the original top multi-path record first. `legacy` puts the blended scores on them. See [experimental retrieval policies](../docs/EXPERIMENTAL-RETRIEVAL-POLICIES.md) |
| `PRME_RERANKER_PRIOR_WEIGHT` | `0.3` | Weight of a reranked candidate's own score in the new order; the cross-encoder gets the rest. `0.0` orders the reranked candidates by the cross-encoder alone (issue #88). A value other than 0.3 needs `PRME_RERANKER_POLICY=score_envelope` or `anchored_score_envelope` (the configuration is refused with `legacy`), is recorded in retrieval receipts and makes a learned ranking profile built under another value inapplicable |
| `PRME_QUERY_INTENT_ORDER` | `entity_first` | `temporal_first` treats a question with temporal wording (when, last, before, since and similar) or a date in it as temporal even when it names a person, place or organization, so it gets temporal affinity scoring. A present-tense question that only its wording put on the current-state path ("Who is Caroline dating since the breakup?") leaves it unless it says current, now, latest or similar, and the temporal context guidance is added where the question's wording does not already select it. A lone capitalized month or weekday word that is part of a name ("Who is June dating?") is read as the name, not a date. `entity_first` checks the entity patterns first, so a temporal question that names someone gets no temporal affinity. Experimental (RFC-0005 Section 3); changing it makes a learned ranking profile built under the other order inapplicable |

## Encryption

| Variable | Default | Description |
|----------|---------|-------------|
| `PRME_ENCRYPTION_ENABLED` | `false` | Enable encryption at rest. Takes effect only with `PRME_ENCRYPTION_KEY` set |
| `PRME_ENCRYPTION_KEY` | `None` | Passphrase or raw key. The `raw_key:` prefix marks a raw Fernet key and `passphrase:` forces derivation; an unprefixed 44-character url-safe base64 value is read as a raw Fernet key, and anything else as a passphrase |

A directory pack uses Fernet file encryption. A single-file pack
(`PRME_PACK_PATH`) uses DuckDB's native encryption instead: DuckDB encrypts the
file and its write-ahead log with AES-256-GCM, and PRME turns on DuckDB's
`temp_file_encryption`, which is off by default even for an encrypted
database, with temporary files in `<pack>.tmp` beside the pack. For a
single-file pack, a passphrase becomes the DuckDB key through
PBKDF2-HMAC-SHA256 (600,000 iterations) with the fixed salt
`prme-single-file-pack-v1`, so the same passphrase gives the same key for every
pack; a raw key is used as given. A missing or wrong key, or encryption
configured for an unencrypted pack, raises `EncryptionError` and leaves the
file unchanged.

See [Deployment](deployment.md#encryption-at-rest) for details.

Opt-in evidence encryption has separate purpose keys and explicit capabilities;
it does not reuse `PRME_ENCRYPTION_KEY` or change this default. Install the
`local-privacy` extra, provision the required public descriptors, and inject
`CryptoProtector` into `PrivacyRuntime`. Ordinary sealed writers never open a
native private key. See [privacy key provisioning, diagnostics and rotation](../docs/PRIVACY-KEYS.md).
Protected product readiness still depends on the remaining privacy release gates.

## Write Queue

| Variable | Default | Description |
|----------|---------|-------------|
| `PRME_WRITE_QUEUE_SIZE` | `1000` | DuckDB write queue size |
| `PRME_MATERIALIZATION_QUEUE_SIZE` | `500` | Graph materialization queue |
| `PRME_MATERIALIZATION_BUDGET_MS` | `100` | Budget per materialization batch |

## Programmatic Configuration

```python
from prme.config import PRMEConfig

config = PRMEConfig(
    db_path="./custom.duckdb",
    vector_path="./custom.usearch",
    lexical_path="./custom_lexical",
    extraction={"provider": "anthropic", "model": "claude-sonnet-4-5-20250514"},
    embedding={"provider": "fastembed", "model_name": "BAAI/bge-small-en-v1.5"},
    scoring={"w_semantic": 0.30, "w_lexical": 0.15},
)

from prme import MemoryClient
client = MemoryClient(config=config)
```
