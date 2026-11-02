# Visual memory run: probe388-v2

Commit `4475e077`, Ollama 0.40.13, finished 2026-11-02T17:23:03-06:00.

## Settings

```json
{
  "run": "probe388-v2",
  "sources": "meeting,photos",
  "fps": 1.0,
  "change_threshold": 0.55,
  "max_gap": 30.0,
  "repeat_similarity": 0.93,
  "mode": "store",
  "vision_model": "qwen3.5:9b",
  "prompt_version": 2,
  "extract_model": "prme-qwen3.5:35b-a3b-8k",
  "reader_model": "qwen3.5:9b",
  "judge_model": "qwen3.5:9b",
  "token_budget": 4000
}
```

## Frames and keyframes

| Source | Frames | Keyframes | Described | Repeats dropped | Admitted | Median change | Max change |
|---|---|---|---|---|---|---|---|
| meeting.mp4 | 220 | 13 | 13 | 2 | 11 | 0.162 | 3.834 |
| photos | 5 photos | 5 | 5 | 0 | 5 | | |

Described 18 images; 16 admitted after dropping repeated descriptions.

## Describe calls (local vision model)

```json
{
  "calls": 18,
  "fresh": 18,
  "seconds": {
    "n": 18,
    "mean": 4.2,
    "median": 3.79,
    "min": 2.66,
    "max": 8.53
  }
}
```

## Pack build (store)

```json
{
  "mode": "store",
  "observations": 16,
  "errors": 0,
  "seconds": {
    "n": 16,
    "mean": 0.08,
    "median": 0.07,
    "min": 0.06,
    "max": 0.15
  },
  "events": 16,
  "nodes": {
    "note": 16
  },
  "edges": {},
  "pack_bytes": 8445434,
  "thumbnails": 16,
  "thumbnail_bytes": 121817
}
```

## Answers

```json
{
  "total": 23,
  "retrieval_hits": 23,
  "correct": 23,
  "unresolved": 0,
  "context_tokens": {
    "n": 23,
    "mean": 1823.22,
    "median": 1802,
    "min": 1802,
    "max": 1864
  },
  "retrieval_seconds": {
    "n": 23,
    "mean": 0.04,
    "median": 0.03,
    "min": 0.02,
    "max": 0.14
  },
  "by_source": {
    "meeting": {
      "questions": 16,
      "retrieval_hits": 16,
      "correct": 16
    },
    "photos": {
      "questions": 7,
      "retrieval_hits": 7,
      "correct": 7
    }
  },
  "by_kind": {
    "single_fact": {
      "questions": 16,
      "correct": 16
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
    }
  }
}
```

| Id | Kind | Hit | Correct | Answer |
|---|---|---|---|---|
| m01 | single_fact | yes | yes | Priya Raman facilitated the Falcon weekly sync. |
| m02 | latest_value | yes | yes | The Falcon launch date is now November 10th. |
| m03 | earlier_value | yes | yes | The Falcon launch date was originally set for November 3rd before being changed to November 10th. |
| m04 | latest_value | yes | yes | The Falcon budget is now $14,000. |
| m05 | earlier_value | yes | yes | The Falcon budget was $12,500 before it changed to $14,000. |
| m06 | single_fact | yes | yes | Northwind Sensors supplies the Falcon sensors. |
| m07 | single_fact | yes | yes | Dmitri needs to order 40 sensors. |
| m08 | single_fact | yes | yes | Mei books the Oakland venue for the Falcon launch. |
| m09 | chart | yes | yes | Q3 had the most sensor failures with 15. |
| m10 | chart | yes | yes | There were 7 sensor failures in Q2. |
| m11 | single_fact | yes | yes | The "Battery supplier delay" risk is rated High. |
| m12 | single_fact | yes | yes | The firmware bug number listed in the risk register is 1182. |
| m13 | single_fact | yes | yes | The next Falcon sync is scheduled for Tuesday, October 21, 2026, at 10:00. |
| m14 | single_fact | yes | yes | The demo for Acme is scheduled for October 28, 2026. |
| m15 | aggregation | yes | yes | Three action items were assigned: Dmitri to order 40 sensors by Friday, Mei to book the Oakland venue, and Sam to write the test plan. |
| m16 | single_fact | yes | yes | Sam writes the Falcon test plan. |
| p01 | single_fact | yes | yes | You spent $11.50 on two oat lattes at Blue Bottle Coffee. |
| p02 | single_fact | yes | yes | I ordered 2 x Oat Latte at Blue Bottle Coffee. |
| p03 | single_fact | yes | yes | Flight UA 884 leaves from gate B12. |
| p04 | single_fact | yes | yes | The flight from SFO to JFK is scheduled for October 18, 2026. |
| p05 | single_fact | yes | yes | The sign indicates a 2-hour limit from Monday to Saturday between 8 AM and 6 PM, with permit holders being exempt. |
| p06 | single_fact | yes | yes | Your dentist appointment is on Thursday, October 23rd at 3pm with Dr. Okafor. |
| p07 | single_fact | yes | yes | You should bring your insurance card to the appointment. |

## Extracted node sample

- `note`: This is a bar chart titled "Sensor failures per quarter" that displays data for three quarters labeled Q1, Q2, and Q3 on the horizontal axis. The bars are solid blue in color with numerical values sho
- `note`: This is a photo of a digital boarding pass for a United Airlines flight. The document features a dark blue header with white text and a plain white background for the details.

The passenger listed on
- `note`: This is a photo of a digital graphic featuring a parking sign centered on a colorful, wavy background that transitions from green at the bottom through orange and purple to blue at the top. The sign h
- `note`: This is a photo of a handwritten note on a yellow background with blue and red ink. The note contains information about an upcoming dentist appointment.

The appointment is scheduled for Thursday, Oct
- `note`: This is a photo of a printed receipt from Blue Bottle Coffee in Oakland, CA. The receipt shows details of a purchase made on October 12, 2026, at 8:41 AM.

The store's total is "$11.50".
The item purc
- `note`: This is a photo of an abstract digital artwork featuring smooth, flowing waves of color that transition from deep green at the bottom through orange and pink bands into soft purple and blue tones at t
- `note`: This is a video frame showing a digital note or memo on a light background with colored text. The note contains information about an event called "Falcon launch" and includes details like dates, budge
- `note`: This is a video frame showing a digital note or memo with colored text on a light background. The note contains information about an event called "Falcon launch" and includes details like the date, bu
- `note`: This is a video frame showing a digital note or slide with text on a light background. The text includes details about a Falcon launch event, written in blue and green fonts.

The launch date is "3 No
- `note`: This is a video frame showing a digital note or slide with text on a light background. The text includes details about an event called "Falcon launch" and lists information such as the date, budget, v
- `note`: This is a video frame showing a digital presentation slide with a dark blue header and white background. The slide contains bullet points in black text with small blue circular markers next to each it
- `note`: This is a video frame showing a digital slide titled "Risk register" with a dark blue header and light gray background. The slide lists three bullet points in black text, each marked with a small blue
- `note`: This is a video frame showing a digital slide with a dark blue header and white background. The slide lists action items in black text with small blue bullet points.

The title of the slide is "Action
- `note`: This is a video frame showing a digital whiteboard with colored text entries for a project titled "Falcon launch". The background is light beige, and the text appears in blue, red, and green colors.


- `note`: This is a video frame showing a presentation slide with a dark blue header and light gray background. The slide contains bullet points in black text with small blue circular markers, along with a clos
- `note`: This is a video frame showing an abstract digital wallpaper with flowing waves of color, including green, red, purple, and blue, set against a black background. There are no people or physical objects
