"""Show what PRME stores and retrieves when a user corrects themselves.

``benchmarks/conversations/corrections-v1.json`` is a short scripted chat in
the chat probe's format with three cases: a changed plan, a wrong fact fixed
in a later session, and two conflicting statements in one session, plus a
control fact that no correction touches. Its key lists the cases under
``corrections``. Two paths store the same information:

- ``ingest``: the whole chat goes through ``ingest()`` with the current
  defaults (the chat probe's build, with the owner named as the speaker of
  their turns), so extraction decides what is a correction.
- ``explicit``: the application stores one short fact per statement with
  ``store()`` and calls ``supersede()`` for the two corrections, as
  ``docs/MEMORY-CORRECTIONS.md`` shows. It needs no model. The conflicting
  statements stay as two stored facts, because the application cannot know
  which one holds.

For each path and case the report lists the records that mention the case's
values with their lifecycle state, the supersedes and contradicts edges
between them, and the record lines of the probe question's rendered
``retrieve()`` context that mention each value, in order. It judges nothing
beyond those facts: whether the new value is active, how many active records
still hold only the old value, whether the extracted claims that hold only the
old value were retired, which value the context shows first, how many of its
lines hold only the old value without a "superseded" or "replaced" tag, and
whether such a line comes before the first line with the new value. Source
messages stay records by design, so a message that stated the old value is
still active and can reach the context.

    uv run python -m benchmarks.diagnostics.corrections_probe run \\
      --model deepseek-v4.1-flash:cloud --cloud --output /tmp/corrections-v1

``--set`` builds the ingest path with other settings, under its own
``--label`` so the defaults' build is kept (#277). With
``enable_superseded_source_suppression`` (#270) retrieval leaves out the
source text of a claim that a correction retired, so use it with
``enable_extraction_corrections``:

    uv run python -m benchmarks.diagnostics.corrections_probe run \\
      --model deepseek-v4.1-flash:cloud --cloud --label ingest-corrections \\
      --set enable_extraction_corrections=true --replace --output /tmp/corrections-v1-on
    uv run python -m benchmarks.diagnostics.corrections_probe run \\
      --model deepseek-v4.1-flash:cloud --cloud --label ingest-suppression \\
      --set enable_extraction_corrections=true --set enable_superseded_source_suppression=true \\
      --replace --output /tmp/corrections-v1-suppressed
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import re
import shutil

from benchmarks.diagnostics import chat_probe
from benchmarks.diagnostics.chat_probe import CLAIM_TYPES, _claim_line, _quiet_offline_cli, _records, _table
from benchmarks.diagnostics.extracted_packs import DEFAULT_MODEL
from benchmarks.diagnostics.product_packing import gate_config, parse_overrides
from prme import MemoryEngine

KIND = "prme-corrections-probe-report"
SCHEMA_VERSION = 1
DEFAULT_CONVERSATION = Path(__file__).resolve().parents[1] / "conversations" / "corrections-v1.json"
INGEST_LABEL = "ingest-defaults"
EXPLICIT_LABEL = "explicit-supersede"

# The explicit path: what an application would store for each statement, when
# it was said, and which earlier statement it replaces. Case IDs match the key.
EXPLICIT_FACTS = [
    {"id": "nurse", "case": "job", "text": "Riley works as an ER nurse in Seattle.", "at": "2026-03-02T18:00:00+00:00"},
    {"id": "austin", "case": "move", "text": "Riley plans to move to Austin this summer.",
     "at": "2026-03-02T18:04:00+00:00"},
    {"id": "march-3", "case": "birthday", "text": "Riley's sister Jo's birthday is March 3.",
     "at": "2026-03-02T18:08:00+00:00"},
    {"id": "black", "case": "coffee", "text": "Riley drinks coffee black.", "at": "2026-03-20T08:00:00+00:00"},
    {"id": "oat-milk", "case": "coffee", "text": "Riley takes oat milk in coffee every morning.",
     "at": "2026-03-20T08:04:00+00:00"},
    {"id": "denver", "case": "move", "text": "Riley is moving to Denver and starts at a hospital there in July.",
     "at": "2026-04-14T19:30:00+00:00", "replaces": "austin"},
    {"id": "march-13", "case": "birthday", "text": "Riley's sister Jo's birthday is March 13.",
     "at": "2026-04-14T19:34:00+00:00", "replaces": "march-3"},
]


def load_cases(conversation: dict) -> list[dict]:
    """The key's correction cases, each checked against the key's probes."""
    probes = {probe["id"]: probe for probe in conversation["key"]["probes"]}
    cases = conversation["key"].get("corrections") or []
    if not cases:
        raise ValueError(f"{conversation['name']}: the key lists no corrections")
    for case in cases:
        if case["probe"] not in probes:
            raise ValueError(f"{conversation['name']}: case {case['id']} names an unknown probe")
        if bool(case.get("values")) == bool(case.get("old") and case.get("new")):
            raise ValueError(f"{conversation['name']}: case {case['id']} needs old and new values, or values")
    return cases


def _mentions(text: str, values: list[str]) -> list[str]:
    lowered = text.casefold()
    return [value for value in values if value.casefold() in lowered]


# The bracketed groups that open a reader line: reference, date, status tags.
_LINE_OPENING = re.compile(r"- (?:\[[^\]]*\] )*")
MARKED_REPLACED = ("superseded", "replaced")


def _marked_replaced(line: str) -> bool:
    """Whether a reader line carries a status tag that says it was replaced."""
    opening = _LINE_OPENING.match(line)
    tags = re.findall(r"\[([^\]]*)\]", opening.group(0)) if opening else []
    return any(tag.startswith(MARKED_REPLACED) for tag in tags)


def case_report(case: dict, nodes: list[dict], edges: list[dict], context: str) -> dict:
    """What one path stored and retrieved for one case."""
    old, new = case.get("old", []), case.get("new", [])
    values = case.get("values") or [*old, *new]
    by_id = {node["id"]: node for node in nodes}
    records = []
    for node in nodes:
        found = _mentions(node["content"], values)
        if node["type"] == "entity" or not found:
            continue
        replaced_by = by_id.get(node.get("superseded_by") or "")
        records.append({"id": node["id"], "type": node["type"], "state": node["state"], "mentions": found,
                        "claim": _claim_line(node) if node["type"] in CLAIM_TYPES else None,
                        "content": node["content"], "superseded_by": node.get("superseded_by"),
                        "replacement": replaced_by["content"] if replaced_by else None})
    ids = {record["id"] for record in records}
    links = [{"type": edge["type"], "from": by_id[edge["source"]]["content"], "to": by_id[edge["target"]]["content"]}
             for edge in edges if edge["type"] in {"supersedes", "contradicts"}
             and (edge["source"] in ids or edge["target"] in ids)]
    lines = [{"position": index, "line": line, "mentions": _mentions(line, values)}
             for index, line in enumerate(row for row in context.splitlines() if row.startswith("- "))]
    shown = [line for line in lines if line["mentions"]]
    report = {"id": case["id"], "kind": case["kind"], "label": case["label"], "probe": case["probe"],
              "records": records, "edges": links, "context_lines": shown, "context_records": len(lines)}
    if old and new:
        only_old = [record for record in records if not _mentions(record["content"], new)]
        old_claims = [record for record in only_old if record["type"] in CLAIM_TYPES]
        first = next((line for line in shown), None)
        # Old-only lines that nothing marks as replaced, and whether one is shown before the new value.
        unmarked = [index for index, line in enumerate(shown)
                    if _mentions(line["line"], old) and not _mentions(line["line"], new)
                    and not _marked_replaced(line["line"])]
        first_new = next((index for index, line in enumerate(shown) if _mentions(line["line"], new)), None)
        report["facts"] = {
            "new_value_active": any(record["state"] in chat_probe.ACTIVE and _mentions(record["content"], new)
                                    for record in records),
            "active_records_with_only_the_old_value": sum(record["state"] in chat_probe.ACTIVE
                                                          for record in only_old),
            "extracted_claims_with_only_the_old_value": len(old_claims),
            "of_them_superseded": sum(record["state"] == "superseded" for record in old_claims),
            "context_lines_with_only_the_old_value": sum(not _mentions(line["line"], new) for line in shown
                                                         if _mentions(line["line"], old)),
            "context_lines_with_the_new_value": sum(bool(_mentions(line["line"], new)) for line in shown),
            "first_context_line_shows": ("new" if first and _mentions(first["line"], new)
                                         else "old" if first else "neither"),
            "old_only_lines_in_the_context": len(unmarked),
            "old_value_shown_before_the_new_value": bool(unmarked) and (first_new is None or unmarked[0] < first_new),
        }
    else:
        report["facts"] = {
            "active_records_per_value": {value: sum(record["state"] in chat_probe.ACTIVE and value in record["mentions"]
                                                    for record in records) for value in values},
            "context_lines_per_value": {value: sum(value in line["mentions"] for line in shown) for value in values},
            "contradicts_edges": sum(link["type"] == "contradicts" for link in links),
        }
    return report


async def path_report(pack: Path, conversation: dict, cases: list[dict], contexts: Path,
                      overrides: dict | None = None) -> dict:
    """Measure one finished pack: every case's records and its probe's context."""
    owner = conversation["owner"]
    reference_time = datetime.fromisoformat(conversation["reference_time"])
    probes = {probe["id"]: probe for probe in conversation["key"]["probes"]}
    contexts.mkdir(parents=True, exist_ok=True)
    found = []
    async with MemoryEngine.open(gate_config(pack, overrides)) as engine:
        nodes, edges, _ = await _records(engine, owner)
        for node in nodes:
            # _records leaves out the replacement pointer, so read it from the node itself.
            stored = await engine.get_node(node["id"], user_id=owner, include_superseded=True)
            node["superseded_by"] = str(stored.superseded_by) if stored and stored.superseded_by else None
        for case in cases:
            probe = probes[case["probe"]]
            response = await engine.retrieve(probe["question"], user_id=owner, reference_time=reference_time)
            context = response.bundle.render()
            (contexts / f"{probe['id']}.txt").write_text(f"Q: {probe['question']}\n\n{context}\n")
            report = case_report(case, nodes, edges, context)
            report["question"] = probe["question"]
            report["tokens_used"], report["token_budget"] = response.bundle.tokens_used, response.bundle.token_budget
            found.append(report)
    return {"records": len(nodes), "cases": found}


async def build_explicit(pack: Path, conversation: dict) -> None:
    """Store the application's facts with store() and apply its two corrections with supersede()."""
    owner = conversation["owner"]
    (pack / "lexical_index").mkdir(parents=True)
    async with MemoryEngine.open(gate_config(pack)) as engine:
        nodes = {}
        for fact in EXPLICIT_FACTS:
            event = await engine.store(fact["text"], user_id=owner, event_time=datetime.fromisoformat(fact["at"]))
            nodes[fact["id"]] = (event, (await engine.get_event_nodes(event, user_id=owner))[0])
            if "replaces" in fact:
                await engine.supersede(str(nodes[fact["replaces"]][1].id), str(nodes[fact["id"]][1].id),
                                       evidence_id=event, user_id=owner, actor_id=owner)


async def run(output: Path, *, conversation_path: Path = DEFAULT_CONVERSATION, root: Path | None = None,
              model: str = DEFAULT_MODEL, cloud: bool = False, replace: bool = False, skip_ingest: bool = False,
              prepare=None, overrides: dict | None = None, label: str = INGEST_LABEL) -> dict:
    """Build both paths, measure them and write the report to ``output``.

    ``overrides`` are the ingest build's settings and ``label`` its folder.
    ``prepare`` is passed to the ingest build, for tests.
    """
    conversation = chat_probe.load_conversation(conversation_path)
    cases = load_cases(conversation)
    root = chat_probe.default_root() if root is None else root
    paths = {}
    if not skip_ingest:
        build = await chat_probe.build(label, conversation_path=conversation_path, root=root, model=model,
                                       cloud=cloud, speaker=True, replace=replace, prepare=prepare,
                                       overrides=overrides)
        folder = root / conversation["name"] / label
        paths["ingest"] = await path_report(folder / "pack", conversation, cases, output / "contexts" / "ingest",
                                            overrides)
        paths["ingest"]["build"] = {**build["build"], "settings": build["settings"], "label": label}
        paths["ingest"]["model"] = json.loads((folder / "build.json").read_text())["model"]
    explicit = root / conversation["name"] / EXPLICIT_LABEL
    if explicit.exists():
        if not replace:
            raise ValueError(f"{explicit} exists; use --replace")
        shutil.rmtree(explicit)
    await build_explicit(explicit / "pack", conversation)
    paths["explicit"] = await path_report(explicit / "pack", conversation, cases, output / "contexts" / "explicit")
    report = {"kind": KIND, "schema_version": SCHEMA_VERSION, "conversation": conversation["name"],
              "conversation_sha256": chat_probe.conversation_digest(conversation_path),
              "reference_time": conversation["reference_time"], "paths": paths}
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (output / "report.md").write_text(report_markdown(report))
    return report


def report_markdown(report: dict) -> str:
    lines = [f"# Corrections probe: {report['conversation']}", "",
             f"Reference time {report['reference_time']}. Each probe question makes one `retrieve()` call.", ""]
    for name, path in report["paths"].items():
        lines += [f"## Path: {name}", ""]
        if "model" in path:
            lines += [f"Extraction model `{path['model'].get('model')}`; settings "
                      f"`{json.dumps(path['build'].get('settings'))}`; {path['build'].get('calls')} extraction calls.",
                      ""]
        for case in path["cases"]:
            lines += [f"### {case['label']} ({case['kind']})", ""]
            lines += _table(["Fact", "Value"], [[key.replace("_", " ").replace("old only", "old-only"),
                                                 json.dumps(value) if isinstance(value, dict) else value]
                                                for key, value in case["facts"].items()])
            lines += ["", "Records that mention its values:", ""]
            lines += _table(["Type", "State", "Text", "Replaced by"],
                            [[record["type"], record["state"], record["claim"] or record["content"],
                              record["replacement"] or ""] for record in case["records"]] or [["", "", "none", ""]])
            if case["edges"]:
                lines += ["", "Edges: " + "; ".join(f"{edge['from']} {edge['type']} {edge['to']}"
                                                    for edge in case["edges"])]
            lines += ["", f"Context for \"{case['question']}\" ({case['context_records']} records, "
                          f"{case['tokens_used']}/{case['token_budget']} tokens), lines that mention its values:", ""]
            lines += [f"{line['position'] + 1}. `{line['line']}`" for line in case["context_lines"]] or ["none"]
            lines += [""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.diagnostics.corrections_probe",
                                     description="Show what PRME stores and retrieves when a user corrects "
                                                 "themselves, through ingest() and through explicit supersede().")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("run", help="Build both paths, measure them and write the report")
    command.add_argument("--output", type=Path, required=True, help="Folder for report.json, report.md and contexts/")
    command.add_argument("--conversation", type=Path, default=DEFAULT_CONVERSATION)
    command.add_argument("--root", type=Path, default=None,
                         help="Where builds live; the main checkout's data/chat-probe-v1 by default")
    command.add_argument("--model", default=DEFAULT_MODEL, help="An Ollama model tag for extraction")
    command.add_argument("--cloud", action="store_true",
                         help="Accept an Ollama cloud model: Ollama's hosted service receives the turns")
    command.add_argument("--replace", action="store_true",
                         help="Delete this label's earlier ingest build and the explicit build first")
    command.add_argument("--label", default=INGEST_LABEL, help="Folder name of the ingest build")
    command.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                         help="A setting for the ingest build, such as enable_extraction_corrections=true")
    command.add_argument("--skip-ingest", action="store_true", help="Run only the explicit path, with no model")
    args = parser.parse_args(argv)
    _quiet_offline_cli()
    overrides = parse_overrides(args.overrides)
    report = asyncio.run(run(args.output, conversation_path=args.conversation, root=args.root, model=args.model,
                             cloud=args.cloud, replace=args.replace, skip_ingest=args.skip_ingest,
                             overrides=overrides, label=args.label))
    print(report_markdown(report))


if __name__ == "__main__":
    main()
