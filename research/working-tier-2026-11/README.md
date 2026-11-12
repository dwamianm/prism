# Working tier for writes (P6)

Research prototype for thesis section 5: facts reach memory at robot speed and the durable store catches up behind
them. Nothing here runs in the product. The engine's default path is unchanged; every file in this folder is a
harness or the prototype itself.

## Files

| File | What it is |
|---|---|
| `tier.py` | The prototype: `WorkingTier.accept`, the working table, the memory-mapped log, the drainer, the reads, the restart replay. |
| `harness.py` | P2's instrumented harness, plus timers on the tier's own methods. |
| `common.py` | P2's harness, copied unchanged: the observations, the engine, the guard that fails any model call. |
| `stream.py` | Bar 1 and bar 2: accept latency at 200 facts a second, and time from accept until an address lookup returns the fact. |
| `build.py` | Builds a pack through either write path for bar 3. |
| `compare.py` | Bar 3: compares two packs record by record, on P2's comparison. |
| `crash.py` | Bar 4: offer facts, `os._exit(9)`, reopen, replay, and check that nothing accepted was lost. |
| `reads.py` | Bar 5: `retrieve()` and `retrieve_with_working()` at backlog 0, 1,000 and 12,000. |
| `loop_lag.py` | Bar 2 cause: a 5 ms timer beside a drain, recording how late each tick runs and how long a `lookup()` takes. |
| `offloop.py` | Bar 2 fix attempt: the same read on the loop against a plain thread, beside a drain. It fails on the shared connection; `REPORT.md` has the result. |
| `privacy_probe.py` | Bar 6: refusal and hiding, compared with the current path. |

Every command runs with providers blocked:

```sh
OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1 PRME_EXTRACTION_API_KEY= \
  PYTHONPATH=. uv run python research/working-tier-2026-11/stream.py --pack <pack> --stored <n> --out <file>
```

## What the tier does, in one paragraph

`accept` validates the caller's stated assertion the way `ingest_batch(assertions=...)` does, puts the fact into a
dict keyed by its address under the owner and scope, and writes one record into a memory-mapped log. It makes no
database statement, no index write and no syscall, so it stays off the two costs that bound the current path: the
DuckDB statement and the GIL handoff (see `REPORT.md`). The drainer, one task on the engine's loop, appends the
source events exactly as `IngestionPipeline._append_event` does and runs the engine's unmodified
`process_pending` and `process_extractions` over them. A fact leaves the working table once its durable
materialization and its extraction are both complete. On open, the tier replays the log and returns any stale
`running` lease to `pending`, so a kill loses nothing that the log holds and the derivation gap P5 measured between
the graph commit and the lexical commit is closed.

## Design decisions

- **Memory-mapped log, not a socket write.** `os.write` releases the GIL, so a caller on a busy process waits up to
  one interpreter switch interval for it (7.5 ms at the default 5 ms). An `mmap` slice assignment does not. See
  `REPORT.md`, "Bar 1".
- **No model, ever.** Stated facts derive through `IngestionPipeline._structured_record`, as they do on the current
  path. No decision model is needed anywhere in the design.
- **Refusal is the current path's, not a new rule.** Every read calls `PrivacyRepository.require_legacy_range` first.
  The open question of "inputs read" against "every node of the scope" is left to the owner.
- **A working fact replaces only its own address.** `retrieve_with_working` returns the stored response untouched
  and the working facts beside it, keyed by address, so a caller merges and a working fact never moves a stored one.
