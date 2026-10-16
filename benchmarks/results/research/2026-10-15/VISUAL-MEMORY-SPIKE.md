# Visual memory spike (2026-10-15)

**Status:** Exploration; the numbers below are from the spike's own pipeline code. Nothing in
`src/prme/` changed for the spike. The code was `benchmarks/vision/` (`corpus.py`, `spike.py`,
`questions.json`); raw outputs are in the ignored `benchmarks/vision/runs/`. The design that follows
from it is RFC-0018 (`docs/RFC-0018-Visual-Memory.md`), whose phase 1 later the same day moved the
pipeline into `src/prme/vision/` and turned `spike.py` into a harness over `MemoryEngine.observe()`.

**Question:** can PRME remember what a video or a photo showed, with the same provenance,
timing and retrieval it has for text, using only local models?

**Setup:** Apple M5 Pro, 48 GB; Ollama 0.40.4; `qwen3.5:9b` (Q4_K_M) as the vision model,
reader and judge; `prme-qwen3.5:35b-a3b-8k` as the extraction model, as the extracted-pack
baselines use; `BAAI/bge-small-en-v1.5` (the pack's default embedding) for the repeat filter.
Base commit `58caddb1`, branch `spike/visual-memory`. No hosted model and no paid API was used.

## Corpus

Everything is generated on the machine (`benchmarks/vision/corpus.py`):

- `meeting.mp4`: a synthetic 3 minute 40 second meeting recording at one frame per second,
  eight scenes: a title slide, a whiteboard (launch date "3 Nov", budget "$12,500", vendor,
  owner), a site photo, the same whiteboard with the date crossed out and "10 Nov" and
  "$14,000" written in, an action-items slide, a bar chart, a risk register and a wrap-up
  slide. Every frame has a few pixels of shake and a little exposure drift, so no two frames
  are byte-identical.
- Five photos: a receipt, a boarding pass, a parking sign on a landscape, a handwritten
  fridge note and one real landscape from the macOS desktop pictures.
- The real 4K, 240 frames per second, 2 minute aerial "Tahoe Day" wallpaper video that
  ships with macOS, sampled at one frame per second.
- 24 questions with gold answers (`benchmarks/vision/questions.json`): single facts, the
  latest value, the earlier value, chart values, an aggregation and a scene description.

## Pipeline

1. `ffmpeg` samples frames at one per second, scaled to 1280 wide (the 4K video took 73 s to
   decode its 120 frames with the hardware decoder).
2. Keyframes: a frame is kept when it differs from the last kept frame by at least 0.55 on
   `frame_change()` (320x180 grayscale, blurred, aligned by phase correlation, z-scored,
   largest block-mean difference over a 13x37 grid), or every 30 s as a heartbeat, capped at
   40 per video.
3. The vision model describes each keyframe (prompt in `spike.py`). Descriptions are cached
   by frame hash, model and prompt version.
4. Repeat filter: a description whose quoted text is the same set as the previous kept
   frame's, and whose embedding cosine similarity is at least 0.93, is dropped.
5. Each remaining description is admitted with `ingest()` (run 1 and 2) or `store()`
   (run 3): role `tool`, `event_time` = capture clock, metadata with the source, frame time,
   frame SHA-256, thumbnail path, vision model and prompt version. A 320 pixel thumbnail
   goes into the pack under `frames/`.
6. Each question runs `retrieve()` at the 4K budget under the production retrieval
   defaults (the evidence gate's isolated configuration), the reader answers from
   `bundle.render()`, and the judge compares the answer with the gold answer.

## Runs

| | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| Description prompt | v1, narrated ("the slide says ...") | v2, facts first (standalone sentences, subject named) | v2 |
| Repeat filter | word overlap at or above 0.85 with the quote gate | embedding cosine at or above 0.93 with the quote gate | same as run 2 |
| Admission | `ingest()`, no retry | `ingest()`, retries like the pack builder | `store()` |
| Keyframes described | 58 (13 meeting, 40 aerial, 5 photos) | 58 | 58 (cached) |
| Kept after repeat filter | 53 | 17 (11 meeting, 1 aerial, 5 photos) | 17 |
| Describe seconds per keyframe, mean (median) | 5.24 (4.88) | 5.25 (5.30) | cached |
| Prompt / output tokens per description, mean | 1,096 / 103 | 1,158 / 122 | |
| Admission seconds per observation, mean | 27.0 (extraction) | 20.2 (extraction) | 0.08 |
| Extraction errors | 32 of 53 (1 `IncompleteOutputException`, 31 pending behind it) | 0 | none run |
| Nodes | 52 fact, 56 entity, 3 decision | 20 fact, 67 entity, 3 decision, 1 superseded | 17 note |
| Pack size / thumbnails | 14.1 MB / 504 KB | 13.4 MB / 132 KB | 7.8 MB / 132 KB |
| Context tokens per question, mean | 3,643 | 2,042 | 2,005 |
| Retrieval hit (needle in context) | 24 / 24 | 24 / 24 | 24 / 24 |
| Correct (judge) | 22 / 24 | 22 / 24 | 22 / 24 |
| Misses | both latest-value questions (reader answered the crossed-out value) | next-sync answer left out "10:00"; aerial scene answered "I don't know" | same as run 2 |

Per-run detail: `visual-memory-spike-{1,2,3-store}.REPORT.md` and `.report.json` beside this file.

## What the spike settled

1. **The pipeline works end to end on local models.** Every question's evidence reached
   the 4K context in all three runs, and 22 of 24 answers were judged correct. Receipts,
   boarding passes, signs, handwriting, a bar chart and a whiteboard were all read
   correctly by the 9B vision model on this corpus.
2. **The description prompt matters more than extraction.** Under the narrated prompt
   (run 1), extraction recorded the narration as the claim ("The first bullet point says
   ...", "Video frame at 00:03 of Tahoe Day.mov.") and produced no fact about the
   whiteboard values at all; the reader then answered the crossed-out launch date. Under
   the facts-first prompt (run 2), both latest-value questions passed, and the extracted
   facts read "The Falcon launch is scheduled for November 10 ...", "The budget for the
   Falcon launch is $14,000". Run 3 then showed that `store()` over the same descriptions
   scores the same 22 of 24: on a 17 event pack the raw descriptions carry the answers.
   Extraction's value here is structure (a superseded launch-date record, entity nodes for
   every name, flight and gate), not answer accuracy, and it costs about 20 s per
   observation against 0.08 s.
3. **Keyframe selection needs two levels.** Pixel change alone cannot tell a slide whose
   text changed (0.70) from camera shake on text (up to 0.37) with a comfortable margin,
   and a slow aerial pan crosses the threshold every two or three seconds. The repeat
   filter on descriptions fixes both: it dropped 41 of 58 described frames in run 2 (39 of
   the 40 aerial frames and the two whiteboard heartbeats) and never dropped a frame
   whose quoted text changed. Word overlap was the wrong similarity: the same whiteboard
   described twice overlapped only 0.61, while its embedding similarity was 0.985; the
   same lake in new words scored 0.94 to 0.975, and two different slides 0.87 to 0.96 but
   with different quoted text, which the quote gate rejects.
4. **One extraction failure blocks the session.** In run 1 one description's extraction
   output was cut off (`IncompleteOutputException`, the 36B model at an 8K context) and the
   next 31 admissions raised "Extraction is pending behind earlier work or an active
   lease". The events were saved and the photo questions still passed on raw text, but no
   later frame got facts. The extracted-pack builder's retry loop
   (`engine.retry_extraction` then `process_extractions`) is the right pattern and run 2
   uses it; a product `observe()` must too.
5. **Cost.** About 5.3 s per keyframe for description and, with extraction, about 20 s per
   kept observation on this machine. One hour of meeting video at the run 2 rates (13
   keyframes and 11 kept observations per 3.7 minutes) is roughly 210 descriptions (19
   minutes) and 180 extractions (60 minutes), or 15 s of admission without extraction. A
   static screen or a slow pan costs the heartbeat rate: 120 descriptions per hour at one
   per 30 s, almost all dropped by the repeat filter.
6. **Two defects surfaced that are not about vision.** The extracted fact "The Falcon
   launch is scheduled for November 10" was given `valid_from` 2025-11-10, and in run 1
   "Demo for Acme on 28 October" showed as `[2025-10-28]` in the reader context: a
   month-day without a year resolves to the past occurrence even when the event time is
   2026-10-14 and the slide is about the future. The reader also dropped "10:00" from a
   one-sentence answer and the judge scored that as wrong; the time was in the context.

## Phase 1 product path check (2026-10-15, later the same day)

After RFC-0018 phase 1 moved the pipeline into `src/prme/vision/`, the harness was run again
over the same corpus and questions through `MemoryEngine.observe()` with `store()` admission
(`visual-memory-phase1-store.REPORT.md` and `.report.json` beside this file; Ollama 0.40.4,
the same `qwen3.5:9b` reader and judge, the same prompt version 2, on the uncommitted phase 1
tree over base commit `606e5db3`). It reproduces run 3: 24 of 24 questions had their evidence
in context and 22 of 24 were judged correct, with the same two misses (the shortened next-sync
answer without "10:00", and "I don't know" for the aerial scene). Two differences are by design.
The product has no per-video keyframe cap, so all 50 aerial keyframes were described (66 in
total, 8 of them fresh calls at 6.2 s mean, the rest from the spike's cache) and 49 were dropped
as repeats; and the repeat filter now normalizes quoted text (case, dashes, surrounding
punctuation), so the whiteboard quoted as "3-Nov" and "3 Nov" counted as the same set and one
more heartbeat frame was dropped: 16 observations instead of 17. The 16 thumbnails (123 KB)
are rows of the pack's `blobs` table rather than files under `frames/`.

## Limits of this spike

- Seventeen events fit in one context. Retrieval was never stressed, so nothing here
  says how visual memory ranks against thousands of observations.
- The corpus is synthetic except for one landscape photo and one aerial video, and its
  text is clean. Real phone video, handheld whiteboards and low light are untested; the
  one probe outside the corpus, a 1280x720 slide, saw the 9B model drop the "42" from
  "Sprint 42 planning" while the 4B model kept it.
- One reader and one judge, both the 9B model, temperature 0. The two misses in runs 2
  and 3 are reader behavior (a shortened answer; "aerial" absent from a description that
  said "lakeside scene"), not retrieval.
- Keyframe thresholds were set on this corpus and are not validated elsewhere.
- No audio. A meeting recording without transcription answers only what was shown.

## Follow-ups

- RFC-0018 phase 1: `observe()` and `prme observe` for files and photos, behind
  `VisionConfig.enabled = False`, with the retry loop and the two-level keyframe policy;
  thumbnails in both pack layouts. Done later the same day; see the section above.
- A benchmark with real recordings and annotated questions before any default changes;
  run under the DeepSeek track rules with its own baseline.
- An issue for month-day resolution to the past year (finding 6), with the run 2 pack as
  evidence.
- Normalize quoted text (case, hyphens, punctuation) in the repeat filter: the crossed-out
  "3-Nov" versus "3 Nov" kept three extra whiteboard frames in run 2. Done in phase 1
  (`prme.vision.describe.normalize_quote`).
- Decide whether extraction runs on observations by default or only on request, given
  finding 2 and the 250x cost difference.

## Sources consulted for the market position

- Memories.ai: [docs](https://docs.memories.ai/home/overview), [Video Datalake API](https://memories.ai/docs/),
  [wearables announcement](https://www.techbuzz.ai/articles/memories-ai-builds-visual-memory-layer-for-ai-wearables)
- Mem0: [multimodal support](https://docs.mem0.ai/platform/features/multimodal-support),
  [open source features](https://docs.mem0.ai/open-source/features/overview),
  [a 2026 review](https://weavai.app/blog/en/2026/05/09/mem0-review-2026-ai-agent-memory-king-26-accuracy/)
- Screenpipe: [repository](https://github.com/screenpipe/screenpipe), [Hacker News launch](https://news.ycombinator.com/item?id=49024620),
  [MCP write-up](https://daniliants.com/insights/screenpipe-24-7-local-screen-memory-for-ai-agents-via-mcp/)
- [MultiMem comparison table](https://github.com/zhangjianbang-nb/multimem)
- PRME's own [competitive landscape snapshot (2026-09-17)](../../../../docs/COMPETITIVE-LANDSCAPE-2026-09-17.md) for Supermemory, Zep, Letta and LangMem.
