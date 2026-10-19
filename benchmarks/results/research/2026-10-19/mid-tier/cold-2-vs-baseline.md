# Mid tier comparison

Baseline `main-7f9ce556` (commit `6d741a9a`, warm) against candidate `cold-2` (commit `eda1bc06`, cold). Candidate settings: the defaults. The engine source is identical.

- Warning: The candidate extracted live (1,181 calls, cold). Model output is close to repeatable but not exact, so part of any difference can be that noise, not the change. Compare against a baseline that replayed the same cache.

## LoCoMo

1,540 questions, 1,536 with annotated evidence. Intervals are 95% of the paired change; a change is clear of zero only when the questions and the conversations intervals both exclude zero.

| Measure | Baseline | Candidate | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| all evidence packed | 83.1% | 83.5% | +0.4 | -0.1 to +1.0 | -0.1 to +1.0 | 12 / 6 / 1518 | no |
| evidence recall | 88.9% | 89.1% | +0.2 | -0.3 to +0.7 | -0.3 to +0.8 | 22 / 17 / 1497 | no |
| evidence in top 25 | 70.0% | 70.0% | -0.0 | -0.7 to +0.6 | -0.9 to +1.0 | 21 / 25 / 1490 | no |
| all evidence returned | 98.3% | 98.3% | +0.0 | -0.3 to +0.3 | -0.3 to +0.3 | 2 / 2 / 1532 | no |
| projected accuracy | 77.0% | 77.3% | +0.3 | -0.1 to +0.7 | -0.1 to +0.7 | 12 / 6 / 1522 | no |

All evidence packed, by category:

| Category | Baseline | Candidate | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| multi-hop | 50.0% | 51.4% | +1.4 | +0.0 to +3.2 | +0.0 to +2.7 | 5 / 1 / 276 | no |
| open-domain | 56.5% | 55.4% | -1.1 | -3.3 to +0.0 | -3.4 to +0.0 | 0 / 1 / 91 | no |
| single-hop | 94.3% | 94.6% | +0.4 | -0.4 to +1.1 | -0.2 to +1.1 | 6 / 3 / 832 | no |
| temporal | 90.7% | 90.7% | +0.0 | -0.9 to +0.9 | -0.9 to +0.9 | 1 / 1 / 319 | no |

Gained all evidence (12): conv-26-q0011, conv-26-q0090, conv-26-q0121, conv-30-q0081, conv-41-q0025, conv-43-q0061, conv-44-q0015, conv-44-q0105, conv-47-q0001, conv-48-q0015, conv-48-q0080, conv-50-q0152

Lost all evidence (6): conv-42-q0084, conv-43-q0129, conv-47-q0003, conv-49-q0110, conv-50-q0027, conv-50-q0130

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
| build-locomo | 6 min 19 s | 21 min 45 s |
| build-longmemeval | 10 min 49 s | 34 min 05 s |
| gate-locomo | 4 min 21 s | 3 min 47 s |
| gate-longmemeval | 12 s | 11 s |

Whole run: 11 min 00 s against 34 min 17 s. The mid tier is a screen: see the limits in either report.
