# Local physical partition resource probe

Registered before resource results. The code under test is the installed Python
3.13 package built from `4e43d0b`; all 119 package Python files were verified.
The diagnostic runner is committed before execution and records its own hash.

Compare 1, 10 and 100 separate packs, with either all engines resident or at most
one engine leased at a time. Use one caller-owned, warmed BGE-small provider per
fresh process. Each pack gets the same owner, project scope and two matching
entity names, plus a project-specific retention fact. Run unscoped duplicate
maintenance, retrieve the fact, check the next partition's foreign event/node
IDs are missing, close everything and reopen/check every pack. This tests a
small public store/organize/retrieve/reopen workflow, not the whole namespace RFC.

Collect per-pack cold open, population, access open, retrieval and reopen times;
resident/peak memory, native threads and file descriptors at every stage; final
disk size; package/runtime identity; and process exit status. Start with one
smoke run at one partition. Once it passes, run three fresh-process repetitions
per count/mode, serially, alternating mode order across repetitions. OS caches
may be warm. All populated packs contain only three events and two active nodes.
There is no extrapolation to large histories, production concurrency, network
clients, PostgreSQL, authentication, failure recovery or QA accuracy.

Stop a worker after a sampled 4 GiB RSS or 1,000-thread budget is exceeded and
retain the incomplete result. These are operational limits for this experiment,
not product acceptance thresholds. Do not repeatedly run a larger resident arm
after the first budget stop; keep the missing repetitions explicit. Resource
samples cannot guarantee a transient allocation never exceeds the limit.

Use the result to decide whether an unbounded collection of resident engines is
a sensible basis for the first namespace API. If cost grows materially, evaluate
a bounded lease/cache design and a shared-table PostgreSQL prototype before
selecting a hosted default. Performance here cannot establish superiority over
another product or justify weakening isolation.

## Follow-up registered after the default-thread resource probe

The default resident worker stopped at 54 open packs / 1,017 sampled threads;
all three leased 100-pack runs completed. The coordinator mistakenly skipped
later small resident repetitions too. Those four runs were completed afterward
and retained in a separate supplemental record; the planned alternating order
therefore has a documented deviation. The two later resident 100-pack trials
remain skipped after the resource stop, as intended.

`f1386a7` adds an optional, positive `duckdb_threads` setting at native database
creation, with the old default unchanged. After verifying the installed wheel,
run three fresh-process 100-pack resident trials with one DuckDB worker per
pack, the same shared provider, corpus, checks and resource budgets. Keep this
follow-up distinct from the preceding default-thread runs; it is selected after
observing the thread growth. A successful tiny-pack probe supports an explicit
resource-control option, not a universal default or a namespace manager.
