# Visual memory spike: spike-1

Commit `58caddb1`, Ollama 0.40.4, finished 2026-10-15T14:24:28-05:00.

## Settings

```json
{
  "run": "spike-1",
  "sources": "meeting,photos,tahoe",
  "fps": 1.0,
  "change_threshold": 0.55,
  "max_gap": 30.0,
  "repeat_similarity": 0.85,
  "cap": 40,
  "mode": "ingest",
  "vision_model": "qwen3.5:9b",
  "extract_model": "prme-qwen3.5:35b-a3b-8k",
  "reader_model": "qwen3.5:9b",
  "judge_model": "qwen3.5:9b",
  "token_budget": 4000
}
```

## Frames and keyframes

| Source | Frames | Keyframes | Median change | Max change |
|---|---|---|---|---|
| meeting | 220 | 13 | 0.165 | 3.82 |
| tahoe | 120 | 40 | 0.479 | 0.846 |
| photos | 5 photos | 5 | | |

Described 58 images; 53 left after dropping repeated descriptions.

## Describe calls (local vision model)

```json
{
  "calls": 58,
  "fresh": 58,
  "seconds": {
    "n": 58,
    "mean": 5.24,
    "median": 4.88,
    "min": 4.13,
    "max": 10.59
  },
  "prompt_tokens": {
    "n": 58,
    "mean": 1096.22,
    "median": 1080.0,
    "min": 594,
    "max": 2660
  },
  "eval_tokens": {
    "n": 58,
    "mean": 102.86,
    "median": 95.0,
    "min": 75,
    "max": 155
  }
}
```

## Pack build (ingest)

```json
{
  "mode": "ingest",
  "observations": 53,
  "errors": 32,
  "seconds": {
    "n": 21,
    "mean": 27.02,
    "median": 29.72,
    "min": 5.82,
    "max": 38.85
  },
  "events": 53,
  "nodes": {
    "decision": 3,
    "entity": 56,
    "fact": 52
  },
  "edges": {
    "has_fact": 55,
    "mentions": 17,
    "supersedes": 6
  },
  "pack_bytes": 14081769,
  "thumbnail_bytes": 503707
}
```

## Answers

```json
{
  "total": 24,
  "retrieval_hits": 24,
  "correct": 22,
  "unresolved": 0,
  "context_tokens": {
    "n": 24,
    "mean": 3642.92,
    "median": 3897.5,
    "min": 1585,
    "max": 3900
  },
  "retrieval_seconds": {
    "n": 24,
    "mean": 0.22,
    "median": 0.14,
    "min": 0.09,
    "max": 0.48
  },
  "by_source": {
    "meeting": {
      "questions": 16,
      "retrieval_hits": 16,
      "correct": 14
    },
    "photos": {
      "questions": 7,
      "retrieval_hits": 7,
      "correct": 7
    },
    "tahoe": {
      "questions": 1,
      "retrieval_hits": 1,
      "correct": 1
    }
  },
  "by_kind": {
    "single_fact": {
      "questions": 16,
      "correct": 16
    },
    "latest_value": {
      "questions": 2,
      "correct": 0
    },
    "earlier_value": {
      "questions": 2,
      "correct": 2
    },
    "chart": {
      "questions": 2,
      "correct": 2
    },
    "aggregation": {
      "questions": 1,
      "correct": 1
    },
    "scene": {
      "questions": 1,
      "correct": 1
    }
  }
}
```

| Id | Kind | Hit | Correct | Answer |
|---|---|---|---|---|
| m01 | single_fact | yes | yes | Priya Raman facilitated the Falcon weekly sync. |
| m02 | latest_value | yes | no | The Falcon launch date is 3-Nov. |
| m03 | earlier_value | yes | yes | The Falcon launch date was originally scheduled for November 3rd before being moved to November 10th. |
| m04 | latest_value | yes | no | The Falcon budget is $12,500. |
| m05 | earlier_value | yes | yes | The Falcon budget was $12,500 before it changed to $14,000. |
| m06 | single_fact | yes | yes | Northwind Sensors supplies the Falcon sensors. |
| m07 | single_fact | yes | yes | Dmitri needs to order 40 sensors. |
| m08 | single_fact | yes | yes | Mei books the Oakland venue. |
| m09 | chart | yes | yes | Q3 had the most sensor failures with 15. |
| m10 | chart | yes | yes | There were 7 sensor failures in Q2. |
| m11 | single_fact | yes | yes | Battery supplier delay. |
| m12 | single_fact | yes | yes | The firmware bug number listed in the risk register is 1182. |
| m13 | single_fact | yes | yes | The next Falcon sync is on Tuesday, 21 October at 10:00. |
| m14 | single_fact | yes | yes | The demo for Acme is on 28 October. |
| m15 | aggregation | yes | yes | Three action items were assigned: Dmitri to order 40 sensors by Friday, Mei to book the Oakland venue, and Sam to write the test plan. |
| m16 | single_fact | yes | yes | Sam writes the Falcon test plan. |
| p01 | single_fact | yes | yes | I spent $11.50 at Blue Bottle Coffee. |
| p02 | single_fact | yes | yes | I ordered two oat lattes. |
| p03 | single_fact | yes | yes | Flight UA 884 leaves from Gate B12. |
| p04 | single_fact | yes | yes | The flight from San Francisco to New York is on October 18, 2026. |
| p05 | single_fact | yes | yes | The sign indicates a 2-hour limit from 8 AM to 6 PM, Monday through Saturday, with permit holders exempt. |
| p06 | single_fact | yes | yes | Your dentist appointment is on Thursday, October 23rd at 3pm with Dr. Okafor. |
| p07 | single_fact | yes | yes | You should bring your insurance card. |
| t01 | scene | yes | yes | The aerial video shows a serene lake scene with clear turquoise water, large gray boulders, snow-capped mountains, and two green pine trees  |

## Extracted node sample

- `decision`: The first bullet point says "Dmitri orders 40 sensors by Friday".
- `decision`: The second bullet point says "Mei books the Oakland venue".
- `decision`: The third bullet point says "Sam writes the test plan".
- `entity`: 00:03
- `entity`: 00:06
- `entity`: 00:12
- `entity`: 00:18
- `entity`: 00:21
- `entity`: 00:24
- `entity`: 00:27
- `entity`: 00:30
- `entity`: 00:36
- `entity`: 00:39
- `entity`: Acme
- `entity`: Action items
- `entity`: Battery supplier delay
- `entity`: Demo for Acme
- `entity`: Dmitri
- `entity`: Falcon launch
- `entity`: Falcon weekly sync
- `entity`: Firmware bug 1182
- `entity`: Mei
- `entity`: Northwind Sensors
- `entity`: Oakland venue
- `entity`: Priya
- `entity`: Priya Raman
- `entity`: Q1
- `entity`: Q2
- `entity`: Q3
- `entity`: Risk register
- `entity`: Sam
- `entity`: Sensor failures per quarter
- `entity`: Site photo A
- `entity`: Tahoe
- `entity`: Tahoe Day
- `entity`: Tahoe Day.mov
- `entity`: Tuesday 14 October 2026
- `entity`: Tuesday 21 October, 10:00
- `entity`: Venue permit
- `entity`: Wrap up
- `entity`: actions
- `entity`: blue sky
- `entity`: budget
- `entity`: gray boulders
- `entity`: green pine trees
- `entity`: lake
- `entity`: launch
- `entity`: meeting.mp4
- `entity`: pine trees
- `entity`: risks
- `entity`: rocks beneath the surface
- `entity`: rocky island or peninsula
- `entity`: rocky outcrop
- `entity`: sensors
- `entity`: shore
- `entity`: snow-capped mountains
- `entity`: snow-capped mountains
- `entity`: test plan
- `entity`: turquoise water
- `fact`: At the bottom of the image, white text on a black background reads: "Site photo A, taken last week".
- `fact`: Below that, two bullet points are listed: the first says "Next sync: Tuesday 21 October, 10:00" and the second says "Demo for Acme on 28 October".
- `fact`: In the background, snow-capped mountains stretch across the horizon under a bright blue sky.
- `fact`: In the background, snow-capped mountains stretch across the horizon under a cloudless, deep blue sky.
- `fact`: In the background, snow-capped mountains stretch across the horizon under a deep blue sky.
- `fact`: In the background, snow-capped mountains stretch across the horizon under a deep blue sky.
- `fact`: In the background, snow-capped mountains stretch across the horizon under a solid, cloudless blue sky.
- `fact`: In the background, snow-capped mountains stretch across the horizon.
- `fact`: In the background, snow-capped mountains stretch across the horizon.
- `fact`: In the distance, snow-capped mountains line the horizon under a deep blue sky.
- `fact`: Large gray boulders are scattered across the shallow, turquoise water in the foreground and middle ground.
- `fact`: Large gray boulders are scattered across the shallow, turquoise water in the foreground and middle ground.
- `fact`: Large, smooth gray boulders are scattered throughout the shallow water and along the rocky edge.
- `fact`: Large, smooth gray boulders are scattered throughout the shallow water and along the rocky edge.
- `fact`: On the right side of the image, a small rocky island or peninsula features two green pine trees growing among more rocks.
- `fact`: On the right side of the image, a small rocky island or peninsula features two green pine trees growing among more rocks.
- `fact`: On the right side of the image, two green pine trees grow from a cluster of rocks jutting into the lake.
- `fact`: On the right side of the image, two green pine trees grow from a cluster of rocks jutting into the lake.
- `fact`: On the right side of the image, two green pine trees grow from a cluster of rocks jutting into the lake.
- `fact`: On the right side of the image, two green pine trees grow from rocky terrain near the shore.
- `fact`: On the right side of the image, two green pine trees grow from rocky terrain near the shore.
- `fact`: On the right side of the image, two green pine trees grow on a rocky outcrop near the shore.
- `fact`: On the right side of the image, two green pine trees stand atop a rocky outcrop jutting into the lake.
- `fact`: On the right side of the image, two green pine trees stand atop a rocky outcrop jutting into the lake.
- `fact`: On the right side, two green pine trees grow from rocky terrain at the water’s edge.
- `fact`: On the right side, two green pine trees grow on a rocky outcrop jutting into the lake.
- `fact`: On the right side, two green pine trees grow on a rocky outcrop jutting into the lake.
- `fact`: On the right side, two green pine trees grow on a rocky outcrop jutting into the lake.
- `fact`: On the right side, two green pine trees stand atop a rocky outcrop near the shore.
- `fact`: The first bullet point says "Battery supplier delay: High", the second says "Firmware bug 1182: Medium", and the third says "Venue permit: Low".
- `fact`: The first bullet point says "Battery supplier delay: High", the second says "Firmware bug 1182: Medium", and the third says "Venue permit: Low".
- `fact`: The first bullet point says "Battery supplier delay: High", the second says "Firmware bug 1182: Medium", and the third says "Venue permit: Low".
- `fact`: The water is clear and turquoise, with large gray boulders scattered throughout the foreground and middle ground.
- `fact`: The water is clear and turquoise, with large gray boulders scattered throughout the foreground and midground.
- `fact`: The water is clear and turquoise, with large gray boulders scattered throughout the shallow areas near the shore.
- `fact`: The water is turquoise and transparent, revealing large gray boulders partially submerged near the shore.
- `fact`: This is a photograph of a serene lake scene under a clear blue sky.
- `fact`: Video frame at 00:00 of Tahoe Day.mov. This is a photograph of a serene lake scene on a bright, sunny day.
- `fact`: Video frame at 00:03 of Tahoe Day.mov.
- `fact`: Video frame at 00:06 of Tahoe Day.mov.
- `fact`: Video frame at 00:09 of Tahoe Day.mov.
- `fact`: Video frame at 00:12 of Tahoe Day.mov.
- `fact`: Video frame at 00:18 of Tahoe Day.mov.
- `fact`: Video frame at 00:21 of Tahoe Day.mov.
- `fact`: Video frame at 00:24 of Tahoe Day.mov.
- `fact`: Video frame at 00:27 of Tahoe Day.mov.
- `fact`: Video frame at 00:30 of Tahoe Day.mov.
- `fact`: Video frame at 00:36 of Tahoe Day.mov.
- `fact`: Video frame at 00:39 of Tahoe Day.mov.
- `fact`: Video frame at 02:25 of meeting.mp4. This is a bar chart titled "Sensor failures per quarter".
- `fact`: Video frame at 02:55 of meeting.mp4. This is a digital presentation slide with a dark blue header bar and a light gray background. The title in the header reads "Risk register" in white text.
- `fact`: Video frame at 03:20 of meeting.mp4.
