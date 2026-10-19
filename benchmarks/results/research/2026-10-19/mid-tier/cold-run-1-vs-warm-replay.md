# Mid tier comparison

Baseline `baseline-cold` (commit `ec2a7022`, cold) against candidate `baseline-warm` (commit `ec2a7022`, partly warm). Candidate settings: the defaults. The engine source is identical.

- Warning: The baseline extracted live (1,187 calls, cold). Model output is close to repeatable but not exact, so part of any difference can be that noise, not the change. Compare against a baseline that replayed the same cache.
- Warning: The candidate extracted live (1 calls, partly warm). Model output is close to repeatable but not exact, so part of any difference can be that noise, not the change. Compare against a baseline that replayed the same cache.

## LoCoMo

1,540 questions, 1,536 with annotated evidence. Intervals are 95% of the paired change; a change is clear of zero only when the questions and the conversations intervals both exclude zero.

| Measure | Baseline | Candidate | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| all evidence packed | 83.1% | 83.1% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 1536 | no |
| evidence recall | 88.9% | 88.9% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 1536 | no |
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
| build-locomo | 15 min 23 s | 7 min 14 s |
| build-longmemeval | 24 min 21 s | 11 min 42 s |
| gate-locomo | 3 min 10 s | 4 min 27 s |
| gate-longmemeval | 10 s | 10 s |

Whole run: 24 min 31 s against 11 min 52 s. The mid tier is a screen: see the limits in either report.
