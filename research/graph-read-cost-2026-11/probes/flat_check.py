"""P8 bar 3 control: the same two paths on the flat pack, where the graph walk finds no seeds at all.

    PYTHONPATH=. .venv/bin/python research/graph-read-cost-2026-11/probes/flat_check.py \
        --pack /tmp/p8/n20000-s1 --stored 20000 --rounds 2 \
        --out research/graph-read-cost-2026-11/results/interleave-flat.json

The cheap shape only changes what the graph stage does after it has seeds. The flat pack has no edges, so every
question finds no seed and the stage returns before it reads anything. If the two paths still measure the same
here, that is the check that the change costs nothing when there is nothing to walk. It reuses the bar 3 harness
unchanged.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import interleave  # noqa: E402

if __name__ == "__main__":
    parser = interleave.argparse.ArgumentParser()
    parser.add_argument("--pack", required=True)
    parser.add_argument("--stored", type=int, required=True)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--work", default=None)
    parser.add_argument("--out", required=True)
    arguments = parser.parse_args()
    result = asyncio.run(interleave.main(arguments))
    with open(arguments.out, "w") as handle:
        json.dump(result, handle, indent=1)
    print(json.dumps({key: result[key] for key in ("current_ms", "cheap_ms", "identity", "pairs",
                                                  "load_before", "load_after")}, indent=1))
