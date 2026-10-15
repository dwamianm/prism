# RFC-0018: Visual Memory from Images and Video

**Status:** Draft; feasibility spike measured 2026-10-15 (`benchmarks/vision/`)
**Date:** 2026-10-15
**Depends on:** RFC-0001, RFC-0002, RFC-0005, RFC-0014, RFC-0015, RFC-0016

## Problem and evidence

PRME remembers text. An event's `content` is a string, every node's `content`
is a string, and the embedding provider encodes text. A person's day is not only
text: meetings happen on whiteboards and slides, errands leave receipts, signs
and tickets, and a growing share of agent work is watching a screen or a camera.
None of that reaches the memory today.

The market on 2026-10-15 (sources in the spike report):

- Memories.ai sells a hosted "large visual memory model" with a video data lake
  API, an MCP server and a desktop "computer history" product, and targets
  wearables and robots.
- Screenpipe records the screen and audio all day into a local SQLite database
  behind an MCP server. It is a capture tool with search: no graph, no
  provenance from a fact to a frame, no temporal validity, source-available
  with a paid desktop app.
- Mem0 turns images into text facts; its video and audio support is beta. It
  keeps no frame timestamp index. Supermemory lists multimodal ingestion. Zep,
  Letta and LangMem are text only.

The open position is the one PRME already holds for text, carried into vision:
local-first, every fact traceable to the exact frame and capture time, values
that change over time carried as separate dated records that supersede one
another, a portable pack, and a deterministic rebuild. Nobody offers that for
video. This RFC adds images and video as memory sources without changing what
retrieval, packing, receipts or the organizer do.

## Design in one paragraph

A frame becomes memory the way a message does. A capture layer turns a video,
a photo, a screen or a camera into keyframes. A local vision model writes a
plain-text description of each keyframe that quotes visible text verbatim. That
description is admitted as an event through `ingest()`, with the capture clock
as `event_time`, role `tool`, and metadata that names the source, the frame
time, the frame's SHA-256, the vision model and the prompt version. Extraction,
the graph, supersedence, the organizer, retrieval, packing and receipts then
work unchanged, because the observation is text with provenance. A small
thumbnail of each keyframe stays in the pack so a fact can show the frame it
came from. Raw media stays outside the pack, referenced by path and hash.

## Spike (2026-10-15)

`benchmarks/vision/spike.py` runs the whole pipeline on this machine against
the local Ollama server: a synthetic meeting recording whose whiteboard values
change part way through, five photos (a receipt, a boarding pass, a parking
sign, a fridge note and a real landscape), and the real 4K aerial video that
ships with macOS. Twenty-four questions with gold answers test single facts,
the latest value, the earlier value, chart reading, aggregation and scene
description. The full account, with every number, is
`benchmarks/results/research/2026-10-15/VISUAL-MEMORY-SPIKE.md`.

| | Run 1 (narrated prompt, `ingest()`) | Run 2 (facts-first prompt, `ingest()`) | Run 3 (facts-first, `store()`) |
|---|---|---|---|
| Keyframes described / kept | 58 / 53 | 58 / 17 | 58 / 17 |
| Describe seconds per keyframe, mean | 5.24 | 5.25 | cached |
| Admission seconds per observation, mean | 27.0 | 20.2 | 0.08 |
| Extraction errors | 32 of 53 | 0 | none run |
| Evidence in context / correct | 24 / 22 of 24 | 24 / 22 of 24 | 24 / 22 of 24 |
| Misses | both latest-value questions | shortened answer; aerial scene | same as run 2 |

### What the spike settled

- The pipeline works end to end on local models: receipts, a boarding pass, a
  sign, handwriting, a bar chart and a whiteboard were read correctly, and every
  question's evidence reached the 4K context.
- The description prompt decides what extraction can do. A narrated
  description ("the slide says ...") became narration facts and no whiteboard
  values; a facts-first description (standalone sentences with their subject
  named) produced the right facts, a superseded launch-date record, and fixed
  both latest-value questions.
- On a 17 event pack, `store()` over the same descriptions scored the same as
  `ingest()`, at 0.08 s instead of 20 s per observation. Extraction's value is
  structure, and whether observations are extracted by default is an open
  decision below.
- Pixel change alone cannot select keyframes on text; a second filter on the
  descriptions is needed and works (41 of 58 frames dropped, none wrongly).
- One failed extraction blocks every later one in the session until it is
  retried; `observe()` must carry the pack builder's retry loop.
- Two defects outside vision: a month-day without a year resolved to the past
  year, and the reader shortened one answer. Both are tracked in the report.

## Observation events

An observation is an ordinary event. Nothing in the event store, the graph or
the indexes changes shape. The contract is in the fields:

| Field | Value |
|---|---|
| `content` | The vision model's description, nothing else: a scene line, then one standalone sentence per fact, visible text quoted verbatim in double quotes. |
| `role` | `tool`. The camera and the vision model are instruments; `infer_source_type` maps the role to `TOOL_OUTPUT`, so an observed fact is never recorded as something the owner stated. |
| `event_time` | The capture clock: the video's start plus the frame offset, or the photo's capture time. Retrieval's recency and time windows read it unchanged. |
| `session_id` | `video:<file>` for frames of one recording, `photo:<file>` for a photo, a capture session id for a live source. |
| `metadata.modality` | `video_frame`, `photo`, later `screen_frame` and `camera_frame`. |
| `metadata.source_file`, `metadata.frame_time_s` | Where the frame came from and when inside the source. |
| `metadata.frame_sha256` | Content hash of the keyframe image. The thumbnail and any external raw frame are addressed by it. |
| `metadata.thumbnail` | Pack-relative path of the evidence thumbnail (`frames/<sha256>.jpg`). |
| `metadata.vision_model`, `metadata.describe_prompt_version` | Which model and prompt produced the description. A different model or prompt produces new observations; existing ones are never rewritten. |

Extraction treats the description like any other turn: it derives Fact,
Entity and relationship records whose `evidence_refs` point at the observation
event, so `NodeProvenance` leads from a fact to the frame, its time and its
thumbnail.

## Keyframe policy

Continuous capture is mostly repetition. The spike uses three filters in
order, each cheaper than the next stage it protects:

1. **Pixel change.** Each frame is reduced to a 320x180 grayscale signature,
   blurred slightly, aligned to the last kept frame by phase correlation to
   remove camera shake, contrast-normalized, and compared block by block. A
   frame is kept when the largest block difference reaches a threshold (0.55 in
   the spike). On the spike corpus a slide whose text changed on the same
   layout scored about 0.7, any other scene change above 2.3, shake and
   exposure noise at most 0.37, and consecutive seconds of a slow aerial pan
   about 0.5.
2. **Heartbeat.** A frame is kept every `max_gap` seconds (30 in the spike)
   regardless, so a long static scene is still sampled and a text-only change
   near the noise floor is caught within one gap.
3. **Repeat drop.** After description, a frame whose quoted text is the same
   set as the previous kept frame's and whose description embedding (the
   pack's own text embedding) has cosine similarity at or above 0.93 to it is
   dropped as a repeat. Any difference in quoted text keeps the frame, so a
   whiteboard with one changed value is never dropped. Word overlap was tried
   first and failed: the same whiteboard described twice overlapped only 0.61.

The pixel filter is the weak point. A handheld camera on text can produce
noise close to a real text change, and a slow pan over scenery produces a
keyframe every few seconds that the repeat filter later drops at the cost of
one vision call each. A middle stage, cheap OCR or an image-embedding
similarity check between the pixel filter and the vision model, is the first
improvement to try. All thresholds are configuration, named in `VisionConfig`
below, and every kept and dropped frame is logged with its score.

## Description prompt

The prompt is part of the contract and is versioned
(`describe_prompt_version`). Version 2, the one that worked, asks for two
parts: one or two sentences on what kind of image it is and what it shows, then
the facts the image states as standalone sentences that name their subject,
with visible text quoted verbatim, crossed-out values reported as crossed out
and replaced, and every chart value read as its own sentence. A hint names the
source kind (a video frame, a photo). The source file and frame time are not
written into the text; they live in metadata and `event_time`, because
version 1's "Video frame at 00:03 of ..." prefix was extracted as a fact.

## Evidence thumbnails and raw media

The pack keeps one thumbnail per observation, 320 pixels wide, JPEG, about
20 KB, named by the frame's SHA-256. In a directory pack it lives under
`frames/`; in a single-file pack (RFC-0014 amendment) it is a row in a `blobs`
table keyed by the same hash. Raw frames and videos stay outside the pack,
referenced by path and hash in metadata. The pack therefore stays small and
copyable, `prme rebuild` needs no media (descriptions are the durable events),
and provenance can still show the frame. A TTL on thumbnails is an organizer
archival policy, not a change to the event log.

## Determinism and rebuild

The vision model is called once, at admission, like extraction. Its output is
recorded as the event's content and never recomputed by a rebuild or a replay.
Given the same event log, `prme rebuild` regenerates the same indexes with no
model call, and retrieval receipts replay as they do for text. Changing the
model, its weights, or the prompt is a new `describe_prompt_version` or
`vision_model` and produces new observations alongside the old ones; the
append-only rule of RFC-0002 applies.

## Temporal semantics

Values that change across frames are separate dated observations, exactly as
two messages that state different values are. The launch date read as "3 Nov"
at 00:15 and "10 Nov" at 01:15 are two events with their own `event_time`;
retrieval orders them by recency, and the reader context marks the newer one.
Store-time supersedence and claim merge apply with the same configuration
switches as for text. A crossed-out value is described as crossed out and
still quoted, so the earlier value stays answerable.

## Privacy and consent

Continuous capture sees passwords, faces and other people's conversations.
The capture layer filters before anything is admitted:

- Screen capture excludes named applications and windows, and skips frames
  whose accessibility tree shows a password field. The default list is
  conservative and the user extends it.
- Camera capture is opt-in per session; face blurring before description is a
  capture option.
- Vision models run locally by default. A hosted vision provider is a separate
  explicit option, as the extraction provider is today.
- Thumbnails inherit the pack's encryption. Their retention is a policy with a
  default TTL, and deleting a thumbnail never touches the event log.
- Recording other people has legal limits that vary by place. The
  documentation says so; the engine does not decide it.

## Public surface

```python
client.observe(path_or_bytes, *, user_id, captured_at=None, source=None,
               session_id=None, metadata=None, scope=Scope.PERSONAL) -> list[str]
```

`observe()` accepts an image or a video. It runs the keyframe policy, the
vision provider and admission, and returns the observation event ids. The CLI
mirrors it: `prme observe <file>... [--user-id] [--captured-at]`. Whether
admission is `ingest()` or `store()` is `VisionConfig.extract`; the spike found
no accuracy difference on a small pack and a 250x cost difference, so the
default is a decision for phase 1, taken with a larger pack. When extraction
runs, a failed extraction is retried as the extracted-pack builder retries
(`retry_extraction`, then `process_extractions`), because one failure leaves
every later observation in the session pending.

```python
class VisionConfig(_ProjectSettings):
    enabled: bool = False
    provider: str = "ollama"
    model: str = "qwen3.5:9b"
    base_url: str | None = None
    describe_prompt_version: int = 2
    extract: bool = True  # ingest() with extraction, or store() when False
    fps: float = 1.0
    change_threshold: float = 0.55
    max_gap_seconds: float = 30.0
    repeat_similarity: float = 0.93
    thumbnail_width: int = 320
    keep_thumbnails: bool = True
```

Everything is off by default. A pack that never used `observe()` is
byte-for-byte what it is today. This follows the epic #77 rule that new
behavior goes behind an explicit option first.

## Phases

1. **Files and photos.** `observe()` and `prme observe` for saved video and
   photos; the spike pipeline moved into `src/prme/vision/` with tests over the
   synthetic corpus; thumbnails in both pack layouts. Benchmark: the spike
   corpus grown into a recorded-meeting set with annotated questions, run
   under the DeepSeek track's rules with a visual baseline of its own.
2. **Live screen.** An external capture process (event-driven: app switch,
   click, typing pause, like Screenpipe's) feeds `observe()` through the HTTP
   API, with the application and password-field filters above. If it is used
   on coding sessions, it touches the shelved coding-memory decision in
   `CLAUDE.md`; that decision stands until a new study says otherwise.
3. **Camera and wearables.** A phone or glasses client streams keyframes to
   the HTTP API. This is the Memories.ai comparison and needs the
   image-embedding stage and the organizer summarization job below.

Later, independent of phase: a second vector index with an image-text model
(CLIP or SigLIP) for "have I seen this before" and image-to-frame queries,
and an organizer job that collapses a run of observations from one session
into a Summary node with the frames as evidence.

## Open questions

- Ownership of observed facts. A receipt in the owner's camera roll is about
  the owner, but the description says "the receipt shows", not "I paid".
  Whether extraction should bind such facts to the owner, and under which
  rule, needs the same kind of evidence as `enable_speaker_references` had.
- Vision model errors. The spike's 9B model dropped a number from a slide
  title in one probe. Reading accuracy on text, charts and handwriting across
  the local models is unmeasured beyond the spike corpus.
- Extraction coverage of documents. Extraction is tuned for conversation
  turns. Over the facts-first descriptions it produced entities for every
  name, flight and gate but few facts for the receipt and the boarding pass;
  the raw descriptions answered those questions. Whether to extend the
  extraction prompt for observations or to rely on the description text is
  part of the `extract` decision above.
- Dates without a year. An extracted "scheduled for November 10" was given a
  validity start in 2025 although the frame's event time was 2026-10-14. This
  is a temporal-resolution defect that text shares and needs its own issue.
- Cost of continuous capture at scale. The spike measures seconds per
  keyframe; an eight-hour screen day at one keyframe per 20 seconds is 1,440
  vision calls. Whether the keyframe policy cuts that enough on real sessions
  is unknown until phase 2 captures some.
- Audio. Meetings are mostly speech. Transcription is a separate source with
  the same event shape and belongs in its own RFC, but a meeting recording
  without it answers far fewer questions than one with it.
