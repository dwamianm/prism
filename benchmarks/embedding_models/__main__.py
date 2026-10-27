"""``python -m benchmarks.embedding_models <command>``: the experiment of dwamianm/prism#366 (``GATE.md``)."""
from __future__ import annotations

import sys


def main() -> None:
    commands = {"latency": "benchmarks.embedding_models.latency", "pack": "benchmarks.embedding_models.pack",
                "scale": "benchmarks.embedding_models.scale", "storage": "benchmarks.embedding_models.storage",
                "operations": "benchmarks.embedding_models.operations", "switch": "benchmarks.embedding_models.switch",
                "report": "benchmarks.embedding_models.report", "reembed": "benchmarks.embedding_models.reembed",
                "job": "benchmarks.embedding_models.job"}
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        raise SystemExit(f"usage: python -m benchmarks.embedding_models {{{'|'.join(commands)}}} ...")
    import importlib

    importlib.import_module(commands[sys.argv[1]]).main(sys.argv[2:])


main()
