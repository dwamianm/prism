"""The scheduled full benchmark's verdict and report (#272, scripts/full-benchmark/weekly_report.py).

The report script reads gate-compare results and says whether the run lost
anything against the last stable release and against the previous scheduled
run. A stable release waits for that verdict, so its rule is tested here: a
measure is a loss only when its paired 95% interval lies wholly below zero.
"""
import ast
import importlib.util
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts" / "full-benchmark"


def load_report_module():
    """Load the script by path. It is not part of an importable package."""
    spec = importlib.util.spec_from_file_location("weekly_report", SCRIPTS / "weekly_report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def weekly():
    return load_report_module()


def entry(before, after, low, high, wins=1, losses=1, ties=98, queries=100):
    return {"queries": queries, "before": before, "after": after, "delta": after - before,
            "interval_95": [low, high], "wins": wins, "losses": losses, "ties": ties}


MEASURE_KEYS = ("all_evidence_packed", "all_evidence_packed_with_text", "evidence_recall",
                "all_evidence_returned", "projected_accuracy")


def compare_result(benchmark, **overrides):
    """A gate-compare result for one benchmark: every measure ties unless overridden."""
    data = {key: entry(0.9, 0.9, 0.0, 0.0, 0, 0, 100) for key in MEASURE_KEYS}
    data.update(overrides)
    data["all_evidence_packed_by_category"] = {"multi-hop": entry(0.5, 0.5, -0.05, 0.05)}
    data["lost_all_evidence"] = []
    data["gained_all_evidence"] = []
    return {"kind": "offline-evidence-gate-comparison", "complete": True,
            "before": {"provenance": {"commit": "d810ee5552e388f85aac62f4cf8c6c6038f0b1c8"}},
            "after": {"provenance": {"commit": "a" * 40}}, "benchmarks": {benchmark: data}}


def conversation_result(**overrides):
    """A gate-conversations result for LoCoMo: every measure ties unless given a (low, high) interval."""
    measures = {key: {"queries": 100, "groups": 10, "delta": 0.0, "interval_95": [0.0, 0.0]} for key in MEASURE_KEYS}
    for key, (low, high) in overrides.items():
        measures[key] = {**measures[key], "interval_95": [low, high]}
    return {"kind": "gate-conversation-intervals", "benchmarks": {"locomo": measures}}


def make_run(tmp_path, stable=None, previous=None, previous_id="", dry=False, conversations="ties"):
    """A run folder as weekly-run.sh leaves it. stable and previous map a benchmark to a compare result or a refusal.

    ``conversations`` is "ties" (LoCoMo's conversation-level intervals tie wherever a LoCoMo comparison exists),
    False (none were computed), or a mapping of "stable" and "previous" to a result or a refusal text."""
    run_dir = tmp_path / "20261022-053000-aaaaaaaa"
    run_dir.mkdir()
    (run_dir / "run.env").write_text("\n".join([
        "RUN_ID=20261022-053000-aaaaaaaa", "COMMIT=" + "a" * 40, "STATUS=" + ("dry-run" if dry else "complete"),
        "DRY_RUN=" + ("1" if dry else "0"), "STARTED=2026-10-22T05:30:00Z", "FINISHED=2026-10-22T20:45:00Z",
        "HOST=mini", "OLLAMA_VERSION=0.40.4", "OLLAMA_VERSION_END=0.40.4", "MODEL=deepseek-v4.1-flash:cloud",
        "MODEL_DIGEST=e04da138d31e0c9468e982e1ae9503d06cb7e170caa16a90c17d931c4aa140f8",
        "LABEL_LOCOMO=weekly-locomo-x", "LABEL_LME=weekly-lme-x", "PREVIOUS_RUN=" + previous_id, ""]))
    for name, results in (("stable", stable), ("previous", previous)):
        for benchmark, result in (results or {}).items():
            base = run_dir / "compare-{}-{}".format(name, benchmark)
            if isinstance(result, str):
                base.with_suffix(".refused").write_text(result + "\n")
            else:
                base.with_suffix(".json").write_text(json.dumps(result))
    for name, results in (("stable", stable), ("previous", previous)):
        found = conversations if isinstance(conversations, dict) else (
            {name: conversation_result()} if conversations == "ties" and isinstance((results or {}).get("locomo"), dict)
            else {})
        base = run_dir / "compare-{}-locomo-conversations".format(name)
        if isinstance(found.get(name), str):
            base.with_suffix(".refused").write_text(found[name] + "\n")
        elif found.get(name) is not None:
            base.with_suffix(".json").write_text(json.dumps(found[name]))
    (run_dir / "summary-weekly-locomo-x.json").write_text(json.dumps({
        "packs": 10, "turns": 5882, "calls": 100, "cache_hits": 175, "prompt_tokens": 1, "completion_tokens": 1,
        "wall_seconds": 800.0, "claims_per_turn": 0.87, "evidence_claim_coverage": 0.84}))
    (run_dir / "driver.log").write_text(
        "2026-10-22T05:31:00Z build weekly-locomo-x: attempt 1, 0 of 10 packs complete so far\n"
        "2026-10-22T05:44:00Z gate weekly-locomo-x: start\n2026-10-22T05:53:00Z gate weekly-locomo-x: exit 0\n")
    return run_dir


def both(locomo, longmemeval):
    return {"locomo": locomo, "longmemeval": longmemeval}


class TestRowStatus:
    def test_an_interval_wholly_below_zero_is_a_loss(self, weekly):
        assert weekly.row_status(-0.014, -0.028, -0.001) == "loss"

    def test_a_drop_whose_interval_reaches_zero_is_noise(self, weekly):
        assert weekly.row_status(-0.004, -0.015, 0.006) == "within noise"
        assert weekly.row_status(-0.018, -0.054, 0.0) == "within noise"

    def test_an_interval_wholly_above_zero_is_a_gain(self, weekly):
        assert weekly.row_status(0.016, 0.001, 0.047) == "gain"

    def test_nothing_moving_is_unchanged(self, weekly):
        assert weekly.row_status(0.0, 0.0, 0.0) == "unchanged"

    def test_float_dust_around_zero_is_never_a_loss(self, weekly):
        assert weekly.row_status(0.0, -1e-15, -1e-15) == "unchanged"


class TestVerdict:
    def comparison(self, weekly, locomo, longmemeval, conversations=None):
        conversations = conversation_result() if conversations is None else conversations
        return {"locomo": weekly.compared_benchmark(locomo, "locomo", conversations["benchmarks"]["locomo"]),
                "longmemeval": weekly.compared_benchmark(longmemeval, "longmemeval")}

    def test_no_loss_when_every_interval_reaches_zero(self, weekly):
        locomo = compare_result("locomo", all_evidence_packed=entry(0.84, 0.836, -0.014, 0.006))
        verdict = weekly.judge(self.comparison(weekly, locomo, compare_result("longmemeval")))
        assert verdict["result"] == "no loss"
        assert verdict["line"] == "no loss"

    def test_a_loss_names_the_measure_and_its_interval(self, weekly):
        locomo = compare_result("locomo", all_evidence_packed=entry(0.84, 0.826, -0.028, -0.001))
        verdict = weekly.judge(self.comparison(weekly, locomo, compare_result("longmemeval")))
        assert verdict["result"] == "loss"
        assert verdict["line"] == ("loss: LoCoMo all evidence packed, question level -1.4 pp "
                                   "(95% interval -2.8 to -0.1 pp)")

    def test_two_losses_are_both_named(self, weekly):
        locomo = compare_result("locomo", all_evidence_packed=entry(0.84, 0.826, -0.028, -0.001))
        lme = compare_result("longmemeval", projected_accuracy=entry(0.92, 0.9, -0.03, -0.01))
        verdict = weekly.judge(self.comparison(weekly, locomo, lme))
        assert [item["benchmark"] for item in verdict["losses"]] == ["locomo", "longmemeval"]
        assert "LongMemEval-S projected accuracy -2.0 pp" in verdict["line"]

    def test_a_category_below_zero_is_not_part_of_the_verdict(self, weekly):
        locomo = compare_result("locomo")
        locomo["benchmarks"]["locomo"]["all_evidence_packed_by_category"] = {"multi-hop": entry(0.5, 0.4, -0.2, -0.05)}
        assert weekly.judge(self.comparison(weekly, locomo, compare_result("longmemeval")))["result"] == "no loss"

    def test_a_missing_benchmark_is_not_no_loss(self, weekly):
        comparison = {"locomo": weekly.compared_benchmark(compare_result("locomo"), "locomo",
                                                          conversation_result()["benchmarks"]["locomo"]),
                      "longmemeval": {"state": "refused", "reason": "gate-compare refused the pair (exit 1): The reports use different datasets"}}
        verdict = weekly.judge(comparison)
        assert verdict["result"] == "not compared"
        assert verdict["line"].startswith("not compared: LongMemEval-S (gate-compare refused the pair")

    def test_a_loss_wins_over_a_missing_benchmark(self, weekly):
        comparison = {"locomo": weekly.compared_benchmark(
            compare_result("locomo", evidence_recall=entry(0.9, 0.8, -0.2, -0.1)), "locomo",
            conversation_result()["benchmarks"]["locomo"]),
            "longmemeval": {"state": "missing", "reason": "no gate report to compare"}}
        verdict = weekly.judge(comparison)
        assert verdict["result"] == "loss"
        assert "; not compared: LongMemEval-S" in verdict["line"]

    def test_a_loss_only_at_the_conversation_level_is_a_loss(self, weekly):
        """The question-level interval reaches zero; the conversation-level one lies wholly below it."""
        locomo = compare_result("locomo", all_evidence_packed=entry(0.84, 0.834, -0.015, 0.003))
        conversations = conversation_result(all_evidence_packed=(-0.011, -0.002))
        verdict = weekly.judge(self.comparison(weekly, locomo, compare_result("longmemeval"), conversations))
        assert verdict["result"] == "loss"
        assert verdict["line"] == ("loss: LoCoMo all evidence packed, conversation level -0.6 pp "
                                   "(95% interval -1.1 to -0.2 pp)")
        assert [(item["measure"], item["level"]) for item in verdict["losses"]] == [
            ("all_evidence_packed", "conversation level")]

    def test_a_loss_only_at_the_question_level_is_a_loss_too(self, weekly):
        """Both levels count: the conversation-level interval reaching zero does not excuse the question level."""
        locomo = compare_result("locomo", all_evidence_packed=entry(0.84, 0.826, -0.028, -0.001))
        conversations = conversation_result(all_evidence_packed=(-0.05, 0.01))
        verdict = weekly.judge(self.comparison(weekly, locomo, compare_result("longmemeval"), conversations))
        assert verdict["result"] == "loss" and "question level" in verdict["line"]
        assert "conversation level" not in verdict["line"]

    def test_a_loss_at_both_levels_names_both(self, weekly):
        locomo = compare_result("locomo", evidence_recall=entry(0.9, 0.85, -0.07, -0.03))
        conversations = conversation_result(evidence_recall=(-0.08, -0.02))
        verdict = weekly.judge(self.comparison(weekly, locomo, compare_result("longmemeval"), conversations))
        assert [item["level"] for item in verdict["losses"]] == ["question level", "conversation level"]
        assert "LoCoMo evidence recall, question level -5.0 pp (95% interval -7.0 to -3.0 pp); " in verdict["line"]
        assert "LoCoMo evidence recall, conversation level -5.0 pp (95% interval -8.0 to -2.0 pp)" in verdict["line"]

    def test_locomo_without_conversation_intervals_cannot_read_no_loss(self, weekly):
        locomo = weekly.compared_benchmark(compare_result("locomo"), "locomo", None, "gate-conversations failed (exit 1)")
        comparison = {"locomo": locomo, "longmemeval": weekly.compared_benchmark(
            compare_result("longmemeval"), "longmemeval")}
        verdict = weekly.judge(comparison)
        assert verdict["result"] == "not compared"
        assert verdict["line"] == "not compared: LoCoMo conversation-level intervals (gate-conversations failed (exit 1))"

    def test_conversation_intervals_with_a_measure_missing_cannot_read_no_loss(self, weekly):
        partial = conversation_result()["benchmarks"]["locomo"]
        del partial["evidence_recall"]
        comparison = {"locomo": weekly.compared_benchmark(compare_result("locomo"), "locomo", partial),
                      "longmemeval": weekly.compared_benchmark(compare_result("longmemeval"), "longmemeval")}
        assert weekly.judge(comparison)["result"] == "not compared"

    def test_longmemeval_has_the_question_level_only_and_a_loss_there_needs_no_conversations(self, weekly):
        lme = compare_result("longmemeval", projected_accuracy=entry(0.92, 0.9, -0.03, -0.01))
        verdict = weekly.judge(self.comparison(weekly, compare_result("locomo"), lme))
        assert verdict["line"] == "loss: LongMemEval-S projected accuracy -2.0 pp (95% interval -3.0 to -1.0 pp)"

    def test_a_gain_needs_both_levels_above_zero(self, weekly):
        locomo = compare_result("locomo", all_evidence_packed=entry(0.84, 0.86, 0.005, 0.03))
        both_levels = weekly.compared_benchmark(locomo, "locomo", conversation_result(
            all_evidence_packed=(0.004, 0.035))["benchmarks"]["locomo"])
        one_level = weekly.compared_benchmark(locomo, "locomo", conversation_result(
            all_evidence_packed=(-0.01, 0.04))["benchmarks"]["locomo"])
        assert weekly.row_result(both_levels["rows"][0]) == "gain"
        assert weekly.row_result(one_level["rows"][0]) == "within noise"


class TestReport:
    def test_a_clean_run_reads_no_loss_and_none_yet_for_the_first_previous(self, weekly, tmp_path):
        run_dir = make_run(tmp_path, stable=both(compare_result("locomo"), compare_result("longmemeval")))
        report = weekly.write_report(str(run_dir), reference_dir=None)
        assert report["verdict"]["stable"]["result"] == "no loss"
        assert report["verdict"]["previous"] is None
        text = (run_dir / "report.md").read_text()
        assert "**Verdict against the last stable release (version unknown): no loss.**\n" in text
        assert "none yet, this is the first" in text
        assert json.loads((run_dir / "report.json").read_text())["kind"] == "scheduled-full-benchmark-report"

    def test_the_reference_names_the_stable_release(self, weekly, tmp_path):
        run_dir = make_run(tmp_path, stable=both(compare_result("locomo"), compare_result("longmemeval")))
        reference = tmp_path / "reference"
        reference.mkdir()
        (reference / "reference.env").write_text("NAME=v0.15.0\nCOMMIT=d810ee55\n")
        weekly.write_report(str(run_dir), reference_dir=str(reference))
        assert "**Verdict against the last stable release (v0.15.0): no loss.**\n" in (run_dir / "report.md").read_text()

    def test_a_previous_run_is_judged_on_its_own(self, weekly, tmp_path):
        losing = compare_result("longmemeval", all_evidence_returned=entry(1.0, 0.99, -0.02, -0.001))
        run_dir = make_run(tmp_path, stable=both(compare_result("locomo"), compare_result("longmemeval")),
                           previous=both(compare_result("locomo"), losing), previous_id="20261019-053000-bbbbbbbb")
        report = weekly.write_report(str(run_dir))
        assert report["verdict"]["stable"]["result"] == "no loss"
        assert report["verdict"]["previous"]["result"] == "loss"
        assert "Against the previous scheduled run (20261019-053000-bbbbbbbb): loss. LongMemEval-S" in (
            run_dir / "report.md").read_text()

    def test_a_refused_comparison_is_reported_not_hidden(self, weekly, tmp_path):
        run_dir = make_run(tmp_path, stable=both(compare_result("locomo"),
                                                 "gate-compare refused the pair (exit 1): Question sets differ"))
        report = weekly.write_report(str(run_dir))
        assert report["verdict"]["stable"]["result"] == "not compared"
        assert "Question sets differ" in (run_dir / "report.md").read_text()

    def test_no_comparison_files_at_all_is_not_no_loss(self, weekly, tmp_path):
        report = weekly.write_report(str(make_run(tmp_path)))
        assert report["verdict"]["stable"]["result"] == "not compared"

    def test_the_build_table_reports_cache_replay_and_times(self, weekly, tmp_path):
        run_dir = make_run(tmp_path, stable=both(compare_result("locomo"), compare_result("longmemeval")))
        weekly.write_report(str(run_dir))
        text = (run_dir / "report.md").read_text()
        assert "| LoCoMo | 10 | 100 | 175/275 (64%) | 13.3 min | 1 | 9.0 min | 0.87 | 84.0% |" in text
        assert "| LongMemEval-S | not built |" in text

    def test_a_dry_run_says_so_at_the_top(self, weekly, tmp_path):
        run_dir = make_run(tmp_path, stable=both(compare_result("locomo"), compare_result("longmemeval")), dry=True)
        weekly.write_report(str(run_dir))
        assert "**DRY RUN.**" in (run_dir / "report.md").read_text()

    def test_every_table_row_has_as_many_cells_as_its_header(self, weekly, tmp_path):
        run_dir = make_run(tmp_path, stable=both(compare_result("locomo"), "refused: no reference"))
        weekly.write_report(str(run_dir))
        width = None
        for line in (run_dir / "report.md").read_text().splitlines():
            if not line.startswith("|"):
                width = None
                continue
            cells = len(line.strip().strip("|").split("|"))
            width = width or cells
            assert cells == width, line

    def test_the_verdict_subcommand_reads_the_written_report(self, weekly, tmp_path, capsys):
        run_dir = make_run(tmp_path, stable=both(compare_result("locomo"), compare_result("longmemeval")))
        weekly.write_report(str(run_dir))
        assert weekly.main(["verdict", str(run_dir)]) == 0
        assert capsys.readouterr().out.splitlines()[0] == "stable: no loss"


class TestConversationLevel:
    """LoCoMo's conversation-level intervals in the report, over fixtures with no model and no Mac mini."""

    def losing_at_the_conversation_level(self):
        locomo = compare_result("locomo", all_evidence_packed=entry(0.84, 0.834, -0.015, 0.003))
        return both(locomo, compare_result("longmemeval")), {"stable": conversation_result(
            all_evidence_packed=(-0.011, -0.002))}

    def test_the_table_shows_both_intervals_and_the_level_of_a_loss(self, weekly, tmp_path):
        stable, conversations = self.losing_at_the_conversation_level()
        run_dir = make_run(tmp_path, stable=stable, conversations=conversations)
        report = weekly.write_report(str(run_dir))
        text = (run_dir / "report.md").read_text()
        assert "| Benchmark | Measure | Before | After | Change | Question-level 95% interval | Conversation-level 95% interval |" in text
        assert ("| LoCoMo | All evidence packed | 84.0% | 83.4% | -0.6 pp | -1.5 to +0.3 pp | -1.1 to -0.2 pp | "
                "1 | 1 | 98 | loss (conversation level) |") in text
        assert "| LoCoMo | Evidence recall | 90.0% | 90.0% | +0.0 pp | +0.0 to +0.0 pp | +0.0 to +0.0 pp | 0 | 0 | 100 | unchanged |" in text
        assert "| LongMemEval-S | Evidence recall | 90.0% | 90.0% | +0.0 pp | +0.0 to +0.0 pp | n/a | 0 | 0 | 100 | unchanged |" in text
        assert text.startswith("# Scheduled full benchmark") and (
            "**Verdict against the last stable release (version unknown): loss.** LoCoMo all evidence packed, "
            "conversation level -0.6 pp (95% interval -1.1 to -0.2 pp).") in text
        row = report["comparisons"]["stable"]["locomo"]["rows"][0]
        assert row["status"] == "within noise" and row["conversation"]["status"] == "loss"

    def test_a_loss_at_either_level_is_the_headline_verdict_the_release_rule_reads(self, weekly, tmp_path):
        stable, conversations = self.losing_at_the_conversation_level()
        weekly.write_report(str(make_run(tmp_path, stable=stable, conversations=conversations)))
        data = json.loads((tmp_path / "20261022-053000-aaaaaaaa" / "report.json").read_text())
        assert data["verdict"]["stable"]["result"] == "loss"
        assert data["verdict"]["stable"]["losses"][0]["level"] == "conversation level"

    def test_conversation_intervals_that_were_not_computed_read_not_compared(self, weekly, tmp_path):
        run_dir = make_run(tmp_path, stable=both(compare_result("locomo"), compare_result("longmemeval")),
                           conversations=False)
        report = weekly.write_report(str(run_dir))
        assert report["verdict"]["stable"]["result"] == "not compared"
        text = (run_dir / "report.md").read_text()
        assert "not compared.** LoCoMo conversation-level intervals (the conversation-level intervals were not computed)." in text
        assert "| not computed |" in text

    def test_a_failed_resampling_says_why(self, weekly, tmp_path):
        run_dir = make_run(tmp_path, stable=both(compare_result("locomo"), compare_result("longmemeval")),
                           conversations={"stable": "gate-conversations failed (exit 1): ValueError: Question sets differ"})
        report = weekly.write_report(str(run_dir))
        assert report["verdict"]["stable"]["result"] == "not compared"
        assert "gate-conversations failed (exit 1): ValueError: Question sets differ" in (run_dir / "report.md").read_text()

    def test_the_previous_run_is_judged_at_both_levels_too(self, weekly, tmp_path):
        stable = both(compare_result("locomo"), compare_result("longmemeval"))
        previous = both(compare_result("locomo", evidence_recall=entry(0.9, 0.89, -0.03, 0.01)),
                        compare_result("longmemeval"))
        conversations = {"stable": conversation_result(),
                         "previous": conversation_result(evidence_recall=(-0.02, -0.004))}
        run_dir = make_run(tmp_path, stable=stable, previous=previous, previous_id="20261019-053000-bbbbbbbb",
                           conversations=conversations)
        report = weekly.write_report(str(run_dir))
        assert report["verdict"]["stable"]["result"] == "no loss"
        assert report["verdict"]["previous"]["result"] == "loss"
        assert "LoCoMo evidence recall, conversation level" in report["verdict"]["previous"]["line"]

    def test_a_refused_question_level_comparison_is_not_rescued_by_the_conversation_level(self, weekly, tmp_path):
        run_dir = make_run(tmp_path, stable=both("gate-compare refused the pair (exit 1): Question sets differ",
                                                 compare_result("longmemeval")),
                           conversations={"stable": conversation_result()})
        assert weekly.write_report(str(run_dir))["verdict"]["stable"]["result"] == "not compared"

    def test_real_gate_compare_and_gate_conversations_output_give_a_loss_where_only_one_level_shows_it(
            self, weekly, tmp_path):
        """Fixture gate reports through the real comparison code: the question-level interval spans zero and the
        conversation-level one lies below it, so the report must say loss at the conversation level."""
        from benchmarks.diagnostics.product_packing import compare_gates, conversation_intervals
        from tests.test_product_packing_diagnostic import even_loss_fixtures

        before, after = even_loss_fixtures()
        questions = compare_gates(before, after, samples=500)
        conversations = conversation_intervals(before, after, samples=500)
        locomo = questions["benchmarks"]["locomo"]["all_evidence_packed"]
        assert locomo["interval_95"][0] < 0 < locomo["interval_95"][1]
        run_dir = make_run(tmp_path, stable=both(questions, compare_result("longmemeval")),
                           conversations={"stable": conversations})
        report = weekly.write_report(str(run_dir))
        assert report["verdict"]["stable"]["result"] == "loss"
        levels = {(item["measure"], item["level"]) for item in report["verdict"]["stable"]["losses"]}
        assert ("all_evidence_packed", "conversation level") in levels
        assert ("all_evidence_packed", "question level") not in levels


class TestOutboundText:
    """The report and the pull request text are published: plain US English, no em or en dashes."""

    def test_neither_the_report_nor_the_pull_request_text_has_a_long_dash(self, weekly, tmp_path):
        losing = compare_result("locomo", all_evidence_packed=entry(0.84, 0.826, -0.028, -0.001))
        run_dir = make_run(tmp_path, stable=both(losing, compare_result("longmemeval")))
        weekly.write_report(str(run_dir))
        text = (run_dir / "report.md").read_text() + weekly.pr_body(str(run_dir), files_verified=9)
        assert "—" not in text and "–" not in text

    def test_the_pull_request_text_links_the_issue_without_closing_it(self, weekly, tmp_path):
        run_dir = make_run(tmp_path, stable=both(compare_result("locomo"), compare_result("longmemeval")))
        weekly.write_report(str(run_dir))
        body = weekly.pr_body(str(run_dir), files_verified=9)
        assert "Part of dwamianm/prism-monorepo#272." in body
        assert not re.search(r"\b(closes|fixes|resolves)\b", body, re.IGNORECASE)
        assert "Every copied file matches the sha256 of its original on the Mac mini (9 files)." in body
        assert "benchmarks/results/research/weekly/20261022-053000-aaaaaaaa/" in body


class TestScripts:
    @pytest.mark.parametrize("script", sorted(path.name for path in SCRIPTS.glob("*.sh")))
    def test_every_script_parses(self, script):
        """bash -n over each script, so a syntax slip cannot reach the Mac mini's 00:30 trigger."""
        done = subprocess.run(["bash", "-n", str(SCRIPTS / script)], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr

    def test_the_report_script_runs_on_the_mac_minis_system_python(self):
        """The Mac mini's /usr/bin/python3 is 3.9: no match statements, no X | Y types at runtime."""
        source = (SCRIPTS / "weekly_report.py").read_text()
        assert "from __future__ import annotations" in source
        ast.parse(source, feature_version=(3, 9))


# ---- weekly-run.sh and install-schedule.sh, over stand-ins for uv, curl and caffeinate -------------------------

FAKE_UV = '''#!/usr/bin/env python3
"""Stands in for uv: simulates the benchmark commands that run-builds.sh and weekly-run.sh call."""
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ["FAKE_UV_LOG"], "a") as handle:
    handle.write(" ".join(args) + "\\n")
if args[:1] == ["sync"]:
    sys.exit(0)


def opt(name):
    return args[args.index(name) + 1] if name in args else None


module = next((args[i + 1] for i, a in enumerate(args) if a == "-m"), "")
verb = args[args.index(module) + 1] if module in args else ""
if "extracted_packs" in module and verb == "build":
    benchmark, label, root = opt("--benchmark"), opt("--label"), Path(opt("--root"))
    total = {"locomo": 10, "longmemeval": 500}[benchmark]
    (root / label).mkdir(parents=True, exist_ok=True)
    marker = root / label / ".attempt"
    attempts = int(marker.read_text()) + 1 if marker.exists() else 1
    marker.write_text(str(attempts))
    short = os.environ.get("FAKE_BUILD_PAUSE_ALWAYS") == "1" or (
        os.environ.get("FAKE_BUILD_PAUSE_ONCE") == "1" and attempts == 1)
    for index in range(total - 1 if short else total):
        pack = root / label / benchmark / "q{:04d}".format(index)
        pack.mkdir(parents=True, exist_ok=True)
        (pack / "manifest.json").write_text("{}")
    sys.exit(1 if short else 0)
if "extracted_packs" in module and verb == "summary":
    print(json.dumps({"label": opt("--label"), "packs": 10, "turns": 5882, "calls": 275, "cache_hits": 0,
                      "prompt_tokens": 1, "completion_tokens": 1, "wall_seconds": 780.0, "claims_per_turn": 0.9,
                      "evidence_claim_coverage": 0.84}))
    sys.exit(0)
if "product_packing" in module and verb == "gate":
    output = Path(opt("--output"))
    output.write_text(json.dumps({"benchmark": opt("--benchmark"), "quality": float(os.environ.get("FAKE_QUALITY", "0.9"))}))
    output.with_suffix(".md").write_text("# gate\\n")
    sys.exit(0)
if "product_packing" in module and verb == "gate-compare":
    before, after = (json.loads(Path(args[args.index("gate-compare") + k]).read_text()) for k in (1, 2))
    output = Path(opt("--output"))
    delta = after["quality"] - before["quality"]

    def entry():
        return {"queries": 100, "before": before["quality"], "after": after["quality"], "delta": delta,
                "interval_95": [delta - 0.01, delta + 0.01], "wins": 1, "losses": 1, "ties": 98}

    data = {key: entry() for key in ("all_evidence_packed", "all_evidence_packed_with_text", "evidence_recall",
                                     "all_evidence_returned", "projected_accuracy")}
    data["all_evidence_packed_by_category"] = {"multi-hop": entry()}
    data["lost_all_evidence"], data["gained_all_evidence"] = [], []
    output.write_text(json.dumps({"benchmarks": {after["benchmark"]: data},
                                  "before": {"provenance": {"commit": "d810ee55"}}}))
    output.with_suffix(".md").write_text("# compare\\n")
    sys.exit(0)
if "product_packing" in module and verb == "gate-conversations":
    before, after = (json.loads(Path(args[args.index("gate-conversations") + k]).read_text()) for k in (1, 2))
    if os.environ.get("FAKE_CONV_FAIL") == "1":
        sys.stderr.write("ValueError: the conversation resampling failed\\n")
        sys.exit(2)
    delta = after["quality"] - before["quality"]
    width = float(os.environ.get("FAKE_CONV_WIDTH", "0.01"))
    measures = {key: {"queries": 100, "groups": 10, "delta": delta, "interval_95": [delta - width, delta + width]}
                for key in ("all_evidence_packed", "all_evidence_packed_with_text", "evidence_recall",
                            "all_evidence_returned", "projected_accuracy")}
    Path(opt("--output")).write_text(json.dumps({"benchmarks": {"locomo": measures}}))
    sys.exit(0)
sys.stderr.write("fake uv: unhandled " + " ".join(args) + "\\n")
sys.exit(3)
'''

FAKE_CURL = '''#!/bin/bash
[ "${FAKE_OLLAMA:-up}" = down ] && exit 7
case "$*" in
  *api/version*) echo '{"version":"0.40.4"}' ;;
  *api/tags*) echo '{"models":[{"name":"deepseek-v4.1-flash:cloud","digest":"e04da138d31e0c9468e982e1ae9503d06cb7e170caa16a90c17d931c4aa140f8"}]}' ;;
  *api/chat*) echo '{"message":{"content":"ready"}}' ;;
esac
'''

FAKE_CAFFEINATE = '''#!/bin/bash
while [ "${1:-}" != "" ] && [ "${1#-}" != "$1" ]; do shift; done
exec "$@"
'''

# Free disk is a fact about the machine: a CI runner has about 70 GB and the Mac mini 390 GB. The stand-in reports 200 GB.
FAKE_DF = '''#!/bin/bash
echo "Filesystem 1024-blocks Used Available Capacity Mounted on"
echo "fake 1000000000 1 209715200 1% /"
'''

COPIED_SCRIPTS = ("settings.sh", "run-builds.sh", "weekly-settings.sh", "weekly-run.sh", "weekly_report.py",
                  "status-weekly.sh", "install-schedule.sh", "uninstall-schedule.sh")


def git(cwd, *args, env=None):
    done = subprocess.run(["git", "-C", str(cwd), "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


# What git reads from the machine: a CI runner may set a different identity, default branch or hooks.
GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "test",
           "GIT_AUTHOR_EMAIL": "test@example.com", "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com",
           "GIT_TERMINAL_PROMPT": "0"}


class Scheduler:
    """A scheduler folder, a prism clone with a local origin, and stand-ins for the tools a run calls."""

    def __init__(self, tmp_path):
        self.root = tmp_path
        self.home = tmp_path / "scheduler"
        (self.home / "bin").mkdir(parents=True)
        for name in COPIED_SCRIPTS:
            (self.home / "bin" / name).write_bytes((SCRIPTS / name).read_bytes())
        self.origin = tmp_path / "origin.git"
        self.git_env = {**os.environ, **GIT_ENV, "HOME": str(tmp_path)}
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True, env=self.git_env)
        self.seed = tmp_path / "seed"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.seed)], check=True, capture_output=True,
                       env=self.git_env)
        git(self.seed, "checkout", "-q", "-b", "main", env=self.git_env)
        self.prism = tmp_path / "prism"
        self.count = 0
        self.advance()
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.prism)], check=True, capture_output=True,
                       env=self.git_env)
        fakes = tmp_path / "fakebin"
        fakes.mkdir()
        for name, source in (("uv", FAKE_UV), ("curl", FAKE_CURL), ("caffeinate", FAKE_CAFFEINATE), ("df", FAKE_DF)):
            (fakes / name).write_text(source)
            (fakes / name).chmod(0o755)
        self.uv_log = tmp_path / "uv.log"
        # Names that would match a real process on a busy machine stay unmatched.
        # Nothing comes from the machine running the tests: the stand-in tools first on PATH (uv, curl, caffeinate and df,
        # so free disk is a fixed 200 GB against the production 120 GB rule), a fixed time zone, git's own configuration
        # off, and no model, API key or HOME of the real user.
        self.env = {**os.environ, **GIT_ENV, "HOME": str(tmp_path), "TZ": "UTC", "LC_ALL": "C",
                    "SCHEDULER_HOME": str(self.home), "PRISM": str(self.prism),
                    "PATH": str(fakes) + os.pathsep + os.environ["PATH"], "FAKE_UV_LOG": str(self.uv_log),
                    "BUSY_PATTERN": "no-such-process-pattern-for-tests", "RETRY_SLEEP": "0", "FETCH_RETRY_SLEEP": "0"}
        for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PRME_EXTRACTION_API_KEY", "XDG_CONFIG_HOME"):
            self.env.pop(name, None)
        seed_dir = self.prism / "data" / "full-benchmark-d810ee55"
        seed_dir.mkdir(parents=True)
        for benchmark, name in (("locomo", "locomo"), ("longmemeval", "lme")):
            (seed_dir / "gate-full-defaults-{}-deepseek-d810ee55.json".format(name)).write_text(
                json.dumps({"benchmark": benchmark, "quality": 0.9}))

    def advance(self):
        """A new commit on origin/main; returns its sha."""
        self.count += 1
        (self.seed / "file.txt").write_text("change {}\n".format(self.count))
        git(self.seed, "add", "file.txt", env=self.git_env)
        git(self.seed, "commit", "-q", "-m", "change {}".format(self.count), env=self.git_env)
        git(self.seed, "push", "-q", "origin", "main", env=self.git_env)
        return git(self.seed, "rev-parse", "HEAD", env=self.git_env)

    def run(self, *args, **env):
        return subprocess.run(["bash", str(self.home / "bin" / "weekly-run.sh"), *args], capture_output=True, text=True,
                              env={**self.env, **env}, cwd=str(self.root), timeout=300)

    def schedule_log(self):
        return (self.home / "state" / "schedule.log").read_text()

    def runs(self):
        folder = self.home / "runs"
        return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []

    def run_env(self, run_id):
        return dict(line.split("=", 1) for line in (self.home / "runs" / run_id / "run.env").read_text().splitlines()
                    if "=" in line)

    def worktrees(self):
        return sorted(path.name for path in self.root.glob("prism-weekly-*"))


@pytest.fixture
def sched(tmp_path):
    return Scheduler(tmp_path)


class TestWeeklyRun:
    def test_a_dry_run_changes_nothing_but_its_own_report(self, sched):
        done = sched.run("--dry-run")
        assert done.returncode == 0, done.stdout + done.stderr
        assert sched.runs() == []
        assert not (sched.home / "state" / "last-completed.env").exists()
        assert sched.worktrees() == []
        (dry,) = (sched.home / "dry-runs").iterdir()
        assert "**DRY RUN.**" in (dry / "report.md").read_text()
        assert "dry run: would run: BUILDS=defaults run-builds.sh" in done.stdout
        assert not any("extracted_packs build" in line for line in sched.uv_log.read_text().splitlines())

    def test_a_first_run_builds_gates_compares_and_writes_the_report(self, sched):
        done = sched.run()
        assert done.returncode == 0, done.stdout + done.stderr
        (run_id,) = sched.runs()
        assert sched.run_env(run_id)["STATUS"] == "complete"
        run_dir = sched.home / "runs" / run_id
        report = json.loads((run_dir / "report.json").read_text())
        assert report["verdict"]["stable"]["result"] == "no loss"
        assert report["verdict"]["previous"] is None
        assert (sched.home / "state" / "last-completed.env").read_text().startswith("RUN_ID=" + run_id)
        commands = sched.uv_log.read_text()
        # Only the batched defaults are built, every label shares one extraction cache, and the packs stay in the scheduler's folder.
        assert "extraction_batch_target_tokens" not in commands
        assert commands.count("--cache-dir {}".format(sched.home / "cache" / "extraction")) == 2
        assert "--root {}".format(sched.home / "packs") in commands
        assert (sched.home / "packs" / ("weekly-lme-" + run_id) / "longmemeval").is_dir()

    def test_an_unchanged_main_skips_and_says_why(self, sched):
        assert sched.run().returncode == 0
        done = sched.run()
        assert done.returncode == 0
        assert len(sched.runs()) == 1
        assert "skip: origin/main" in sched.schedule_log().splitlines()[-1]
        assert "has not changed since the last completed run" in sched.schedule_log().splitlines()[-1]

    def test_a_changed_main_runs_again_and_compares_with_the_previous_run(self, sched):
        sched.run()
        sched.advance()
        time.sleep(1.1)  # run ids start with the second, and sort by it
        done = sched.run(FAKE_QUALITY="0.9")
        assert done.returncode == 0, done.stdout + done.stderr
        first, second = sched.runs()
        assert sched.run_env(second)["PREVIOUS_RUN"] == first
        report = json.loads((sched.home / "runs" / second / "report.json").read_text())
        assert report["verdict"]["previous"]["result"] == "no loss"
        assert report["previous_run"]["run_id"] == first
        assert (sched.home / "runs" / second / "compare-previous-longmemeval.json").is_file()

    def test_force_runs_even_when_main_is_unchanged(self, sched):
        sched.run()
        assert sched.run("--force").returncode == 0
        assert len(sched.runs()) == 2

    def test_a_loss_against_the_stable_release_is_the_verdict(self, sched):
        sched.run(FAKE_QUALITY="0.8")
        (run_id,) = sched.runs()
        report = json.loads((sched.home / "runs" / run_id / "report.json").read_text())
        assert report["verdict"]["stable"]["result"] == "loss"
        assert "loss: LoCoMo all evidence packed, question level -10.0 pp" in sched.schedule_log()

    def test_locomo_conversation_intervals_are_resampled_for_the_stable_and_the_previous_comparison(self, sched):
        sched.run()
        sched.advance()
        time.sleep(1.1)
        assert sched.run().returncode == 0
        first, second = sched.runs()
        for name in ("stable", "previous"):
            assert (sched.home / "runs" / second / "compare-{}-locomo-conversations.json".format(name)).is_file()
        assert not (sched.home / "runs" / first / "compare-previous-locomo-conversations.json").exists()
        commands = sched.uv_log.read_text().splitlines()
        assert sum("product_packing gate-conversations" in line for line in commands) == 3
        assert not any("gate-conversations" in line and "longmemeval" in line for line in commands)
        report = json.loads((sched.home / "runs" / second / "report.json").read_text())
        assert report["comparisons"]["stable"]["locomo"]["rows"][0]["conversation"]["status"] == "within noise"
        assert report["comparisons"]["stable"]["longmemeval"]["rows"][0]["conversation"] is None

    def test_a_loss_that_only_the_conversation_level_shows_is_the_verdict(self, sched):
        # 0.895 against the stable 0.9: the question-level interval (+/- 0.01) reaches zero, the conversation-level
        # one (+/- 0.002) lies wholly below it.
        done = sched.run(FAKE_QUALITY="0.895", FAKE_CONV_WIDTH="0.002")
        assert done.returncode == 0, done.stdout + done.stderr
        (run_id,) = sched.runs()
        report = json.loads((sched.home / "runs" / run_id / "report.json").read_text())
        assert report["verdict"]["stable"]["result"] == "loss"
        assert {item["level"] for item in report["verdict"]["stable"]["losses"]} == {"conversation level"}
        assert "LoCoMo all evidence packed, conversation level -0.5 pp" in sched.schedule_log()

    def test_a_failed_conversation_resampling_reads_not_compared_but_the_run_still_completes(self, sched):
        done = sched.run(FAKE_CONV_FAIL="1")
        assert done.returncode == 0, done.stdout + done.stderr
        (run_id,) = sched.runs()
        assert sched.run_env(run_id)["STATUS"] == "complete"
        run_dir = sched.home / "runs" / run_id
        assert "gate-conversations failed (exit 2)" in (run_dir / "compare-stable-locomo-conversations.refused").read_text()
        report = json.loads((run_dir / "report.json").read_text())
        assert report["verdict"]["stable"]["result"] == "not compared"
        assert "conversations stable: skipped" in sched.schedule_log()

    def test_a_pause_resumes_inside_the_run(self, sched):
        done = sched.run(FAKE_BUILD_PAUSE_ONCE="1")
        assert done.returncode == 0, done.stdout + done.stderr
        (run_id,) = sched.runs()
        driver = (sched.home / "runs" / run_id / "driver.log").read_text()
        assert "attempt 2" in driver and driver.rstrip().endswith("ALL DONE")
        assert json.loads((sched.home / "runs" / run_id / "report.json").read_text())["builds"]["locomo"]["attempts"] == 2

    def test_an_incomplete_run_is_resumed_not_started_over(self, sched):
        failed = sched.run(FAKE_BUILD_PAUSE_ALWAYS="1", MAX_ATTEMPTS="2")
        assert failed.returncode == 1
        (run_id,) = sched.runs()
        assert sched.run_env(run_id)["STATUS"] == "incomplete"
        assert not (sched.home / "state" / "last-completed.env").exists()
        assert "is incomplete: the next trigger resumes it" in sched.schedule_log()
        done = sched.run()
        assert done.returncode == 0, done.stdout + done.stderr
        assert sched.runs() == [run_id]
        assert sched.run_env(run_id)["STATUS"] == "complete"
        assert "resuming run " + run_id in sched.schedule_log()

    def test_a_run_left_running_by_a_dead_process_is_marked_interrupted_and_resumed(self, sched):
        sched.run(FAKE_BUILD_PAUSE_ALWAYS="1", MAX_ATTEMPTS="1")
        (run_id,) = sched.runs()
        env_file = sched.home / "runs" / run_id / "run.env"
        env_file.write_text(env_file.read_text().replace("STATUS=incomplete", "STATUS=running"))
        dead = subprocess.Popen(["true"])
        dead.wait()
        (sched.home / "state" / "weekly.lock").mkdir()
        (sched.home / "state" / "weekly.lock" / "pid").write_text(str(dead.pid))
        done = sched.run()
        assert done.returncode == 0, done.stdout + done.stderr
        log = sched.schedule_log()
        assert "clearing the stale lock of pid {}".format(dead.pid) in log
        assert "was left running by a process that is gone" in log
        assert sched.runs() == [run_id] and sched.run_env(run_id)["STATUS"] == "complete"

    def test_a_live_lock_skips_and_leaves_the_lock_alone(self, sched, tmp_path):
        holder = tmp_path / "weekly-run-holder.sh"
        holder.write_text("#!/bin/bash\nsleep 60\n")
        process = subprocess.Popen(["bash", str(holder)])
        try:
            (sched.home / "state").mkdir()
            (sched.home / "state" / "weekly.lock").mkdir()
            (sched.home / "state" / "weekly.lock" / "pid").write_text(str(process.pid))
            done = sched.run()
            assert done.returncode == 0
            assert "skip: a scheduled run is going (pid {}".format(process.pid) in sched.schedule_log()
            assert sched.runs() == []
            assert (sched.home / "state" / "weekly.lock" / "pid").read_text() == str(process.pid)
        finally:
            process.kill()
            process.wait()

    def test_a_benchmark_build_started_by_someone_else_skips(self, sched, tmp_path):
        other = tmp_path / "extracted_packs-build-by-hand.sh"
        other.write_text("#!/bin/bash\nsleep 60\n")
        process = subprocess.Popen(["bash", str(other)])
        try:
            done = sched.run(BUSY_PATTERN="extracted_packs-build-by-hand")
            assert done.returncode == 0
            assert "skip: a benchmark build or gate is running" in sched.schedule_log()
            assert sched.runs() == []
        finally:
            process.kill()
            process.wait()

    def test_too_little_free_disk_refuses_and_says_why(self, sched):
        done = sched.run(MIN_FREE_GB="1000")
        assert done.returncode == 1
        assert "refusing to start: only 200 GB free" in sched.schedule_log()
        assert "a run needs at least 1000 GB" in sched.schedule_log()
        assert sched.runs() == [] and sched.worktrees() == []

    def test_the_production_rule_is_120_gb_free(self, sched):
        assert sched.run(MIN_FREE_GB="200").returncode == 0
        sched.advance()
        done = sched.run(MIN_FREE_GB="201")
        assert done.returncode == 1 and "a run needs at least 201 GB" in sched.schedule_log()
        weekly_settings = (SCRIPTS / "weekly-settings.sh").read_text()
        assert 'MIN_FREE_GB="${MIN_FREE_GB:-120}"' in weekly_settings

    def test_the_oldest_packs_go_before_a_run_refuses_for_disk(self, sched):
        sched.run(KEEP_RUNS="2")
        sched.advance()
        time.sleep(1.1)
        sched.run(KEEP_RUNS="2")
        sched.advance()
        first, second = sched.runs()
        done = sched.run(KEEP_RUNS="2", MIN_FREE_GB="1000")
        assert done.returncode == 1
        assert not (sched.home / "packs" / ("weekly-lme-" + first)).exists()
        assert (sched.home / "packs" / ("weekly-lme-" + second)).exists()
        assert "pruning the packs of runs beyond the newest 1 first" in sched.schedule_log()
        assert len(sched.runs()) == 2

    def test_an_ollama_that_does_not_answer_refuses(self, sched):
        done = sched.run(FAKE_OLLAMA="down")
        assert done.returncode == 1
        assert "refusing to start: no Ollama server answers" in sched.schedule_log()
        assert sched.runs() == []

    def test_a_paid_api_key_in_the_clone_refuses(self, sched):
        (sched.prism / ".env").write_text("ANTHROPIC_API_KEY=not-a-real-key\n")
        done = sched.run()
        assert done.returncode == 1
        assert "holds ANTHROPIC_API_KEY" in sched.schedule_log()
        assert "not-a-real-key" not in sched.schedule_log()

    def test_older_runs_lose_their_packs_and_worktrees_but_keep_their_reports(self, sched):
        # Things that are not this scheduler's own must survive: another build's packs and another worktree.
        other_packs = sched.home / "packs" / "ingest-baseline-lme-deepseek"
        other_packs.mkdir(parents=True)
        (other_packs / "keep.txt").write_text("keep")
        other_tree = sched.root / "prism-full-benchmark"
        other_tree.mkdir()
        (other_tree / "keep.txt").write_text("keep")
        for _ in range(4):
            assert sched.run(KEEP_RUNS="2").returncode == 0
            sched.advance()
            time.sleep(1.1)  # run ids start with the second, and sort by it
        run_ids = sched.runs()
        assert len(run_ids) == 4
        for run_id in run_ids:
            assert (sched.home / "runs" / run_id / "report.md").is_file()
        names = sorted(path.name for path in (sched.home / "packs").iterdir() if path.name.startswith("weekly-lme"))
        kept = ["weekly-lme-" + run_id for run_id in run_ids[2:]]
        assert names == sorted(kept + [name + ".log" for name in kept])
        assert [sched.run_env(run_id).get("PACKS_DELETED") is not None for run_id in run_ids] == [True, True, False, False]
        assert sched.worktrees() == sorted("prism-weekly-" + sched.run_env(run_id)["COMMIT"][:8] for run_id in run_ids[2:])
        assert (other_packs / "keep.txt").read_text() == "keep" and (other_tree / "keep.txt").read_text() == "keep"

    def test_a_dry_run_only_says_what_it_would_delete(self, sched):
        for _ in range(3):
            assert sched.run().returncode == 0
            sched.advance()
            time.sleep(1.1)
        before = sorted(path.name for path in (sched.home / "packs").iterdir())
        assert sched.run("--dry-run", KEEP_RUNS="2").returncode == 0
        assert sorted(path.name for path in (sched.home / "packs").iterdir()) == before
        assert "dry run: would delete" in sched.schedule_log()


class TestInstallSchedule:
    def test_the_plist_fires_monday_and_thursday_at_half_past_midnight_and_never_at_load(self, sched):
        import plistlib

        done = subprocess.run(["bash", str(sched.home / "bin" / "install-schedule.sh"), "--print"], capture_output=True,
                              env=sched.env)
        assert done.returncode == 0, done.stderr
        plist = plistlib.loads(done.stdout)
        assert plist["Label"] == "com.prism.weekly-benchmark"
        assert plist["StartCalendarInterval"] == [{"Weekday": 1, "Hour": 0, "Minute": 30},
                                                  {"Weekday": 4, "Hour": 0, "Minute": 30}]
        assert plist["ProgramArguments"] == ["/bin/bash", str(sched.home / "bin" / "weekly-run.sh")]
        assert "RunAtLoad" not in plist and "KeepAlive" not in plist
        assert plist["EnvironmentVariables"]["SCHEDULER_HOME"] == str(sched.home)
