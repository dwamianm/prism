"""How many stored claims the relation normalizer maps (dwamianm/prism#399).

``enable_relation_routing`` reads each claim's open predicate through
``prme.retrieval.relations.normalize_predicate``. This reports, for chat probe
builds (or any pack), how many active claims map to a relation of the
vocabulary, by relation, and the predicates that do not map, by count. It reads
the pack only and calls no model.

    uv run python -m benchmarks.diagnostics.relation_coverage <build folder or pack> [...] [--owner <id>] \
      --output <report.json>

writes the report as JSON and prints it as Markdown.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import shutil
import sys
import tempfile

from benchmarks.diagnostics.product_packing import gate_config
from prme import MemoryEngine
from prme.retrieval.relations import coverage
from prme.types import NodeType

_ALL = 1_000_000


def _pack(path: Path) -> Path:
    return path / "pack" if (path / "pack" / "memory.duckdb").exists() else path


def _owner(path: Path) -> str | None:
    report = path / "report.json"
    return json.loads(report.read_text()).get("owner") if report.exists() else None


async def predicates(pack: Path, owner: str) -> list[str | None]:
    """The predicate of every active claim of ``owner`` in a copy of ``pack``."""
    with tempfile.TemporaryDirectory(prefix="relation-coverage-") as work:
        copy = Path(work) / "pack"
        shutil.copytree(pack, copy)
        async with MemoryEngine.open(gate_config(copy)) as engine:
            nodes = await engine.query_nodes(user_id=owner, node_type=NodeType.FACT, limit=_ALL)
    return [(node.metadata or {}).get("predicate") for node in nodes]


async def run(paths: list[Path], owner: str | None) -> dict:
    report = {}
    for path in paths:
        who = owner or _owner(path)
        if who is None:
            raise ValueError(f"{path}: no report.json names the owner; pass --owner")
        report[str(path)] = coverage(await predicates(_pack(path), who))
    return report


def markdown(report: dict) -> str:
    lines = []
    for path, result in report.items():
        share = result["mapped"] / result["claims"] if result["claims"] else 0.0
        lines += [f"## {path}", "",
                  f"Claims {result['claims']}, mapped {result['mapped']} ({share:.0%}).", "",
                  "| Relation | Claims |", "|---|---|",
                  *(f"| {name} | {count} |" for name, count in result["by_relation"].items()), "",
                  "| Unmapped predicate | Claims |", "|---|---|",
                  *(f"| `{name}` | {count} |" for name, count in result["unmapped"].items()), ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", type=Path, help="chat probe build folders or packs")
    parser.add_argument("--owner", help="the owner's user id; read from each build's report.json by default")
    parser.add_argument("--output", type=Path, required=True, help="where to write the report as JSON")
    args = parser.parse_args(argv)
    report = asyncio.run(run(args.paths, args.owner))
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    sys.stdout.write(markdown(report))


if __name__ == "__main__":
    main()
