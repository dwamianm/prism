# MemConflict diagnostic adapter

[MemConflict](https://arxiv.org/abs/2605.20926) separates temporal updates,
static contradictions and conditional applicability. This adapter consumes the
released dialogues sequentially and records retrieved context and local-reader
answers. It does **not** implement the upstream semantic judge or report official
accuracy. The upstream judge requires separate validation; string overlap would
not be an adequate substitute.

Reference source: [TaoZhen1110/MemConflict](https://github.com/TaoZhen1110/MemConflict),
revision `ec51d5d36e87f7665d1337f3a88cbde95fc2a964`, file `Data/Step4_4.jsonl`.
SHA-256: `8ef9ec8589eccb86f63ab3a819a9180217405351a8d5846866721ea74babe092`.
No upstream dataset or implementation is vendored. Obtain a local checkout
separately and provide the data path.

```sh
python -m benchmarks.integrations.memconflict \
  --dataset /path/to/MemConflict/Data/Step4_4.jsonl \
  --audit-only --invalid-messages skip --output /tmp/memconflict-audit.json

python -m benchmarks.integrations.memconflict \
  --dataset /path/to/MemConflict/Data/Step4_4.jsonl \
  --invalid-messages skip --split dev --profiles 1 --questions 3 \
  --model qwen3.5:4b --base-url http://127.0.0.1:11434 \
  --token-budget 2048 --output /tmp/memconflict-diagnostic.json
```

The audit found 30 profiles, 1,579 sessions and 3,750 questions in this release.
Of 142,129 dialogue entries, 142,093 have usable roles and content. Twenty-four
lack `role`; another twelve lack usable content. The default `error` policy
rejects unusable messages. Explicit `skip` counts omissions and preserves original
source positions. It does not reconstruct missing text. The audit covers the
entire input, including profiles outside the selected run. Per-question counts
record omissions in the actual evaluated prefix.

Input boundaries:

- Memory receives only dialogue content, role, neutral positional source IDs,
  session identifiers and dates. Profiles, outlines, conflict annotations,
  triggers, expected answers and category labels never enter memory or reader
  requests. The reader gets the question and reference date at query time.
- Each question follows ingestion of its session, before any later session.
  Every profile has a separate temporary pack. Questions with recurring IDs
  receive a composite profile/session/question identity.
- Sessions must be chronological and have unique IDs. Turn keys sort
  numerically. Duplicate question IDs within a session and ambiguous turn order
  fail validation instead of silently changing the evaluation.
- Split seed `prme-memconflict-v1` assigns entire profiles by SHA-256 modulo five.
  The first profile was inspected during adapter development and is explicitly
  assigned to development. This yields five development and 25 test profiles.
  These are PRME splits, not official benchmark splits.
- A limited run selects questions round-robin across the three conflict types,
  chronologically within each type. It never selects by answers or outcomes.
  Three questions from one development profile are a smoke diagnostic, not a
  representative accuracy estimate. Use `--profiles 0 --questions 0` for all
  questions in a split; keep test results out of subsequent tuning.

The memory profile stores raw NOTE messages with both conversation roles and
explicit event dates. It disables QA pairing, reinforcement, supersedence,
reranking and opportunistic maintenance. This isolates raw dialogue storage and
retrieval; it does not evaluate extraction or automatic conflict resolution.
Three contexts go to the same local Ollama reader: PRME's actual budgeted product
context, BM25 with the shared whole-turn evaluator packer, and empty memory.
The PRME/BM25 comparison combines ranking and formatting differences and cannot
isolate either algorithm. The empty context tests unsupported answering.

Reports retain the dataset checksum, selected IDs, package/dependency/config
provenance, reader model digest, prompts, sampling settings, contexts, answers,
complete candidate snapshots and question-level prefix counts. Candidate snapshots
permit packing experiments without changing retrieval or source inputs. The CLI supervises native interpreter shutdown
and marks completion false on an abnormal child exit. Success here means the
replay completed, not that its answers are correct. Reports contain upstream
text and should remain local unless redistribution is appropriate. Commit
aggregate measurements and hashes instead.

Structural validity does not establish that synthetic gold answers are supported
by the actual dialogue. Audit source support before treating a reader's agreement
with a label as correctness. No production default should be tuned to hidden
profile attributes that were never said in the conversation.
