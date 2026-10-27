| Measure | emb366-bge-small | emb366-bge-base |
|---|---|---|
| extraction calls | 0 | 0 |
| active claims | 24 | 24 |
| distinct claim texts | 24 | 24 |
| claims whose text repeats another's | 0 | 0 |
| claims repeating a subject, predicate and object | 0 | 0 |
| claims whose text is the whole message | 2 | 2 |
| claims from assistant turns only | 0 | 0 |
| user claims attached to the named owner | 12 | 12 |
| user claims attached to a first-person pronoun | 5 | 5 |
| entities | 25 | 25 |
| entities with no link | 0 | 0 |
| pronoun entities | 2 | 2 |
| key entities found / split / missing | 11/0/0 | 11/0/0 |
| key links linked / via pronoun / not linked / missing | 9/0/5/0 | 9/0/5/0 |
| probes with the answer in context | 7/7 | 7/7 |
| probes with the answer ranked in the top 3 | 7/7 | 6/7 |
| probes with the answer ranked in the top 5 | 7/7 | 7/7 |
| probes with a stale value in context | 0 | 0 |
| repeated records in contexts | 0/105 | 0/105 |

| Probe | emb366-bge-small | emb366-bge-base |
|---|---|---|
| Which cities will Alex and Jordan visit in Japan? | yes | yes |
| What coding language is Alex learning? | yes | yes |
| Which languages does Alex write at work? | yes | yes |
| How does Alex prefer to travel in Japan? | yes | yes |
| Who is allergic to shellfish? | yes | yes |
| What did the team choose for the shipment table? | yes | yes |
| Who should review the runbook? | yes | yes |

| Probe answer rank | emb366-bge-small | emb366-bge-base |
|---|---|---|
| Which cities will Alex and Jordan visit in Japan? | 2 | 4 |
| What coding language is Alex learning? | 3 | 2 |
| Which languages does Alex write at work? | 1 | 1 |
| How does Alex prefer to travel in Japan? | 3 | 3 |
| Who is allergic to shellfish? | 1 | 1 |
| What did the team choose for the shipment table? | 1 | 1 |
| Who should review the runbook? | 1 | 1 |

| Link | emb366-bge-small | emb366-bge-base |
|---|---|---|
| Jordan is Alex's partner | linked | linked |
| Alex is planning a trip to Japan | linked | linked |
| Jordan is going on the trip to Japan | linked | linked |
| Tokyo is a city of the trip to Japan | not_linked | not_linked |
| Kyoto is a city of the trip to Japan | not_linked | not_linked |
| Kanazawa is a city of the trip to Japan | not_linked | not_linked |
| Osaka is a city of the trip to Japan | not_linked | not_linked |
| Dana is Alex's manager | not_linked | not_linked |
| Dana wants Sam to review the runbook | linked | linked |
| Alex chose Tokyo for the trip ("We chose Tokyo, ...") | linked | linked |
| Jordan chose Tokyo for the trip ("We chose Tokyo, ...") | linked | linked |
| Alex dropped Hiroshima from the trip ("We dropped Hiroshima") | linked | linked |
| Jordan dropped Hiroshima from the trip ("We dropped Hiroshima") | linked | linked |
| Alex's team chose Postgres partitioning over TimescaleDB ("We chose ...") | linked | linked |

