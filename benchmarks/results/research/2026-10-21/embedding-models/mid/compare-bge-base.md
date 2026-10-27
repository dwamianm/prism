# Mid tier comparison

Baseline `emb366-bge-small` (commit `349a9926`, warm) against candidate `emb366-bge-base` (commit `349a9926`, warm). Candidate settings: the defaults. The engine source is identical.

- Warning: The baseline ran on a working tree with uncommitted changes.
- Warning: The candidate ran on a working tree with uncommitted changes.

## LoCoMo

1,540 questions, 1,536 with annotated evidence. Intervals are 95% of the paired change; a change is clear of zero only when the questions and the conversations intervals both exclude zero.

| Measure | Baseline | Candidate | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| all evidence packed | 83.6% | 83.6% | +0.0 | -1.0 to +1.0 | -0.7 to +0.7 | 27 / 27 / 1482 | no |
| evidence recall | 89.2% | 89.6% | +0.4 | -0.3 to +1.1 | -0.2 to +1.1 | 49 / 42 / 1445 | no |
| evidence in top 25 | 70.3% | 72.3% | +2.0 | +1.0 to +3.0 | +1.0 to +2.9 | 82 / 39 / 1415 | yes |
| all evidence returned | 98.0% | 98.4% | +0.4 | -0.1 to +0.9 | +0.0 to +0.9 | 11 / 5 / 1520 | no |
| projected accuracy | 77.4% | 77.4% | +0.0 | -0.6 to +0.6 | -0.4 to +0.5 | 27 / 27 / 1486 | no |

All evidence packed, by category:

| Category | Baseline | Candidate | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| multi-hop | 51.8% | 50.4% | -1.4 | -5.0 to +1.8 | -3.7 to +0.7 | 10 / 14 / 258 | no |
| open-domain | 55.4% | 55.4% | +0.0 | -5.4 to +6.5 | -3.9 to +4.2 | 4 / 4 / 84 | no |
| single-hop | 94.5% | 95.1% | +0.6 | -0.2 to +1.5 | -0.2 to +1.6 | 10 / 5 / 826 | no |
| temporal | 91.0% | 90.7% | -0.3 | -1.9 to +1.2 | -1.4 to +0.6 | 3 / 4 / 314 | no |

Gained all evidence (27): conv-26-q0055, conv-26-q0139, conv-26-q0144, conv-30-q0081, conv-41-q0047, conv-42-q0011, conv-42-q0014, conv-42-q0134, conv-42-q0135, conv-43-q0059, conv-44-q0044, conv-47-q0008, conv-47-q0011, conv-47-q0012, conv-47-q0049, conv-47-q0078, conv-47-q0103, conv-48-q0048, conv-48-q0080, conv-49-q0018, and 7 more

Lost all evidence (27): conv-26-q0002, conv-26-q0043, conv-26-q0048, conv-26-q0066, conv-41-q0035, conv-41-q0072, conv-42-q0051, conv-42-q0068, conv-42-q0095, conv-43-q0019, conv-43-q0105, conv-44-q0012, conv-44-q0016, conv-44-q0018, conv-44-q0020, conv-47-q0026, conv-47-q0051, conv-47-q0054, conv-48-q0014, conv-48-q0141, and 7 more

## LongMemEval-S

12 questions, 12 with annotated evidence. Intervals are 95% of the paired change; with this few questions they are wide.

| Measure | Baseline | Candidate | Change (points) | 95% questions | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---|
| all evidence packed | 100.0% | 100.0% | +0.0 | +0.0 to +0.0 | 0 / 0 / 12 | no |
| evidence recall | 100.0% | 100.0% | +0.0 | +0.0 to +0.0 | 0 / 0 / 12 | no |
| evidence in top 25 | 100.0% | 100.0% | +0.0 | +0.0 to +0.0 | 0 / 0 / 12 | no |
| all evidence returned | 100.0% | 100.0% | +0.0 | +0.0 to +0.0 | 0 / 0 / 12 | no |
| projected accuracy | 95.5% | 95.5% | +0.0 | +0.0 to +0.0 | 0 / 0 / 12 | no |

All evidence packed, by category:

| Category | Baseline | Candidate | Change (points) | 95% questions | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---|
| knowledge-update | 100.0% | 100.0% | +0.0 | n/a | 0 / 0 / 2 | no |
| multi-session | 100.0% | 100.0% | +0.0 | n/a | 0 / 0 / 2 | no |
| single-session-assistant | 100.0% | 100.0% | +0.0 | n/a | 0 / 0 / 2 | no |
| single-session-preference | 100.0% | 100.0% | +0.0 | n/a | 0 / 0 / 2 | no |
| single-session-user | 100.0% | 100.0% | +0.0 | n/a | 0 / 0 / 2 | no |
| temporal-reasoning | 100.0% | 100.0% | +0.0 | n/a | 0 / 0 / 2 | no |

Gained all evidence (0): none

Lost all evidence (0): none

## Stage times

| Stage | Baseline | Candidate |
|---|---:|---:|
| build-locomo | 36 min 12 s | 35 min 13 s |
| build-longmemeval | 75 min 31 s | 74 min 01 s |
| gate-locomo | 5 min 20 s | 5 min 20 s |
| gate-longmemeval | 11 s | 12 s |

Whole run: 75 min 41 s against 74 min 13 s. The mid tier is a screen: see the limits in either report.
