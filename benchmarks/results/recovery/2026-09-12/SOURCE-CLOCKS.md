# Source clocks across raw writes and MCP

At `f9665fb`, Python `store()` and `ingest_fast()` reject source datetimes without
a timezone before writing an event or deferred job. Previously both accepted
these ambiguous clocks; the two pre-fix regressions failed. The synchronous
client propagates the same validation error. MCP `memory_store` now accepts an
aware `event_time`, and returned nodes expose source time and validity separately.
An omitted source clock remains unknown. Historical rows and saved plans are not
rewritten.

The [verification record](source-clocks-f9665fb.json) includes observed native
exit codes, log and wheel hashes, and the reproduced failure. Focused tests
passed **56 checks with three skips**, including both live storage backends.
They cover admission, missing/offset clocks, indexing outage, restart/recovery,
owner isolation and actual MCP transport validation. The complete source run
passed **2,941 tests with 81 skips** in 455.12 seconds and exited zero.

An installed Python 3.13 wheel passed **151 checks with 12 skips** in 32.98
seconds and exited zero. Production imports came from `site-packages`; all 124
installed source files matched the frozen checkout. These invocations overlap
and should not be added together. Lint passed after correcting existing unused
fixture imports in four test files. The initial optional `python -m build`
frontend was absent; the project's `uv build` frontend built the wheel normally.

The [fresh service check](openai-file-health-after-usage-reset.json) at 02:50 UTC
still returned HTTP 429 after explicitly reading the updated root `.env`.
No credentials, endpoint or provider error body were printed. This does not
identify the cause of the provider limit. Local reader evaluation can continue.
These reliability tests do not establish extraction accuracy or market leadership.
