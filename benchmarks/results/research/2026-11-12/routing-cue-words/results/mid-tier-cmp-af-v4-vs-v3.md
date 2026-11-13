# Mid tier comparison

Baseline `t27-af-v3` (commit `56b51d53`, reused) against candidate `t27-af-v4` (commit `7472a46f`, reused). Candidate settings: the defaults. The engine source differs.

- Warning: The candidate ran on a working tree with uncommitted changes.
- Warning: The baseline reused the packs of the build address-first. A change that acts when memories are stored is not measured.
- Warning: The candidate reused the packs of the build address-first. A change that acts when memories are stored is not measured.

## LoCoMo

1,540 questions, 1,536 with annotated evidence. Intervals are 95% of the paired change; a change is clear of zero only when the questions and the conversations intervals both exclude zero.

| Measure | Baseline | Candidate | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| all evidence packed | 84.4% | 84.4% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 1536 | no |
| evidence recall | 90.0% | 90.0% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 1536 | no |
| evidence in top 25 | 69.0% | 69.5% | +0.5 | +0.2 to +0.8 | +0.1 to +0.9 | 8 / 0 / 1528 | yes |
| all evidence returned | 98.0% | 98.0% | +0.1 | +0.0 to +0.2 | +0.0 to +0.2 | 1 / 0 / 1535 | no |
| projected accuracy | 78.0% | 78.0% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 1540 | no |

All evidence packed, by category:

| Category | Baseline | Candidate | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| multi-hop | 52.5% | 52.5% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 282 | no |
| open-domain | 53.3% | 53.3% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 92 | no |
| single-hop | 95.5% | 95.5% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 841 | no |
| temporal | 92.5% | 92.5% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 321 | no |

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
| build-locomo | 0 s | 0 s |
| build-longmemeval | 0 s | 0 s |
| gate-locomo | 3 min 00 s | 2 min 46 s |
| gate-longmemeval | 26 s | 21 s |

Whole run: 3 min 00 s against 2 min 46 s. The mid tier is a screen: see the limits in either report.

