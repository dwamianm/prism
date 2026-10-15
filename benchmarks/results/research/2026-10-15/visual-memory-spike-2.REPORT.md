# Visual memory spike: spike-2

Commit `58caddb1`, Ollama 0.40.4, finished 2026-10-15T14:42:39-05:00.

## Settings

```json
{
  "run": "spike-2",
  "sources": "meeting,photos,tahoe",
  "fps": 1.0,
  "change_threshold": 0.55,
  "max_gap": 30.0,
  "repeat_similarity": 0.93,
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

Described 58 images; 17 left after dropping repeated descriptions.

## Describe calls (local vision model)

```json
{
  "calls": 58,
  "fresh": 58,
  "seconds": {
    "n": 58,
    "mean": 5.25,
    "median": 5.3,
    "min": 3.06,
    "max": 10.56
  },
  "prompt_tokens": {
    "n": 58,
    "mean": 1158.31,
    "median": 1142.0,
    "min": 657,
    "max": 2723
  },
  "eval_tokens": {
    "n": 58,
    "mean": 122.4,
    "median": 125.0,
    "min": 61,
    "max": 148
  }
}
```

## Pack build (ingest)

```json
{
  "mode": "ingest",
  "observations": 17,
  "errors": 0,
  "seconds": {
    "n": 17,
    "mean": 20.21,
    "median": 18.2,
    "min": 9.92,
    "max": 35.97
  },
  "events": 17,
  "nodes": {
    "decision": 3,
    "entity": 67,
    "fact": 20
  },
  "edges": {
    "has_fact": 23,
    "mentions": 6,
    "supersedes": 1
  },
  "pack_bytes": 13416474,
  "thumbnail_bytes": 132094
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
    "mean": 2041.67,
    "median": 2099.0,
    "min": 938,
    "max": 2161
  },
  "retrieval_seconds": {
    "n": 24,
    "mean": 0.11,
    "median": 0.06,
    "min": 0.05,
    "max": 0.49
  },
  "by_source": {
    "meeting": {
      "questions": 16,
      "retrieval_hits": 16,
      "correct": 15
    },
    "photos": {
      "questions": 7,
      "retrieval_hits": 7,
      "correct": 7
    },
    "tahoe": {
      "questions": 1,
      "retrieval_hits": 1,
      "correct": 0
    }
  },
  "by_kind": {
    "single_fact": {
      "questions": 16,
      "correct": 15
    },
    "latest_value": {
      "questions": 2,
      "correct": 2
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
      "correct": 0
    }
  }
}
```

| Id | Kind | Hit | Correct | Answer |
|---|---|---|---|---|
| m01 | single_fact | yes | yes | Priya Raman facilitated the Falcon weekly sync. |
| m02 | latest_value | yes | yes | The Falcon launch is scheduled for November 10. |
| m03 | earlier_value | yes | yes | The original Falcon launch date was November 3. |
| m04 | latest_value | yes | yes | The Falcon budget is $14,000. |
| m05 | earlier_value | yes | yes | The original Falcon budget was $12,500. |
| m06 | single_fact | yes | yes | The vendor supplying the Falcon sensors is Northwind Sensors. |
| m07 | single_fact | yes | yes | Dmitri needs to order 40 sensors. |
| m08 | single_fact | yes | yes | Mei books the Oakland venue. |
| m09 | chart | yes | yes | Q3 had the most sensor failures with a value of 15. |
| m10 | chart | yes | yes | There were 7 sensor failures in Q2. |
| m11 | single_fact | yes | yes | The risk rated High for the Falcon launch is the battery supplier delay. |
| m12 | single_fact | yes | yes | The firmware bug number is 1182. |
| m13 | single_fact | yes | no | The next Falcon sync is scheduled for Tuesday, October 21, 2026. |
| m14 | single_fact | yes | yes | The demo for Acme is scheduled for October 28, 2026. |
| m15 | aggregation | yes | yes | Three action items were assigned: Dmitri to order 40 sensors, Mei to book the Oakland venue, and Sam to write the test plan. |
| m16 | single_fact | yes | yes | Sam writes the Falcon test plan. |
| p01 | single_fact | yes | yes | You spent $11.50 at Blue Bottle Coffee. |
| p02 | single_fact | yes | yes | I ordered 2 x Oat Latte. |
| p03 | single_fact | yes | yes | Flight UA 884 leaves from gate B12. |
| p04 | single_fact | yes | yes | The flight from San Francisco (SFO) to New York (JFK) is scheduled for October 18, 2026. |
| p05 | single_fact | yes | yes | The sign indicates a 2-hour limit from Monday to Saturday between 8 AM and 6 PM, with permit holders exempt. |
| p06 | single_fact | yes | yes | Your dentist appointment is scheduled for Thursday, October 23rd at 3pm with Dr. Okafor. |
| p07 | single_fact | yes | yes | You should bring your insurance card. |
| t01 | scene | yes | no | I don't know. |

## Extracted node sample

- `decision`: Dmitri orders 40 sensors by Friday.
- `decision`: Mei books the Oakland venue.
- `decision`: Sam writes the test plan.
- `entity`: 14C
- `entity`: 18 Oct 2026
- `entity`: 2 HOUR LIMIT
- `entity`: 28 October
- `entity`: 8 AM - 6 PM
- `entity`: 8:41 AM
- `entity`: ALEX CHEN
- `entity`: Acme
- `entity`: Action items
- `entity`: B12
- `entity`: Battery supplier delay
- `entity`: Blue Bottle Coffee
- `entity`: Dmitri
- `entity`: Dr. Okafor
- `entity`: Falcon launch
- `entity`: Falcon weekly sync
- `entity`: Firmware bug 1182
- `entity`: JFK
- `entity`: MON - SAT
- `entity`: Mei
- `entity`: Northwind Sensors
- `entity`: Oakland venue
- `entity`: Oakland, CA
- `entity`: Oat Latte
- `entity`: October 12, 2026
- `entity`: Permit holders
- `entity`: Priya
- `entity`: Priya Raman
- `entity`: Q1
- `entity`: Q2
- `entity`: Q3
- `entity`: SFO
- `entity`: Sam
- `entity`: Sensor failures per quarter
- `entity`: Site photo A
- `entity`: Thursday, October 23rd
- `entity`: Tuesday 14 October 2026
- `entity`: Tuesday 21 October, 10:00
- `entity`: UA 884
- `entity`: United Airlines
- `entity`: VISA
- `entity`: Venue permit
- `entity`: abstract digital artwork
- `entity`: black background
- `entity`: blue
- `entity`: deep green
- `entity`: dentist
- `entity`: gray boulders
- `entity`: green
- `entity`: green pine trees
- `entity`: insurance card
- `entity`: island-like rock formation
- `entity`: lakeside scene
- `entity`: orange
- `entity`: parking sign
- `entity`: pink
- `entity`: purple
- `entity`: red
- `entity`: rocky outcrop
- `entity`: sensors
- `entity`: shore
- `entity`: sky
- `entity`: snow-capped mountains
- `entity`: soft purple
- `entity`: test plan
- `entity`: turquoise water
- `entity`: you
- `fact`: Priya Raman is listed as the facilitator for this session.
- `fact`: The Falcon launch is scheduled for November 10, as the date "3 Nov" has been crossed out and replaced with "10 Nov".
- `fact`: The Falcon launch is scheduled for November 10.
- `fact`: The Falcon launch is scheduled for November 10.
- `fact`: The budget for the Falcon launch is "$14,000".
- `fact`: The first item states "Battery supplier delay: High".
- `fact`: The owner of the Falcon launch is "Priya".
- `fact`: The second item states "Firmware bug 1182: Medium".
- `fact`: The sky is bright blue with no clouds.
- `fact`: The text at the bottom reads "Site photo A, taken last week".
- `fact`: The third item states "Venue permit: Low".
- `fact`: The vendor for the Falcon launch is "Northwind Sensors".
- `fact`: This is a photo of a digital graphic featuring a parking sign centered on a colorful, wavy background that transitions from green at the bottom through orange and purple to blue at the top.
- `fact`: This is a photo of a printed receipt from Blue Bottle Coffee in Oakland, CA.
- `fact`: This is a photo of an abstract digital artwork featuring smooth, flowing waves of color that transition from deep green at the bottom through orange and pink bands into soft purple and blue tones at t
- `fact`: This is a video frame showing a serene lakeside scene with large gray boulders scattered in clear turquoise water, snow-capped mountains in the distance, and two green pine trees on a rocky outcrop to
- `fact`: This is a video frame showing a serene lakeside scene with large gray boulders scattered in clear turquoise water, snow-capped mountains in the distance, and two green pine trees on a rocky outcrop to
- `fact`: This is a video frame showing a serene lakeside scene with large gray boulders scattered in clear turquoise water, snow-capped mountains in the distance, and two green pine trees on a rocky outcrop to
- `fact`: This is a video frame showing a serene lakeside scene with large gray boulders scattered in clear turquoise water, snow-capped mountains in the distance, and two green pine trees on a rocky outcrop to
- `fact`: You should bring your insurance card.
