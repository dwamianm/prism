"""The fixed development slice of LoCoMo and LongMemEval-S (benchmarks/diagnostics/dev_slice.py)."""
import hashlib
import json

import pytest

from benchmarks.diagnostics import dev_slice, extracted_packs
from benchmarks.diagnostics.product_packing import _COMPARED_INPUTS, _slice_cases
from tests.test_extracted_packs import locomo  # noqa: F401  (fixture)

TRACK = dev_slice.TRACK


def _row(question, kind, correct, *, cluster="c", text=None, context=None):
    row = {"question_id": question, "question_type": kind, "cluster": cluster, "correct": correct,
           "outcome": "judged"}
    if text:
        row["context_text_sha256"] = text
    if context:
        row["context_sha256"] = context
    return row


def _write(root, name, rows, kind="ollama-answer-result"):
    path = root / "benchmarks" / "results" / "research" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"kind": kind, "rows": rows}))
    return path


def _locomo_rows(conversations):
    """Every conversation gets one question of each category it lists."""
    return [_row(f"{conversation}-q{index}", kind, True, cluster=conversation, text=f"{conversation}{index}")
            for conversation, kinds in conversations.items() for index, kind in enumerate(kinds)]


ALL = ["single-hop", "multi-hop", "temporal", "open-domain"]
TURNS = {"conv-a": 100, "conv-b": 50, "conv-c": 400, "conv-d": 300, "conv-e": 200}


@pytest.fixture
def results(tmp_path):
    # conv-b is the shortest but lacks open-domain, so it is never chosen.
    conversations = _locomo_rows({"conv-a": ALL, "conv-b": ALL[:3], "conv-c": ALL, "conv-d": ALL, "conv-e": ALL})
    _write(tmp_path, dev_slice.BASELINE["locomo"], conversations)
    lme = [_row("wrong-1", "multi-session", False, text="w1"),
           _row("wrong-flips", "multi-session", False, text="w2"),
           _row("wrong-old-flips", "temporal-reasoning", False, text="w3", context="c3")]
    lme += [_row(f"right-{index}", "multi-session", True, text=f"r{index}") for index in range(6)]
    _write(tmp_path, dev_slice.BASELINE["longmemeval"], lme)
    _write(tmp_path, f"2026-09-24/{TRACK}-prme-longmemeval-result.json", [
        _row("wrong-flips", "multi-session", True, text="w2"),
        # Only the full-context hash, which is not comparable with a text hash.
        _row("wrong-1", "multi-session", True, context="other"),
        _row("wrong-old-flips", "temporal-reasoning", True, context="c3"),
    ])
    # A rescoring by another judge is not a repeated answer.
    _write(tmp_path, f"2026-09-25/{TRACK}-prme-longmemeval-lenient-judge-result.json",
           [_row("wrong-1", "multi-session", True, text="w1")], kind="lenient-judge-result")
    return tmp_path


def test_the_slice_takes_the_shortest_complete_conversations_and_the_stable_failures_with_canaries(results):
    chosen = dev_slice.select(results, locomo_turns=TURNS)
    conversations = chosen["benchmarks"]["locomo"]
    assert conversations["units"] == ["conv-a", "conv-d", "conv-e"]
    assert len(conversations["questions"]) == 12 and conversations["summary"]["of"] == 19
    lme = chosen["benchmarks"]["longmemeval"]
    assert lme["tags"]["wrong-1"] == "stable_wrong"
    assert "wrong-flips" not in lme["questions"] and "wrong-old-flips" not in lme["questions"]
    canaries = [question for question in lme["questions"] if lme["tags"][question] == "stable_right"]
    expected = sorted((f"right-{index}" for index in range(6)), key=lambda q: hashlib.sha256(q.encode()).hexdigest())
    assert sorted(canaries) == sorted(expected[:dev_slice.CANARIES_PER_TYPE])
    assert lme["units"] == lme["questions"]
    runs = [source["path"] for source in chosen["sources"]["longmemeval"]["answer_runs"]]
    # The baseline is itself an answer run; the lenient-judge rescoring is not.
    assert runs == [f"benchmarks/results/research/2026-09-24/{TRACK}-prme-longmemeval-result.json",
                    f"benchmarks/results/research/{dev_slice.BASELINE['longmemeval']}"]


def test_a_slice_stays_fixed_to_the_files_it_names(results):
    chosen = dev_slice.select(results, locomo_turns=TURNS)
    dev_slice.check(chosen, results, locomo_turns=TURNS)
    # A later run is not read, even one that would make a slice question noisy.
    _write(results, f"2026-09-26/{TRACK}-prme-longmemeval-result.json",
           [_row("wrong-1", "multi-session", True, text="w1")])
    dev_slice.check(chosen, results, locomo_turns=TURNS)
    assert dev_slice.select(results, locomo_turns=TURNS) != chosen
    # A named file that changed is refused.
    _write(results, dev_slice.BASELINE["longmemeval"], [_row("wrong-1", "multi-session", True, text="w1")])
    with pytest.raises(ValueError, match="changed since the slice was selected"):
        dev_slice.check(chosen, results, locomo_turns=TURNS)


def test_the_committed_slice_follows_from_the_files_it_names():
    dev_slice.check(json.loads(dev_slice.DEFAULT_SLICE.read_text()))


def _slice_file(tmp_path, locomo_questions, lme_questions=()):
    record = {"kind": dev_slice.KIND, "schema_version": dev_slice.SCHEMA_VERSION, "name": "test",
              "benchmarks": {"locomo": {"units": ["conv-1"], "questions": list(locomo_questions)},
                             "longmemeval": {"units": list(lme_questions), "questions": list(lme_questions)}}}
    path = tmp_path / "slice.json"
    path.write_text(json.dumps(record))
    return path


def test_the_gate_replays_only_the_slice_and_refuses_one_it_cannot_cover(tmp_path):
    from benchmarks.diagnostics.product_packing import GateCase

    def case(question):
        return GateCase(benchmark="locomo", question_id=question, category="single-hop", question="?",
                        user_id="conv-1", reference_time=None, pack=tmp_path, pack_sha256="",
                        saved_context_sha256="", evidence=None)

    cases = [case("conv-1-q0000"), case("conv-1-q0001")]
    kept, identity = _slice_cases(cases, _slice_file(tmp_path, ["conv-1-q0001"]), ["locomo"])
    assert [kept_case.question_id for kept_case in kept] == ["conv-1-q0001"]
    assert identity["name"] == "test" and len(identity["sha256"]) == 64
    with pytest.raises(ValueError, match="lacks 1 locomo slice questions"):
        _slice_cases(cases, _slice_file(tmp_path, ["conv-1-q0001", "conv-9-q0000"]), ["locomo"])


def test_gate_reports_over_different_slices_are_not_compared():
    compared = _COMPARED_INPUTS["development slices"]
    assert compared({"provenance": {}}) is None
    assert compared({"provenance": {"slice": {"name": "a", "sha256": "1"}}}) != compared({"provenance": {}})


def test_the_builder_builds_the_slices_packs(tmp_path, monkeypatch, locomo):  # noqa: F811
    seen = {}

    async def fake_build(label, **kwargs):
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(extracted_packs, "build", fake_build)
    monkeypatch.setattr(extracted_packs, "_quiet_offline_cli", lambda: None)
    extracted_packs.main(["build", "--label", "trial", "--slice", str(_slice_file(tmp_path, ["conv-1-q0000"]))])
    assert seen["units"] == ["conv-1"]
    with pytest.raises(SystemExit):
        extracted_packs.main(["build", "--label", "trial", "--unit", "conv-1", "--slice"])
