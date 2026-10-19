# Mid tier comparison

Baseline `main-7f9ce556` (commit `6d741a9a`, warm) against candidate `main-975dba21` (commit `5ad7a9e7`, warm). Candidate settings: the defaults. The engine source differs.

## LoCoMo

1,540 questions, 1,536 with annotated evidence. Intervals are 95% of the paired change; a change is clear of zero only when the questions and the conversations intervals both exclude zero.

| Measure | Baseline | Candidate | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| all evidence packed | 83.1% | 83.1% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 1536 | no |
| evidence recall | 88.9% | 88.9% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 1536 | no |
| evidence in top 25 | 70.0% | 69.9% | -0.1 | -0.3 to +0.1 | -0.3 to +0.1 | 2 / 3 / 1531 | no |
| all evidence returned | 98.3% | 98.3% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 1536 | no |
| projected accuracy | 77.0% | 77.0% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 1540 | no |

All evidence packed, by category:

| Category | Baseline | Candidate | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| multi-hop | 50.0% | 50.0% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 282 | no |
| open-domain | 56.5% | 56.5% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 92 | no |
| single-hop | 94.3% | 94.3% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 841 | no |
| temporal | 90.7% | 90.7% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 321 | no |

Gained all evidence (0): none

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
| build-locomo | 6 min 19 s | 6 min 39 s |
| build-longmemeval | 10 min 49 s | 11 min 45 s |
| gate-locomo | 4 min 21 s | 4 min 46 s |
| gate-longmemeval | 12 s | 12 s |

Whole run: 11 min 00 s against 11 min 57 s. The mid tier is a screen: see the limits in either report.
