# Visual memory spike: spike-3-store

Commit `58caddb1`, Ollama 0.40.4, finished 2026-10-15T14:47:38-05:00.

## Settings

```json
{
  "run": "spike-3-store",
  "sources": "meeting,photos,tahoe",
  "fps": 1.0,
  "change_threshold": 0.55,
  "max_gap": 30.0,
  "repeat_similarity": 0.93,
  "cap": 40,
  "mode": "store",
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
  "fresh": 0,
  "seconds": null,
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

## Pack build (store)

```json
{
  "mode": "store",
  "observations": 17,
  "errors": 0,
  "seconds": {
    "n": 17,
    "mean": 0.08,
    "median": 0.07,
    "min": 0.07,
    "max": 0.14
  },
  "events": 17,
  "nodes": {
    "note": 17
  },
  "edges": {},
  "pack_bytes": 7796245,
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
    "mean": 2005.33,
    "median": 1985.0,
    "min": 1985,
    "max": 2047
  },
  "retrieval_seconds": {
    "n": 24,
    "mean": 0.04,
    "median": 0.03,
    "min": 0.03,
    "max": 0.15
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
| m11 | single_fact | yes | yes | The risk rated High for the Falcon launch is a battery supplier delay. |
| m12 | single_fact | yes | yes | The firmware bug number listed in the risk register is 1182. |
| m13 | single_fact | yes | no | The next Falcon sync is scheduled for Tuesday, October 21, 2026. |
| m14 | single_fact | yes | yes | The demo for Acme is scheduled for October 28, 2026. |
| m15 | aggregation | yes | yes | Three action items were assigned: Dmitri to order 40 sensors by Friday, Mei to book the Oakland venue, and Sam to write the test plan. |
| m16 | single_fact | yes | yes | Sam writes the Falcon test plan. |
| p01 | single_fact | yes | yes | You spent $11.50 at Blue Bottle Coffee. |
| p02 | single_fact | yes | yes | I ordered 2 x Oat Latte at Blue Bottle Coffee. |
| p03 | single_fact | yes | yes | Flight UA 884 leaves from gate B12. |
| p04 | single_fact | yes | yes | The flight from San Francisco (SFO) to New York (JFK) is on October 18, 2026. |
| p05 | single_fact | yes | yes | The sign indicates a 2-hour limit from Monday to Saturday between 8 AM and 6 PM, with permit holders being exempt. |
| p06 | single_fact | yes | yes | Your dentist appointment is scheduled for Thursday, October 23rd at 3pm with Dr. Okafor. |
| p07 | single_fact | yes | yes | You should bring your insurance card. |
| t01 | scene | yes | no | I don't know. |

## Extracted node sample

- `note`: This is a bar chart titled "Sensor failures per quarter" that displays data for three quarters labeled Q1, Q2, and Q3 on the horizontal axis. The bars are blue in color and represent numerical values 
- `note`: This is a photo of a digital boarding pass for a United Airlines flight. The document features a dark blue header with white text and a plain white background for the details.

The passenger listed on
- `note`: This is a photo of a digital graphic featuring a parking sign centered on a colorful, wavy background that transitions from green at the bottom through orange and purple to blue at the top. The sign h
- `note`: This is a photo of a handwritten note on a yellow sticky pad. The writing includes blue and red ink with clear, legible text.

The appointment is for the dentist.  
It is scheduled for Thursday, Octob
- `note`: This is a photo of a printed receipt from Blue Bottle Coffee in Oakland, CA. The receipt shows details of a purchase made on October 12, 2026, at 8:41 AM.

The store's total is "$11.50".
The customer 
- `note`: This is a photo of an abstract digital artwork featuring smooth, flowing waves of color that transition from deep green at the bottom through orange and pink bands into soft purple and blue tones at t
- `note`: This is a video frame showing a digital note or memo displayed on a light gray background with a thin border. The text appears in two colors: blue for the top four lines and green for the bottom two l
- `note`: This is a video frame showing a digital note or memo displayed on a light gray background with colored text. The note contains information about an event called "Falcon launch" and includes details su
- `note`: This is a video frame showing a digital note or memo on a light gray background with colored text. The note contains information about an event called "Falcon launch" and includes details like dates, 
- `note`: This is a video frame showing a digital note or memo with colored text on a light background. The note contains information about an event called "Falcon launch" and includes details like dates, budge
- `note`: This is a video frame showing a digital note or memo with colored text on a light background. The note includes details about an event called "Falcon launch" and lists associated information such as d
- `note`: This is a video frame showing a digital presentation slide with a dark blue header and light gray background. The slide lists three risk items in black text with blue bullet points.

The first item st
- `note`: This is a video frame showing a digital presentation slide with a dark blue header and white background. The slide contains bullet points in black text with small blue circular markers next to each it
- `note`: This is a video frame showing a presentation slide with a dark blue header and light gray background. The slide contains bullet points in black text with small blue circular markers, along with a clos
- `note`: This is a video frame showing a presentation slide with a dark blue header and white background. The slide lists action items in black text with small blue bullet points.

The title of the slide is "A
- `note`: This is a video frame showing a serene lakeside scene with large gray boulders scattered in clear turquoise water, snow-capped mountains in the distance, and two green pine trees on a rocky outcrop to
- `note`: This is a video frame showing an abstract digital wallpaper with flowing waves of color, including green, red, purple, and blue, set against a black background. There are no people or physical objects
