# Description prompt version 3: counts (2026-11-02)

**Status:** Evidence for dwamianm/prism#388. Description prompt version 3 became the default for new
observations on 2026-11-02 by the owner's decision to turn new features on by default, not on this
evaluation alone. It is a small local evaluation, not a benchmark.

**Question:** does version 3 make the vision model state visible counts (people, hands, raised
fingers, repeated objects) as numbers, and does it cost anything on the whiteboard and receipt
probes?

**Setup:** Apple M5 Pro, 48 GB; Ollama 0.40.13; vision models `qwen3.5:9b` (the default),
`gemma4:26b` and `qwen3.5:35b-a3b`, all Q4_K_M, one at a time, each unloaded after its frames.
Temperature 0, seed 0, thinking off: the request `OllamaVisionProvider` sends. Source kind `photo`.
The code was the branch tree for #388 over base commit `4475e077`. No hosted model and no paid API
was used.

## Labeled set

Twelve frames with known counts (`benchmarks/vision/counts.py`):

- Six synthetic frames drawn with Pillow (`count_frames`): 3 red balls, 5 blue blocks, 7 yellow
  stars, 4 green triangles with 2 orange balls, 9 gold coins, 3 standing figures. They are drawn
  the same way on every run and are committed as code only.
- Six gesture frames from the owner's camera session of 2026-11-02 in the Nenavi camera demo: 1, 2
  (a peace sign), 3, 4 and 5 raised fingers on one hand, and both hands raised with all fingers
  spread (10 fingers, 2 hands; the frame from the issue). They are the 320 by 180 pixel thumbnails
  the demo's pack keeps, read from a copy of the pack through `MemoryEngine.get_blob`; the demo's
  model described the full camera frames. Each was labeled by eye; frames whose count could not be
  read with confidence were left out. The frames show the owner, so they are not committed and were
  deleted after the run; the labels file lived outside the repository (`--extra-labels`).

The run also described three probe frames of the spike corpus (`corpus.py`): the whiteboard before
and after the launch date is crossed out, and the receipt.

A count is read from the description by the script, not by a model: a number followed within four
words by the counted noun. A frame is correct when every labeled count it has is stated and right.
The script's reading was then checked by hand against every description; the review moved seven
frames to correct and one count from missing to wrong, listed under "Review" below.

## Count accuracy and seconds per frame

Frames correct after review, out of 12 (6 gesture, 6 synthetic). "Wrong" counts the labeled counts
a description stated with the wrong number; "missing" the labeled counts it did not state as a
number. Seconds per frame are the server's time per description without model loading, over the 15
frames of the run (the 12 labeled frames and the 3 probe frames).

| Model | Version | Frames correct | Gesture | Synthetic | Wrong | Missing | Seconds per frame, mean (median) | Output tokens, mean |
|---|---|---|---|---|---|---|---|---|
| `qwen3.5:9b` | 2 | 8 / 12 | 2 / 6 | 6 / 6 | 0 | 4 | 4.48 (4.14) | 142 |
| `qwen3.5:9b` | 3 | 12 / 12 | 6 / 6 | 6 / 6 | 0 | 0 | 4.03 (3.95) | 120 |
| `gemma4:26b` | 2 | 7 / 12 | 1 / 6 | 6 / 6 | 1 | 4 | 2.09 (2.13) | 103 |
| `gemma4:26b` | 3 | 9 / 12 | 4 / 6 | 5 / 6 | 3 | 0 | 2.03 (2.12) | 92 |
| `qwen3.5:35b-a3b` | 2 | 7 / 12 | 2 / 6 | 5 / 6 | 1 | 4 | 2.32 (2.33) | 109 |
| `qwen3.5:35b-a3b` | 3 | 11 / 12 | 6 / 6 | 5 / 6 | 1 | 0 | 2.25 (2.30) | 99 |

Before review, the script scored 7, 12, 6, 7, 6 and 9 frames correct in the same row order.
Version 3's prompt is 96 tokens longer (752 to 848 tokens per call with the image for the Qwen
models, 492 to 588 for Gemma); its descriptions were shorter, so it was not slower.

What changed, by model:

- `qwen3.5:9b`: under version 2 the gesture frames read "making a peace sign", "raising his right
  hand with all fingers spread", "both hands raised in front of him, palms facing forward", and the
  3 finger frame was called a peace sign. Under version 3 every finger count was stated and right,
  including "Each hand shows five fingers fully extended, ten fingers total when counting both hands
  together." The overcount the issue reports (four for three, five for four) did not occur on these
  thumbnails under either version.
- `gemma4:26b`: version 3 states every count, but two finger counts are wrong (4 for 3, as under
  version 2, and 4 for 5) and the coins are 10 instead of 9, which version 2 had right. Version 3
  turned missing counts into wrong ones as often as into right ones for this model.
- `qwen3.5:35b-a3b`: every finger count right under version 3; the coins are 10 instead of 9 under
  both versions.

## Whiteboard and receipt probes

**Questions** (`benchmarks/vision/spike.py` over `meeting.mp4` and the photos, `store()` admission,
`qwen3.5:9b` as vision model, reader and judge, 4K context; `description-v2-probe.REPORT.md` and
`description-v3-probe.REPORT.md` beside this file):

| Version | Whiteboard questions (m02 to m06) | Receipt questions (p01, p02) | All 23 questions | Keyframes described | Admitted |
|---|---|---|---|---|---|
| 2 | 5 / 5 | 2 / 2 | 23 / 23 | 18 | 16 |
| 3 | 5 / 5 | 2 / 2 | 22 / 23 | 18 | 15 |

The version 3 miss is m13, the next sync: the reader answered "Tuesday, 21 October 2026" without
"10:00", though both versions described the slide with "Tuesday 21 October, 10:00" and the time was
in the context both times. The spike's runs 2 and 3 had the same reader miss under version 2. The
whiteboard heartbeat frame at 46 s was dropped as a repeat under version 3 (similarity 1.0 to the
frame at 15 s) and admitted under version 2; the whiteboard values stayed answerable.

**Values quoted in the description** of the three probe frames (19 values: the title, dates, budget,
crossed-out mark, vendor and owner on the whiteboards; store, city, date, time, item, total and card
on the receipt), all three models:

| Model | Version 2 | Version 3 | Values lost or gained |
|---|---|---|---|
| `qwen3.5:9b` | 19 / 19 | 18 / 19 | Version 3 left the title "Falcon launch" out of the crossed-out whiteboard |
| `gemma4:26b` | 18 / 19 | 19 / 19 | Version 2 said "changed", not crossed out; version 3 said crossed out, but wrote the budget as "₹14,000" |
| `qwen3.5:35b-a3b` | 18 / 19 | 18 / 19 | Neither said the date was crossed out |

## Budget

126 vision calls of the 150 allowed: 90 for the count evaluation (15 frames, 2 versions, 3 models)
and 36 for the two question probes (18 keyframes per version), plus 92 text calls to `qwen3.5:9b` as
reader and judge. About 11 minutes of model time in all: 4.6 minutes for the count evaluation and
6.5 minutes for the probes, corpus and packs included.

## What this does not show

- It is small: 12 labeled frames, one description each per model and version, at temperature 0. One
  frame moves a model's score by about 8 points, and there are no repeated runs or intervals.
- The gesture frames are one person, one room and one light, as thumbnails; the issue's overcount
  came from full frames. A larger set of real frames, other people and other hands is untested.
- The synthetic frames are flat shapes on plain backgrounds. Version 2 already counts them, because
  it asks for three to five facts about an image without text.
- Version 3 adds count sentences that nobody asked about. `qwen3.5:9b` said "two people" in 2 of
  the 6 gesture frames, where one person and a figure on a wall poster are visible, once marking the
  count uncertain; `qwen3.5:35b-a3b` added "There is 1 television mounted on the wall." With
  extraction, such sentences become facts; their effect on retrieval in a large pack is unmeasured.
- Only one of the 36 version 3 descriptions of labeled frames marked a count uncertain, so whether
  the uncertainty marks land on the right counts is untested.
- Question answering over counts ("how many fingers was I holding up?") was not measured, nor
  admission with `ingest()` and extraction. The question probes ran on `qwen3.5:9b` only.
- The count reader is a pattern, checked by hand on this run; on other descriptions it can miss a
  count ("triangular shapes") or misread one ("each ball has one white dot").

## Review

The hand review changed these scores from the script's reading:

- `qwen3.5:9b`, version 2, 1 finger: "raising his right hand to show the number one" states the
  count; scored correct.
- `gemma4:26b` (both versions) and `qwen3.5:35b-a3b` (version 2), 5 blocks: each said "five blue
  squares" and then counted them by background region, which the script read as other counts; the
  total is right. Gemma's version 2 region counts and the 35B model's were wrong; Gemma's version 3
  region counts were right.
- `gemma4:26b`, version 3, both hands: "5 raised fingers on the left hand", the same on the right
  and "10 raised fingers in total"; scored correct. Its 9 coins, "ten gold-colored circular
  objects", is wrong rather than missing.
- `qwen3.5:35b-a3b`, version 3: "three red balls" and "four green triangular shapes" are right.
