# Keyframe calibration for fixed cameras (2026-11-02)

**Status:** Evidence for dwamianm/prism#389. Synthetic scenes and the spike corpus only. No real
webcam recording has been measured yet; the script for that is below, and the false-keep rate on real
recordings waits for the owner's clips.

**Question:** the keyframe filter keeps a frame when `frame_change` (the largest block difference of two
aligned, z-scored signatures) reaches `vision.change_threshold`, 0.55. On the Nenavi camera demo's
synthetic hand, one more raised finger scored 0.34 to 0.48, so no finger count was kept. Can the
threshold follow the source, so a fixed camera keeps small changes while a hand-held recording or a pan
keeps 0.55, without keeping noise, exposure steps or shake?

**Setup:** Apple M5 Pro, ffmpeg 9.0.2, Ollama 0.40.13, base commit `4475e077`. The scene generators are
ported from the demo's watch tests (`apps/vision-demo/server/tests/test_watch.py` in
dwamianm/prism-monorepo) into `tests/test_vision_keyframe_calibration.py`: a 640x360 desk with sensor
noise (sigma 4 per channel) and JPEG compression on every frame, scaled to 1280x720 as `observe()` decodes
a clip. The corpus is the spike run `phase1-store` (`meeting.mp4` and the macOS "Tahoe Day" aerial video).
The only model calls were 60 to the local `qwen3.5:9b`, for the answer check below. No hosted model and no
paid API was used.

## What was chosen

`vision.calibrate_keyframes` (on by default) makes two changes. The 0.16.1 policy stays available
exactly: `PRME_VISION__CALIBRATE_KEYFRAMES=false`.

1. **Noise-floor calibration.** Every frame that scores under the threshold is a still frame, and its
   score is noise. The threshold is `vision.noise_multiplier` (5) times the median of a video's last 20
   still-frame scores, never under `vision.min_change_threshold` (0.30) and never over
   `vision.change_threshold` (0.55). It starts at 0.55 and one still frame is enough to lower it, so a
   pose held for two samples is kept. The state is a `KeyframePolicy` per video (`NoiseFloor` inside it),
   which a stream session (#390) can hold per session.
2. **A checked alignment** (`aligned_frame_change`). Phase correlation sometimes locks onto no shift on a
   noisy, low-texture frame, and a whole-pixel shift leaves half a pixel of residual. Under a 0.30 floor
   that reads as change. The new score tries the phase-correlation shift and every whole-pixel shift
   within 3 signature pixels, refines the best by half a pixel, and keeps the one that leaves the
   smallest difference. The block score is unchanged, so real changes score what they did. It costs about
   5 ms a frame against 1 ms. `frame_change` itself is unchanged; the demo imports it.

### Rejected

- **A changed-blocks term.** On `meeting.mp4`, whose frames have hand-held shake and exposure drift,
  still frames had up to 124 of the 481 blocks over 0.2 under the 0.16.1 score, while one more finger
  changes 3 to 6 blocks. Counting changed blocks would read hand-held text as a large change and does
  not help a small change like a finger.
- **Source profiles alone** (a fixed-camera 0.30 and a hand-held 0.55 chosen by the caller). With the
  new score at a fixed 0.30, the Tahoe pan went from 45 to 77 keyframes, each a vision call that the
  repeat filter drops. On the synthetic low-light clip below, a fixed 0.30 with the 0.16.1 score kept 35
  false frames in two minutes. A profile also needs the caller to know the camera; calibration finds it.
- **A floor under 0.30, or a smaller multiplier.** A 25% exposure step scores 0.26 to 0.30 (0.27 at most at
  1280x720, 0.30 at the demo's 640x360) and one more finger 0.34, so 0.30 sits between them. The multiplier matters only once 5 times the median is over
  the floor, a median over 0.06. On the measured sources with such a median (the meeting recording, the
  Tahoe pan and the dim cameras), no still frame scored more than 2.22 times the running median it was
  compared under (the meeting, 103 frames), so 5 leaves a margin of more than two. Multipliers 4, 5, 6
  and 8 gave the same selections on every measured source.
- **Raising the threshold above 0.55 for a pan.** It would cut Tahoe's vision calls, but it is outside
  this issue: the ceiling keeps every source at least as sensitive as 0.16.1.

## Synthetic scenes

Scores at 1280x720 over 10 noise seeds:

| Pair | 0.16.1 score min / max | Aligned score min / max |
|---|---|---|
| sensor noise | 0.011 / 0.014 | 0.011 / 0.014 |
| exposure +12% | 0.054 / 0.057 | 0.054 / 0.057 |
| exposure -12% | 0.017 / 0.020 | 0.017 / 0.020 |
| exposure +25% | 0.261 / 0.267 | 0.263 / 0.267 |
| shake 3,2 px | 0.172 / 0.198 | 0.052 / 0.064 |
| shake -3,2 px | 0.092 / 0.176 | 0.055 / 0.061 |
| shake 2,3 px | 0.102 / 0.150 | 0.043 / 0.048 |
| shake 1,1 px | 0.056 / 0.153 | 0.053 / 0.064 |
| shake 4,4 px | 0.010 / 0.013 | 0.010 / 0.013 |
| shake 6,-4 px | 0.101 / 0.119 | 0.016 / 0.022 |
| finger 2 vs 1 | 0.341 / 0.354 | 0.341 / 0.354 |
| finger 3 vs 2 | 0.450 / 0.459 | 0.450 / 0.459 |
| finger 4 vs 3 | 0.471 / 0.478 | 0.471 / 0.478 |
| finger 5 vs 4 | 0.388 / 0.394 | 0.388 / 0.394 |
| phone put down | 2.000 / 2.009 | 2.000 / 2.009 |
| person sits down | 1.492 / 1.501 | 1.446 / 1.456 |

The 0.16.1 score's alignment slips on some draws: over 48 shake pairs at 1280x720, 3 scored over 0.35
(up to 0.383), and at the demo's 640x360 a 4 pixel shake scored up to 0.486. The aligned score of the
same pairs stayed under 0.05.

Selection, change threshold 0.55, heartbeat 30 s, 10 seeds (the finger sequence is 1 to 5 fingers, two
seconds each):

| Scene | 0.16.1 | Calibrated |
|---|---|---|
| Finger counts kept of 5 at 1 frame a second (min / max over seeds) | 1 / 1 | 5 / 5 |
| Finger counts kept of 5 at 2 frames a second | 1 / 1 | 5 / 5 |
| Noise, 12% exposure steps and six shakes: frames kept after the first, all seeds | 0 | 0 |

A dim camera (fewer photons: lower exposure and more sensor noise), 14 still frames, then 1 to 3 fingers
two frames each:

| Exposure, noise sigma | Still score median | Calibrated threshold | False keeps 0.16.1 | False keeps calibrated | Counts kept of 3, 0.16.1 / calibrated |
|---|---|---|---|---|---|
| 1.0, 4 | 0.012 | 0.30 | 0 | 0 | 1 / 3 |
| 0.4, 8 | 0.037 | 0.30 | 0 | 0 | 1 / 3 |
| 0.3, 12 | 0.062 | 0.318 | 0 | 0 | 1 / 3 |
| 0.25, 16 | 0.088 | 0.44 | 0 | 0 | 1 / 2 |
| 0.2, 24 | 0.156 | 0.55 | 0 | 0 | 2 / 2 |
| 0.15, 32 | 0.282 | 0.55 | 5 | 0 | 3 / 2 |

As the noise grows the threshold rises to 0.55 and false keeps stay at zero; at the darkest level the
0.16.1 policy's three counts come with five false keeps.

## Still-scene script on synthetic clips

`benchmarks/vision/keyframe_calibration.py still` on two synthetic two-minute clips at 1 frame a second,
with the demo's other sensitivities for comparison (`--fixed 0.30 0.45`):

| Clip | Setting | Keeps | Keeps per minute | False keeps | False-keep rate | Threshold min / median / max |
|---|---|---|---|---|---|---|
| normal light | 0.16.1 | 4 | 2.0 | 0 | 0.0 | 0.55 / 0.55 / 0.55 |
| normal light | calibrated | 4 | 2.0 | 0 | 0.0 | 0.30 / 0.30 / 0.30 |
| normal light | 0.16.1 score, fixed 0.30 | 4 | 2.0 | 0 | 0.0 | 0.30 |
| normal light | 0.16.1 score, fixed 0.45 | 4 | 2.0 | 0 | 0.0 | 0.45 |
| low light (exposure 0.2, sigma 24) | 0.16.1 | 7 | 3.5 | 5 | 0.042 | 0.55 / 0.55 / 0.55 |
| low light | calibrated | 4 | 2.0 | 0 | 0.0 | 0.55 / 0.55 / 0.55 |
| low light | 0.16.1 score, fixed 0.30 | 36 | 18.0 | 35 | 0.294 | 0.30 |
| low light | 0.16.1 score, fixed 0.45 | 8 | 4.0 | 7 | 0.059 | 0.45 |

Four keeps in two minutes are the first frame and three heartbeats.

## Spike corpus

Keyframes only, no model call (`keyframe_calibration.py corpus`, `keyframe-calibration-corpus.json`):

| Video | Setting | Keyframes | Kept frames |
|---|---|---|---|
| meeting.mp4 (220 frames) | 0.16.1 | 13 | 0, 15, 45 heartbeat, 46, 55, 75, 92, 102, 107, 115, 145, 175, 200 |
| meeting.mp4 | calibrated | 10 | 0, 15, 45 heartbeat, 55, 75, 105 heartbeat, 115, 145, 175, 200 |
| Tahoe Day.mov (120 frames) | 0.16.1 | 48 | every 2 to 3 s |
| Tahoe Day.mov | calibrated | 45 | every 2 to 4 s |

- The 0.16.1 setting reproduces the recorded run's keyframes exactly on both videos (13 and 48).
- Every meeting scene start (0, 15, 55, 75, 115, 145, 175, 200) is kept under both settings, and so is
  Tahoe's first frame, the only Tahoe observation the recorded run admitted. Each question's scene has a
  kept frame under both.
- The calibrated setting drops 46, 92, 102 and 107, which were hand-held noise inside one whiteboard
  scene. The calibrated threshold on the meeting stayed between 0.30 and 0.55 and kept no frame for noise.

One dependency is not settled by keyframes alone: in the recorded run, frame 75's description lacked
"10 Nov", and question m02 ("What is the Falcon launch date now?") was answered from frame 92's
description, a noise keep that the calibrated setting no longer makes. So the meeting was observed again
under both settings with `store()` admission and the local `qwen3.5:9b`, within a limit of 60 model calls
(`keyframe-calibration-meeting-answers.json`):

| Setting | Keyframes | Described | Repeats dropped | Admitted | Retrieval hits (needle in context) | Judged correct |
|---|---|---|---|---|---|---|
| 0.16.1 | 13 | 13 | 2 | 11 | 16 of 16 | 12 of 12 judged (m01 to m12) |
| calibrated | 10 | 10 | 2 | 8 | 16 of 16 | 11 of 11 judged (m01 to m11) |

The recorded describe cache did not match the frames decoded today, so both packs were described fresh
(14 calls), and every question's context differed between the settings, so the reader and judge ran for
each until the limit (23 each). m12 under the calibrated setting and m13 to m16 under both were not judged.
In today's descriptions, frames 75 and 105 both state "10 Nov" and "$14,000", and the m02 answer was right
under both settings.

## Not measured

- Real webcam recordings in normal and low light (criterion 2 of #389). The script reports keeps per
  minute and the false-keep rate for them under both settings; the owner's clips will decide whether 0.30
  and 5 hold on a real sensor, with its auto exposure, white balance and compression.
- Real hand-held video, which has rotation and rolling shutter as well as translation.
- Answers for m12 (calibrated) and m13 to m16, and any repeated run.
- The camera demo's live gate (settle time and forced keeps), which is #390's work.

## How to run

```bash
uv run python -m benchmarks.vision.keyframe_calibration still normal-light.mov low-light.mov --fixed 0.30 0.45 --json still.json
uv run python -m benchmarks.vision.keyframe_calibration still clip.mov --fps 2   # the rate a live camera sends
uv run python -m benchmarks.vision.keyframe_calibration corpus --run-dir benchmarks/vision/runs/phase1-store --json corpus.json
```
