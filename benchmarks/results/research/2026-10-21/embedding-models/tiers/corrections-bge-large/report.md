# Corrections probe: corrections-v1

Reference time 2026-05-10T12:00:00+00:00. Each probe question makes one `retrieve()` call.

## Path: ingest

Extraction model `deepseek-v4.1-flash:cloud`; settings `{"overrides": {}, "speaker": true, "organize": false}`; 0 extraction calls.

### Riley plans to move to Austin, then chooses Denver (changed mind)

| Fact | Value |
|---|---|
| new value active | True |
| active records with only the old value | 3 |
| extracted claims with only the old value | 2 |
| of them superseded | 0 |
| context lines with only the old value | 1 |
| context lines with the new value | 1 |
| first context line shows | old |
| old-only lines in the context | 1 |
| old value shown before the new value | True |

Records that mention its values:

| Type | State | Text | Replaced by |
|---|---|---|---|
| decision | tentative | We → moving_to → Denver |  |
| decision | tentative | I → NOT turned_down → the Austin job |  |
| note | tentative | Change of plans: I turned down the Austin job. We're moving to Denver instead, and I start at a hospital there in July. |  |
| fact | tentative | I → planning_to_move_to → Austin |  |
| note | tentative | Big news: I'm planning to move to Austin this summer. A hospital there offered me a day-shift position. |  |

Context for "Where is Riley moving?" (17 records, 643/4096 tokens), lines that mention its values:

3. `- [2026-03-02 18:04] "Riley": "Big news: I'm planning to move to Austin this summer. A hospital there offered me a day-shift position."`
6. `- [2026-04-14 19:30] "Riley": "Change of plans: I turned down the Austin job. We're moving to Denver instead, and I start at a hospital there in July."`

### Jo's birthday is given as March 3, then corrected to March 13 in a later session (wrong fact fixed later)

| Fact | Value |
|---|---|
| new value active | True |
| active records with only the old value | 3 |
| extracted claims with only the old value | 2 |
| of them superseded | 0 |
| context lines with only the old value | 1 |
| context lines with the new value | 1 |
| first context line shows | old |
| old-only lines in the context | 1 |
| old value shown before the new value | True |

Records that mention its values:

| Type | State | Text | Replaced by |
|---|---|---|---|
| fact | tentative | Jo → birthday → March 13 |  |
| note | tentative | Yes, soon. And sorry, I gave you Jo's birthday wrong last time. It's actually March 13. |  |
| fact | tentative | Jo → birthday → March 3 |  |
| fact | tentative | Jo → is_sister_of → I |  |
| note | tentative | Also, my sister Jo's birthday is March 3. Can you remind me to send her something before then? |  |

Context for "When is Jo's birthday?" (17 records, 702/4096 tokens), lines that mention its values:

1. `- [2026-03-02 18:08] "Riley": "Also, my sister Jo's birthday is March 3. Can you remind me to send her something before then?"`
2. `- [2026-04-14 19:34] "Riley": "Yes, soon. And sorry, I gave you Jo's birthday wrong last time. It's actually March 13."`

### Riley says black coffee and oat milk in the same session (conflict in one session)

| Fact | Value |
|---|---|
| active records per value | {"black": 2, "oat milk": 2} |
| context lines per value | {"black": 1, "oat milk": 1} |
| contradicts edges | 0 |

Records that mention its values:

| Type | State | Text | Replaced by |
|---|---|---|---|
| preference | tentative | I → takes_in_coffee → oat milk |  |
| note | tentative | Not really. Though honestly, I take oat milk in my coffee every morning. |  |
| fact | tentative | I → drinks → coffee black |  |
| note | tentative | I'm trying to cut back on sugar. I drink my coffee black, always have. |  |

Context for "How does Riley take coffee?" (17 records, 643/4096 tokens), lines that mention its values:

3. `- [2026-03-20 08:00] "Riley": "I'm trying to cut back on sugar. I drink my coffee black, always have."`
4. `- [2026-03-20 08:04] "Riley": "Not really. Though honestly, I take oat milk in my coffee every morning."`

### Riley is a nurse; no correction touches it (control)

| Fact | Value |
|---|---|
| active records per value | {"nurse": 2} |
| context lines per value | {"nurse": 1} |
| contradicts edges | 0 |

Records that mention its values:

| Type | State | Text | Replaced by |
|---|---|---|---|
| fact | tentative | I → works_as → ER nurse |  |
| note | tentative | Hi, I'm Riley. I work as an ER nurse in Seattle, mostly night shifts. |  |

Context for "What does Riley do for work?" (17 records, 643/4096 tokens), lines that mention its values:

1. `- [2026-03-02 18:00] "Riley": "Hi, I'm Riley. I work as an ER nurse in Seattle, mostly night shifts."`

## Path: explicit

### Riley plans to move to Austin, then chooses Denver (changed mind)

| Fact | Value |
|---|---|
| new value active | True |
| active records with only the old value | 0 |
| extracted claims with only the old value | 0 |
| of them superseded | 0 |
| context lines with only the old value | 0 |
| context lines with the new value | 1 |
| first context line shows | new |
| old-only lines in the context | 0 |
| old value shown before the new value | False |

Records that mention its values:

| Type | State | Text | Replaced by |
|---|---|---|---|
| note | tentative | Riley is moving to Denver and starts at a hospital there in July. |  |
| note | superseded | Riley plans to move to Austin this summer. | Riley is moving to Denver and starts at a hospital there in July. |

Edges: Riley is moving to Denver and starts at a hospital there in July. supersedes Riley plans to move to Austin this summer.

Context for "Where is Riley moving?" (5 records, 172/4096 tokens), lines that mention its values:

1. `- [2026-04-14 19:30] "Riley is moving to Denver and starts at a hospital there in July."`

### Jo's birthday is given as March 3, then corrected to March 13 in a later session (wrong fact fixed later)

| Fact | Value |
|---|---|
| new value active | True |
| active records with only the old value | 0 |
| extracted claims with only the old value | 0 |
| of them superseded | 0 |
| context lines with only the old value | 0 |
| context lines with the new value | 1 |
| first context line shows | new |
| old-only lines in the context | 0 |
| old value shown before the new value | False |

Records that mention its values:

| Type | State | Text | Replaced by |
|---|---|---|---|
| note | tentative | Riley's sister Jo's birthday is March 13. |  |
| note | superseded | Riley's sister Jo's birthday is March 3. | Riley's sister Jo's birthday is March 13. |

Edges: Riley's sister Jo's birthday is March 13. supersedes Riley's sister Jo's birthday is March 3.

Context for "When is Jo's birthday?" (5 records, 231/4096 tokens), lines that mention its values:

1. `- [2026-04-14 19:34] "Riley's sister Jo's birthday is March 13."`

### Riley says black coffee and oat milk in the same session (conflict in one session)

| Fact | Value |
|---|---|
| active records per value | {"black": 1, "oat milk": 1} |
| context lines per value | {"black": 1, "oat milk": 1} |
| contradicts edges | 0 |

Records that mention its values:

| Type | State | Text | Replaced by |
|---|---|---|---|
| note | tentative | Riley takes oat milk in coffee every morning. |  |
| note | tentative | Riley drinks coffee black. |  |

Context for "How does Riley take coffee?" (5 records, 172/4096 tokens), lines that mention its values:

1. `- [2026-03-20 08:04] "Riley takes oat milk in coffee every morning."`
2. `- [2026-03-20 08:00] "Riley drinks coffee black."`

### Riley is a nurse; no correction touches it (control)

| Fact | Value |
|---|---|
| active records per value | {"nurse": 1} |
| context lines per value | {"nurse": 1} |
| contradicts edges | 0 |

Records that mention its values:

| Type | State | Text | Replaced by |
|---|---|---|---|
| note | tentative | Riley works as an ER nurse in Seattle. |  |

Context for "What does Riley do for work?" (5 records, 172/4096 tokens), lines that mention its values:

1. `- [2026-03-02 18:00] "Riley works as an ER nurse in Seattle."`
