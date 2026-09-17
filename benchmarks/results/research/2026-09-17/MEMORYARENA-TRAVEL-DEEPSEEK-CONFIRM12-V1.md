# MemoryArena travel confirmation V1

## Outcome

The fresh, preregistered 12-group confirmation completed all 156 traveler-arm
executions with zero agent failures and complete group/person coverage. PRME
failed both registered non-inferiority gates, so the V11 confirmed-plan policy
is rejected as a generally non-inferior replacement for native full history.

| Arm | Strict PS | Strict SPS | Strict SR | Passed slots | Input tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| Native full history | 7.6923 | 80.2416 | 0.0000 | 366 / 459 | 1,610,296 |
| PRME | 0.0000 | 65.1182 | 0.0000 | 310 / 459 | 1,292,091 |
| PRME minus native | -7.6923 | -15.1234 | 0.0000 | -56 | -19.76% |

The registered floors were -5 points for strict PS and strict SPS. Development
V11 had passed at +6.4103 PS and -4.9371 SPS, but neither margin generalized to
the untouched cohort. The confirmation result supersedes that development gate
for product claims. PRME's context remained materially smaller, but the saved
tokens did not preserve enough task quality.

## Post-hoc failure localization

The immutable submissions isolate a large representation-sensitive failure in
group 56. Native full history passed 40 of 45 changed constraint slots; PRME
passed none. The PRME context did contain the complete base request and plan,
including values such as `Salt Lake City(Utah)`. Its generated plans repeatedly
removed the parenthesized state suffix, yielding values such as `Salt Lake City`
that failed the preregistered full-string metric. This one group contributes 40
of the net 56-slot deficit.

The failure is not explained by missing retrieval. It shows that placing a
confirmed plan inside the current prompt did not make the model preserve exact
surface values as reliably as native assistant history. The remaining groups
were mixed: PRME gained 11 slots in group 224 and three in group 71, while losing
nine in group 146 and six in group 151. Across the cohort, 280 changed slots
were correct in both arms, 30 only in PRME, 86 only in native history, and 63 in
neither.

This diagnosis was performed after the registered decision and cannot rescue
the failed gate. The cohort is spent. Any change prompted by it must be developed
on the already examined development cohort and confirmed on newly selected
groups.

## Decision

Do not claim task-level confirmation for the current adapter or promote the V11
policy as a general default. The next candidate should make the context contract
explicitly preserve copied values verbatim, including parenthesized geographic
qualifiers, while retaining complete source records. It needs a fresh registered
confirmation after development.

## Evidence identity

- Registration SHA-256:
  `1a77d492bfdcb7218a03dcccace1f6795bdc55ffab1d2ad644e531dbda65e9f3`
- Result file SHA-256:
  `55e7a7d46d865d0e3e79785f5013262878eab24e511be88ab9c5ad3cc68aa692`
- Canonical result SHA-256:
  `a7d2e17ec23be9c40c10d8085781cea02c4890f1d88dbdb61b589eb5a006aa1e`
- Registered PRME revision:
  `c6751d647d338008deba10870beb3d79a84f6cfe`
- Pinned MemoryArena revision:
  `6cd9de14b71915e39ac742a20dc33785e14b6aab`

## Limits

The hosted model alias does not pin remote weights, and each arm used one
generation. Strict normalized-string scoring measures exact task outputs rather
than semantic itinerary validity. The trial establishes a failed confirmation
for this exact protocol and cohort; it does not rank general memory systems.
