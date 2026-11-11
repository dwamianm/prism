# Results

Numbers from run1 of each arm; run2 repeats it from a cold cache with the same seed.

## Experiment R (read)

Columns: chat store() and ingest() answer at rank 1 and all answers in top 3 (of 13); two hops rank 1 (placement first) and both records in top 5 (of 8); churn at 10 minutes, newest place first (of 18); flood rare event at rank 1 and in top 5 (of 8); unknown said when it should be (cat through store() plus 6 unseen, of 7) and the cat through ingest(); false unknowns (of 60 answerable); old above new (chat store / ingest of 4 each, churn of 18); decisions per question (median / max); added ms per question (median / p95).

| arm | model | store r1 | store top3 | ingest r1 | ingest top3 | hops r1 | hops both5 | churn r1 | flood r1 | flood top5 | unknown /7 | cat ingest | false unk /60 | old>new s/i/c | calls | ms | READ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R0 | random | 5 | 6 | 7 | 6 | 0 | 0 | 4 | 6 | 6 | 0 | no | 0 | 2/1/14 | 0 / 0 | 0 / 0 | fail |
| R1 | random | 2 | 3 | 3 | 2 | 0 | 0 | 0 | 3 | 4 | 1 | no | 1 | 3/1/17 | 10 / 10 | 0 / 0 | fail |
| R2 | random | 2 | 3 | 3 | 2 | 0 | 0 | 0 | 2 | 3 | 1 | no | 1 | 3/1/17 | 10 / 21 | 0 / 91 | fail |
| R3 | random | 0 | 2 | 1 | 4 | 0 | 0 | 1 | 2 | 5 | 1 | no | 1 | 2/2/15 | 14 / 24 | 0 / 91 | fail |
| R2b (post-hoc) | random | 2 | 3 | 3 | 2 | 0 | 0 | 0 | 1 | 1 | 3 | no | 3 | 3/1/17 | 10 / 26 | 0 / 174.6 | fail |
| R4 (post-hoc) | random | 2 | 5 | 3 | 5 | 0 | 0 | 0 | 1 | 2 | 3 | no | 3 | 3/1/18 | 2 / 26 | 0 / 174.6 | fail |
| R0 | clef | 5 | 6 | 7 | 6 | 0 | 0 | 4 | 6 | 6 | 0 | no | 0 | 2/1/14 | 0 / 0 | 0 / 0 | fail |
| R1 | clef | 8 | 8 | 8 | 6 | 0 | 0 | 4 | 6 | 6 | 7 | yes | 17 | 1/0/14 | 10 / 10 | 3744.9 / 4747.9 | fail |
| R2 | clef | 8 | 8 | 8 | 6 | 0 | 0 | 4 | 6 | 6 | 7 | yes | 17 | 1/0/14 | 10 / 15 | 3753.1 / 4961.8 | fail |
| R3 | clef | 8 | 8 | 8 | 6 | 0 | 0 | 13 | 6 | 6 | 7 | yes | 17 | 1/0/5 | 10 / 28 | 4298.4 / 10526.6 | fail |
| R2b (post-hoc) | clef | 8 | 8 | 8 | 6 | 7 | 8 | 4 | 6 | 6 | 7 | yes | 10 | 1/0/14 | 10 / 22 | 3974.1 / 10846.7 | fail |
| R4 (post-hoc) | clef | 8 | 7 | 8 | 6 | 7 | 8 | 4 | 6 | 6 | 7 | yes | 10 | 1/0/14 | 1 / 22 | 542.3 / 10846.7 | fail |
| R0 | clef-flash | 5 | 6 | 7 | 6 | 0 | 0 | 4 | 6 | 6 | 0 | no | 0 | 2/1/14 | 0 / 0 | 0 / 0 | fail |
| R1 | clef-flash | 7 | 8 | 6 | 5 | 0 | 0 | 4 | 6 | 6 | 7 | yes | 16 | 2/1/14 | 10 / 10 | 1267.8 / 1482.3 | fail |
| R2 | clef-flash | 7 | 8 | 6 | 5 | 0 | 0 | 4 | 6 | 6 | 7 | yes | 16 | 2/1/14 | 10 / 10 | 1267.8 / 1482.3 | fail |
| R3 | clef-flash | 7 | 8 | 5 | 5 | 0 | 0 | 4 | 6 | 6 | 7 | yes | 16 | 2/2/14 | 10 / 28 | 1391 / 3858.4 | fail |
| R2b (post-hoc) | clef-flash | 8 | 9 | 8 | 7 | 6 | 6 | 4 | 6 | 6 | 7 | yes | 6 | 2/1/14 | 10 / 19 | 1356.7 / 2966.2 | fail |
| R4 (post-hoc) | clef-flash | 8 | 8 | 8 | 7 | 6 | 6 | 4 | 6 | 6 | 7 | yes | 6 | 2/1/14 | 1 / 19 | 165.9 / 2966.2 | fail |
| R0 | qwen3.5-9b | 5 | 6 | 7 | 6 | 0 | 0 | 4 | 6 | 6 | 0 | no | 0 | 2/1/14 | 0 / 0 | 0 / 0 | fail |
| R1 | qwen3.5-9b | 8 | 8 | 7 | 6 | 0 | 0 | 4 | 6 | 6 | 7 | yes | 8 | 2/1/14 | 10 / 10 | 1353.8 / 1438.9 | fail |
| R2 | qwen3.5-9b | 8 | 8 | 7 | 6 | 0 | 0 | 4 | 6 | 6 | 7 | yes | 8 | 2/1/14 | 10 / 10 | 1353.8 / 1438.9 | fail |
| R3 | qwen3.5-9b | 9 | 8 | 7 | 6 | 0 | 0 | 14 | 6 | 6 | 7 | yes | 8 | 2/1/4 | 10 / 28 | 1411.4 / 3139.5 | fail |
| R2b (post-hoc) | qwen3.5-9b | 9 | 9 | 8 | 7 | 0 | 0 | 4 | 6 | 6 | 7 | yes | 6 | 2/1/14 | 10 / 21 | 1376.3 / 2571.8 | fail |
| R4 (post-hoc) | qwen3.5-9b | 9 | 9 | 8 | 7 | 0 | 0 | 4 | 6 | 6 | 7 | yes | 6 | 2/1/14 | 1 / 21 | 136.8 / 2571.8 | fail |

Bars per arm (unknown >= 6 of 7; false unknowns <= 1; store rank-1 gain over R0 >= 4; median added ms <= 300):

- random R1: rank-1 gain -3; unknown_right_at_least_6_of_7 NO, false_unknowns_at_most_1 yes, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 yes
- random R2: rank-1 gain -3; unknown_right_at_least_6_of_7 NO, false_unknowns_at_most_1 yes, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 yes
- random R3: rank-1 gain -5; unknown_right_at_least_6_of_7 NO, false_unknowns_at_most_1 yes, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 yes
- random R2b: rank-1 gain -3; unknown_right_at_least_6_of_7 NO, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 yes
- random R4: rank-1 gain -3; unknown_right_at_least_6_of_7 NO, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 yes
- clef R1: rank-1 gain +3; unknown_right_at_least_6_of_7 yes, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 NO
- clef R2: rank-1 gain +3; unknown_right_at_least_6_of_7 yes, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 NO
- clef R3: rank-1 gain +3; unknown_right_at_least_6_of_7 yes, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 NO
- clef R2b: rank-1 gain +3; unknown_right_at_least_6_of_7 yes, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 NO
- clef R4: rank-1 gain +3; unknown_right_at_least_6_of_7 yes, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 NO
- clef-flash R1: rank-1 gain +2; unknown_right_at_least_6_of_7 yes, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 NO
- clef-flash R2: rank-1 gain +2; unknown_right_at_least_6_of_7 yes, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 NO
- clef-flash R3: rank-1 gain +2; unknown_right_at_least_6_of_7 yes, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 NO
- clef-flash R2b: rank-1 gain +3; unknown_right_at_least_6_of_7 yes, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 NO
- clef-flash R4: rank-1 gain +3; unknown_right_at_least_6_of_7 yes, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 yes
- qwen3.5-9b R1: rank-1 gain +3; unknown_right_at_least_6_of_7 yes, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 NO
- qwen3.5-9b R2: rank-1 gain +3; unknown_right_at_least_6_of_7 yes, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 NO, median_added_ms_at_most_300 NO
- qwen3.5-9b R3: rank-1 gain +4; unknown_right_at_least_6_of_7 yes, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 yes, median_added_ms_at_most_300 NO
- qwen3.5-9b R2b: rank-1 gain +4; unknown_right_at_least_6_of_7 yes, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 yes, median_added_ms_at_most_300 NO
- qwen3.5-9b R4: rank-1 gain +4; unknown_right_at_least_6_of_7 yes, false_unknowns_at_most_1 NO, rank1_chat_store_gain_at_least_4 yes, median_added_ms_at_most_300 yes

Decision counts in run1 (judgments by answer; other tags by count):

- random read: {'R1.judge=no': 192, 'R1.judge=partly': 262, 'R1.judge=yes': 226, 'R2.judge=no': 9, 'R2.judge=yes': 10, 'R2.pick_record=an item': 9, 'R2.pick_word=an item': 9, 'R2.pick_word=none': 1, 'R2b.judge=no': 17, 'R2b.judge=partly': 8, 'R2b.judge=yes': 9, 'R2b.pick_record=an item': 19, 'R2b.pick_record=none of them': 1, 'R2b.pick_word=an item': 13, 'R2b.pick_word=none': 6, 'R3.conflict=no, both can be true': 91, 'R3.conflict=yes, they conflict': 82, 'R3.true_now=an item': 82}
- random write: {'W1=cannot tell': 55, 'W1=no longer true': 61, 'W1=still true': 103, 'W2.depth1=cannot tell': 4, 'W2.depth1=no longer true': 6, 'W2.depth1=still true': 9, 'W2.depth2=cannot tell': 2, 'W2.depth2=no longer true': 2, 'W2.depth2=still true': 1, 'W2b.depends_on=an item': 4, 'W2b.depends_on=none': 5, 'W2b.recheck.depth1=cannot tell': 1}
- clef read: {'R1.judge=no': 889, 'R1.judge=partly': 32, 'R1.judge=yes': 160, 'R2.judge=no': 8, 'R2.pick_record=an item': 4, 'R2.pick_word=an item': 2, 'R2.pick_word=none': 4, 'R2b.judge=no': 62, 'R2b.judge=yes': 34, 'R2b.pick_record=an item': 45, 'R2b.pick_record=none of them': 30, 'R2b.pick_word=an item': 33, 'R2b.pick_word=none': 12, 'R3.conflict=no, both can be true': 15, 'R3.conflict=yes, they conflict': 89, 'R3.true_now=an item': 89}
- clef write: {'W1=cannot tell': 120, 'W1=no longer true': 36, 'W1=still true': 470, 'W2.depth1=cannot tell': 46, 'W2.depth1=no longer true': 4, 'W2.depth1=still true': 21, 'W2.depth2=cannot tell': 21, 'W2.depth2=no longer true': 2, 'W2.depth2=still true': 8, 'W2b.depends_on=an item': 1, 'W2b.depends_on=none': 12, 'W2b.recheck.depth1=no longer true': 1}
- clef-flash read: {'R1.judge=no': 539, 'R1.judge=partly': 1, 'R1.judge=yes': 140, 'R2b.judge=no': 27, 'R2b.judge=yes': 17, 'R2b.pick_record=an item': 19, 'R2b.pick_record=none of them': 13, 'R2b.pick_word=an item': 17, 'R2b.pick_word=none': 2, 'R3.conflict=no, both can be true': 14, 'R3.conflict=yes, they conflict': 82, 'R3.true_now=an item': 82}
- clef-flash write: {'W1=cannot tell': 39, 'W1=no longer true': 36, 'W1=still true': 579, 'W2.depth1=cannot tell': 59, 'W2.depth1=still true': 10, 'W2b.depends_on=an item': 1, 'W2b.depends_on=none': 2}
- qwen3.5-9b read: {'R1.judge=no': 534, 'R1.judge=yes': 146, 'R2b.judge=no': 11, 'R2b.judge=yes': 3, 'R2b.pick_record=an item': 11, 'R2b.pick_record=none of them': 13, 'R2b.pick_word=an item': 5, 'R2b.pick_word=none': 6, 'R3.conflict=no, both can be true': 35, 'R3.conflict=yes, they conflict': 59, 'R3.true_now=an item': 59}
- qwen3.5-9b write: {'W1=cannot tell': 771, 'W1=no longer true': 18, 'W1=still true': 33, 'W2.depth1=cannot tell': 44, 'W2b.depends_on=an item': 4, 'W2b.depends_on=none': 18}

## Experiment W (write)

| arm | model | retired correctly /10 | retired wrongly | wrong ids | calls per new fact (mean / max) | ms per new fact (mean / p95) | WRITE |
|---|---|---|---|---|---|---|---|
| W0 | random | 0 | 0 | - | 0 / 0 | 0 / 0 | fail |
| W1 | random | 8 | 15 | F03, F05, F06, F07, F11, F15*, F16, F21, F22, F23, F24, F25*, F26, F27, F28* | 1.86 / 5 | 0 / 0 | fail |
| W2 | random | 8 | 15 | F03, F05, F06, F07, F11, F15*, F16, F21, F22, F23, F24, F25*, F26, F27, F28* | 1.93 / 9 | 0 / 0 | fail |
| W2b (post-hoc) | random | 8 | 15 | F03, F05, F06, F07, F11, F15*, F16, F21, F22, F23, F24, F25*, F26, F27, F28* | 2.09 / 7 | 0 / 0 | fail |
| W0 | clef | 0 | 0 | - | 0 / 0 | 0 / 0 | fail |
| W1 | clef | 9 | 4 | F21, F22, F25*, F26 | 5.07 / 14 | 2365.3 / 6341.3 | fail |
| W2 | clef | 10 | 7 | F21, F22, F23, F24, F25*, F26, F27 | 6.84 / 44 | 3289.4 / 15598.1 | fail |
| W2b (post-hoc) | clef | 9 | 4 | F21, F22, F25*, F26 | 5.35 / 20 | 2634.1 / 7379.7 | fail |
| W0 | clef-flash | 0 | 0 | - | 0 / 0 | 0 / 0 | fail |
| W1 | clef-flash | 9 | 3 | F07, F15*, F25* | 5.07 / 16 | 811.5 / 2309.3 | fail |
| W2 | clef-flash | 9 | 3 | F07, F15*, F25* | 6.67 / 35 | 1092.3 / 4002.3 | fail |
| W2b (post-hoc) | clef-flash | 9 | 3 | F07, F15*, F25* | 5.14 / 16 | 834.3 / 2382.6 | fail |
| W0 | qwen3.5-9b | 0 | 0 | - | 0 / 0 | 0 / 0 | fail |
| W1 | qwen3.5-9b | 6 | 0 | - | 6.37 / 21 | 1007.6 / 3332.6 | PASS |
| W2 | qwen3.5-9b | 6 | 0 | - | 7.4 / 43 | 1246.3 / 3437.6 | PASS |
| W2b (post-hoc) | qwen3.5-9b | 6 | 0 | - | 6.88 / 25 | 1163.2 / 3437.6 | PASS |

A * marks a fact the key calls contestable.

Post-hoc, not a bar: of the key's retirements that happened, how many were triggered by the new fact that really ends them (write_causes_posthoc.json, written after the first 4b run).

- random W1: 1 of 8 for the right reason (F08)
- random W2: 0 of 8 for the right reason (-)
- random W2b: 1 of 8 for the right reason (F08)
- clef W1: 7 of 9 for the right reason (F01, F08, F09, F10, F14, F18, F19)
- clef W2: 8 of 10 for the right reason (F01, F08, F09, F10, F14, F18, F19, F20)
- clef W2b: 7 of 9 for the right reason (F01, F08, F09, F10, F14, F18, F19)
- clef-flash W1: 7 of 9 for the right reason (F01, F04, F08, F09, F10, F14, F19)
- clef-flash W2: 7 of 9 for the right reason (F01, F04, F08, F09, F10, F14, F19)
- clef-flash W2b: 7 of 9 for the right reason (F01, F04, F08, F09, F10, F14, F19)
- qwen3.5-9b W1: 6 of 6 for the right reason (F01, F08, F09, F10, F14, F19)
- qwen3.5-9b W2: 6 of 6 for the right reason (F01, F08, F09, F10, F14, F19)
- qwen3.5-9b W2b: 6 of 6 for the right reason (F01, F08, F09, F10, F14, F19)

## Repeat from a cold cache

Same prompt, different choice: the model's own repeatability. Sequence identical: the whole run repeated. PRME top 5 differs: questions where PRME's own order changed between the two fresh pack builds, which changes the prompts asked after it.

| model | same prompt, different choice (read / write) | read sequence identical | write sequence identical | PRME top 5 differs | read wall s (run1 / run2) |
|---|---|---|---|---|---|
| random | 0 of 778 / 0 of 114 | no | yes | chat_store:pet, chat_store:sister, chat_ingest:sister, churn-10min-2moves-wool-scarf | 28.5 / 28.4 |
| clef | not run | | | | |
| clef-flash | 0 of 725 / 0 of 290 | no | yes | chat_store:pet, chat_store:sister, chat_ingest:sister | 165.5 / 174.1 |
| qwen3.5-9b | 0 of 660 / 0 of 335 | no | yes | chat_store:pet, chat_store:sister, chat_ingest:sister | 160.0 / 123.9 |
