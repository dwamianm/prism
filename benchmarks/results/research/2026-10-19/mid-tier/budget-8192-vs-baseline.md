# Mid tier comparison

Baseline `main-7f9ce556` (commit `6d741a9a`, warm) against candidate `budget-8192` (commit `eda1bc06`, reused). Candidate settings: {"packing": {"token_budget": 8192}}. The engine source is identical.

- Warning: The candidate reused the packs of the build main-7f9ce556. A change that acts when memories are stored is not measured.

## LoCoMo

1,540 questions, 1,536 with annotated evidence. Intervals are 95% of the paired change; a change is clear of zero only when the questions and the conversations intervals both exclude zero.

| Measure | Baseline | Candidate | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| all evidence packed | 83.1% | 88.9% | +5.8 | +4.6 to +7.0 | +4.7 to +6.8 | 91 / 2 / 1443 | yes |
| evidence recall | 88.9% | 93.5% | +4.6 | +3.7 to +5.5 | +3.8 to +5.3 | 133 / 3 / 1400 | yes |
| evidence in top 25 | 70.0% | 70.0% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 1536 | no |
| all evidence returned | 98.3% | 98.3% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 1536 | no |
| projected accuracy | 77.0% | 80.8% | +3.7 | +3.0 to +4.5 | +3.1 to +4.4 | 91 / 2 / 1447 | yes |

All evidence packed, by category:

| Category | Baseline | Candidate | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| multi-hop | 50.0% | 66.7% | +16.7 | +12.4 to +21.3 | +12.6 to +20.7 | 48 / 1 / 233 | yes |
| open-domain | 56.5% | 68.5% | +12.0 | +5.4 to +18.5 | +6.6 to +17.6 | 11 / 0 / 81 | yes |
| single-hop | 94.3% | 96.2% | +1.9 | +1.0 to +3.0 | +0.8 to +3.0 | 17 / 1 / 823 | yes |
| temporal | 90.7% | 95.3% | +4.7 | +2.5 to +7.2 | +2.0 to +7.7 | 15 / 0 / 306 | yes |

Gained all evidence (91): conv-26-q0007, conv-26-q0011, conv-26-q0015, conv-26-q0032, conv-26-q0038, conv-26-q0047, conv-26-q0055, conv-26-q0059, conv-26-q0070, conv-26-q0105, conv-26-q0146, conv-30-q0027, conv-30-q0031, conv-30-q0044, conv-30-q0075, conv-30-q0081, conv-41-q0057, conv-41-q0072, conv-41-q0107, conv-42-q0011, and 71 more

Lost all evidence (2): conv-47-q0003, conv-49-q0110

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
| build-locomo | 6 min 19 s | 0 s |
| build-longmemeval | 10 min 49 s | 0 s |
| gate-locomo | 4 min 21 s | 2 min 57 s |
| gate-longmemeval | 12 s | 11 s |

Whole run: 11 min 00 s against 2 min 57 s. The mid tier is a screen: see the limits in either report.
