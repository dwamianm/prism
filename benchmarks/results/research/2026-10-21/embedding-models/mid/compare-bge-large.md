# Mid tier comparison

Baseline `emb366-bge-small` (commit `349a9926`, warm) against candidate `emb366-bge-large-b` (commit `349a9926`, warm). Candidate settings: the defaults. The engine source is identical.

- Warning: The baseline ran on a working tree with uncommitted changes.
- Warning: The candidate ran on a working tree with uncommitted changes.

## LoCoMo

1,540 questions, 1,536 with annotated evidence. Intervals are 95% of the paired change; a change is clear of zero only when the questions and the conversations intervals both exclude zero.

| Measure | Baseline | Candidate | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| all evidence packed | 83.6% | 84.4% | +0.8 | -0.1 to +1.8 | +0.2 to +1.5 | 34 / 21 / 1481 | no |
| evidence recall | 89.2% | 90.0% | +0.8 | +0.1 to +1.6 | +0.3 to +1.5 | 58 / 37 / 1441 | yes |
| evidence in top 25 | 70.3% | 75.1% | +4.8 | +3.6 to +6.2 | +3.9 to +5.6 | 147 / 34 / 1355 | yes |
| all evidence returned | 98.0% | 98.9% | +0.8 | +0.4 to +1.3 | +0.3 to +1.4 | 13 / 0 / 1523 | yes |
| projected accuracy | 77.4% | 78.0% | +0.6 | -0.1 to +1.2 | +0.1 to +1.1 | 34 / 21 / 1485 | no |

All evidence packed, by category:

| Category | Baseline | Candidate | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| multi-hop | 51.8% | 52.5% | +0.7 | -2.8 to +4.3 | -2.4 to +3.6 | 14 / 12 / 256 | no |
| open-domain | 55.4% | 56.5% | +1.1 | -3.3 to +6.5 | -4.9 to +6.8 | 3 / 2 / 87 | no |
| single-hop | 94.5% | 95.4% | +0.8 | -0.1 to +1.9 | -0.3 to +2.3 | 12 / 5 / 824 | no |
| temporal | 91.0% | 91.9% | +0.9 | -0.6 to +2.5 | +0.0 to +2.3 | 5 / 2 / 314 | no |

Gained all evidence (34): conv-26-q0032, conv-26-q0047, conv-26-q0078, conv-26-q0105, conv-26-q0139, conv-26-q0144, conv-30-q0075, conv-30-q0081, conv-41-q0047, conv-42-q0011, conv-42-q0184, conv-43-q0005, conv-43-q0021, conv-43-q0035, conv-43-q0059, conv-44-q0027, conv-44-q0040, conv-44-q0044, conv-47-q0008, conv-47-q0027, and 14 more

Lost all evidence (21): conv-26-q0002, conv-26-q0048, conv-26-q0050, conv-26-q0051, conv-30-q0003, conv-41-q0035, conv-41-q0072, conv-42-q0005, conv-42-q0119, conv-44-q0018, conv-47-q0003, conv-47-q0026, conv-47-q0051, conv-47-q0054, conv-50-q0011, conv-50-q0041, conv-50-q0057, conv-50-q0070, conv-50-q0088, conv-50-q0119, and 1 more

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
| build-locomo | 36 min 12 s | 39 min 55 s |
| build-longmemeval | 75 min 31 s | 83 min 17 s |
| gate-locomo | 5 min 20 s | 5 min 39 s |
| gate-longmemeval | 11 s | 16 s |

Whole run: 75 min 41 s against 83 min 33 s. The mid tier is a screen: see the limits in either report.
