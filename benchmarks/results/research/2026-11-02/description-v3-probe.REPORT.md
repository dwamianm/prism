# Visual memory run: probe388-v3

Commit `4475e077`, Ollama 0.40.13, finished 2026-11-02T17:26:12-06:00.

## Settings

```json
{
  "run": "probe388-v3",
  "sources": "meeting,photos",
  "fps": 1.0,
  "change_threshold": 0.55,
  "max_gap": 30.0,
  "repeat_similarity": 0.93,
  "mode": "store",
  "vision_model": "qwen3.5:9b",
  "prompt_version": 3,
  "extract_model": "prme-qwen3.5:35b-a3b-8k",
  "reader_model": "qwen3.5:9b",
  "judge_model": "qwen3.5:9b",
  "token_budget": 4000
}
```

## Frames and keyframes

| Source | Frames | Keyframes | Described | Repeats dropped | Admitted | Median change | Max change |
|---|---|---|---|---|---|---|---|
| meeting.mp4 | 220 | 13 | 13 | 3 | 10 | 0.162 | 3.834 |
| photos | 5 photos | 5 | 5 | 0 | 5 | | |

Described 18 images; 15 admitted after dropping repeated descriptions.

## Describe calls (local vision model)

```json
{
  "calls": 18,
  "fresh": 18,
  "seconds": {
    "n": 18,
    "mean": 4.07,
    "median": 3.88,
    "min": 2.99,
    "max": 7.84
  }
}
```

## Pack build (store)

```json
{
  "mode": "store",
  "observations": 15,
  "errors": 0,
  "seconds": {
    "n": 15,
    "mean": 0.07,
    "median": 0.06,
    "min": 0.06,
    "max": 0.13
  },
  "events": 15,
  "nodes": {
    "note": 15
  },
  "edges": {},
  "pack_bytes": 8440531,
  "thumbnails": 15,
  "thumbnail_bytes": 113475
}
```

## Answers

```json
{
  "total": 23,
  "retrieval_hits": 23,
  "correct": 22,
  "unresolved": 0,
  "context_tokens": {
    "n": 23,
    "mean": 1647.22,
    "median": 1626,
    "min": 1626,
    "max": 1688
  },
  "retrieval_seconds": {
    "n": 23,
    "mean": 0.03,
    "median": 0.03,
    "min": 0.02,
    "max": 0.12
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
    }
  }
}
```

| Id | Kind | Hit | Correct | Answer |
|---|---|---|---|---|
| m01 | single_fact | yes | yes | Priya Raman facilitated the Falcon weekly sync. |
| m02 | latest_value | yes | yes | The Falcon launch date is now 10 Nov. |
| m03 | earlier_value | yes | yes | The Falcon launch date was originally set for November 3rd before being changed to November 10th. |
| m04 | latest_value | yes | yes | The Falcon budget is now $14,000. |
| m05 | earlier_value | yes | yes | The Falcon budget was $12,500 before it changed to $14,000. |
| m06 | single_fact | yes | yes | Northwind Sensors supplies the Falcon sensors. |
| m07 | single_fact | yes | yes | Dmitri needs to order 40 sensors. |
| m08 | single_fact | yes | yes | Mei books the Oakland venue. |
| m09 | chart | yes | yes | Q3 had the most sensor failures with 15. |
| m10 | chart | yes | yes | There were 7 sensor failures in Q2. |
| m11 | single_fact | yes | yes | The battery supplier delay is rated as High. |
| m12 | single_fact | yes | yes | The firmware bug number listed in the risk register is 1182. |
| m13 | single_fact | yes | no | The next Falcon sync is scheduled for Tuesday, 21 October 2026. |
| m14 | single_fact | yes | yes | The demo for Acme is scheduled for October 28, 2026. |
| m15 | aggregation | yes | yes | Three action items were assigned: Dmitri to order 40 sensors, Mei to book the Oakland venue, and Sam to write the test plan. |
| m16 | single_fact | yes | yes | Sam writes the Falcon test plan. |
| p01 | single_fact | yes | yes | You spent $11.50 at Blue Bottle Coffee. |
| p02 | single_fact | yes | yes | I ordered two oat lattes. |
| p03 | single_fact | yes | yes | Flight UA 884 leaves from gate B12. |
| p04 | single_fact | yes | yes | The flight from San Francisco (SFO) to New York (JFK) is on 18 Oct 2026. |
| p05 | single_fact | yes | yes | The sign indicates a 2-hour limit from Monday to Saturday between 8 AM and 6 PM, with permit holders exempt. |
| p06 | single_fact | yes | yes | Your dentist appointment is on Thursday, October 23, 2026, at 3 PM with Dr. Okafor. |
| p07 | single_fact | yes | yes | You should bring your insurance card. |

## Extracted node sample

- `note`: This is a bar chart titled "Sensor failures per quarter" that displays data for three quarters labeled Q1, Q2, and Q3 on the horizontal axis. The bars are blue in color and represent numerical values 
- `note`: This is a photo of a digital boarding pass for a United Airlines flight. The image displays passenger details, flight information, and seat assignment on a white background with blue header text.

The
- `note`: This is a photo of a digital graphic featuring a parking sign centered on a colorful, wavy background with gradients of green, orange, purple, and blue. The sign has a white body with a green header a
- `note`: This is a photo of a handwritten note on a yellow background with blue and red ink. The note contains information about a dentist appointment.

The subject of the note is "Dentist!".
The date and time
- `note`: This is a photo of a printed receipt from Blue Bottle Coffee in Oakland, CA. The receipt shows details of a purchase made on October 12, 2026, at 8:41 AM.

The store's total is "$11.50".
Two oat latte
- `note`: This is a video frame showing a digital note or memo with colored text on a light background. The note contains information about an event called "Falcon launch" and includes details like the date, bu
- `note`: This is a video frame showing a digital note or slide with text on a light background. The text includes details about a Falcon launch event.

The launch date is "3 Nov".
The budget is "$12,500".
The 
- `note`: This is a video frame showing a digital presentation slide with a white background and a dark blue header bar. The slide contains black text organized into bullet points, each marked by a small blue d
- `note`: This is a video frame showing a digital slide titled "Risk register" with a dark blue header bar and a light gray background containing three bullet points in black text. The slide lists risk items wi
- `note`: This is a video frame showing a digital slide with a dark blue header bar and white background. The slide lists action items in black text with small blue bullet points.

The title reads "Action items
- `note`: This is a video frame showing a digital whiteboard with colored text on a light background. The board displays information about a Falcon launch event, including details like the date, budget, vendor,
- `note`: This is a video frame showing a digital whiteboard with colored text on a light background. The board displays information about an event titled "Falcon launch" in blue letters.

The launch date was o
- `note`: This is a video frame showing a presentation slide with a dark blue header bar and a light gray background. The slide contains bullet points in black text with small blue circular markers next to them
- `note`: This is a video frame showing an abstract digital wallpaper with flowing waves of color including green, red, purple, and blue. There are no people or physical objects present in the scene.

The text 
- `note`: This is an abstract digital artwork featuring smooth, flowing waves of color. The composition includes gradients of blue, purple, pink, orange, and green arranged in layered, undulating forms that cre
