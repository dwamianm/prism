# Local extraction model selection

The existing frozen 12-case authored extraction probe completed with the
installed `prme-qwen3.5:9b-8k` Ollama profile. It passed all 12 cases and the
native supervised process exited 0. The model digest was
`1805492e55c52f46b37bbf5f4f5a8db62081b6b7c4b97c05b569d7871c7c95d8`.

| Model | Checks passed | Elapsed seconds |
|---|---:|---:|
| `prme-qwen3.5:9b-8k` | 12/12 | 302.098 |
| `qwen3.5:4b` | 10/12 | 375.146 |
| `gemma4:26b` | 11/12 | 442.405 |

The 4B and Gemma values are the prior complete results recorded on 2026-09-12.
Elapsed time was observed under different host conditions and is not a
controlled speed comparison. The new run reuses the previously frozen cases,
prompt, response schema, and assessment workflow; their hashes are present in
the result. It was not separately preregistered, and no failed case was retried
or replaced.

These authored cases test claim kind, epistemic qualification, polarity,
conditions, entity links, and full source preservation. They are development
probes rather than held-out or comparative product accuracy. The result supports
using the already installed 9B profile for the bounded `ingest()` evaluation. It
does not justify downloading a larger model or making a leadership claim.

Artifacts: [complete result](qwen9b-extraction-probe-results.json) and
[completion record](qwen9b-extraction-probe-completion.json).
