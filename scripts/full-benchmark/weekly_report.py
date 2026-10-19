#!/usr/bin/env python3
"""The verdict and the report of one scheduled full benchmark run (issue #272).

weekly-run.sh runs the builds and the offline evidence gate, then compares the
run's gate reports with the last stable release's and with the previous
scheduled run's (``gate-compare``). This script reads those comparisons and
writes ``report.json`` and ``report.md`` into the run folder, and the pull
request text that publish-results.sh uses.

    weekly_report.py report RUN_DIR [--reference-dir DIR] [--previous-run-dir DIR]
    weekly_report.py verdict RUN_DIR
    weekly_report.py pr-body RUN_DIR [--files-verified N]

The verdict follows the stable gate in CLAUDE.md: a measure lost when its paired
95% interval lies wholly below zero, at either level. LoCoMo's two levels are the
question-level interval (gate-compare) and the conversation-level interval
(``product_packing gate-conversations``); both count, so a loss at one level is a
loss even when the other interval reaches zero. LongMemEval-S has the
question-level interval only. A point estimate below zero whose intervals reach
zero is noise and is not a loss. The measures are the five that gate-compare
headlines for each benchmark. A LoCoMo comparison with no conversation-level
intervals cannot read "no loss". The per-category rows are listed in the report
but are not part of the verdict: their intervals rest on 30 to 130 questions each.

Standard library only, and Python 3.9 compatible: the Mac mini runs it with the
system python3, outside any worktree's environment.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

KIND = "scheduled-full-benchmark-report"
SCHEMA_VERSION = 1
BENCHMARKS = (("locomo", "LoCoMo", "LABEL_LOCOMO"), ("longmemeval", "LongMemEval-S", "LABEL_LME"))
MEASURES = (
    ("all_evidence_packed", "All evidence packed"),
    ("all_evidence_packed_with_text", "All evidence packed with memory text"),
    ("evidence_recall", "Evidence recall"),
    ("all_evidence_returned", "All evidence among the candidates"),
    ("projected_accuracy", "Projected accuracy"),
)
# Only an interval wholly below zero by more than this counts as a loss, so a
# float that should be zero never does.
EPSILON = 1e-12
CATEGORIES = "all_evidence_packed_by_category"
# LoCoMo's questions come from 10 conversations, so the stable gate counts a conversation-level interval too.
# LongMemEval-S resamples questions only: each question has its own history.
CONVERSATION_LEVEL = ("locomo",)


def read_env(path):
    """KEY=VALUE lines of a run.env or reference.env; {} when the file is missing."""
    values = {}
    try:
        with open(path) as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    values[key.strip()] = value.strip()
    except OSError:
        pass
    return values


def read_json(path):
    try:
        with open(path) as handle:
            value = json.load(handle)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def read_text(path):
    try:
        with open(path) as handle:
            return handle.read().strip()
    except OSError:
        return None


def pct(value):
    return "n/a" if value is None else "{:.1%}".format(value)


def pp(value):
    return "n/a" if value is None else "{:+.1f} pp".format(value * 100)


def interval(low, high):
    return "{:+.1f} to {:+.1f} pp".format(low * 100, high * 100)


def duration(seconds):
    if seconds is None:
        return "n/a"
    if seconds < 90:
        return "{:.0f} s".format(seconds)
    if seconds < 5400:
        return "{:.1f} min".format(seconds / 60)
    return "{:.1f} h".format(seconds / 3600)


def parse_time(text):
    """An ISO time as written by this scheduler (UTC, trailing Z) or by Python."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def row_status(delta, low, high):
    """loss: the interval lies wholly below zero. gain: wholly above. unchanged: exactly zero."""
    if high < -EPSILON:
        return "loss"
    if low > EPSILON:
        return "gain"
    if abs(delta) <= EPSILON and abs(low) <= EPSILON and abs(high) <= EPSILON:
        return "unchanged"
    return "within noise"


def make_row(key, label, entry):
    """One measure at the question level (gate-compare's interval). ``conversation`` is added for LoCoMo."""
    low, high = entry["interval_95"]
    return {
        "measure": key, "label": label, "queries": entry["queries"], "before": entry["before"],
        "after": entry["after"], "delta": entry["delta"], "low": low, "high": high,
        "wins": entry["wins"], "losses": entry["losses"], "ties": entry["ties"],
        "status": row_status(entry["delta"], low, high), "conversation": None,
    }


def attach_conversation(row, stat):
    """Add LoCoMo's conversation-level interval to a row; False when the stat holds none for this measure."""
    if not stat or not stat.get("interval_95"):
        return False
    low, high = stat["interval_95"]
    row["conversation"] = {"low": low, "high": high, "groups": stat.get("groups"),
                           "status": row_status(row["delta"], low, high)}
    return True


def loss_levels(row):
    """The levels at which a row's interval lies wholly below zero."""
    levels = []
    if row["status"] == "loss":
        levels.append("question level")
    if row["conversation"] is not None and row["conversation"]["status"] == "loss":
        levels.append("conversation level")
    return levels


def row_result(row):
    """The row's result over its levels: a loss at either level is a loss; a gain needs every level above zero."""
    statuses = [row["status"]] + ([row["conversation"]["status"]] if row["conversation"] is not None else [])
    levels = loss_levels(row)
    if levels:
        return "loss ({})".format("both levels" if len(levels) == 2 else levels[0])
    if all(status == "gain" for status in statuses):
        return "gain"
    if all(status == "unchanged" for status in statuses):
        return "unchanged"
    return "within noise"


def compared_benchmark(compare, benchmark, conversations=None, conversations_reason=None):
    """The rows of one benchmark from a gate-compare result, or None when it holds none.

    LoCoMo also needs its conversation-level intervals (``conversations``: gate-conversations' measures). When they
    are absent, ``conversations_reason`` says why and the benchmark cannot be judged as free of loss.
    """
    data = (compare.get("benchmarks") or {}).get(benchmark)
    if not data:
        return None
    rows = [make_row(key, label, data[key]) for key, label in MEASURES if key in data]
    missing = None
    if benchmark in CONVERSATION_LEVEL:
        if conversations is None:
            missing = conversations_reason or "the conversation-level intervals were not computed"
        elif not all(attach_conversation(row, conversations.get(row["measure"])) for row in rows):
            missing = "the conversation-level intervals are missing a measure or have fewer than two conversations"
    categories = [dict(make_row(category, category, entry)) for category, entry in sorted((data.get(CATEGORIES) or {}).items())]
    return {
        "state": "compared", "rows": rows, "categories": categories, "conversation_missing": missing,
        "lost_all_evidence": list(data.get("lost_all_evidence") or []),
        "gained_all_evidence": list(data.get("gained_all_evidence") or []),
    }


def load_conversations(run_dir, name, benchmark):
    """gate-conversations' measures for a benchmark, or (None, why they are missing)."""
    base = os.path.join(run_dir, "compare-{}-{}-conversations".format(name, benchmark))
    data = read_json(base + ".json")
    measures = ((data or {}).get("benchmarks") or {}).get(benchmark)
    if measures:
        return measures, None
    return None, read_text(base + ".refused") or "the conversation-level intervals were not computed"


def load_comparison(run_dir, name):
    """Per benchmark: the compared rows, why gate-compare refused, or that nothing was compared."""
    result = {}
    commits = {}
    for benchmark, _, _ in BENCHMARKS:
        base = os.path.join(run_dir, "compare-{}-{}".format(name, benchmark))
        compare = read_json(base + ".json")
        refused = read_text(base + ".refused")
        conversations, reason = None, None
        if benchmark in CONVERSATION_LEVEL:
            conversations, reason = load_conversations(run_dir, name, benchmark)
        if compare is not None:
            found = compared_benchmark(compare, benchmark, conversations, reason)
            if found is not None:
                result[benchmark] = found
                provenance = (compare.get("before") or {}).get("provenance") or {}
                commits[benchmark] = provenance.get("commit")
                continue
        if refused:
            result[benchmark] = {"state": "refused", "reason": refused}
        else:
            result[benchmark] = {"state": "missing", "reason": "no gate report to compare"}
    return result, commits


def describe_loss(benchmark, benchmark_name, row, level):
    """One loss: the measure, its change and the interval that lies below zero. LoCoMo names the level too."""
    low, high = (row["low"], row["high"]) if level == "question level" else (
        row["conversation"]["low"], row["conversation"]["high"])
    return "{} {}{} {} (95% interval {})".format(
        benchmark_name, row["label"][0].lower() + row["label"][1:],
        ", " + level if benchmark in CONVERSATION_LEVEL else "", pp(row["delta"]), interval(low, high))


def judge(comparison):
    """The verdict over one comparison: loss, no loss, or not compared (a benchmark is missing)."""
    losses, missing = [], []
    for benchmark, name, _ in BENCHMARKS:
        found = comparison[benchmark]
        if found["state"] != "compared":
            missing.append("{} ({})".format(name, found["reason"].splitlines()[-1] if found["reason"] else found["state"]))
            continue
        if found["conversation_missing"]:
            missing.append("{} conversation-level intervals ({})".format(name, found["conversation_missing"]))
        for row in found["rows"]:
            for level in loss_levels(row):
                losses.append({"benchmark": benchmark, "measure": row["measure"], "level": level,
                               "text": describe_loss(benchmark, name, row, level)})
    if losses:
        result = "loss"
        detail = "; ".join(item["text"] for item in losses)
        if missing:
            detail += "; not compared: " + "; ".join(missing)
    elif missing:
        result = "not compared"
        detail = "; ".join(missing)
    else:
        result = "no loss"
        detail = ""
    line = result + (": " + detail if detail else "")
    return {"result": result, "detail": detail, "line": line, "losses": losses, "missing": missing}


def verdict_sentence(verdict):
    """The verdict word, then what it rests on, as one sentence."""
    return verdict["result"] + "." + (" " + verdict["detail"] + "." if verdict["detail"] else "")


def parse_driver_log(path):
    """Gate durations and build attempts from run-builds.sh's driver.log."""
    gates, attempts = {}, {}
    starts = {}
    try:
        with open(path) as handle:
            lines = handle.read().splitlines()
    except OSError:
        return gates, attempts
    for line in lines:
        match = re.match(r"^(\S+Z) gate (\S+): start$", line)
        if match:
            starts[match.group(2)] = parse_time(match.group(1))
            continue
        match = re.match(r"^(\S+Z) gate (\S+): exit (\d+)$", line)
        if match and starts.get(match.group(2)) is not None:
            gates[match.group(2)] = (parse_time(match.group(1)) - starts[match.group(2)]).total_seconds()
            continue
        match = re.match(r"^\S+Z build (\S+): attempt (\d+), ", line)
        if match:
            attempts[match.group(1)] = max(attempts.get(match.group(1), 0), int(match.group(2)))
    return gates, attempts


def build_info(run_dir, env):
    gates, attempts = parse_driver_log(os.path.join(run_dir, "driver.log"))
    builds = {}
    for benchmark, _, label_key in BENCHMARKS:
        label = env.get(label_key)
        summary = read_json(os.path.join(run_dir, "summary-{}.json".format(label))) if label else None
        entry = {"label": label, "built": summary is not None, "attempts": attempts.get(label),
                 "gate_seconds": gates.get(label)}
        if summary:
            calls, hits = summary.get("calls") or 0, summary.get("cache_hits") or 0
            entry.update({
                "packs": summary.get("packs"), "turns": summary.get("turns"), "calls": calls,
                "cache_hits": hits, "prompt_tokens": summary.get("prompt_tokens"),
                "completion_tokens": summary.get("completion_tokens"), "wall_seconds": summary.get("wall_seconds"),
                "claims_per_turn": summary.get("claims_per_turn"),
                "evidence_claim_coverage": summary.get("evidence_claim_coverage"),
                "server_versions": summary.get("server_versions"),
            })
        builds[benchmark] = entry
    return builds


def reference_info(reference_dir, commits):
    env = read_env(os.path.join(reference_dir, "reference.env")) if reference_dir else {}
    commit = env.get("COMMIT") or next((value for value in commits.values() if value), None)
    return {"name": env.get("NAME") or "version unknown", "commit": commit}


def build_report(run_dir, reference_dir=None, previous_run_dir=None):
    env = read_env(os.path.join(run_dir, "run.env"))
    stable, stable_commits = load_comparison(run_dir, "stable")
    previous, previous_commits = load_comparison(run_dir, "previous")
    previous_id = env.get("PREVIOUS_RUN") or (os.path.basename(previous_run_dir.rstrip("/")) if previous_run_dir else "")
    previous_commit = None
    if previous_run_dir:
        previous_commit = read_env(os.path.join(previous_run_dir, "run.env")).get("COMMIT")
    started, finished = parse_time(env.get("STARTED")), parse_time(env.get("FINISHED"))
    builds = build_info(run_dir, env)
    have_previous = bool(previous_id)
    report = {
        "kind": KIND, "schema_version": SCHEMA_VERSION,
        "run": {key.lower(): env.get(key) for key in (
            "RUN_ID", "COMMIT", "STATUS", "DRY_RUN", "STARTED", "FINISHED", "HOST", "OLLAMA_VERSION",
            "OLLAMA_VERSION_END", "MODEL", "MODEL_DIGEST", "LABEL_LOCOMO", "LABEL_LME")},
        "run_seconds": (finished - started).total_seconds() if started and finished else None,
        "stable_reference": reference_info(reference_dir, stable_commits),
        "previous_run": {"run_id": previous_id or None, "commit": previous_commit or next(
            (value for value in previous_commits.values() if value), None)},
        "builds": builds,
        "comparisons": {"stable": stable, "previous": previous if have_previous else None},
        "verdict": {"stable": judge(stable), "previous": judge(previous) if have_previous else None},
        "rule": ("A measure lost when its paired 95% interval lies wholly below zero at either level: the question "
                 "level, and for LoCoMo the conversation level too. The verdict covers the five headline measures of "
                 "each benchmark."),
    }
    return report


def md_table(rows):
    lines = ["| Benchmark | Measure | Before | After | Change | Question-level 95% interval | "
             "Conversation-level 95% interval | Wins | Losses | Ties | Result |",
             "|---|---|---:|---:|---:|---|---|---:|---:|---:|---|"]
    lines += rows
    return lines


def comparison_rows(comparison):
    rows = []
    for benchmark, name, _ in BENCHMARKS:
        found = comparison[benchmark]
        if found["state"] != "compared":
            rows.append("| {} | not compared | | | | | | | | | {} |".format(
                name, (found["reason"].splitlines() or [found["state"]])[-1]))
            continue
        for row in found["rows"]:
            if row["conversation"] is not None:
                conversation = interval(row["conversation"]["low"], row["conversation"]["high"])
            else:
                conversation = "not computed" if benchmark in CONVERSATION_LEVEL else "n/a"
            rows.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                name, row["label"], pct(row["before"]), pct(row["after"]), pp(row["delta"]),
                interval(row["low"], row["high"]), conversation, row["wins"], row["losses"], row["ties"],
                row_result(row)))
    return rows


def category_rows(comparison):
    rows = []
    for benchmark, name, _ in BENCHMARKS:
        found = comparison[benchmark]
        if found["state"] != "compared":
            continue
        for row in found["categories"]:
            rows.append("| {} | {} | {} | {} | {} | {} | {} |".format(
                name, row["measure"], pct(row["before"]), pct(row["after"]), pp(row["delta"]),
                interval(row["low"], row["high"]), row["status"]))
    return rows


def evidence_lines(comparison):
    lines = []
    for benchmark, name, _ in BENCHMARKS:
        found = comparison[benchmark]
        if found["state"] == "compared":
            lines.append("- {}: {} question(s) lost all their packed evidence, {} gained it.".format(
                name, len(found["lost_all_evidence"]), len(found["gained_all_evidence"])))
    return lines


def report_markdown(report, title=True):
    run = report["run"]
    stable, verdicts = report["stable_reference"], report["verdict"]
    commit = run.get("commit") or ""
    dry = run.get("dry_run") == "1"
    lines = []
    if title:
        lines += ["# Scheduled full benchmark {}".format(run.get("run_id") or ""), ""]
    if dry:
        lines += ["**DRY RUN.** The builds and gates did not run. The run's gate reports are copies of the stable "
                  "reference's, so every row ties by construction. This report checks the plumbing only.", ""]
    head = verdicts["stable"]
    lines.append("**Verdict against the last stable release ({}): {}.**{}".format(
        stable["name"], head["result"], " " + head["detail"] + "." if head["detail"] else ""))
    if verdicts["previous"] is not None:
        lines += ["", "Against the previous scheduled run ({}): {}".format(
            report["previous_run"]["run_id"], verdict_sentence(verdicts["previous"]))]
    else:
        lines += ["", "Against the previous scheduled run: none yet, this is the first."]
    lines += ["", "The release rule reads the first line: a stable release needs the latest scheduled run to show no "
              "loss against the last stable release. A measure lost when its paired 95% interval lies wholly below zero at "
              "either level: the question level, and for LoCoMo the conversation level too (LongMemEval-S has the "
              "question level only)."]

    lines += ["", "## Run", "", "| | |", "|---|---|"]
    lines.append("| Commit | `{}` |".format(commit))
    lines.append("| Started | {} |".format(run.get("started") or "n/a"))
    lines.append("| Finished | {} |".format(run.get("finished") or "n/a"))
    lines.append("| Run time | {} |".format(duration(report["run_seconds"])))
    lines.append("| Status | {} |".format(run.get("status") or "n/a"))
    lines.append("| Machine | {} |".format(run.get("host") or "n/a"))
    versions = run.get("ollama_version") or "n/a"
    if run.get("ollama_version_end") and run.get("ollama_version_end") != run.get("ollama_version"):
        versions += " at the start, {} at the end".format(run["ollama_version_end"])
    lines.append("| Ollama | {} |".format(versions))
    digest = (run.get("model_digest") or "")[:8]
    lines.append("| Extraction model | {}{} |".format(run.get("model") or "n/a", " (identity {})".format(digest) if digest else ""))
    lines.append("| Stable reference | {} (commit `{}`) |".format(stable["name"], stable["commit"] or "unknown"))
    if report["previous_run"]["run_id"]:
        lines.append("| Previous run | {} (commit `{}`) |".format(
            report["previous_run"]["run_id"], report["previous_run"]["commit"] or "unknown"))

    lines += ["", "## Builds and gates", "",
              "| Benchmark | Packs | Model calls | From the cache | Build time | Attempts | Gate time | Claims per turn | Evidence turns with a claim |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for benchmark, name, _ in BENCHMARKS:
        build = report["builds"][benchmark]
        if not build["built"]:
            lines.append("| {} | not built | | | | {} | | | |".format(name, build["attempts"] or ""))
            continue
        total = build["calls"] + build["cache_hits"]
        replay = "{:,}/{:,} ({:.0%})".format(build["cache_hits"], total, build["cache_hits"] / total) if total else "n/a"
        claims = "n/a" if build["claims_per_turn"] is None else "{:.2f}".format(build["claims_per_turn"])
        lines.append("| {} | {} | {:,} | {} | {} | {} | {} | {} | {} |".format(
            name, build["packs"], build["calls"], replay, duration(build["wall_seconds"]), build["attempts"] or "n/a",
            duration(build["gate_seconds"]), claims, pct(build["evidence_claim_coverage"])))
    lines += ["", "A build with more than one attempt paused (for example at an Ollama usage limit) and resumed. "
              "A run whose model calls are mostly replayed from the cache has an unchanged prompt; its build time is "
              "the warm-cache time."]

    lines += ["", "## Against the last stable release ({})".format(stable["name"]), ""]
    lines += md_table(comparison_rows(report["comparisons"]["stable"]))
    lines += [""] + evidence_lines(report["comparisons"]["stable"])
    if report["comparisons"]["previous"] is not None:
        lines += ["", "## Against the previous scheduled run ({})".format(report["previous_run"]["run_id"]), ""]
        lines += md_table(comparison_rows(report["comparisons"]["previous"]))
        lines += [""] + evidence_lines(report["comparisons"]["previous"])

    categories = category_rows(report["comparisons"]["stable"])
    if categories:
        lines += ["", "## All evidence packed by category, against the last stable release (not part of the verdict)", "",
                  "| Benchmark | Category | Before | After | Change | 95% interval | Result |",
                  "|---|---|---:|---:|---:|---|---|"] + categories
        lines += ["", "Each category rests on 30 to 130 questions, so a category marked loss is a reason to look, "
                  "not a failed gate."]
    lines += ["", "Question-level intervals resample questions and conversation-level intervals resample LoCoMo's 10 whole "
              "conversations (paired bootstrap, 2,000 samples, seed 42 for both). Projected accuracy is a planning "
              "estimate from the saved GPT-5.4 run, not an answer score. The answer track is not part of the scheduled run."]
    return "\n".join(lines) + "\n"


def write_report(run_dir, reference_dir=None, previous_run_dir=None):
    report = build_report(run_dir, reference_dir, previous_run_dir)
    with open(os.path.join(run_dir, "report.json"), "w") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    with open(os.path.join(run_dir, "report.md"), "w") as handle:
        handle.write(report_markdown(report))
    return report


def pr_body(run_dir, files_verified=None):
    report = read_json(os.path.join(run_dir, "report.json"))
    if report is None:
        raise SystemExit("No report.json in {}".format(run_dir))
    run, stable, verdicts = report["run"], report["stable_reference"], report["verdict"]
    commit = run.get("commit") or ""
    run_id = run.get("run_id") or ""
    folder = "benchmarks/results/research/weekly/{}".format(run_id)
    lines = ["## Summary", "",
             "- Brings back scheduled full benchmark run `{}` from the Mac mini, as `memory_bank/HANDOFF-2026-10-19-WEEKLY-BENCHMARK.md` "
             "describes: the comparison report, the gate comparisons, the build summaries and the driver log.".format(run_id),
             "- **Verdict against the last stable release ({}): {}.**{}".format(
                 stable["name"], verdicts["stable"]["result"],
                 " " + verdicts["stable"]["detail"] + "." if verdicts["stable"]["detail"] else ""),
             "- Results only: no code under `src/` or `benchmarks/` changes, and no default changes.", "",
             "## What was run, where, and at which commit", "",
             "- Machine: {}, with Ollama {} and the extraction model {} (identity `{}`). No paid API was used.".format(
                 run.get("host") or "the Mac mini", run.get("ollama_version") or "n/a", run.get("model") or "n/a",
                 (run.get("model_digest") or "")[:8] or "n/a"),
             "- Commit: `{}` (`main` when the run started at {}).".format(commit, run.get("started") or "n/a"),
             "- Run time: {}; status: {}.".format(duration(report.get("run_seconds")), run.get("status") or "n/a"),
             "- Builds: the defaults on LoCoMo and LongMemEval-S through `ingest()` (`scripts/full-benchmark/run-builds.sh` with "
             "`BUILDS=defaults`), then `product_packing gate` over each and `gate-compare` against the stable reference and "
             "the previous scheduled run. The answer track did not run.", "",
             report_markdown(report, title=False).rstrip("\n").replace("\n## ", "\n### "), "",
             "## Files", "",
             "- `{}/`: `report.md`, `report.json`, `run.env`, `driver.log`, the build summaries (`summary-*.json`), the gate "
             "comparisons (`compare-*.json` and `.md`) and the gate reports' Markdown (`gate-*.md`). The gate reports' JSON "
             "(about 2.7 MB) and the packs (about 33 GB) stay on the Mac mini; each comparison JSON records the sha256 of the "
             "two gate reports it read.".format(folder), "",
             "## Test plan", ""]
    if files_verified is not None:
        lines.append("- [x] Every copied file matches the sha256 of its original on the Mac mini ({} files).".format(files_verified))
    lines += ["- [x] Results only, so no code tests apply.", "", "Part of dwamianm/prism-monorepo#272."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report", help="Write report.json and report.md into a run folder")
    report.add_argument("run_dir")
    report.add_argument("--reference-dir", help="The stable reference folder (reference.env names it and its commit)")
    report.add_argument("--previous-run-dir", help="The previous scheduled run's folder, for its id and commit")
    verdict = commands.add_parser("verdict", help="Print the verdict lines of a run folder's report.json")
    verdict.add_argument("run_dir")
    body = commands.add_parser("pr-body", help="Print the results pull request text for a run folder")
    body.add_argument("run_dir")
    body.add_argument("--files-verified", type=int, help="How many copied files matched their original's sha256")
    args = parser.parse_args(argv)
    if args.command == "report":
        written = write_report(args.run_dir, args.reference_dir, args.previous_run_dir)
        verdicts = written["verdict"]
        print("stable: {}".format(verdicts["stable"]["line"] or "not compared"))
        if verdicts["previous"] is not None:
            print("previous: {}".format(verdicts["previous"]["line"] or "not compared"))
    elif args.command == "verdict":
        data = read_json(os.path.join(args.run_dir, "report.json"))
        if data is None:
            print("no report")
            return 1
        print("stable: {}".format(data["verdict"]["stable"]["line"] or "not compared"))
        if data["verdict"]["previous"] is not None:
            print("previous: {}".format(data["verdict"]["previous"]["line"] or "not compared"))
    else:
        sys.stdout.write(pr_body(args.run_dir, args.files_verified))
    return 0


if __name__ == "__main__":
    sys.exit(main())
