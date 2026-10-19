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
from benchmarks.fast_cycle import compare, contextual, ingest, store

REPO = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE = store.FIXTURES / "baseline-main.json"
STAGES = ("store", "ingest")
# Stages that run only when asked for with --stages, because they call a model the default run does not need.
OPT_IN_STAGES = ("store-contextual",)
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
                     cache_dir: Path, database_url: str | None, contextual_options: dict | None = None) -> dict:
    results: dict = {}
    for name in stages:
        work = out / "work" / name
        shutil.rmtree(work, ignore_errors=True)
        started = time.perf_counter()
        if name == "store":
            results[name] = await store.run(work, overrides=overrides, database_url=database_url)
        elif name == "store-contextual":
            results[name] = await contextual.run(work, overrides=overrides, database_url=database_url,
                                                 use_cache=use_cache, **(contextual_options or {}))
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
    if "store-contextual" in stages:
        entry, info = stages["store-contextual"], stages["store-contextual"]["contextual"]
        lines += [f"Store with situating sentences: the same calls with a sentence from `{info['model']}` (prompt "
                  f"`{info['prompt']}`, run {info['run']}) before {info['sentences']} of {entry['calls']} memories: "
                  f"{info['model_calls']} model calls and {info['replayed']} replayed from the cache, "
                  f"fixture `{entry['fixture']}`."]
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
    scopes = [name for name in ("store", "store-contextual") if name in stages]
    scopes += stages.get("ingest", {}).get("measures", {})
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
    return "\n".join(lines) + "\n"


def _question_text(report: dict, key: str) -> str:
    scope, _, rest = key.partition("/")
    probe = rest.partition(":")[2]
    if scope in {"store", "store-contextual"}:
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
    run.add_argument("--stages", default=",".join(STAGES),
                     help=f"Comma-separated stages to run (default: {','.join(STAGES)}; also {','.join(OPT_IN_STAGES)}, "
                          "which asks a model for a sentence per memory unless the cache holds it)")
    run.add_argument("--contextual-prompt", choices=sorted(contextual.PROMPTS), default=contextual.DEFAULT_PROMPT,
                     help="The prompt of the store-contextual stage")
    run.add_argument("--contextual-run", type=int, default=1, metavar="N",
                     help="Which generation of the store-contextual stage's sentences to use (default: 1)")
    run.add_argument("--contextual-fixture", type=Path, default=store.DEFAULT_FIXTURE,
                     help="The store fixture of the store-contextual stage (default: the store stage's)")
    run.add_argument("--contextual-cache-dir", type=Path, default=contextual.CACHE_DIR,
                     help="The committed cache of the model's sentences")
    run.add_argument("--model", default=ingest.DEFAULT_MODEL,
                     help="The Ollama model that extracts in the ingest stage")
    run.add_argument("--baseline", default=str(DEFAULT_BASELINE),
                     help="A report to judge this run against, or none (default: the committed baseline)")
    run.add_argument("--accept", action="append", default=[], metavar="MEASURE",
                     help="A measure whose worsening this change means to cause; repeatable")
    run.add_argument("--no-cache", action="store_true",
                     help="Ignore the committed extraction cache: every ingest request goes to the model")
    run.add_argument("--update-cache", action="store_true",
                     help="Copy the extraction responses this run added to the committed cache")
    run.add_argument("--cache-dir", type=Path, default=ingest.CACHE_DIR, help="The committed extraction cache")
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
    if not stages or set(stages) - {*STAGES, *OPT_IN_STAGES}:
        parser.error(f"--stages takes some of: {', '.join((*STAGES, *OPT_IN_STAGES))}")
    try:
        overrides = parse_overrides(args.overrides)
    except ValueError as exc:
        parser.error(str(exc))
    _quiet_logs()
    rules = compare.load_rules()
    database_url = args.database_url or os.environ.get(DATABASE_URL_ENV)
    args.out.mkdir(parents=True, exist_ok=True)
    contextual_options = {"prompt": args.contextual_prompt, "run_number": args.contextual_run,
                          "fixture_path": args.contextual_fixture, "cache_dir": args.contextual_cache_dir}
    results = asyncio.run(run_stages(args.out, stages=stages, overrides=overrides, model=args.model,
                                     use_cache=not args.no_cache, cache_dir=args.cache_dir,
                                     database_url=database_url, contextual_options=contextual_options))
    report = build_report(args.label, results, overrides, rules)
    (args.out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    (args.out / "report.md").write_text(report_markdown(report, rules))
    if args.update_cache and "ingest" in results:
        added = ingest.update_cache(Path(results["ingest"]["working_cache"]), args.cache_dir)
        files, size = ingest.cache_size(args.cache_dir)
        print(f"fast cycle: added {added} extraction responses; the committed cache holds {files} ({size} bytes)",
              file=sys.stderr)
    if args.update_cache and "store-contextual" in results:
        added = contextual.update_cache(Path(results["store-contextual"]["working_cache"]), args.contextual_cache_dir)
        print(f"fast cycle: added {added} situating sentences to the committed cache", file=sys.stderr)
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
