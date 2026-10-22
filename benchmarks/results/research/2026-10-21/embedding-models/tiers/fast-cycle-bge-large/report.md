# Fast cycle: emb366-bge-large

Commit `349a9926`, engine tree `cda53eae`. Settings: `{}`.

Store: 75 `store()` calls on duckdb, fixture `store-v1`, 17 questions.
Ingest: 84 turns of 3 chats, extraction by `deepseek-v4.1-flash:cloud`: 0 model calls and 84 replayed from the extraction cache (replay).

## Time

| Stage | Seconds |
|---|---|
| store | 22.3 |
| ingest | 139.2 |
| ingest: defects-v1 | 24.7 |
| ingest: corrections-v1 | 28.7 |
| ingest: chat-v1 | 85.7 |
| total | 161.5 |

## Storage

| Measure | Where | Value | Better |
|---|---|---|---|
| Nodes made per store call | store | 1 | lower |
| Edges made per store call | store | 0 | not judged |
| Context tokens used per question | store | 1785.706 | lower |
| Extraction requests | defects-v1 | 14 | lower |
| Extraction requests | corrections-v1 | 16 | lower |
| Extraction requests | chat-v1 | 54 | lower |
| Extraction tokens (prompt and completion) | defects-v1 | 84400 | lower |
| Extraction tokens (prompt and completion) | corrections-v1 | 96475 | lower |
| Extraction tokens (prompt and completion) | chat-v1 | 335364 | lower |
| Active claims per turn | defects-v1 | 1.571 | not judged |
| Active claims per turn | corrections-v1 | 1.25 | not judged |
| Active claims per turn | chat-v1 | 2.056 | not judged |
| Claims whose text repeats another's | defects-v1 | 0 | lower |
| Claims whose text repeats another's | corrections-v1 | 0 | lower |
| Claims whose text repeats another's | chat-v1 | 0 | lower |
| Claims repeating a subject, predicate and object | defects-v1 | 0 | lower |
| Claims repeating a subject, predicate and object | corrections-v1 | 0 | lower |
| Claims repeating a subject, predicate and object | chat-v1 | 5 | lower |
| Claims whose text is the whole message | defects-v1 | 2 | lower |
| Claims whose text is the whole message | corrections-v1 | 0 | lower |
| Claims whose text is the whole message | chat-v1 | 5 | lower |
| Nodes per turn | defects-v1 | 4.5 | lower |
| Nodes per turn | corrections-v1 | 3.375 | lower |
| Nodes per turn | chat-v1 | 4.148 | lower |
| Edges per turn | defects-v1 | 6.429 | not judged |
| Edges per turn | corrections-v1 | 3.812 | not judged |
| Edges per turn | chat-v1 | 6.111 | not judged |

## Meaning

| Measure | Where | Value | Better |
|---|---|---|---|
| Key entities found | store | 0 | higher |
| Key entities found | defects-v1 | 11 | higher |
| Key entities found | corrections-v1 | 2 | higher |
| Key entities found | chat-v1 | 15 | higher |
| Key entities split across nodes | defects-v1 | 0 | lower |
| Key entities split across nodes | corrections-v1 | 0 | lower |
| Key entities split across nodes | chat-v1 | 2 | lower |
| Key entities missing | defects-v1 | 0 | lower |
| Key entities missing | corrections-v1 | 0 | lower |
| Key entities missing | chat-v1 | 0 | lower |
| Questions with a graph candidate | store | 0 | higher |
| Questions with a word-search candidate | store | 17 | higher |
| User claims attached to the named owner | defects-v1 | 11 | higher |
| User claims attached to the named owner | corrections-v1 | 12 | higher |
| User claims attached to the named owner | chat-v1 | 36 | higher |
| User claims attached to I or We | defects-v1 | 4 | lower |
| User claims attached to I or We | corrections-v1 | 1 | lower |
| User claims attached to I or We | chat-v1 | 3 | lower |
| Pronoun entities | defects-v1 | 2 | lower |
| Pronoun entities | corrections-v1 | 2 | lower |
| Pronoun entities | chat-v1 | 15 | lower |
| Entities with no link | defects-v1 | 0 | lower |
| Entities with no link | corrections-v1 | 0 | lower |
| Entities with no link | chat-v1 | 0 | lower |
| Key links linked | defects-v1 | 9 | higher |
| Key links linked | corrections-v1 | 1 | higher |
| Key links linked | chat-v1 | 14 | higher |
| Key links linked only through a pronoun | defects-v1 | 0 | not judged |
| Key links linked only through a pronoun | corrections-v1 | 0 | not judged |
| Key links linked only through a pronoun | chat-v1 | 0 | not judged |
| Key links not linked | defects-v1 | 5 | lower |
| Key links not linked | corrections-v1 | 0 | lower |
| Key links not linked | chat-v1 | 0 | lower |

## Results

| Measure | Where | Value | Better |
|---|---|---|---|
| Answers ranked first | store | 7 | higher |
| Answers ranked in the top 3 | store | 13 | higher |
| Answers ranked in the top 3 | defects-v1 | 6 | higher |
| Answers ranked in the top 3 | corrections-v1 | 3 | higher |
| Answers ranked in the top 3 | chat-v1 | 15 | higher |
| Answers ranked in the top 5 | defects-v1 | 7 | higher |
| Answers ranked in the top 5 | corrections-v1 | 3 | higher |
| Answers ranked in the top 5 | chat-v1 | 15 | higher |
| Answers in the context | store | 17 | higher |
| Answers in the context | defects-v1 | 7 | higher |
| Answers in the context | corrections-v1 | 4 | higher |
| Answers in the context | chat-v1 | 17 | higher |
| Mean reciprocal rank of the answer | store | 0.604 | higher |
| Questions with a stale value ranked above the answer | store | 2 | lower |
| Questions with a stale value in the context | store | 5 | lower |
| Questions with a stale value in the context | defects-v1 | 0 | lower |
| Questions with a stale value in the context | corrections-v1 | 2 | lower |
| Questions with a stale value in the context | chat-v1 | 3 | lower |
| Repeated records in the contexts | store | 0 | lower |
| Repeated records in the contexts | defects-v1 | 0 | lower |
| Repeated records in the contexts | corrections-v1 | 0 | lower |
| Repeated records in the contexts | chat-v1 | 18 | lower |
| Corrections whose context shows the old value first | corrections-v1 | 1 | lower |
| Context lines with only the old value | corrections-v1 | 2 | lower |
| Active records with only the old value | corrections-v1 | 6 | lower |
| Extracted claims with only the old value, not retired | corrections-v1 | 4 | lower |
| Corrections whose new value is active | corrections-v1 | 2 | higher |

## Rank of each answer

The 1-based place of the first result that holds the answer, among the results `retrieve()` returned; none when no result does.

| Question | Chat | Rank |
|---|---|---|
| what coding language is alex learning? | store | 4 |
| which cities will Alex and jordan visit in Japan? | store | 19 |
| When is the production cutover? | store | 3 |
| When is the Japan trip? | store | 3 |
| Does Alex like pair programming? | store | 1 |
| Is Alex leading the observability rewrite? | store | 1 |
| Who is on call the week of 2026-10-26? | store | 2 |
| Who is Alex's manager? | store | 1 |
| What is Jordan allergic to? | store | 1 |
| What is Alex's dog called? | store | 20 |
| How much is the Japan trip budget? | store | 1 |
| What is the Datadog dashboard for the migration called? | store | 2 |
| How many rows does the shipment_events table have? | store | 2 |
| Why did Alex reject TimescaleDB? | store | 1 |
| Which gravel race did Alex sign up for? | store | 2 |
| Which airline did Alex book for the Japan trip? | store | 4 |
| How does Alex format SQL? | store | 1 |
| Which cities will Alex and Jordan visit in Japan? | defects-v1 | 4 |
| What coding language is Alex learning? | defects-v1 | 2 |
| Which languages does Alex write at work? | defects-v1 | 1 |
| How does Alex prefer to travel in Japan? | defects-v1 | 3 |
| Who is allergic to shellfish? | defects-v1 | 1 |
| What did the team choose for the shipment table? | defects-v1 | 1 |
| Who should review the runbook? | defects-v1 | 1 |
| Where is Riley moving? | corrections-v1 | 3 |
| When is Jo's birthday? | corrections-v1 | 2 |
| How does Riley take coffee? | corrections-v1 | none |
| What does Riley do for work? | corrections-v1 | 1 |
| Where does Dana work now? | chat-v1 | 13 |
| What is Dana's job title now? | chat-v1 | 18 |
| When did Dana start the new job? | chat-v1 | 2 |
| Who was Dana's manager at Northwind? | chat-v1 | 2 |
| What does Dana's partner do for work? | chat-v1 | 3 |
| What is Sam allergic to? | chat-v1 | 1 |
| What kind of dog does Dana have, and what is it called? | chat-v1 | 1 |
| What is the name of Dana's niece? | chat-v1 | 1 |
| When was Ivy born? | chat-v1 | none |
| Where does Dana's sister live? | chat-v1 | 3 |
| Who is Dana's running partner? | chat-v1 | 2 |
| What was Dana's half marathon finishing time? | chat-v1 | 1 |
| Does Dana drink coffee? | chat-v1 | 3 |
| What is Dana's diet now? | chat-v1 | 1 |
| What news did Melanie share after the race? | chat-v1 | 1 |
| What is Dana planning for July? | chat-v1 | 1 |
| Where will Dana stop overnight on the drive? | chat-v1 | 3 |
| Which programming language does Dana prefer? | chat-v1 | 2 |
