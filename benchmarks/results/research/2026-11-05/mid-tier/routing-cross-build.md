# Mid tier comparison

Baseline `main-41005d66` (commit `41005d66`, warm) against candidate `routing-41005d66` (commit `41005d66`, warm). Candidate settings: {"enable_relation_routing": true}. The engine source is identical.

## LoCoMo

1,540 questions, 1,536 with annotated evidence. Intervals are 95% of the paired change; a change is clear of zero only when the questions and the conversations intervals both exclude zero.

| Measure | Baseline | Candidate | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| all evidence packed | 83.6% | 83.8% | +0.2 | +0.0 to +0.5 | +0.0 to +0.5 | 3 / 0 / 1533 | no |
| evidence recall | 89.2% | 89.3% | +0.1 | +0.0 to +0.3 | +0.0 to +0.3 | 5 / 0 / 1531 | no |
| evidence in top 25 | 70.2% | 69.9% | -0.3 | -0.6 to +0.0 | -0.6 to +0.1 | 2 / 8 / 1526 | no |
| all evidence returned | 98.0% | 98.0% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 1536 | no |
| projected accuracy | 77.4% | 77.5% | +0.1 | +0.0 to +0.3 | +0.0 to +0.3 | 3 / 0 / 1537 | no |

All evidence packed, by category:

| Category | Baseline | Candidate | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| multi-hop | 51.8% | 52.5% | +0.7 | +0.0 to +1.8 | +0.0 to +1.6 | 2 / 0 / 280 | no |
| open-domain | 55.4% | 55.4% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 92 | no |
| single-hop | 94.5% | 94.5% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 841 | no |
| temporal | 91.0% | 91.3% | +0.3 | +0.0 to +0.9 | +0.0 to +1.0 | 1 / 0 / 320 | no |

Gained all evidence (3): conv-41-q0040, conv-44-q0002, conv-44-q0047

Lost all evidence (0): none

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
| build-locomo | 48 min 20 s | 40 min 60 s |
| build-longmemeval | 102 min 37 s | 91 min 15 s |
| gate-locomo | 8 min 08 s | 7 min 52 s |
| gate-longmemeval | 13 s | 13 s |

Whole run: 102 min 50 s against 91 min 28 s. The mid tier is a screen: see the limits in either report.
