# Round 2 changes

Same blindness rules as round 1: no probe question, no answer key, no results file was read. Only the guide's
Round 2 section, the turns, the relations vocabulary and the assertion form were used.

## defects-v1

- Assertions with a time: 0. The conversation has no date that is certain to the day. "late March next year" is a
  month, so `trip_timing` keeps the words; "before Friday" is a deadline whose Friday is not certain (the turn is
  itself a Friday), so `runbook reviewed by Sam before Friday` keeps its words. Both are listed in the notes.
- Added back: 0. Nothing was left out in round 1 for the mention rule; the refusals there were the old rule applied
  to facts that were written.
- Renamed: 0.
- Refused now: 0.

## chat-v1

- Assertions with a time: 13. "May 17" on the two race involvement facts of 24 February; "last Thursday" as
  2026-03-12 for the birth; "on Monday" as 2026-03-16 for the two interview facts; "May 4" as 2026-05-04 for the
  start at Brightpath; "yesterday" as 2026-04-13 for the Northwind closure; "on Saturday" as 2026-04-11 for the
  long run and both long run distances; "on Sunday" as 2026-05-17 for the race and its result.
- Removed: 1. The `race_date` attribute fact held "May 17" in its value; the date is now a time on the facts it
  belongs to, so the value no longer carries it and the attribute is gone from the attribute list.
- Added back: 0.
- Renamed: 1. `long_run_distance` "10 miles" became "10 mile", the turn's own words, because the literal value
  check refused it ("10 mile long run").
- Refused now: 0.
- Still no time: "last month" for quitting coffee and "last weekend" for the engagement are not certain to a day;
  "July" for the Portland trip and "this summer" for the visit are months or a season. All are in the notes.

## member-v1

- Assertions with a time: 0. "in December" is a month and nothing else in the conversation names a date.
- Restructured: the trip was one entity, `trip to Jamaica`, a name no turn says (the turn says "a trip for us in
  December to Jamaica"). It is replaced by three `visits Jamaica` facts (the owner, Theo, Nico) and
  `trip_month December` on the owner. The `located_in Jamaica` assertion is removed because `visits` already says
  it. Five assertions became four.
- Added back: 0.
- Renamed: 1 beyond that restructure: `marriage` became "getting married", the turn's own words, because the
  object was refused as a name no turn says.
- Refused now: 0. Before the engine's speaker rule landed, the nine assistant-turn plan facts ("Your individual
  deductible is...", "Prenatal visits are covered...", "Oncology visits... are covered", "Dental is a separate
  plan", "Preventive cleanings are covered twice a year") were refused, because the subject is the member and the
  member's name never appears in those turns. The build the checker now runs counts an assistant turn's "your" as
  the speaker, so all nine are accepted unchanged.

## Totals

Every file checks clean on this build: defects-v1 31 of 31, chat-v1 57 of 57 (53 O1, 4 O2), member-v1 23 of
23 (all O1).

| | time | removed | added back | renamed | refused now |
|---|---|---|---|---|---|
| defects-v1 | 0 | 0 | 0 | 0 | 0 |
| chat-v1 | 13 | 1 | 0 | 1 | 0 |
| member-v1 | 0 | 1 (of five restructured) | 0 | 1 | 0 |
