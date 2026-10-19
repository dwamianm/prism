"""The fast cycle: store, automatic ingestion and retrieval in minutes, with a pass or fail (a prototype).

The multi-day benchmarks answer one question about a release. A change to
storage, ingestion or retrieval needs an answer the same day, so this runs the
normal paths on fixed fixtures and says whether storage efficiency, meaning
and results moved the wrong way:

- ``store``: a fresh engine stores 75 memories with ``store()``, one node per
  call as ``POST /v1/store`` does, with the real local embedding model and no
  extraction, then answers 17 questions (``store.py``).
- ``ingest``: three scripted chats go through ``ingest()`` with extraction by
  a model through Ollama, which an extraction cache replays without a model
  call while the prompt is unchanged (``ingest.py``).

    uv run python -m benchmarks.fast_cycle run --label candidate --out /tmp/cycle
    uv run python -m benchmarks.fast_cycle compare \\
      benchmarks/fixtures/fast-cycle/baseline-main.json /tmp/cycle/report.json

``run`` writes ``report.json`` and ``report.md`` with the measures in three
groups (storage, meaning, results) and the time of each stage. ``compare``
judges a report against a baseline by ``benchmarks/fixtures/fast-cycle/rules.json``
and exits non-zero when a measure the rules gate got worse by more than its
tolerance. It is a regression screen over small fixtures, not an accuracy
estimate; BENCHMARKS.md ("Fast cycle") says what it cannot show.
"""
from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    from benchmarks.fast_cycle import cycle

    return cycle.main(argv)
