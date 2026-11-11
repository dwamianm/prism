"""Build one oracle arm of a chat probe from its facts file (``oracle.py``), with no model call.

    uv run python research/oracle-extraction-2026-11/run_oracle.py defects-v1 ox-B1 --levels O1,O2 \
        --set 'memory_mode="address_first"' --replace

The pack goes where the probe's own builds go (``benchmarks.diagnostics.chat_probe.default_root()``), so
``python -m benchmarks.diagnostics.chat_probe --conversation ... compare <label> <label>`` reads it like any build.
For member-v1, pass ``--speaker --set enable_speaker_grounding=true``, as the probe rule does.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from oracle import HERE, ROOT, build  # noqa: E402


def main(argv: list[str] | None = None) -> None:
    from benchmarks.diagnostics.product_packing import parse_overrides

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("name", choices=("defects-v1", "chat-v1", "member-v1"))
    parser.add_argument("label")
    parser.add_argument("--levels", default="O1,O2", help="O1 or O1,O2")
    parser.add_argument("--facts", type=Path, default=None, help="default: facts/<name>.json beside this script")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--speaker", action="store_true")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    levels = tuple(level.strip() for level in args.levels.split(","))
    if levels not in {("O1",), ("O1", "O2")}:
        raise SystemExit("--levels is O1 or O1,O2")
    facts = (args.facts or HERE / "facts" / f"{args.name}.json").resolve()
    built = build(args.label, name=args.name, facts_path=facts, levels=levels,
                  overrides=parse_overrides(args.set), speaker=args.speaker, replace=args.replace)
    summary = built["report"]["summary"]
    print(json.dumps({"label": args.label, "folder": str(built["folder"].relative_to(ROOT))
                      if built["folder"].is_relative_to(ROOT) else str(built["folder"]),
                      "oracle": {key: built["oracle"][key] for key in (
                          "model_calls", "turns", "empty_extractions", "mention_patch_uses", "tally")},
                      "summary": summary}, indent=1))


if __name__ == "__main__":
    main()
