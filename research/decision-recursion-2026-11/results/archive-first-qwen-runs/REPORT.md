# Results

Numbers from run1 of each arm; run2 repeats it from a cold cache with the same seed.

## Experiment R (read)

Columns: chat store() and ingest() answer at rank 1 and all answers in top 3 (of 13); two hops rank 1 (placement first) and both records in top 5 (of 8); churn at 10 minutes, newest place first (of 18); flood rare event at rank 1 and in top 5 (of 8); unknown said when it should be (cat through store() plus 6 unseen, of 7) and the cat through ingest(); false unknowns (of 60 answerable); old above new (chat store / ingest of 4 each, churn of 18); decisions per question (median / max); added ms per question (median / p95).

| arm | model | store r1 | store top3 | ingest r1 | ingest top3 | hops r1 | hops both5 | churn r1 | flood r1 | flood top5 | unknown /7 | cat ingest | false unk /60 | old>new s/i/c | calls | ms | READ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R0 | random | 5 | 6 | 7 | 6 | 0 | 0 | 4 | 6 | 6 | 0 | no | 0 | 2/1/14 | 0 / 0 | 0 / 0 | fail |
| R1 | random | 2 | 4 | 3 | 2 | 0 | 0 | 0 | 3 | 4 | 1 | no | 1 | 3/1/17 | 10 / 10 | 0 / 0 | fail |
| R2 | random | 2 | 4 | 3 | 2 | 0 | 0 | 0 | 2 | 3 | 1 | no | 1 | 3/1/17 | 10 / 21 | 0 / 96.1 | fail |
| R3 | random | 0 | 3 | 1 | 4 | 0 | 0 | 1 | 2 | 5 | 1 | no | 1 | 2/2/15 | 14 / 24 | 0 / 96.1 | fail |
| R0 | qwen3.5-4b | 5 | 6 | 7 | 6 | 0 | 0 | 4 | 6 | 6 | 0 | no | 0 | 2/1/14 | 0 / 0 | 0 / 0 | fail |
| R1 | qwen3.5-4b | 5 | 6 | 4 | 5 | 0 | 0 | 4 | 6 | 6 | 7 | yes | 22 | 2/1/14 | 10 / 10 | 979.8 / 1236.7 | fail |
| R2 | qwen3.5-4b | 5 | 6 | 4 | 5 | 0 | 0 | 4 | 6 | 6 | 7 | yes | 22 | 2/1/14 | 10 / 10 | 979.8 / 1236.7 | fail |
| R3 | qwen3.5-4b | 5 | 6 | 3 | 5 | 0 | 0 | 6 | 6 | 6 | 7 | yes | 22 | 2/2/12 | 10 / 28 | 1046.7 / 2823.3 | fail |
| R0 | qwen3.5-9b | 5 | 6 | 7 | 6 | 0 | 0 | 4 | 6 | 6 | 0 | no | 0 | 2/1/14 | 0 / 0 | 0 / 0 | fail |
| R1 | qwen3.5-9b | 8 | 8 | 7 | 6 | 0 | 0 | 4 | 6 | 6 | 7 | yes | 8 | 2/1/14 | 10 / 10 | 1498.5 / 1614.4 | fail |
| R2 | qwen3.5-9b | 8 | 8 | 7 | 6 | 0 | 0 | 4 | 6 | 6 | 7 | yes | 8 | 2/1/14 | 10 / 10 | 1498.5 / 1614.4 | fail |
| R3 | qwen3.5-9b | 9 | 8 | 7 | 6 | 0 | 0 | 14 | 6 | 6 | 7 | yes | 8 | 2/1/4 | 10 / 28 | 1555.4 / 3410.4 | fail |

Bars per arm (unknown >= 6 of 7; false unknowns <= 1; store rank-1 gain over R0 >= 4; median added ms <= 300):

- random R1: rank-1 gain -3; unknown_right_at_least_6_of_7 NO, false_unknowns_at_most_1 yes, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 yes
- random R2: rank-1 gain -3; unknown_right_at_least_6_of_7 NO, false_unknowns_at_most_1 yes, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 yes
- random R3: rank-1 gain -5; unknown_right_at_least_6_of_7 NO, false_unknowns_at_most_1 yes, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 yes
- qwen3.5-4b R1: rank-1 gain +0; unknown_right_at_least_6_of_7 yes, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 NO
- qwen3.5-4b R2: rank-1 gain +0; unknown_right_at_least_6_of_7 yes, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 NO
- qwen3.5-4b R3: rank-1 gain +0; unknown_right_at_least_6_of_7 yes, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 NO
- qwen3.5-9b R1: rank-1 gain +3; unknown_right_at_least_6_of_7 yes, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 NO
- qwen3.5-9b R2: rank-1 gain +3; unknown_right_at_least_6_of_7 yes, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 NO
- qwen3.5-9b R3: rank-1 gain +4; unknown_right_at_least_6_of_7 yes, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 yes, median_added_ms_at_most_300 NO

Decision counts in run1 (judgments by answer; other tags by count):

- random: {'R1.judge=no': 193, 'R1.judge=partly': 262, 'R1.judge=yes': 225, 'R2.judge=no': 9, 'R2.judge=yes': 10, 'R2.pick_record': 9, 'R2.pick_word': 10, 'R3.conflict=no, both can be true': 90, 'R3.conflict=yes, they conflict': 82, 'R3.true_now': 82}
- qwen3.5-4b: {'R1.judge=no': 554, 'R1.judge=yes': 126, 'R3.conflict=no, both can be true': 4, 'R3.conflict=yes, they conflict': 84, 'R3.true_now': 84}
- qwen3.5-9b: {'R1.judge=no': 534, 'R1.judge=yes': 146, 'R3.conflict=no, both can be true': 35, 'R3.conflict=yes, they conflict': 59, 'R3.true_now': 59}

## Experiment W (write)

| arm | model | retired correctly /10 | retired wrongly | wrong ids | calls per new fact (mean / max) | ms per new fact (mean / p95) | WRITE |
|---|---|---|---|---|---|---|---|
| W0 | random | 0 | 0 | - | 0 / 0 | 0 / 0 | fail |
| W1 | random | 8 | 15 | F03, F05, F06, F07, F11, F15*, F16, F21, F22, F23, F24, F25*, F26, F27, F28* | 1.86 / 5 | 0 / 0 | fail |
| W2 | random | 8 | 15 | F03, F05, F06, F07, F11, F15*, F16, F21, F22, F23, F24, F25*, F26, F27, F28* | 1.93 / 9 | 0 / 0 | fail |
| W0 | qwen3.5-4b | 0 | 0 | - | 0 / 0 | 0 / 0 | fail |
| W1 | qwen3.5-4b | 10 | 13 | F03, F05, F06, F07, F11, F15*, F21, F22, F23, F24, F25*, F26, F27 | 2.33 / 8 | 248.2 / 635.2 | fail |
| W2 | qwen3.5-4b | 10 | 14 | F03, F05, F06, F07, F11, F15*, F16, F21, F22, F23, F24, F25*, F26, F27 | 1.51 / 8 | 171.6 / 593.6 | fail |
| W0 | qwen3.5-9b | 0 | 0 | - | 0 / 0 | 0 / 0 | fail |
| W1 | qwen3.5-9b | 6 | 0 | - | 6.37 / 21 | 1057.8 / 3536.5 | PASS |
| W2 | qwen3.5-9b | 6 | 0 | - | 7.4 / 43 | 1314.2 / 3839.2 | PASS |

A * marks a fact the key calls contestable.

Post-hoc, not a bar: of the key's retirements that happened, how many were triggered by the new fact that really ends them (write_causes_posthoc.json, written after the first 4b run).

- random W1: 1 of 8 for the right reason (F08)
- random W2: 0 of 8 for the right reason (-)
- qwen3.5-4b W1: 5 of 10 for the right reason (F01, F08, F09, F14, F19)
- qwen3.5-4b W2: 2 of 10 for the right reason (F14, F19)
- qwen3.5-9b W1: 6 of 6 for the right reason (F01, F08, F09, F10, F14, F19)
- qwen3.5-9b W2: 6 of 6 for the right reason (F01, F08, F09, F10, F14, F19)

## Repeat from a cold cache

Same prompt, different choice: the model's own repeatability. Sequence identical: the whole run repeated. PRME top 5 differs: questions where PRME's own order changed between the two fresh pack builds, which changes the prompts asked after it.

| model | same prompt, different choice (read / write) | read sequence identical | write sequence identical | PRME top 5 differs | read wall s (run1 / run2) |
|---|---|---|---|---|---|
| random | 0 of 746 / 0 of 104 | no | yes | chat_store:pet, chat_store:sister, chat_ingest:sister | 25.4 / 26.8 |
| qwen3.5-4b | 0 of 643 / 0 of 113 | no | yes | chat_store:pet, chat_store:sister, chat_ingest:sister | 90.1 / 88.8 |
| qwen3.5-9b | 0 of 627 / 0 of 318 | no | yes | chat_store:pet, chat_store:sister, chat_ingest:sister, churn-10min-2moves-wool-scarf | 125.9 / 129.5 |
