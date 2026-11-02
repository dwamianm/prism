# Live visual memory: what the camera demo had to build outside PRME

Proposed 2026-11-02. This is a plan, not current functionality. It follows
[RFC-0018](../../../docs/RFC-0018-Visual-Memory.md), whose phases 2 and 3 (live
screen, camera and wearables) are not built yet.

## Why this plan exists

The Nenavi camera demo (`apps/vision-demo` in `dwamianm/prism-monorepo`)
feeds a browser camera into PRME 0.16.1 through `observe()` and asks
questions through `retrieve()`. To make it usable, the demo had to add logic
that belongs in the engine. Each workaround below is a gap in PRME. The goal
is for an application to send frames and ask questions, with PRME doing the
work in between.

The evidence comes from one owner session with a real webcam on 2026-11-02
(holding up one finger more at a time, then a fist, then lowering the hand)
and from synthetic scenes in the demo's tests. It is not a benchmark.

## Summary

| # | Gap | What the demo does now | Proposed engine change | Issue |
| --- | --- | --- | --- | --- |
| 1 | No live stream input | Scores every camera sample itself and decides when to call `observe()` | A streaming observe session with keyframe and repeat state per session | [#390](https://github.com/dwamianm/prism/issues/390) |
| 2 | Keyframe threshold misses small changes on a fixed camera | Its own sensitivity setting, default 0.30 instead of 0.55 | Per-session noise calibration, and a change score that counts how much changed | [#389](https://github.com/dwamianm/prism/issues/389) |
| 3 | Questions about "now" do not get the newest frames | Adds the 6 newest frames to the answer | Current-state intent that adds the session's latest observations | [#387](https://github.com/dwamianm/prism/issues/387) |
| 4 | Rendered context loses order between frames | Builds its own prompt: oldest first, times to the second, newest marked | Seconds in rendered times, and chronological order for current-state questions | [#386](https://github.com/dwamianm/prism/issues/386) |
| 5 | The description prompt never asks for counts | Nothing; counts are often missing | Description prompt version 3 that states visible counts | [#388](https://github.com/dwamianm/prism/issues/388) |

Suggested order: 4, 3, 5, 2, 1. Gap 4 is small and helps every caller. Gap 1
is the largest and is RFC-0018 phase 3.

## 1. No live stream input

Issue: [#390](https://github.com/dwamianm/prism/issues/390).

**Evidence.** `observe()` takes one photo or one video file. The keyframe
policy and the description repeat filter keep their state only inside one
call, so separate camera frames are never compared with each other. A live
camera has to be turned into a series of single photos by the caller.

**What the demo does.** Its page posts a small sample twice a second to its
own `/api/watch` endpoint. The endpoint scores each sample against the last
kept frame with `prme.vision.keyframes.frame_change` and a per-session gate:
keep when the change reaches the threshold and the picture has held still for
half a second, or after 6 seconds of continuous change at most once every 10
seconds. Kept frames are then sent to `observe()` as photos.

Earlier versions of that gate show how easy it is to get wrong. A 10-second
minimum gap kept frames exactly 10.0 seconds apart and lost every finger
count in between. A 1-second hold-still rule was too long for the owner.

**Proposal.** An observe session in the engine and the HTTP API, for example
`engine.observe_frame(frame, *, session_id, captured_at)`, that keeps the
keyframe signature, the settle state, the heartbeat clock and the last
description key for each session. It admits a frame only when the session
policy keeps it, and applies the repeat filter across frames. The settle
time, heartbeat and gap become `VisionConfig` settings.

**Measure.** The finger sequence and the desk scenes from the demo's tests
(`server/tests/test_watch.py`), replayed at 2 samples a second: every held
count kept once, nothing kept for noise, exposure steps or shake.

## 2. Keyframe threshold misses small changes on a fixed camera

Issue: [#389](https://github.com/dwamianm/prism/issues/389).

**Evidence.** `frame_change` returns the strongest change in any one block,
so it measures how strong a local change is, not how large. On a synthetic
hand, one more finger scored 0.35 to 0.47, and 1 to 4 fingers compared with
1 finger never passed 0.51. At the default `change_threshold` of 0.55, no
finger count was kept. Sensor noise, a 12% exposure step and 3 pixels of
shake scored 0.21 or less on a fixed synthetic camera. The RFC-0018 spike
measured noise up to 0.37 on hand-held recordings, which is why 0.55 was
chosen.

**What the demo does.** A sensitivity setting: High 0.30 (its default),
Medium 0.45, Low 0.55.

**Proposal.** Calibrate the threshold per session from the noise floor
measured between still frames (for example, a multiple of the running
median of still-frame scores), or offer source profiles for a fixed camera
and a hand-held one. Consider adding the number of changed blocks to the
score, so a wide change counts more than one noisy block.

**Measure.** The same scenes, plus real webcam recordings in low and normal
light, to find the false-keep rate at each threshold.

## 3. Questions about "now" do not get the newest frames

Issue: [#387](https://github.com/dwamianm/prism/issues/387).

**Evidence.** Asked "How many fingers is the man holding up?", `retrieve()`
returned older frames whose text said "four fingers" and none of the four
newest frames (five fingers, all fingers spread, a fist, hands down). The
search ranks by how well the text matches, and the newest frames used
different words. The context formatter also detected the question as an
aggregation task ("AGGREGATION TASK: The question asks for a count") rather
than a question about the current state.

Session context expansion (`session_context.py`) adds turns next to a
retrieved result, not the newest turns of the session, so it does not help.

The engine's structured path for current state is extraction, then
assertions with supersedence, then `get_assertion_state`. With
`vision.extract` on and `qwen3.5:9b` as the local extraction model, a
whiteboard photo produced no extra nodes in one test, so that path did not
apply. RFC-0018 already lists extraction coverage of observations as an open
question.

**What the demo does.** Adds the session's 6 newest observations to the
answer, labeled as not from search.

**Proposal.** Query analysis recognizes present-tense and "last" state
questions ("is holding", "now", "currently", "last", "latest") and treats
them as current-state questions. For those, candidate generation adds the
newest observations of the most recent observation session, and the context
type is current state, not aggregation.

**Measure.** The owner's question set over a recorded session: the newest
frame that answers the question is in the packed context, and "how many ...
is holding" is not classified as aggregation.

## 4. Rendered context loses order between frames

Issue: [#386](https://github.com/dwamianm/prism/issues/386).

**Evidence.** `bundle.rendered_context` shows times to the minute
(`[2026-11-02 20:14]`), and `format_for_llm` shows only the day
(`(2026-11-02, today)`). Camera frames 1 to 3 seconds apart all look the
same, so the reader cannot tell which is newest. In the default context the
records are also in relevance order. With the demo's first prompt, which
listed memories by rank, the answer model called "four fingers" at 2:14:18
PM the most recent although "five fingers" at 2:14:21 PM came after it.

**What the demo does.** Builds its own answer prompt: memories oldest first,
local times to the second, the newest marked, rank numbers kept for
citations.

**Proposal.** Render seconds when two records in the context fall within the
same minute, or always for observations. For current-state questions, sort
chronologically and mark the newest record, as the knowledge-update context
type already does for days.

**Measure.** A context built from frames seconds apart keeps their order and
marks the newest. The demo can then drop its own prompt and use the bundle.

## 5. The description prompt never asks for counts

Issue: [#388](https://github.com/dwamianm/prism/issues/388).

**Evidence.** A frame with both hands raised, all fingers spread, was
described as "hands raised, palms facing forward", with no count. Prompt
version 2 asks for three to five facts when an image has no text, and never
asks for counts. Counting depends on the model as well: `qwen3.5:9b` called
some frames "three fingers" or "four fingers" when the count was probably one
less.

**Proposal.** Description prompt version 3: when people, hands, raised
fingers or repeated objects are clearly visible, state the count as a
number, and say when a count is uncertain. A new version produces new
observations and leaves old ones unchanged, as RFC-0018 requires.

**Measure.** A small labeled set of gesture and object-count frames, run
against `qwen3.5:9b` and the larger local models (`gemma4:26b`,
`qwen3.5:35b-a3b`) for accuracy and seconds per frame.

## Out of scope

Writing answers stays with the application. In the demo, `qwen3.5:9b` did
not read "all fingers spread" as five or a fist as zero, and
`qwen3.5:35b-a3b` did, from the same memories. That choice belongs to the
caller, not the engine.
