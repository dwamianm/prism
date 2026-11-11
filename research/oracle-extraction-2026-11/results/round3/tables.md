## Top 3 / top 5 per probe

| Probe | B1 (round 2) | B1s | tx-B1 | M1 | Bar (tx-B1 top 3) | Result |
|---|---|---|---|---|---:|---|
| defects-v1 | 5/7 / 5/7 | 6/7 / 7/7 | 6/7 / 6/7 | 7/7 / 7/7 | 7 | fail |
| chat-v1 | 12/18 / 13/18 | 14/18 / 15/18 | 16/18 / 16/18 | 15/18 / 16/18 | 17 (and M1 + 2) | fail |
| member-v1 | 9/12 / 9/12 | 12/12 / 12/12 | 12/12 / 12/12 | 11/12 / 12/12 | 11 | pass |

## Per question, every row where the four differ (answer rank; tx-B1 path from the receipts)

**defects-v1**

| Question | B1 (round 2) | B1s | tx-B1 | M1 | tx-B1 path |
|---|---:|---:|---:|---:|---|
| Which cities will Alex and Jordan visit in Japan? | 25 | 5 | 6 | 2 | 6 (routed: visits) |
| Which languages does Alex write at work? | 18 | 1 | 1 | 2 | 1 (catalog) |
| How does Alex prefer to travel in Japan? | 2 | 1 | 1 | 3 | 1 (routed: prefers, visits) |

**chat-v1**

| Question | B1 (round 2) | B1s | tx-B1 | M1 | tx-B1 path |
|---|---:|---:|---:|---:|---|
| Where does Dana work now? | 1 | 1 | 1 | 2 | 1 (routed: works_at) |
| What is Dana's job title now? | 1 | 9 | 1 | 12 | 1 (routed: works_at) |
| When did Dana start the new job? | 22 | 4 | 6 | 2 | 6 (catalog) |
| Who was Dana's manager at Northwind? | 1 | 1 | 1 | 3 | 1 (routed: manager_of (manager)) |
| What does Dana's partner do for work? | 2 | 1 | 1 | 2 | 1 (routed: partner_of, works_at) |
| What kind of dog does Dana have, and what is it called? | 21 | 6 | 6 | 3 | 6 (routed: involved_in) |
| What is the name of Dana's niece? | 7 | 1 | 1 | 1 | 1 (catalog) |
| When was Ivy born? | 1 | none | 1 | none | 1 (catalog) |
| Who is Dana's running partner? | 1 | 1 | 1 | 2 | 1 (routed: friend_of, partner_of) |
| What was Dana's half marathon finishing time? | 10 | 2 | 2 | 1 | 2 (catalog) |
| Does Dana drink coffee? | 1 | 1 | 1 | 5 | 1 (catalog) |
| What news did Melanie share after the race? | 4 | 1 | 1 | 2 | 1 (catalog) |
| What is Dana planning for July? | 22 | 1 | 1 | 1 | 1 (catalog) |
| Where will Dana stop overnight on the drive? | 3 | 1 | 1 | 3 | 1 (catalog) |
| Which programming language does Dana prefer? | 2 | 2 | 2 | 3 | 2 (routed: prefers) |

**member-v1**

| Question | B1 (round 2) | B1s | tx-B1 | M1 | tx-B1 path |
|---|---:|---:|---:|---:|---|
| What are the names of Maya's dependents? | 7 | 1 | 1 | 1 | 1 (routed: kin_of (child), partner_of) |
| Do you remember the names of my dependents? | 8 | 1 | 1 | 1 | 1 (routed: kin_of (child), partner_of) |
| What was Maya diagnosed with? | 1 | 1 | 1 | 2 | 1 (catalog) |
| What did I tell you about my health? | 3 | 1 | 1 | 1 | 1 (catalog) |
| When is Maya's trip to Jamaica? | 3 | 1 | 1 | 1 | 1 (catalog) |
| Is Maya expecting a baby? | 1 | 1 | 1 | 3 | 1 (catalog) |
| What life event did Maya mention? | 8 | 3 | 3 | 4 | 3 (catalog) |
| What did Maya's coworker Dana ask about? | 1 | 1 | 1 | 2 | 1 (catalog) |

