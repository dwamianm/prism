# MemoryAgentBench four-suite ingress smoke

On 2026-09-14, the pinned PRME adapter completed one real source-context ingest
and one real retrieval from each of MemoryAgentBench's four competency families.
All four packs recorded every upstream chunk, reached `complete`, returned a
nonempty result, stayed within PRME's exact 4,096-token budget, and persisted a
retrieval receipt. A separate fresh-process check reopened the conflict pack and
retrieved source evidence successfully.

| Suite | Source | Upstream chunks | PRME records | Ingest | Retrieve | Packed records | Tokens |
|---|---|---:|---:|---:|---:|---:|---:|
| Accurate retrieval | `eventqa_65536` | 17 | 65 | 9.764 s | 296.01 ms | 7 | 3,988 |
| Test-time learning | `icl_banking77_5900shot_balance` | 26 | 78 | 11.347 s | 103.54 ms | 7 | 3,994 |
| Long-range understanding | `detective_qa` | 25 | 91 | 13.542 s | 152.49 ms | 11 | 3,965 |
| Conflict resolution | `factconsolidation_mh_6k` | 2 | 6 | 0.941 s | 33.90 ms | 6 | 3,753 |

The source was PRME `6700f13`, MemoryAgentBench `fe1735d`, and dataset revision
`7ea0669`. The adapter used FastEmbed `BAAI/bge-small-en-v1.5` and the balanced
product packer. The adjacent JSON artifact retains exact revisions, file and
manifest hashes, runtime versions, task configurations, and raw measurements.

This is an integration check rather than task-quality evidence. It used the
first public source context in each selected configuration and one formatted
query, called no reader, and scored no answers. Its single local timings are not
latency estimates. The test-time-learning suite supplies demonstrations for
in-context adaptation, and the conflict suite supplies a numbered fact pool;
neither directly tests PRME's ranking-profile learning or graph supersedence.

The next evidence step is a preregistered common-reader answer matrix with exact
coverage, failure accounting, reader tokens, retrieved-context hashes, and
upstream metrics. Preserve these smoke results as a transport and persistence
gate; do not cite them as a benchmark score.
