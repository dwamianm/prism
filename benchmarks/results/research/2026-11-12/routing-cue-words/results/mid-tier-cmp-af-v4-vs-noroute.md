# Mid tier comparison

Baseline `t27-af-noroute` (commit `56b51d53`, reused) against candidate `t27-af-v4` (commit `7472a46f`, reused). Candidate settings: the defaults. The engine source differs.

- Warning: The candidate ran on a working tree with uncommitted changes.
- Warning: The baseline reused the packs of the build address-first. A change that acts when memories are stored is not measured.
- Warning: The candidate reused the packs of the build address-first. A change that acts when memories are stored is not measured.

## LoCoMo

1,540 questions, 1,536 with annotated evidence. Intervals are 95% of the paired change; a change is clear of zero only when the questions and the conversations intervals both exclude zero.

| Measure | Baseline | Candidate | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| all evidence packed | 84.0% | 84.4% | +0.4 | -0.1 to +0.9 | -0.1 to +0.8 | 10 / 4 / 1522 | no |
| evidence recall | 89.7% | 90.0% | +0.3 | -0.0 to +0.7 | -0.1 to +0.7 | 12 / 6 / 1518 | no |
| evidence in top 25 | 70.0% | 69.5% | -0.5 | -0.8 to -0.2 | -0.9 to -0.2 | 0 / 11 / 1525 | yes |
| all evidence returned | 98.1% | 98.0% | -0.1 | -0.2 to +0.0 | -0.2 to +0.0 | 0 / 1 / 1535 | no |
| projected accuracy | 77.7% | 78.0% | +0.3 | +0.0 to +0.6 | -0.0 to +0.6 | 10 / 4 / 1526 | no |

All evidence packed, by category:

| Category | Baseline | Candidate | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| multi-hop | 51.4% | 52.5% | +1.1 | -0.7 to +2.8 | -0.7 to +2.9 | 5 / 2 / 275 | no |
| open-domain | 55.4% | 53.3% | -2.2 | -5.4 to +0.0 | -5.4 to +0.0 | 0 / 2 / 90 | no |
| single-hop | 95.2% | 95.5% | +0.2 | +0.0 to +0.6 | +0.0 to +0.6 | 2 / 0 / 839 | no |
| temporal | 91.6% | 92.5% | +0.9 | +0.0 to +2.2 | +0.0 to +3.1 | 3 / 0 / 318 | no |

Gained all evidence (10): conv-30-q0018, conv-41-q0040, conv-41-q0105, conv-43-q0021, conv-43-q0038, conv-44-q0045, conv-44-q0046, conv-44-q0047, conv-50-q0019, conv-50-q0119

Lost all evidence (4): conv-42-q0060, conv-44-q0020, conv-47-q0003, conv-50-q0056

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
| build-locomo | 0 s | 0 s |
| build-longmemeval | 0 s | 0 s |
| gate-locomo | 2 min 31 s | 2 min 46 s |
| gate-longmemeval | 23 s | 21 s |

Whole run: 2 min 31 s against 2 min 46 s. The mid tier is a screen: see the limits in either report.

