# Local partition resource results

Keeping every project engine open is expensive even with one shared embedding
model. An explicit DuckDB worker limit reduces thread growth, but a bounded
lease/cache design is still needed before adding a convenient namespace manager.

The [registered protocol](PARTITION-RESOURCE-PROTOCOL.md) and
[machine-readable record](partition-resources-f1386a7.json) retain package/runner
commits, hashes, individual process exits, the budget stop and order deviation.
The default package is `4e43d0b`; the worker-control package is `f1386a7`.
Both installed builds were verified against all 119 package Python files.

## Measured workload

Each pack contains three authored events and two active nodes after duplicate
maintenance. Every pack uses the same owner, project scope and entity name, but
a distinct retention fact. Public retrieval, source reads, adjacent foreign-ID
reads and full close/reopen checks passed in every completed run. These checks
cover physical separation for those operations, not all namespace requirements.

Runs used Python 3.13.3, DuckDB 1.5.5, FastEmbed 0.8.0 and shared warmed BGE-small
on one 18-CPU, 48-GiB macOS host. Each repetition ran in a fresh process. Below,
memory is the median of each completed run's maximum sampled RSS; threads are
the maximum sampled across repetitions. All process memory is included.

| Open policy | Packs | Completed repetitions | Memory (MiB) | Threads |
|---|---:|---:|---:|---:|
| Resident, native workers | 1 | 3/3 | 364.7 | 65 |
| Resident, native workers | 10 | 3/3 | 541.1 | 227 |
| Resident, native workers | 100 | 0/3 | See stop below | — |
| Lease one, native workers | 1 | 3/3 | 349.5 | 66 |
| Lease one, native workers | 10 | 3/3 | 366.4 | 78 |
| Lease one, native workers | 100 | 3/3 | 372.2 | 90 |
| Resident, one DuckDB worker | 100 | 3/3 | 2,437.6 | 167 |

The first native-worker 100-pack attempt stopped after opening pack 54 at 1,017
threads and 1,428.4 MiB RSS. It had populated only 53 packs and did not reach the
retrieval/reopen checks. Its native exit was 1 (`ResourceBudgetExceeded`); two
later repetitions were deliberately skipped. This is an experiment budget, not
a measured operating-system or product maximum. A coordinator mistake also
skipped four smaller resident repetitions; they ran successfully afterward,
so the intended alternating order was not fully preserved.

All 18 completed repetitions exited 0. The separate one-pack smoke run also
passed but is excluded from the table. Median cold opens were roughly 41–51 ms
and reopens 16–21 ms. These tiny-pack timings include warm-cache and first-use
effects; other host activity and tests overlapped parts of the default study.
They do not support a competitive speed ratio or production latency guarantee.

## Implemented control and next decision

`duckdb_threads` / `PRME_DUCKDB_THREADS` accepts an explicit positive count at
database creation; `None` preserves the native default. Conflicting concurrent
settings for one file fail without reconfiguring the existing engine. The option
does not cap total process threads or memory and does not affect PostgreSQL.
See [local resource configuration](../../../../docs/LOCAL-RESOURCES.md).

Eleven new resource-control contracts passed. The broader source and installed
Python 3.13 suites each passed **127 tests, with five skips**, including live
PostgreSQL, startup recovery, configuration and atomic merge checks. The source
run used DuckDB 1.4.4. These overlapping focused invocations are separate from
the earlier full 2,793-test regression at `31946c2`.

Use bounded active engines as the next local namespace prototype. It must prove
stable partition identity, concurrent lease ownership, cancellation-safe close,
copy/reopen identity, and isolation across ingestion, receipts and pending-work
recovery. PostgreSQL still needs its own partition/pool experiment and mandatory
authorization at hosted interfaces. No public namespace manager or grants are
implemented by this resource-control change. No memory-QA defaults changed.

The latest [explicit-file credential check](openai-file-health-recheck-224231.json)
still returned HTTP 429 for `gpt-4o-mini`; it did not return a successful answer.
The sanitized record does not identify the rate-limit cause or retain secrets.
