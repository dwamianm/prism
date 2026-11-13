# Mid tier comparison

Baseline `t27-main-classic` (commit `56b51d53`, reused) against candidate `t27-main-route-v4` (commit `7472a46f`, reused). Candidate settings: {"memory_mode": "classic", "enable_relation_routing": true}. The engine source differs.

- Warning: The candidate ran on a working tree with uncommitted changes.
- Warning: The baseline reused the packs of the build main-0.17.0a6. A change that acts when memories are stored is not measured.
- Warning: The candidate reused the packs of the build main-0.17.0a6. A change that acts when memories are stored is not measured.

## LoCoMo

1,540 questions, 1,536 with annotated evidence. Intervals are 95% of the paired change; a change is clear of zero only when the questions and the conversations intervals both exclude zero.

| Measure | Baseline | Candidate | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| all evidence packed | 83.6% | 84.0% | +0.4 | +0.1 to +0.7 | +0.1 to +1.0 | 6 / 0 / 1530 | yes |
| evidence recall | 89.2% | 89.5% | +0.3 | +0.1 to +0.5 | +0.0 to +0.7 | 9 / 0 / 1527 | yes |
| evidence in top 25 | 70.4% | 70.3% | -0.1 | -0.3 to -0.0 | -0.3 to -0.0 | 0 / 4 / 1532 | yes |
| all evidence returned | 98.0% | 98.0% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 1536 | no |
| projected accuracy | 77.4% | 77.6% | +0.3 | +0.1 to +0.5 | +0.0 to +0.7 | 6 / 0 / 1534 | yes |

All evidence packed, by category:

| Category | Baseline | Candidate | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| multi-hop | 51.8% | 52.8% | +1.1 | +0.0 to +2.5 | +0.3 to +2.3 | 3 / 0 / 279 | no |
| open-domain | 55.4% | 55.4% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 92 | no |
| single-hop | 94.5% | 94.5% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 841 | no |
| temporal | 91.0% | 91.9% | +0.9 | +0.0 to +2.2 | +0.0 to +3.1 | 3 / 0 / 318 | no |

Gained all evidence (6): conv-30-q0018, conv-41-q0040, conv-44-q0002, conv-44-q0045, conv-44-q0046, conv-44-q0047

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
| build-locomo | 0 s | 0 s |
| build-longmemeval | 0 s | 0 s |
| gate-locomo | 2 min 26 s | 2 min 45 s |
| gate-longmemeval | 20 s | 25 s |

Whole run: 2 min 26 s against 2 min 45 s. The mid tier is a screen: see the limits in either report.

