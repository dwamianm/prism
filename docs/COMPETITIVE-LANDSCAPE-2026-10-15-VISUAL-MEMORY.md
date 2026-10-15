# PRME competitive landscape addendum: visual memory, 2026-10-15

**Status:** Point-in-time product assessment, visual memory only

**Assessment date:** 2026-10-15

**PRME release:** v0.14.2 (latest tag), plus later unreleased changes

**PRME source revision:** `688d2c9d982426a8bc71b8beb730285741206d27`

**Source branch:** `spike/visual-memory`

This addendum records how products handled images and video as memory sources
on the assessment date, and it leaves the conclusions of the
[2026-09-17 snapshot](COMPETITIVE-LANDSCAPE-2026-09-17.md) as they were.

The vendor descriptions below were read on 2026-10-15 from the sources listed
at the end. They were not independently verified and are reported as claims. A
missing feature means the sources did not show it, not that the product lacks it.

## Assessment

According to the sources, Memories.ai, Screenpipe, Mem0 and Supermemory offered
some form of image or video memory, and Zep, Letta and LangMem were text only.
RFC-0018 concludes that nobody offers the combination under "The open position"
for video. That rests on these descriptions and is not a verified absence.

## What the sources described

- **Memories.ai** sold a hosted "large visual memory model" with a video data
  lake API, an MCP server and a desktop "computer history" product, and it
  targeted wearables and robots.
- **Screenpipe** recorded the screen and audio all day into a local SQLite
  database behind an MCP server. The sources showed a capture tool with search,
  with no graph, no fact-to-frame provenance and no temporal validity. It was
  source-available with a paid desktop app.
- **Mem0** turned images into text facts. Its video and audio support was in
  beta, and the sources described no frame timestamp index.
- **Supermemory** listed multimodal ingestion. The 2026-09-17 snapshot, the
  spike report's source for it, says no more, so its frame and time handling is
  unknown here.
- **Zep, Letta and LangMem** were text only as far as the sources showed: the
  2026-09-17 snapshot, which the spike report cites for them, describes no image
  or video ingestion, and RFC-0018 treats all three as text only.

## The open position

The position RFC-0018 proposes is the one PRME already holds for text, carried
into vision:

- Local-first: vision models run locally by default, and a hosted vision
  provider is a separate explicit option.
- Every fact traceable to the exact frame and capture time, through the event's
  `event_time` and metadata.
- Values that change over time are separate dated records that supersede one
  another, under the same configuration switches as for text.
- A portable pack: a small thumbnail per observation stays in the pack, and raw
  media stays outside it, referenced by path and hash.
- A deterministic rebuild: the vision model is called once, at admission, and
  `prme rebuild` needs no media and no model call.

The claim is about the combination, not about video, screen or local storage
alone. Memories.ai and Screenpipe handled video or the screen, and Screenpipe
stored locally and recorded audio, which RFC-0018 leaves to its own RFC.

## What PRME had on the assessment date

- Nothing visual in the engine: nothing under `src/prme/` changed, and no
  release includes a visual feature.
- A feasibility spike in `benchmarks/vision/`, reported in the
  [spike report](../benchmarks/results/research/2026-10-15/VISUAL-MEMORY-SPIKE.md).
  Three runs on one machine with local models each scored 22 of 24 on a
  24-question set.
- A draft design, [RFC-0018](RFC-0018-Visual-Memory.md), with three phases:
  files and photos, live screen, then camera and wearables.

The spike measured only PRME's own pipeline and did not run any product above.
Its limits (17 events that fit in one context, a synthetic corpus, one 9B
reader and judge, no audio) mean it supports no comparison with them.

## Sources captured

- Memories.ai: [docs](https://docs.memories.ai/home/overview),
  [Video Datalake API](https://memories.ai/docs/),
  [wearables announcement](https://www.techbuzz.ai/articles/memories-ai-builds-visual-memory-layer-for-ai-wearables)
- Screenpipe: [repository](https://github.com/screenpipe/screenpipe),
  [Hacker News launch](https://news.ycombinator.com/item?id=49024620),
  [MCP write-up](https://daniliants.com/insights/screenpipe-24-7-local-screen-memory-for-ai-agents-via-mcp/)
- Mem0: [multimodal support](https://docs.mem0.ai/platform/features/multimodal-support),
  [open source features](https://docs.mem0.ai/open-source/features/overview),
  [a 2026 review](https://weavai.app/blog/en/2026/05/09/mem0-review-2026-ai-agent-memory-king-26-accuracy/)
- Comparison table: [MultiMem](https://github.com/zhangjianbang-nb/multimem)
- Supermemory, Zep, Letta, LangMem: [2026-09-17 snapshot](COMPETITIVE-LANDSCAPE-2026-09-17.md)

## Revision policy

This file is an immutable dated assessment except for factual corrections. A
future review should create `COMPETITIVE-LANDSCAPE-YYYY-MM-DD.md`, record the new
PRME commit and release, rerun the public metadata capture, and explain changes
relative to this snapshot.
