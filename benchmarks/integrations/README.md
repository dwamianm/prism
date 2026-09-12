# PrecisionMemBench diagnostic adapter

For the separate chronological dialogue replay, see the
[MemConflict adapter](MEMCONFLICT.md).

`precision_service.py` implements the upstream generic HTTP contract using
PRME's default local FastEmbed retrieval. It owns temporary memory packs and
binds to loopback. It is a scratch benchmark service, not a production endpoint.
Opaque fixture IDs are metadata; they do not affect retrieval ranking. Each
(user ID, arbitrary named scope) receives a separate pack. This application-level
mapping is not evidence of native arbitrary collection support in PRME.

Upstream: https://github.com/tenurehq/precisionmembench
Pinned revision: `85b48d5fd1b38babc7fe922f3beb0308cf3aa6cb`.

Run the service with the project's API extra installed:

```sh
python -m benchmarks.integrations.precision_service --port 53091
```

In a checkout of that upstream revision, add this entry to
`providers.config.json` and run `npm ci --ignore-scripts`:

```json
"prme": {
  "envVar": "PRME_URL",
  "defaultUrl": "http://127.0.0.1:53091",
  "seedDelayMs": 0,
  "beliefToText": "canonical_name_aliases",
  "supportsUpdate": true
}
```

Run each suite sequentially (each resets the scratch service):

```sh
MEMORY_PROVIDER=prme RESEED=true npx ava src/retrieval.external.eval.test.ts
MEMORY_PROVIDER=prme RESEED=true npx ava src/session-retrieval.external.eval.test.ts
```

Interpretation limits of this unmodified upstream adapter:

- The text serialization includes names, aliases, content and why-it-matters.
  Its mode name does not describe the full input.
- The single-turn adapter omits supersession, resolution, type and pinned state
  from provider metadata. The session adapter includes some additional fields.
- Persona, pinned facts, open questions and relation expansion are supplied by
  the upstream fixture reader, not by PRME. Their passing assertions cannot be
  credited as native PRME capabilities.
- Updates append an event and explicitly supersede the previous node with the
  same opaque ID. Raw source history remains in the event log.
- Results use ranked retrieval candidates and the requested count; this does not
  evaluate PRME's token-packed context or LLM extraction.
- This is a small synthetic precision diagnostic. Published vendor results are
  not a controlled comparison with this run. Preserve failures and disclose the
  wrapper when reporting scores; do not claim a general leaderboard result.

For acceptance-floor experiments, pass `--min-score NUMBER` to the service.
Record the value and source revision with every report. Defaults remain unset;
such development sweeps are calibration diagnostics, not held-out results.
