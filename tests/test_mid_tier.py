"""The mid-tier benchmark's sample, report and comparison (benchmarks/mid_tier.py); no model runs."""
import asyncio
import copy
import json

import pytest

from benchmarks import mid_tier
from benchmarks.diagnostics import dev_slice, extracted_packs
from benchmarks.diagnostics.product_packing import parse_overrides
from benchmarks.mid_tier import (
    FIELDS, assemble_report, choose, cluster_interval, compact_row, compare_reports, comparison_markdown, dump_json,
    in_flight_shares, load_sample, measure, plan_sample, public_command, report_markdown, report_rows, run_mid_tier,
    shard_conversations, slice_document, wilson,
)

TYPES = ["knowledge-update", "multi-session", "single-session-assistant", "single-session-preference",
         "single-session-user", "temporal-reasoning"]
CATEGORIES = ["multi-hop", "open-domain", "single-hop", "temporal"]


@pytest.fixture(scope="module")
def sample():
    return load_sample()


@pytest.fixture
def locomo_questions(sample):
    """Six questions per conversation, one per category plus two more single-hop ones."""
    return {name: [f"{name}-q{index:04d}" for index in range(6)] for name in sample["locomo"]["conversations"]}


def gate_row(benchmark, question_id, category, *, annotated=2, packed=2, returned=True, projected=0.8, first=1):
    """A row as the gate writes it, with the fields the mid tier reads; the turns rank from ``first``."""
    evidence = None
    if annotated:
        ranks = {f"{question_id}-t{index}": (first + index if returned or index else None)
                 for index in range(annotated)}
        evidence = {"annotated": annotated, "unresolved": [], "packed": packed, "all_packed": packed == annotated,
                    "ranks": ranks}
    return {"benchmark": benchmark, "question_id": question_id, "category": category, "evidence": evidence,
            "projected_correct": projected}


def gate_rows(plan, locomo_questions, *, lost=(), deep=()):
    """Every planned question as a gate row; ``lost`` questions miss evidence, the others pack all of it.

    The evidence of a ``deep`` question is still packed but ranks far down the candidates.
    """
    rows = {"locomo": [], "longmemeval": []}
    for name in plan["benchmarks"]["locomo"]["units"]:
        for index, question in enumerate(locomo_questions[name]):
            miss = question in lost
            rows["locomo"].append(gate_row("locomo", question, CATEGORIES[index % 4], annotated=2,
                                           packed=1 if miss else 2, projected=0.5 if miss else 0.8,
                                           first=60 if question in deep else 1))
    for question in plan["benchmarks"]["longmemeval"]["questions"]:
        kind = plan["longmemeval_types"][question]
        miss = question in lost
        rows["longmemeval"].append(gate_row("longmemeval", question, kind, annotated=1, packed=0 if miss else 1,
                                            projected=0.7 if miss else 0.9, first=60 if question in deep else 1))
    return rows


def make_report(sample, locomo_questions, label="base", *, lost=(), deep=(), calls=0, hits=40, overrides=None,
                src="tree-a", tokenizer="cl100k_base", lme_questions=6, seconds=600.0):
    plan = plan_sample(sample, locomo_questions, lme_questions=lme_questions)
    stage = {"started_at": "2026-10-19T00:00:00+00:00", "seconds": 100.0, "load_average_1m": {"start": 3.0, "end": 4.0}}
    build = {**stage, "packs": 10, "processes": 3, "turns": 100, "calls": calls, "prompt_tokens": calls * 1000,
             "completion_tokens": calls * 300, "cache_hits": hits}
    return assemble_report(
        label=label, plan=plan, gate_rows=gate_rows(plan, locomo_questions, lost=lost, deep=deep),
        stages={"build-locomo": build, "build-longmemeval": build, "gate-locomo": stage, "gate-longmemeval": stage},
        total={"started_at": stage["started_at"], "finished_at": stage["started_at"], "seconds": seconds,
               "load_average_1m": stage["load_average_1m"], "cache_entries": {"start": 0, "end": 0}},
        code={"commit": "c" * 40, "dirty": False, "worktree_sha256": "w", "src_tree": src},
        model={"model": "deepseek", "provider": "ollama_cloud", "digest": "d", "server_versions": ["0.40.6"]},
        settings={"overrides": overrides or {}, "in_flight": 24, "build_shards": 3, "gate_shards": 3,
                  "datasets": {"locomo": "l", "longmemeval": "m"}, "machine": {"cpus": 18, "platform": "p"}},
        projection={"projection": {"x": 1}, "baseline": {"y": 2}}, tokenizer=tokenizer, command=["python"])


# The sample ---------------------------------------------------------------------------------------------------


def test_a_draw_takes_the_types_in_turn_so_every_prefix_is_stratified():
    pool = {kind: [f"{kind}-{index}" for index in range(10)] for kind in TYPES}
    order = choose(pool, seed=1, per_type=4)
    assert len(order) == 24
    assert [item["type"] for item in order[:6]] == TYPES
    for size in (6, 12, 18, 24):
        counts = [sum(item["type"] == kind for item in order[:size]) for kind in TYPES]
        assert counts == [size // 6] * 6
    assert len({item["question_id"] for item in order}) == 24


def test_a_draw_is_fixed_by_its_seed_and_not_by_the_order_of_the_pool():
    pool = {kind: [f"{kind}-{index}" for index in range(10)] for kind in TYPES}
    shuffled = {kind: list(reversed(ids)) for kind, ids in pool.items()}
    assert choose(pool, seed=7, per_type=3) == choose(shuffled, seed=7, per_type=3)
    assert choose(pool, seed=7, per_type=3) != choose(pool, seed=8, per_type=3)


def test_a_draw_refuses_a_type_with_too_few_questions():
    with pytest.raises(ValueError, match="Only 2 b questions"):
        choose({"a": ["a1", "a2", "a3"], "b": ["b1", "b2"]}, per_type=3)


def test_the_committed_sample_has_two_questions_of_each_type_in_its_default_and_no_abstention(sample):
    order = sample["longmemeval"]["order"]
    assert len(order) == 36 and len({item["question_id"] for item in order}) == 36
    default = order[:sample["rules"]["default_longmemeval_questions"]]
    assert sample["rules"]["default_longmemeval_questions"] == mid_tier.LME_QUESTIONS == 12
    assert {kind: sum(item["type"] == kind for item in default) for kind in TYPES} == {kind: 2 for kind in TYPES}
    assert not any(item["question_id"].endswith("_abs") for item in order)
    assert sample["seed"] == mid_tier.SEED and len(sample["locomo"]["conversations"]) == 10
    assert sum(sample["locomo"]["questions"].values()) == 1540
    assert len(sample["file_sha256"]) == 64


def test_a_sample_file_must_take_the_types_in_turn(tmp_path, sample):
    broken = copy.deepcopy({key: value for key, value in sample.items() if key != "file_sha256"})
    broken["longmemeval"]["order"][0], broken["longmemeval"]["order"][1] = (
        broken["longmemeval"]["order"][1], broken["longmemeval"]["order"][0])
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(broken))
    with pytest.raises(ValueError, match="types in turn"):
        load_sample(path)
    path.write_text(json.dumps({**broken, "kind": "something-else"}))
    with pytest.raises(ValueError, match="not a version 1 mid-tier sample"):
        load_sample(path)


def test_a_plan_is_all_of_locomo_and_a_prefix_of_the_longmemeval_draw(sample, locomo_questions):
    plan = plan_sample(sample, locomo_questions)
    locomo, lme = plan["benchmarks"]["locomo"], plan["benchmarks"]["longmemeval"]
    assert locomo["units"] == sample["locomo"]["conversations"] and len(locomo["questions"]) == 60
    assert lme["units"] == lme["questions"] == [item["question_id"] for item in sample["longmemeval"]["order"][:12]]
    assert plan["trial"] is False and plan["sample_sha256"] == sample["file_sha256"]
    smaller = plan_sample(sample, locomo_questions, lme_questions=6)
    assert smaller["benchmarks"]["longmemeval"]["questions"] == lme["questions"][:6]
    # A different question set is a different plan, so its reports never compare.
    assert smaller["sha256"] != plan["sha256"] == plan_sample(sample, locomo_questions)["sha256"]


def test_a_trial_names_its_conversations_and_a_plan_refuses_what_the_sample_lacks(sample, locomo_questions):
    trial = plan_sample(sample, locomo_questions, conversations=["conv-26", "conv-30"])
    assert trial["trial"] is True and trial["benchmarks"]["locomo"]["units"] == ["conv-26", "conv-30"]
    assert trial["sha256"] != plan_sample(sample, locomo_questions)["sha256"]
    with pytest.raises(ValueError, match="Unknown LoCoMo conversations: conv-99"):
        plan_sample(sample, locomo_questions, conversations=["conv-99"])
    for size in (0, 37):
        with pytest.raises(ValueError, match="Choose from 1 to 36"):
            plan_sample(sample, locomo_questions, lme_questions=size)


def test_a_slice_document_is_a_development_slice_the_gate_accepts(tmp_path, sample, locomo_questions):
    plan = plan_sample(sample, locomo_questions)
    path = tmp_path / "slice.json"
    path.write_text(json.dumps(slice_document("mid", longmemeval=plan["benchmarks"]["longmemeval"])))
    record = dev_slice.load_slice(path)
    assert dev_slice.slice_questions(record, "longmemeval") == frozenset(plan["benchmarks"]["longmemeval"]["questions"])
    assert dev_slice.slice_questions(record, "locomo") == frozenset()
    assert record["identity"]["name"] == "mid" and len(record["identity"]["sha256"]) == 64


def test_conversations_are_split_into_gate_shards_of_similar_size():
    sizes = {"a": 199, "b": 191, "c": 178, "d": 158, "e": 156, "f": 152, "g": 152, "h": 150, "i": 123, "j": 81}
    shards = shard_conversations(sizes, 3)
    assert sorted(name for group in shards for name in group) == sorted(sizes)
    loads = [sum(sizes[name] for name in group) for group in shards]
    assert max(loads) <= 1.1 * sum(sizes.values()) / 3
    assert shard_conversations(sizes, 1) == [sorted(sizes)]
    assert len(shard_conversations({"a": 1, "b": 2}, 5)) == 2


def test_live_calls_share_the_cap_in_proportion_only_when_the_packs_do_not_fit():
    assert in_flight_shares(24, [4, 3, 3, 4, 4, 4]) == [4, 3, 3, 4, 4, 4]
    shares = in_flight_shares(24, [4, 3, 3, 4, 4, 4, 4, 4, 4])
    assert sum(shares) == 24 and min(shares) >= 2 and shares[0] >= shares[1]
    assert in_flight_shares(24, [10, 10, 10, 10]) == [6, 6, 6, 6]
    assert in_flight_shares(2, [1, 1, 1, 9]) == [1, 1, 1, 1]


def test_the_command_in_a_report_names_no_local_path():
    argv = ["run", "--label", "main", "--out", "/Users/me/mid", "--cache-dir", "/data/cache", "--set", "a=1"]
    assert public_command(argv) == ["python", "-m", "benchmarks.mid_tier", "run", "--label", "main", "--out",
                                    "<path>", "--cache-dir", "<path>", "--set", "a=1"]


# Intervals and measures -------------------------------------------------------------------------------------


def test_a_wilson_interval_stays_inside_zero_and_one_at_the_extremes():
    low, high = wilson(12, 12)
    assert high == pytest.approx(1.0) and 0.7 < low < 0.8
    low, high = wilson(0, 12)
    assert low == pytest.approx(0.0) and 0.2 < high < 0.3
    assert wilson(1, 2)[0] > 0 and wilson(0, 0) is None
    low, high = wilson(300, 400)
    assert low < 0.75 < high and high - low < 0.09


def test_a_cluster_interval_resamples_whole_groups_and_needs_enough_of_them():
    values = [1.0] * 40 + [0.0] * 10
    groups = [f"g{index % 10}" for index in range(50)]
    low, high = cluster_interval(values, groups)
    assert low <= 0.8 <= high and cluster_interval(values, groups) == [low, high]
    assert cluster_interval([1.0, 0.0, 1.0, 1.0], ["a", "b", "c", "d"]) is None
    # Groups that all agree leave the interval at one point.
    assert cluster_interval([0.5] * 10, [f"g{index}" for index in range(10)]) == [0.5, 0.5]


def test_a_row_keeps_what_the_measures_need_and_a_question_without_evidence_keeps_none():
    row = compact_row(gate_row("locomo", "conv-26-q0007", "temporal", annotated=3, packed=2))
    assert row == {"question_id": "conv-26-q0007", "category": "temporal", "group": "conv-26", "annotated": 3,
                   "packed": 2, "top_25": 3, "all_packed": False, "all_returned": True, "projected": 0.8}
    assert compact_row(gate_row("longmemeval", "abc123", "multi-session"))["group"] == "abc123"
    bare = compact_row(gate_row("locomo", "conv-30-q0001", "single-hop", annotated=0))
    assert (bare["annotated"], bare["packed"], bare["top_25"], bare["all_packed"], bare["all_returned"]) == (
        None, None, None, None, None)
    unreturned = compact_row(gate_row("locomo", "conv-30-q0002", "single-hop", annotated=2, packed=2, returned=False))
    assert unreturned["all_returned"] is False


def test_the_rank_measure_counts_the_evidence_turns_ranked_in_the_first_25_candidates():
    row = gate_row("longmemeval", "q1", "multi-session", annotated=4, packed=4)
    row["evidence"]["ranks"] = {"a": 3, "b": 25, "c": 26, "d": None}
    compact = compact_row(row)
    assert (compact["top_25"], compact["packed"]) == (2, 4)
    result = measure([compact], clustered=False)
    assert result["evidence_in_top_25"]["mean"] == 0.5 and result["evidence_recall"]["mean"] == 1.0


def test_measures_count_annotated_questions_only_for_evidence_and_all_questions_for_projection():
    rows = [compact_row(gate_row("longmemeval", f"q{index}", "multi-session", annotated=2, packed=index % 3,
                                 projected=0.5)) for index in range(6)]
    rows.append(compact_row(gate_row("longmemeval", "q-bare", "multi-session", annotated=0, projected=0.9)))
    result = measure(rows, clustered=False)
    assert (result["questions"], result["annotated_questions"]) == (7, 6)
    packed = result["all_evidence_packed"]
    assert (packed["count"], packed["of"]) == (2, 6) and packed["share"] == pytest.approx(1 / 3)
    assert packed["interval_95"] == packed["interval_95_questions"] and "interval_95_conversations" not in packed
    assert result["evidence_recall"]["mean"] == pytest.approx(1 / 2)
    assert result["evidence_in_top_25"]["mean"] == 1.0
    assert result["projected_accuracy"]["mean"] == pytest.approx((6 * 0.5 + 0.9) / 7)


def test_a_clustered_measure_makes_the_conversation_interval_the_headline():
    rows = [compact_row(gate_row("locomo", f"conv-{group}-q{index:04d}", "single-hop", annotated=1,
                                 packed=int((group + index) % 4 != 0)))
            for group in range(10) for index in range(20)]
    result = measure(rows, clustered=True)["all_evidence_packed"]
    assert result["interval_95"] == result["interval_95_conversations"]
    assert result["interval_95_questions"] != result["interval_95_conversations"]
    assert result["interval_95_questions"][0] < result["share"] < result["interval_95_questions"][1]


# The report ---------------------------------------------------------------------------------------------------


def test_a_report_holds_the_measures_by_category_the_stages_and_each_questions_row(sample, locomo_questions):
    report = make_report(sample, locomo_questions, lost=("conv-26-q0001",))
    assert report["kind"] == mid_tier.REPORT_KIND and report["fields"] == FIELDS
    locomo = report["measures"]["locomo"]
    assert locomo["overall"]["questions"] == 60 and sorted(locomo["categories"]) == CATEGORIES
    assert locomo["overall"]["all_evidence_packed"]["count"] == 59
    assert locomo["categories"]["open-domain"]["all_evidence_packed"]["count"] == 19
    lme = report["measures"]["longmemeval"]
    assert lme["overall"]["questions"] == 6 and sorted(lme["categories"]) == TYPES
    assert all(item["questions"] == 1 for item in lme["categories"].values())
    assert report["total"]["cache"] == "warm" and report["total"]["cache_hits"] == 80
    rows = report_rows(report, "locomo")
    assert len(rows) == 60 and rows["conv-26-q0001"]["all_packed"] is False
    assert list(report_rows(report, "longmemeval")) == report["sample"]["longmemeval_questions"]


def test_a_report_states_whether_extraction_was_cold_warm_or_partly_warm():
    stage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cache_hits": 0}
    totals = mid_tier.stage_totals
    assert totals({"build-locomo": {**stage, "cache_hits": 9}})["cache"] == "warm"
    assert totals({"build-locomo": {**stage, "calls": 4}})["cache"] == "cold"
    assert totals({"build-locomo": {**stage, "calls": 4, "cache_hits": 1}, "gate-locomo": {}})["cache"] == "partly warm"


def test_a_report_refuses_a_gate_that_missed_a_sampled_question(sample, locomo_questions):
    plan = plan_sample(sample, locomo_questions)
    rows = gate_rows(plan, locomo_questions)
    rows["locomo"].pop()
    with pytest.raises(ValueError, match="did not replay exactly the sample's 60 questions"):
        assemble_report(label="x", plan=plan, gate_rows=rows, stages={}, total={}, code={}, model={}, settings={},
                        projection={}, tokenizer="t", command=[])


def test_a_report_is_json_with_one_line_for_each_question_and_prints_its_tables(sample, locomo_questions):
    report = make_report(sample, locomo_questions)
    text = dump_json(report)
    assert json.loads(text) == report
    row_lines = [line for line in text.splitlines() if line.strip().startswith('["conv-26-q')]
    assert len(row_lines) == 6
    markdown = report_markdown(report)
    for expected in ("# Mid tier: base", "| build-locomo |", "| **Whole run** |", "## LoCoMo", "## LongMemEval-S",
                     "95% conversations", "| temporal |", "| knowledge-update |", "## Limits"):
        assert expected in markdown
    assert "—" not in markdown and "–" not in markdown


# The comparison -----------------------------------------------------------------------------------------------


def test_comparing_a_report_with_itself_finds_no_change(sample, locomo_questions):
    report = make_report(sample, locomo_questions)
    result = compare_reports(report, report, samples=200)
    assert result["same_engine_source"] is True and result["warnings"] == []
    for name in ("locomo", "longmemeval"):
        for stat in result["benchmarks"][name]["metrics"].values():
            assert stat["delta"] == 0 and stat["excludes_zero"] is False and stat["losses"] == 0
        assert result["benchmarks"][name]["gained_all_evidence"] == []
        assert result["benchmarks"][name]["lost_all_evidence"] == []


def test_a_comparison_lists_the_questions_that_gained_and_lost_evidence_and_the_paired_change(
        sample, locomo_questions):
    lost_here = [f"conv-{number}-q{index:04d}" for number in (26, 30, 41, 42, 43, 44, 47, 48)
                 for index in (1, 2, 3)]
    baseline = make_report(sample, locomo_questions, "base", lost=lost_here[:6], src="tree-a")
    candidate = make_report(sample, locomo_questions, "try", lost=lost_here, overrides={"packing": {"token_budget": 8192}},
                            src="tree-b")
    result = compare_reports(baseline, candidate, samples=400)
    locomo = result["benchmarks"]["locomo"]
    assert result["same_engine_source"] is False
    assert locomo["lost_all_evidence"] == lost_here[6:] and locomo["gained_all_evidence"] == []
    packed = locomo["metrics"]["all_evidence_packed"]
    assert packed["before"] == pytest.approx(54 / 60) and packed["after"] == pytest.approx(36 / 60)
    assert packed["delta"] == pytest.approx(-18 / 60) and (packed["wins"], packed["losses"]) == (0, 18)
    assert packed["interval_95_questions"][1] < 0 and packed["interval_95_conversations"][1] < 0
    assert packed["excludes_zero"] is True and packed["conversations"] == 10
    assert locomo["metrics"]["projected_accuracy"]["delta"] < 0
    # The change is in LoCoMo; the LongMemEval-S questions are the same on both sides.
    assert result["benchmarks"]["longmemeval"]["metrics"]["all_evidence_packed"]["delta"] == 0
    reverse = compare_reports(candidate, baseline, samples=400)["benchmarks"]["locomo"]
    assert reverse["gained_all_evidence"] == lost_here[6:] and reverse["lost_all_evidence"] == []
    markdown = comparison_markdown(result)
    assert "`base`" in markdown and "`try`" in markdown and '{"packing": {"token_budget": 8192}}' in markdown
    assert "Lost all evidence (18)" in markdown and "| all evidence packed | 90.0% | 60.0% | -30.0 |" in markdown
    assert "The engine source differs." in markdown and "—" not in markdown and "–" not in markdown


def test_a_change_that_only_moves_evidence_down_the_candidates_shows_in_the_rank_measure(sample, locomo_questions):
    deep = [f"conv-{number}-q{index:04d}" for number in (26, 30, 41, 42, 43, 44, 47, 48, 49, 50)
            for index in (0, 1, 2)]
    baseline = make_report(sample, locomo_questions, "base")
    candidate = make_report(sample, locomo_questions, "try", deep=deep)
    metrics = compare_reports(baseline, candidate, samples=300)["benchmarks"]["locomo"]["metrics"]
    top = metrics["evidence_in_top_25"]
    assert top["before"] == 1.0 and top["after"] == pytest.approx(0.5) and top["excludes_zero"] is True
    # Everything is still packed, so the packing measures do not move.
    assert metrics["all_evidence_packed"]["delta"] == 0 and metrics["evidence_recall"]["delta"] == 0


def test_a_locomo_change_is_clear_of_zero_only_when_the_conversation_interval_excludes_it_too(
        sample, locomo_questions):
    # Every loss sits in one conversation: the question-level interval is clear of zero, the conversation one is not.
    baseline = make_report(sample, locomo_questions, "base")
    candidate = make_report(sample, locomo_questions, "try", lost=[f"conv-26-q{index:04d}" for index in range(6)])
    packed = compare_reports(baseline, candidate, samples=400)["benchmarks"]["locomo"]["metrics"][
        "all_evidence_packed"]
    assert (packed["wins"], packed["losses"]) == (0, 6)
    assert packed["interval_95_questions"][1] < 0 <= packed["interval_95_conversations"][1]
    assert packed["excludes_zero"] is False


def test_a_small_longmemeval_sample_gives_no_interval_below_five_questions(sample, locomo_questions):
    baseline = make_report(sample, locomo_questions, "base", lme_questions=6)
    first = baseline["sample"]["longmemeval_questions"][0]
    candidate = make_report(sample, locomo_questions, "try", lost=[first], lme_questions=6)
    lme = compare_reports(baseline, candidate, samples=200)["benchmarks"]["longmemeval"]
    assert lme["metrics"]["all_evidence_packed"]["losses"] == 1
    # Six questions pass the minimum; one question of a type does not.
    assert lme["metrics"]["all_evidence_packed"]["interval_95_questions"] is not None
    only = lme["categories"][baseline["sample"]["longmemeval_types"][first]]
    assert only["interval_95_questions"] is None and only["excludes_zero"] is False and only["losses"] == 1


@pytest.mark.parametrize("change, message", [
    ({"lme_questions": 7}, "different questions"),
    ({"tokenizer": "other"}, "different tokenizers"),
])
def test_reports_over_different_questions_or_tokenizers_do_not_compare(sample, locomo_questions, change, message):
    baseline = make_report(sample, locomo_questions)
    other = make_report(sample, locomo_questions, **change)
    with pytest.raises(ValueError, match=message):
        compare_reports(baseline, other, samples=100)


def test_reports_over_different_datasets_models_or_a_foreign_file_do_not_compare(sample, locomo_questions):
    baseline = make_report(sample, locomo_questions)
    other = copy.deepcopy(baseline)
    other["settings"]["datasets"]["locomo"] = "changed"
    with pytest.raises(ValueError, match="different datasets"):
        compare_reports(baseline, other)
    other = copy.deepcopy(baseline)
    other["model"]["model"] = "another-model"
    with pytest.raises(ValueError, match="different extraction models"):
        compare_reports(baseline, other)
    with pytest.raises(ValueError, match="candidate is not a version 1 mid-tier report"):
        compare_reports(baseline, {"kind": "gate"})


def test_a_comparison_warns_about_live_extraction_other_servers_and_unclean_trees(sample, locomo_questions):
    baseline = make_report(sample, locomo_questions)
    candidate = make_report(sample, locomo_questions, "try", calls=120, hits=0)
    candidate["code"]["dirty"] = True
    candidate["model"]["server_versions"] = ["0.41.0"]
    candidate["model"]["digest"] = "other"
    warnings = compare_reports(baseline, candidate, samples=100)["warnings"]
    text = " ".join(warnings)
    assert "The candidate extracted live (240 calls, cold)" in text and "not the change" in text
    assert "uncommitted changes" in text and "server versions differ" in text and "model identities differ" in text
    assert "baseline extracted live" not in text


# Running ------------------------------------------------------------------------------------------------------


class Fakes:
    """Stand-ins for the build and gate processes: they write what the real ones leave behind."""

    def __init__(self, locomo_questions, *, pause_first=()):
        self.types = {item["question_id"]: item["type"] for item in load_sample()["longmemeval"]["order"]}
        self.pause = set(pause_first)
        self.builds, self.gates = [], []

    async def build(self, benchmark, label, root, units, shard, *, set_args, model, cache_dir, in_flight, jobs, log):
        mine = units[shard[0]::shard[1]]
        self.builds.append((benchmark, shard, list(mine), in_flight, jobs, set_args, model))
        folder = root / label
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "build.json").write_text(json.dumps({
            "label": label, "overrides": {}, "server_versions": ["0.40.6"], "provenance": {"commit": "c"},
            "model": {"model": model, "provider": "ollama_cloud", "model_digest_sha256": "digest"}}))
        for unit in mine:
            if unit in self.pause:
                self.pause.discard(unit)
                continue
            pack = folder / benchmark / unit
            pack.mkdir(parents=True, exist_ok=True)
            (pack / "manifest.json").write_text(json.dumps({
                "kind": extracted_packs.KIND, "schema_version": extracted_packs.SCHEMA_VERSION, "turns": 10,
                "calls": 2, "prompt_tokens": 2000, "completion_tokens": 600, "seconds": 3.0, "cache_hits": 1,
                "claims": 7}))
        log.write_text("built\n")

    async def gate(self, benchmark, packs, slice_path, output, set_args, log):
        self.gates.append((benchmark, packs.name, slice_path.name, set_args))
        wanted = dev_slice.slice_questions(dev_slice.load_slice(slice_path), benchmark)
        rows = []
        for question in sorted(wanted):
            category = CATEGORIES[int(question[-4:]) % 4] if benchmark == "locomo" else self.types[question]
            rows.append(gate_row(benchmark, question, category))
        output.write_text(json.dumps({
            "rows": rows, "projection": {"x": 1}, "baseline": {"y": 2},
            "provenance": {"datasets": {benchmark: f"{benchmark}-sha"},
                           "engine_config": {"packing": {"tokenizer": "cl100k_base"}}}}))
        log.write_text("gated\n")


def run_it(tmp_path, sample_questions, fakes, **change):
    options = dict(label="main", out=tmp_path / "out", overrides={}, set_args=[], root=tmp_path / "builds",
                   cache_dir=tmp_path / "cache", lme_questions=6, conversations=None, build_shards=None,
                   gate_shards=3, model="deepseek-v4.1-flash:cloud", in_flight=24, attempts=3, retry_wait=0.0,
                   command=["python"], build_fn=fakes.build, gate_fn=fakes.gate, locomo_questions=sample_questions)
    options.update(change)
    return asyncio.run(run_mid_tier(**options))


def test_a_run_builds_both_benchmarks_gates_each_and_writes_one_report(tmp_path, locomo_questions):
    fakes = Fakes(locomo_questions)
    report = run_it(tmp_path, locomo_questions, fakes, set_args=["packing.token_budget=8192"],
                    overrides=parse_overrides(["packing.token_budget=8192"]))
    out = tmp_path / "out"
    assert json.loads((out / "report.json").read_text()) == report
    assert "# Mid tier: main" in (out / "report.md").read_text()
    assert list(report["stages"]) == ["build-locomo", "build-longmemeval", "gate-locomo", "gate-longmemeval"]
    builds = {(benchmark, shard): (packs, share, jobs) for benchmark, shard, packs, share, jobs, *_ in fakes.builds}
    # Four packs to a process: ten LoCoMo packs in three, six LongMemEval-S packs in two. All fit the cap.
    assert sorted(len(packs) for (benchmark, _), (packs, _, _) in builds.items() if benchmark == "locomo") == [3, 3, 4]
    assert sorted(len(packs) for (benchmark, _), (packs, _, _) in builds.items() if benchmark == "longmemeval") == [
        3, 3]
    assert all(share == jobs == len(packs) for packs, share, jobs in builds.values())
    assert (report["stages"]["build-locomo"]["processes"], report["stages"]["build-longmemeval"]["processes"]) == (
        3, 2)
    assert {item[5][0] for item in fakes.builds} == {"packing.token_budget=8192"}
    # The LoCoMo gate ran as three shards that together cover every conversation; LongMemEval-S as one.
    assert sorted(gate[2] for gate in fakes.gates if gate[0] == "locomo") == [
        "locomo-0.json", "locomo-1.json", "locomo-2.json"]
    assert [gate[2] for gate in fakes.gates if gate[0] == "longmemeval"] == ["longmemeval.json"]
    assert all(gate[3] == ["packing.token_budget=8192"] for gate in fakes.gates)
    assert report["stages"]["build-locomo"]["packs"] == 10 and report["stages"]["build-locomo"]["calls"] == 20
    assert report["stages"]["gate-locomo"]["shards"] == 3 and report["stages"]["gate-locomo"]["questions"] == 60
    assert report["stages"]["gate-longmemeval"]["questions"] == 6
    assert report["total"]["cache"] == "partly warm" and report["settings"]["overrides"] == {
        "packing": {"token_budget": 8192}}
    assert report["model"] == {"model": "deepseek-v4.1-flash:cloud", "provider": "ollama_cloud", "digest": "digest",
                               "server_versions": ["0.40.6"]}
    assert report["settings"]["datasets"] == {"locomo": "locomo-sha", "longmemeval": "longmemeval-sha"}
    assert report["measures"]["locomo"]["overall"]["questions"] == 60
    assert (tmp_path / "builds" / "main-locomo" / "build.json").is_file()
    # A finished run is never overwritten.
    with pytest.raises(ValueError, match="exists: use another --out"):
        run_it(tmp_path, locomo_questions, Fakes(locomo_questions))


def test_a_run_builds_again_after_a_pack_pauses_and_gives_up_after_its_attempts(tmp_path, locomo_questions, sample):
    paused = sample["longmemeval"]["order"][2]["question_id"]
    fakes = Fakes(locomo_questions, pause_first=[paused])
    report = run_it(tmp_path, locomo_questions, fakes)
    assert report["stages"]["build-longmemeval"]["builds"] == 2 and report["stages"]["build-locomo"]["builds"] == 1
    # Two processes built the packs twice; the second time the paused pack's process built it again.
    assert sum(1 for build in fakes.builds if build[0] == "longmemeval") == 4
    stuck = Fakes(locomo_questions)

    async def never(*args, **kwargs):
        await stuck.build(*args, **kwargs)
        for pack in (tmp_path / "builds2" / "again-longmemeval" / "longmemeval").glob("*/manifest.json"):
            pack.unlink()

    with pytest.raises(RuntimeError, match=r"6 of 6 longmemeval packs are not complete after 2 builds"):
        run_it(tmp_path, locomo_questions, stuck, label="again", root=tmp_path / "builds2", build_fn=never,
               attempts=2, out=tmp_path / "out2")


def test_a_trial_run_covers_only_the_named_conversations(tmp_path, locomo_questions):
    fakes = Fakes(locomo_questions)
    report = run_it(tmp_path, locomo_questions, fakes, conversations=["conv-26", "conv-30"], lme_questions=1)
    assert report["sample"]["trial"] is True and report["sample"]["locomo_conversations"] == ["conv-26", "conv-30"]
    assert report["measures"]["locomo"]["overall"]["questions"] == 12
    assert len(report["sample"]["longmemeval_questions"]) == 1
    assert "(trial: not the mid tier)" in report_markdown(report)
    # A trial and the full sample have different questions, so they never compare.
    full = make_report(load_sample(), locomo_questions)
    with pytest.raises(ValueError, match="different questions"):
        compare_reports(full, report)
