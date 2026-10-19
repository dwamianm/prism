"""Run the fast cycle: the store stage, the ingest stage, a report and a verdict.

``run`` writes ``report.json`` and ``report.md`` under ``--out`` (with the
packs and contexts the stages built under ``work/``) and, unless
``--baseline none``, judges the report against the committed baseline the way
``compare`` does and exits with its status.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from benchmarks.diagnostics.product_packing import _quiet_offline_cli, parse_overrides
from benchmarks.fast_cycle import compare, ingest, store, store_extract

REPO = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE = store.FIXTURES / "baseline-main.json"
STAGES = ("store", "ingest", *compare.STORE_STAGES[1:])
# The other stages are opt-in: the store-extract ones make a model request per memory on a cold cache.
DEFAULT_STAGES = ("store", "ingest")
DATABASE_URL_ENV = "PRME_FAST_CYCLE_DATABASE_URL"


def _quiet_logs() -> None:
    """Only errors on stderr: ingestion warns about each claim it discards, which buries the verdict."""
    import structlog

    _quiet_offline_cli()
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR),
                        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))


def provenance(overrides: dict) -> dict:
    """The code a report measured: the commit, and the engine tree with whether it was modified."""
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=False).stdout.strip()

    return {"commit": git("rev-parse", "HEAD"), "src_tree": git("rev-parse", "HEAD:src"),
            "src_modified": bool(git("status", "--porcelain", "--", "src")), "settings": overrides}


async def run_stages(out: Path, *, stages: tuple[str, ...], overrides: dict, model: str, use_cache: bool,
                     cache_dir: Path, database_url: str | None, store_fixture: Path = store.DEFAULT_FIXTURE) -> dict:
    results: dict = {}
    for name in stages:
        work = out / "work" / name
        shutil.rmtree(work, ignore_errors=True)
        started = time.perf_counter()
        if name in ("store", "store-session"):
            results[name] = await store.run(work, fixture_path=store_fixture, overrides=overrides,
                                            database_url=database_url, session_ids=name == "store-session")
        elif name in compare.EXTRACT_STAGES:
            results[name] = await store_extract.run(work, cache_dir=cache_dir, use_cache=use_cache, overrides=overrides,
                                                    model=model, session_ids=name == store_extract.SESSION_STAGE)
        else:
            results[name] = await ingest.run(work, cache_dir=cache_dir, use_cache=use_cache, overrides=overrides,
                                             model=model)
        results[name]["seconds"] = time.perf_counter() - started
        print(f"fast cycle: stage {name} took {results[name]['seconds']:.1f}s", file=sys.stderr, flush=True)
    return results


def build_report(label: str, results: dict, overrides: dict, rules: dict) -> dict:
    stages = {name: {key: value for key, value in result.items() if key != "working_cache"}
              for name, result in results.items()}
    seconds = {name: result["seconds"] for name, result in stages.items()}
    return {"kind": compare.REPORT_KIND, "schema_version": compare.SCHEMA_VERSION, "label": label,
            "created_at": datetime.now(timezone.utc).isoformat(), "provenance": provenance(overrides),
            "seconds": {**seconds, "total": sum(seconds.values())}, "stages": stages,
            "measures": compare.grouped(rules, stages)}


# --- The Markdown report -------------------------------------------------------------------------------


def _table(header: list[str], rows: list[list]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    return lines + ["| " + " | ".join(str(cell).replace("|", "\\|") for cell in row) + " |" for row in rows]


def report_markdown(report: dict, rules: dict) -> str:
    stages, provenance_ = report["stages"], report["provenance"]
    lines = [f"# Fast cycle: {report['label']}", "",
             f"Commit `{provenance_['commit'][:8]}`, engine tree `{provenance_['src_tree'][:8]}`"
             f"{' (modified)' if provenance_['src_modified'] else ''}. Settings: `{json.dumps(provenance_['settings'])}`.", ""]
    if "store" in stages:
        entry = stages["store"]
        lines += [f"Store: {entry['calls']} `store()` calls on {entry['backend']}, fixture `{entry['fixture']}`, "
                  f"{len(entry['questions'])} questions."]
    if "store-session" in stages:
        lines += [f"Store-session: the same {stages['store-session']['calls']} memories, each stored with its session as "
                  "`session_id`, the control for store-extract-session."]
    for name in compare.EXTRACT_STAGES:
        if name in stages:
            entry = stages[name]
            lines += [f"{name.capitalize()}: the same {entry['calls']} memories with `extract=True`"
                      f"{' and each session as session_id' if entry['session_ids'] else ''}, extraction by "
                      f"`{entry['model']}`: {entry['live_calls']} model requests and {entry['cache_hits']} replayed from "
                      f"the extraction cache ({entry['mode']})."]
    if "ingest" in stages:
        entry = stages["ingest"]
        lines += [f"Ingest: {sum(chat['turns'] for chat in entry['conversations'].values())} turns of "
                  f"{len(entry['conversations'])} chats, extraction by `{entry['model']}`: {entry['live_calls']} model "
                  f"calls and {entry['cache_hits']} replayed from the extraction cache ({entry['mode']})."]
    lines += ["", "## Time", ""]
    rows = [[name, f"{report['seconds'][name]:.1f}"] for name in stages]
    for name, chat in stages.get("ingest", {}).get("conversations", {}).items():
        rows.append([f"ingest: {name}", f"{chat['seconds']:.1f}"])
    rows.append(["total", f"{report['seconds']['total']:.1f}"])
    lines += _table(["Stage", "Seconds"], rows)
    names = list(rules["measures"])
    scopes = [*(name for name in compare.STORE_STAGES if name in stages), *stages.get("ingest", {}).get("measures", {})]
    for group in compare.GROUPS:
        measures = {key: value for key, value in report["measures"][group].items() if ":" not in key}
        if not measures:
            continue
        lines += ["", f"## {group.capitalize()}", ""]
        ordered = sorted(measures, key=lambda key: (names.index(compare.split_id(key)[1]),
                                                    scopes.index(compare.split_id(key)[0])))
        lines += _table(["Measure", "Where", "Value", "Better"], [
            [compare.rule_for(rules, key)["label"], compare.split_id(key)[0], compare.number(measures[key]),
             compare.rule_for(rules, key)["good"] or "not judged"] for key in ordered])
    lines += ["", "## Rank of each answer", "",
              "The 1-based place of the first result that holds the answer, among the results `retrieve()` returned; "
              "none when no result does.", ""]
    ranks = {key: value for group in compare.GROUPS for key, value in report["measures"][group].items()
             if key.split("/", 1)[1].startswith("answer_rank:")}
    lines += _table(["Question", "Chat", "Rank"], [[_question_text(report, key), key.split("/")[0], compare.number(rank)]
                                                    for key, rank in ranks.items()])
    if "store" in stages and any(name in stages for name in compare.EXTRACT_STAGES):
        lines += _extraction_effect(stages)
    return "\n".join(lines) + "\n"


def _extraction_effect(stages: dict) -> list[str]:
    """Each answer's rank, and whether a stale value outranks it, when the memories are stored with extraction."""
    names = [name for name in compare.STORE_STAGES if name in stages]
    lines = ["", "## Store with extraction against plain store", "",
             "The same memories and questions. Rank is the place of the first result that holds the answer "
             "(none when no result does); a star marks a stale value ranked above it.", ""]
    rows = []
    for question in stages["store"]["questions"]:
        cells = []
        for name in names:
            other = next(row for row in stages[name]["questions"] if row["id"] == question["id"])
            cells.append(f"{compare.number(other['answer_rank'])}{'*' if other['stale_above_answer'] else ''}")
        rows.append([question["question"], *cells])
    return lines + _table(["Question", *names], rows)


def _question_text(report: dict, key: str) -> str:
    scope, _, rest = key.partition("/")
    probe = rest.partition(":")[2]
    if scope in compare.STORE_STAGES:
        return next(question["question"] for question in report["stages"][scope]["questions"]
                    if question["id"] == probe)
    return report["stages"]["ingest"]["conversations"][scope]["questions"][probe]


# --- Command line --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.fast_cycle",
        description="Store, ingest and retrieve on fixed fixtures in minutes, and say pass or fail.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run the stages, write the report and judge it against the baseline")
    run.add_argument("--label", required=True, help="A name for this run, such as main or my-change")
    run.add_argument("--out", type=Path, required=True, help="Folder for report.json, report.md and work/")
    run.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                     help="A setting for both stages: a dotted key and a JSON value, as for the gate")
    run.add_argument("--stages", default=",".join(DEFAULT_STAGES),
                     help=f"Comma-separated stages to run, of {', '.join(STAGES)} (default: {', '.join(DEFAULT_STAGES)}). "
                          "The store-extract stages make a model request per memory on a cold cache")
    run.add_argument("--model", default=ingest.DEFAULT_MODEL,
                     help="The Ollama model that extracts in the ingest stage")
    run.add_argument("--baseline", default=str(DEFAULT_BASELINE),
                     help="A report to judge this run against, or none (default: the committed baseline)")
    run.add_argument("--accept", action="append", default=[], metavar="MEASURE",
                     help="A measure whose worsening this change means to cause; repeatable")
    run.add_argument("--no-cache", action="store_true",
                     help="Ignore the committed extraction cache: every ingest and store-extract request goes to the model")
    run.add_argument("--update-cache", action="store_true",
                     help="Copy the extraction responses this run added to the committed cache")
    run.add_argument("--cache-dir", type=Path, default=ingest.CACHE_DIR, help="The committed extraction cache")
    run.add_argument("--store-fixture", type=Path, default=store.DEFAULT_FIXTURE,
                     help="The store stage's fixture (default: store-v1); a report of another fixture is not "
                          "comparable with the committed baseline, so pass --baseline none")
    run.add_argument("--database-url", default=None,
                     help=f"Run the store stage on PostgreSQL (or set {DATABASE_URL_ENV}); a DuckDB pack otherwise")
    comparison = commands.add_parser("compare", help="Judge a report against a baseline; exit 1 on a failed rule")
    compare.add_arguments(comparison)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "compare":
        return compare.execute(args)
    stages = tuple(name for name in args.stages.split(",") if name)
    if not stages or set(stages) - set(STAGES):
        parser.error(f"--stages takes some of: {', '.join(STAGES)}")
    try:
        overrides = parse_overrides(args.overrides)
    except ValueError as exc:
        parser.error(str(exc))
    _quiet_logs()
    rules = compare.load_rules()
    database_url = args.database_url or os.environ.get(DATABASE_URL_ENV)
    args.out.mkdir(parents=True, exist_ok=True)
    results = asyncio.run(run_stages(args.out, stages=stages, overrides=overrides, model=args.model,
                                     use_cache=not args.no_cache, cache_dir=args.cache_dir,
                                     database_url=database_url, store_fixture=args.store_fixture))
    report = build_report(args.label, results, overrides, rules)
    (args.out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    (args.out / "report.md").write_text(report_markdown(report, rules))
    if args.update_cache:
        for name in (name for name in results if "working_cache" in results[name]):
            added = ingest.update_cache(Path(results[name]["working_cache"]), args.cache_dir)
            files, size = ingest.cache_size(args.cache_dir)
            print(f"fast cycle: {name} added {added} extraction responses; the committed cache holds {files} "
                  f"({size} bytes)", file=sys.stderr)
    times = ", ".join(f"{name} {report['seconds'][name]:.1f}s" for name in stages)
    print(f"fast cycle: {times}, total {report['seconds']['total']:.1f}s; report in {args.out / 'report.md'}")
    if args.baseline == "none":
        return 0
    if not Path(args.baseline).is_file():
        parser.error(f"No baseline at {args.baseline}; pass --baseline none to skip the comparison")
    baseline = json.loads(Path(args.baseline).read_text())
    result = compare.compare(baseline, report, rules, frozenset(args.accept))
    sys.stdout.write(compare.render(result, baseline, report))
    return {"PASS": 0, "FAIL": 1}.get(result["status"], 2)
