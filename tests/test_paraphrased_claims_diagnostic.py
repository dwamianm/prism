"""Paraphrased duplicate claims in ingest() packs and the context they take (#255). No model calls."""
import hashlib
import json
import math
import shutil

import duckdb
import numpy as np
import pytest

from benchmarks.diagnostics import paraphrased_claims as pc
from benchmarks.diagnostics.extracted_packs import build_config, build_unit, locomo_units
from prme.ingestion.schema import ExtractionResult

STAMP = "1:56 pm on 8 May, 2023"
# Two of Caroline's claims say one thing in other words; Melanie's is unrelated.
TURNS = [
    ("D1:1", "Caroline", "Caroline paints sunsets over the lake."),
    ("D1:2", "Melanie", "Melanie adopted a puppy named Oscar."),
    ("D1:3", "Caroline", "Caroline loves painting sunsets at the lake."),
]
CLAIMS = {
    "Caroline paints sunsets over the lake.": ("Caroline", "paints", "sunsets"),
    "Melanie adopted a puppy named Oscar.": ("Melanie", "adopted", "a puppy named Oscar"),
    "Caroline loves painting sunsets at the lake.": ("Caroline", "loves_painting", "sunsets"),
}


def _unit(vector) -> np.ndarray:
    vector = np.asarray(vector, dtype=float)
    return vector / np.linalg.norm(vector)


def _at(cosine: float) -> list[float]:
    """A vector at this cosine similarity from [1, 0, 0]."""
    return [cosine, math.sqrt(1 - cosine * cosine), 0.0]


def _claims(vectors: dict, subjects: dict, *, texts: dict | None = None, sources: dict | None = None,
            unit: str = "conv-1") -> pc.PackClaims:
    ids = tuple(sorted(vectors))
    return pc.PackClaims(
        unit=unit, pack_sha256=f"sha-{unit}", ids=ids, vectors=np.array([_unit(vectors[i]) for i in ids]),
        subjects={i: frozenset(subjects[i]) for i in ids},
        sources={i: frozenset((sources or {}).get(i, {f"event-{i}"})) for i in ids},
        texts={i: (texts or {}).get(i, f"text {i}") for i in ids},
        details={i: {"line": f'- "{i}"', "source_turns": [i]} for i in ids},
        subject_names={subject: subject.title() for i in ids for subject in subjects[i]},
        counts={"current_claims": len(ids)}, embedding={"model": "test", "version": "1", "dimension": 3})


def _pairs(bands: dict[str, int], unit: str = "conv-1") -> list[dict]:
    """Synthetic pairs: ``bands[name]`` pairs in each band, at its lower edge."""
    low = {band.name: band.low for band in pc.BANDS}
    return [{"unit": unit, "a": f"{name}-{index:03d}-a", "b": f"{name}-{index:03d}-b", "similarity": low[name],
             "band": name, "same_source": False, "identical_text": False, "subjects": ["s"]}
            for name, count in bands.items() for index in range(count)]


def _sheet(pairs: list[dict]) -> dict:
    rows = [{"id": pc.pair_id(pair), "unit": pair["unit"], "band": pair["band"], "similarity": pair["similarity"],
             "identical_text": pair["identical_text"], "label": "same_fact", "difference": None,
             "reason": "Both say the same thing."} for pair in pc.sample_pairs(pairs)]
    return {"kind": pc.LABELS_KIND, "schema_version": pc.SCHEMA_VERSION, "seed": pc.SAMPLE_SEED,
            "sizes": dict(pc.SAMPLE_SIZES), "pairs_sha256": pc.digest(pairs), "pairs": rows}


def test_bands_include_their_lower_edge_and_rounding_decides_the_edge():
    assert [pc.band_of(value) for value in (0.899999, 0.9, 0.949999, 0.95, 0.969999, 0.97, 1.0)] == [
        None, "0.90-0.95", "0.90-0.95", "0.95-0.97", "0.95-0.97", "0.97-1.00", "0.97-1.00"]
    claims = _claims({"a": [1, 0, 0], "b": _at(0.8999996), "c": [1, 0, 0], "d": _at(0.8999994)},
                     {"a": {"s"}, "b": {"s"}, "c": {"t"}, "d": {"t"}})
    pairs, _ = pc.similar_pairs(claims)
    # 0.8999996 shows as 0.9 and is banded as it shows; 0.8999994 shows as 0.899999 and is left out.
    assert [(pair["a"], pair["b"], pair["similarity"], pair["band"]) for pair in pairs] == [
        ("a", "b", 0.9, "0.90-0.95")]


def test_pairs_need_a_shared_subject_and_appear_once_whatever_they_share():
    claims = _claims(
        {"a": [1, 0, 0], "b": _at(0.96), "c": [1, 0, 0], "e": _at(0.89), "f": [0, 0, 1], "g": [0, 1, 1],
         "h": [0, 1, 1]},
        {"a": {"s"}, "b": {"s"}, "c": {"t"}, "e": {"t"}, "f": {"s", "t"}, "g": {"u", "v"}, "h": {"u", "v"}},
        texts={"g": "same words", "h": "same words"}, sources={"g": {"turn-1"}, "h": {"turn-1"}})
    pairs, same_subject = pc.similar_pairs(claims)
    # a and c are identical but have different subjects; e is below 0.90; f is orthogonal to everyone.
    assert [(pair["a"], pair["b"], pair["band"], pair["subjects"]) for pair in pairs] == [
        ("a", "b", "0.95-0.97", ["s"]), ("g", "h", "0.97-1.00", ["u", "v"])]
    assert (pairs[0]["identical_text"], pairs[0]["same_source"]) == (False, False)
    assert (pairs[1]["identical_text"], pairs[1]["same_source"]) == (True, True)
    # s: a-b, a-f, b-f; t: c-e, c-f, e-f; u and v share one pair, g-h.
    assert same_subject == 7
    counts = pc.unit_counts(claims, pairs, same_subject)
    assert counts["pairs_at_or_above"] == {"0.90": 2, "0.95": 2, "0.97": 1}
    assert counts["identical_text_at_or_above"] == {"0.90": 1, "0.95": 1, "0.97": 1}
    # Only a-b comes from two turns, and both its lines are short.
    assert counts["short_other_turns_at_or_above"] == {"0.90": 1, "0.95": 1, "0.97": 0}
    assert counts["subjects"] == 4


def test_short_lines_are_counted_after_the_turns_time_and_speaker():
    assert pc._words("(1:56 pm on 8 May, 2023) Caroline: Thanks so much, Nate!") == 4
    assert pc._words("I said (quietly) hi: there") == 5
    assert pc._words("") == 0


def test_the_sample_is_stratified_reproducible_and_keeps_its_total_when_a_band_runs_short():
    pairs = _pairs({"0.90-0.95": 30, "0.95-0.97": 2, "0.97-1.00": 40})
    sample = pc.sample_pairs(pairs)
    assert sample == pc.sample_pairs(list(reversed(pairs)))
    counts = {band.name: sum(pair["band"] == band.name for pair in sample) for band in pc.BANDS}
    # The short band gives both its pairs; its 15 left over go to the other bands in order.
    assert counts == {"0.90-0.95": 30, "0.95-0.97": 2, "0.97-1.00": 18}
    assert len({pc.pair_id(pair) for pair in sample}) == 50
    assert pc.sample_pairs(pairs, seed=1) != sample
    plenty = _pairs({"0.90-0.95": 40, "0.95-0.97": 40, "0.97-1.00": 40})
    assert [sum(pair["band"] == band.name for pair in pc.sample_pairs(plenty)) for band in pc.BANDS] == [17, 17, 16]
    with pytest.raises(ValueError, match="sample size"):
        pc.sample_pairs(pairs, {"0.90-0.95": 1})


def test_labels_must_be_the_seeds_sample_and_each_needs_a_label_and_a_one_line_reason():
    pairs = _pairs({"0.90-0.95": 20, "0.95-0.97": 20, "0.97-1.00": 20})
    doc = {"pairs": pairs}
    sheet = _sheet(pairs)
    assert len(pc.check_labels(sheet, doc)) == 50

    def refused(change, message):
        broken = json.loads(json.dumps(sheet))
        change(broken)
        with pytest.raises(ValueError, match=message):
            pc.check_labels(broken, doc)

    refused(lambda s: s["pairs"][0].update(label=None), "label must be")
    refused(lambda s: s["pairs"][0].update(difference="date"), "only a different-facts pair")
    refused(lambda s: s["pairs"][0].update(label="different_facts", difference="tense"), "only a different-facts")
    refused(lambda s: s["pairs"][0].update(reason="two\nlines"), "one-line reason")
    refused(lambda s: s["pairs"][0].update(similarity=0.99), "band or similarity")
    refused(lambda s: s["pairs"].reverse(), "not the sample its seed draws")
    # A sample drawn again with another seed or other sizes is refused, even when it matches its own seed.
    redrawn = [pc.pair_id(pair) for pair in pc.sample_pairs(pairs, seed=8)]
    refused(lambda s: (s.update(seed=8), [row.update(id=new) for row, new in zip(s["pairs"], redrawn)]),
            "must be drawn with seed 255")
    refused(lambda s: s.update(sizes={"0.90-0.95": 50, "0.95-0.97": 0, "0.97-1.00": 0}), "must be drawn with seed")
    refused(lambda s: s.pop("seed"), "must be drawn with seed")
    refused(lambda s: s.update(pairs_sha256="0" * 64), "drawn from other pairs")
    refused(lambda s: s.pop("pairs_sha256"), "drawn from other pairs")
    sheet["pairs"][0].update(label="different_facts", difference="number")
    assert pc.check_labels(sheet, doc)[0]["difference"] == "number"


def test_a_packed_claim_counts_only_with_a_packed_partner_at_or_above_the_threshold():
    pairs = [{"unit": "u", "a": "x", "b": "y", "similarity": 0.96, "identical_text": False},
             {"unit": "u", "a": "x", "b": "z", "similarity": 0.91, "identical_text": True},
             {"unit": "u", "a": "v", "b": "w", "similarity": 0.98, "identical_text": False}]
    partners = pc.partner_index(pairs)
    question = {"unit": "u", "records": [["x", 10], ["y", 20], ["z", 30], ["w", 40], ["note", 50]],
                "context_tokens": 200}
    rates = {"0.90-0.95": 0.5, "0.95-0.97": 0.8, "0.97-1.00": 1.0}
    counts = pc.question_counts(question, partners, {"all": rates, "paraphrases": rates})
    # w's partner v is not packed, so w never counts.
    assert counts["0.90"]["all"]["records"] == 3 and counts["0.90"]["all"]["tokens"] == 60
    # x has two packed partners: it counts once, with the chance that either is the same fact.
    assert counts["0.90"]["all"]["adjusted_tokens"] == pytest.approx(10 * (1 - 0.2 * 0.5) + 20 * 0.8 + 30 * 0.5)
    assert counts["0.95"]["all"] == {"records": 2, "tokens": 30, "adjusted_tokens": pytest.approx(24.0)}
    assert counts["0.97"]["all"] == {"records": 0, "tokens": 0, "adjusted_tokens": 0.0}
    # Paraphrases leave out the identical-text pair x-z, so z no longer counts.
    assert counts["0.90"]["paraphrases"] == {"records": 2, "tokens": 30, "adjusted_tokens": pytest.approx(24.0)}
    unknown = pc.question_counts(question, partners, {"all": {**rates, "0.95-0.97": None}, "paraphrases": rates})
    assert unknown["0.90"]["all"]["adjusted_tokens"] is None and unknown["0.90"]["all"]["tokens"] == 60


def _row(tokens: int, context_tokens: int, adjusted: float, record_tokens: int) -> dict:
    count = {"records": int(tokens > 0), "tokens": tokens, "adjusted_tokens": adjusted}
    return {"context_tokens": context_tokens, "record_tokens": record_tokens,
            "thresholds": {key: {"all": count, "paraphrases": count} for key in ("0.90", "0.95", "0.97")}}


def test_shares_are_averaged_over_questions_and_the_pooled_share_is_reported_beside_them():
    rows = [_row(30, 100, 15.0, 90), _row(0, 300, 0.0, 280)]
    summary = pc.share_summary(rows, "0.90", "all")
    assert summary["share_mean"] == pytest.approx(0.15) and summary["pooled_share"] == pytest.approx(30 / 400)
    assert summary["adjusted_share_mean"] == pytest.approx(0.075)
    assert summary["adjusted_pooled_share"] == pytest.approx(15 / 400)
    assert summary["record_tokens_share_mean"] == pytest.approx((30 / 90) / 2)
    assert summary["questions_with_a_packed_pair"] == 1 and summary["records_mean"] == 0.5
    # An empty context packs nothing, so its share is 0 rather than a division by zero.
    empty = pc.share_summary([_row(0, 0, 0.0, 0)], "0.90", "all")
    assert (empty["share_mean"], empty["adjusted_share_mean"], empty["pooled_share"]) == (0.0, 0.0, None)


def _labels(rows_by_band: dict[str, list[tuple[str, str | None]]], identical: bool = False) -> list[dict]:
    return [{"band": band, "label": label, "difference": difference, "identical_text": identical}
            for band, rows in rows_by_band.items() for label, difference in rows]


def test_label_bands_set_aside_number_date_and_negation_pairs_and_weigh_bands_by_their_pairs():
    labels = _labels({
        "0.90-0.95": [("same_fact", None), ("different_facts", "date"), ("different_facts", None),
                      ("different_facts", None)],
        "0.95-0.97": [("same_fact", None), ("same_fact", None)],
        "0.97-1.00": [("same_fact", None), ("different_facts", "negation")],
    })
    pairs = _pairs({"0.90-0.95": 100, "0.95-0.97": 50, "0.97-1.00": 10})
    summary = pc.label_summary(labels, pairs)["all"]
    low, top = summary["bands"]["0.90-0.95"], summary["bands"]["0.97-1.00"]
    assert (low["same_fact_rate"], low["same_fact_share_set_aside"], low["meets_rule"]) == (0.25, 1 / 3, False)
    assert top["differ_only_in"]["negation"] == 1 and top["same_fact_share_set_aside"] == 1.0 and top["meets_rule"]
    weighted = summary["weighted_at_or_above"]
    assert weighted["0.90"] == pytest.approx((25 + 50 + 5) / (75 + 50 + 5))
    assert weighted["0.97"] == 1.0


def _shares(adjusted: float | None, upper: float, paraphrases: float | None | str = "same") -> dict:
    """Shares at 0.90 for both pair sets; paraphrases take the adjusted share unless given one."""
    def reading(value: float | None, share: float) -> dict:
        return {"share_mean": share, "adjusted_share_mean": value, "adjusted_pooled_share": value,
                "adjusted_record_tokens_share_mean": value}

    return {"0.90": {"all": reading(adjusted, upper),
                     "paraphrases": reading(adjusted if paraphrases == "same" else paraphrases, upper)}}


def _band_summary(meets: dict[str, bool]) -> dict:
    return {"bands": {name: {"meets_rule": value} for name, value in meets.items()},
            "weighted_at_or_above": {"0.90": 0.9, "0.95": 0.9, "0.97": 1.0 if meets["0.97-1.00"] else 0.5}}


def test_the_decision_needs_both_conditions_and_names_readings_that_would_change_it():
    meets = {"0.90-0.95": False, "0.95-0.97": False, "0.97-1.00": True}
    labels = {"all": _band_summary(meets), "paraphrases": _band_summary(meets)}
    opened = pc.decide(_shares(0.06, 0.08), labels)
    assert opened["follow_up"] and opened["condition_2"]["bands_meeting_rule"] == ["0.97-1.00"]
    assert opened["readings_that_change_the_decision"] == []
    closed = pc.decide(_shares(0.04, 0.08, paraphrases=0.03), labels)
    assert not closed["follow_up"] and closed["outcome"] == "close the issue with the numbers"
    assert closed["readings_that_change_the_decision"] == [
        "condition 1 on the upper bound (every pair at or above 0.90 counted as the same fact)"]
    nothing_meets = {"all": _band_summary(dict.fromkeys(meets, False)),
                     "paraphrases": _band_summary(dict.fromkeys(meets, False))}
    assert not pc.decide(_shares(0.06, 0.08), nothing_meets)["follow_up"]
    # Exactly 5% is enough.
    assert pc.decide(_shares(0.05, 0.08), labels)["condition_1"]["holds"]


def test_a_share_that_cannot_be_estimated_decides_nothing():
    meets = {"0.90-0.95": False, "0.95-0.97": False, "0.97-1.00": True}
    labels = {"all": _band_summary(meets), "paraphrases": _band_summary(meets)}
    with pytest.raises(ValueError, match="cannot be read"):
        pc.decide(_shares(None, 0.08), labels)
    decision = pc.decide(_shares(0.06, 0.08, paraphrases=None), labels)
    name = "both conditions with identical-text pairs set aside"
    assert decision["other_readings"][name]["follow_up"] is None
    assert decision["readings_undetermined"] == [name] and decision["readings_that_change_the_decision"] == []


def test_a_band_meets_the_rule_at_exactly_95_percent_and_wilson_limits_bound_each_rate():
    nineteen = _labels({"0.97-1.00": [("same_fact", None)] * 19 + [("different_facts", None)]})
    assert pc.label_summary(nineteen, _pairs({"0.97-1.00": 20}))["all"]["bands"]["0.97-1.00"]["meets_rule"]
    low, high = pc.wilson_interval(0, 17)
    assert low == pytest.approx(0.0, abs=1e-12) and high == pytest.approx(0.1843, abs=1e-4)
    assert pc.wilson_interval(12, 13) == pytest.approx((0.6669, 0.9863), abs=1e-4)
    assert pc.wilson_interval(0, 0) is None


def test_sampling_error_recomputes_the_adjusted_share_at_each_limit():
    pairs = [{"unit": "u", "a": "x", "b": "y", "similarity": 0.91, "identical_text": False}]
    question = {"unit": "u", "records": [["x", 10], ["y", 10]], "context_tokens": 100}
    bands = pc.label_summary(_labels({"0.90-0.95": [("different_facts", None)] * 4}), _pairs({"0.90-0.95": 4}))
    error = pc.sampling_error(bands["all"]["bands"], [question], pc.partner_index(pairs))
    upper = pc.wilson_interval(0, 4)[1]
    assert error["adjusted_share_mean_at_lower_limits"] == 0.0
    assert error["adjusted_share_mean_at_upper_limits"] == pytest.approx(20 * upper / 100)
    assert error["bands_meeting_rule_at_upper_limits"] == []


def test_stored_vectors_take_the_latest_key_and_refuse_mixed_or_malformed_embeddings():
    def row(node, key, vector, model="m"):
        return node, key, model, "1", len(vector), np.asarray(vector, dtype="<f4").tobytes()

    vectors, embedding, several = pc._stored_vectors(
        [row("a", 2, [0, 1]), row("a", 1, [1, 0]), row("b", 3, [3, 4]), row("other", 4, [1, 1])], {"a", "b"})
    assert several == 1 and embedding == {"model": "m", "version": "1", "dimension": 2}
    assert vectors["a"] == pytest.approx([0, 1]) and vectors["b"] == pytest.approx([0.6, 0.8])
    with pytest.raises(ValueError, match="more than one embedding"):
        pc._stored_vectors([row("a", 1, [1, 0]), row("b", 2, [1, 0], model="n")], {"a", "b"})
    with pytest.raises(ValueError, match="malformed"):
        pc._stored_vectors([row("a", 1, [0, 0])], {"a"})
    with pytest.raises(ValueError, match="malformed"):
        pc._stored_vectors([("a", 1, "m", "1", 3, np.zeros(2, dtype="<f4").tobytes())], {"a"})


# End to end over a pack built through ingest() ----------------------------------

class TopicEmbeddings:
    """Texts that mention sunsets point the same way, apart from a small part of their own."""

    model_name = "topic-test"
    model_version = "1"
    dimension = 384

    async def embed(self, texts):
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimension
            vector[0 if "sunset" in text.lower() else 1 + hashlib.sha256(text.encode()).digest()[1] % 100] = 1.0
            vector[200 + hashlib.sha256(text.encode()).digest()[0] % 150] += 0.2
            vectors.append(vector)
        return vectors


class ScriptedExtraction:
    async def extract(self, content, *, role=None):
        for text, (subject, predicate, obj) in CLAIMS.items():
            if content.endswith(text):
                # An inferred claim shows a tag its turn does not, so packing never folds it into the turn.
                return ExtractionResult.model_validate({
                    "entities": [{"name": subject, "entity_type": "person"}],
                    "facts": [{"subject": subject, "subject_entity_type": "person", "predicate": predicate,
                               "object": obj, "object_entity_type": None, "polarity": "positive",
                               "epistemic_type": "inferred", "evidence_quote": text}]})
        return ExtractionResult.model_validate({"entities": [], "facts": []})


@pytest.fixture
def build(tmp_path, monkeypatch):
    from benchmarks.integrations import run_gpt54_comparison as gpt54
    from benchmarks.integrations.gpt54_budget import digest

    path = tmp_path / "locomo10.json"
    path.write_text(json.dumps([{"sample_id": "conv-1", "conversation": {
        "session_1_date_time": STAMP,
        "session_1": [{"speaker": speaker, "dia_id": dialog_id, "text": text} for dialog_id, speaker, text in TURNS]},
        "qa": [{"question": "What does Caroline paint?", "answer": "Sunsets", "evidence": ["D1:1"], "category": 1},
               {"question": "What did Melanie adopt?", "answer": "A puppy", "evidence": ["D1:2"], "category": 1}]}]))
    monkeypatch.setattr(gpt54, "LOCOMO", path)
    monkeypatch.setattr(gpt54, "LOCOMO_SHA", digest(path))
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: TopicEmbeddings())
    root = tmp_path / "build"
    (root / "locomo").mkdir(parents=True)
    (root / "build.json").write_text(json.dumps({"label": "trial", "model": {"model": "local-model"}}))
    return root


async def test_a_built_pack_yields_its_paraphrase_pair_and_the_gate_contexts_that_pack_both(build, tmp_path):
    [unit] = locomo_units()
    scripted = ScriptedExtraction()

    def prepare(engine):
        engine._pipeline._extraction_provider.extract = scripted.extract

    # The scripted model fakes the per-turn extract() call, so the build extracts one turn per call (#254).
    per_turn = {"extraction_batch_target_tokens": 0}
    await build_unit(build / "locomo" / "conv-1", unit, lambda pack: build_config(pack, per_turn, model="local-model"),
                     prepare=prepare)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    pairs_doc, packs = pc.collect_pairs(build, "locomo", scratch=scratch)
    assert list(scratch.iterdir()) == []
    counts = pairs_doc["units"]["conv-1"]
    assert (counts["current_claims"], counts["paired_claims"], counts["same_subject_pairs"]) == (3, 3, 1)
    assert counts["without_subject"] == counts["without_vector"] == counts["with_several_vectors"] == 0
    [pair] = pairs_doc["pairs"]
    assert pair["band"] == "0.95-0.97" and not pair["identical_text"] and not pair["same_source"]
    assert pairs_doc["embedding"] == {"model": "topic-test", "version": "1", "dimension": 384,
                                      "source": "each pack's stored claim vectors"}

    sheet = pc.labeling_sheet(pairs_doc, packs)
    [row] = sheet["pairs"]
    assert {row["a"]["source_turns"][0], row["b"]["source_turns"][0]} == {"D1:1", "D1:3"}
    assert row["subjects"] == ["Caroline"] and "[inferred]" in row["a"]["line"]
    row.update(label="same_fact", reason="Both say Caroline paints sunsets by the lake.")

    contexts, gate = await pc.replay_contexts(build, "locomo")
    assert [question["question_id"] for question in contexts["questions"]] == ["conv-1-q0000", "conv-1-q0001"]
    assert contexts["tokenizer"] == "cl100k_base" and gate["benchmarks"]["locomo"]["summary"]["questions"] == 2
    for question in contexts["questions"]:
        assert all(cost > 0 for _, cost in question["records"])
        assert sum(cost for _, cost in question["records"]) <= question["context_tokens"]

    report = pc.build_report(pairs_doc, sheet, contexts)
    floor = report["shares"]["0.90"]["all"]
    # The whole conversation fits, so both claims are packed in both contexts.
    assert floor["questions_with_a_packed_pair"] == 2 and floor["records_mean"] == 2
    assert floor["adjusted_share_mean"] == pytest.approx(floor["share_mean"]) and floor["share_mean"] > 0
    assert report["questions"][0]["claims"] == 3
    assert report["labels"]["all"]["bands"]["0.95-0.97"]["meets_rule"]
    assert "**Decision:" in pc.report_markdown(report)
    assert report["sample"] == {"seed": 255, "sizes": pc.SAMPLE_SIZES, "labeled": 1}
    assert report["sampling_error"]["bands"]["0.95-0.97"]["same_fact_rate"][1] == 1.0
    with pytest.raises(ValueError, match="other packs"):
        pc.build_report({**pairs_doc, "units": {"conv-1": {**counts, "pack_sha256": "0" * 64}}}, sheet, contexts)
    with pytest.raises(ValueError, match="no question"):
        pc.build_report(pairs_doc, sheet, {**contexts, "questions": []})
    # Receipts that name records differently from the pairs file would read as no duplicates at all.
    renamed = [{**question, "records": [[f"x-{node_id}", cost] for node_id, cost in question["records"]]}
               for question in contexts["questions"]]
    with pytest.raises(ValueError, match="No packed record is a current claim"):
        pc.build_report(pairs_doc, sheet, {**contexts, "questions": renamed})

    # The command line records each input file's digest and the code that wrote the report.
    files = {name: tmp_path / f"{name}.json" for name in ("pairs", "labels", "contexts")}
    for name, value in zip(files, (pairs_doc, sheet, contexts)):
        files[name].write_text(json.dumps(value))
    arguments = [item for name, path in files.items() for item in (f"--{name}", str(path))]
    output = tmp_path / "out" / "report.json"
    pc.main(["report", *arguments, "--output", str(output)])
    written = json.loads(output.read_text())
    assert written["inputs"] == {f"{name}_sha256": hashlib.sha256(path.read_bytes()).hexdigest()
                                 for name, path in files.items()}
    assert set(written["code"]) == {"pairs", "replay", "report"} and output.with_suffix(".md").is_file()
    with pytest.raises(SystemExit, match="overwrite one of its inputs"):
        pc.main(["report", *arguments, "--output", str(files["pairs"])])
    # A labeled sheet is never replaced: not by a new sheet, nor by the pairs or contexts output.
    for arguments, message in (
            (["pairs", "--output", str(tmp_path / "again.json"), "--sheet", str(files["labels"])],
             "would discard its labels"),
            (["pairs", "--output", str(files["labels"])], "holds a labeling sheet"),
            (["pairs", "--output", str(tmp_path / "same.json"), "--sheet", str(tmp_path / "same.json")],
             "two different files"),
            (["replay", "--output", str(files["labels"])], "holds a labeling sheet")):
        with pytest.raises(SystemExit, match=message):
            pc.main([arguments[0], "--packs", str(build), *arguments[1:]])
    # A reading that could not be estimated is named beside the decision.
    report["decision"]["readings_undetermined"] = ["a reading"]
    assert "Could not be estimated: a reading." in pc.report_markdown(report)

    # Only current claims with a current subject edge are read. On one copy, retire one claim by its
    # lifecycle state alone, mark another superseded by a successor alone and close the third's validity;
    # on another, close one claim's HAS_FACT edge.
    pack = build / "locomo" / "conv-1" / "pack"
    first, second, third = packs["conv-1"].ids
    for name, statements, current, without_subject in (
            ("retired", [("UPDATE nodes SET lifecycle_state = 'superseded' WHERE id = ?", [first]),
                         ("UPDATE nodes SET superseded_by = ? WHERE id = ?", [third, second]),
                         ("UPDATE nodes SET valid_to = now() WHERE id = ?", [third])], 0, 0),
            ("no-subject", [("UPDATE edges SET valid_to = now() WHERE target_id = ? AND edge_type = 'has_fact'",
                             [first])], 3, 1)):
        copy = tmp_path / name
        shutil.copytree(pack, copy)
        with duckdb.connect(str(copy / "memory.duckdb")) as db:
            for statement, parameters in statements:
                db.execute(statement, parameters)
        changed = pc.read_pack(copy, unit="conv-1", user_id="conv-1", benchmark="locomo", pack_sha256=name)
        assert (changed.counts["current_claims"], changed.counts["without_subject"]) == (current, without_subject)
        assert len(changed.ids) == current - without_subject and first not in changed.ids


def test_a_step_outside_a_git_checkout_is_refused(monkeypatch):
    monkeypatch.setattr("benchmarks.retrieval_eval.provenance", lambda config: {
        "commit": None, "dirty": False, "worktree_sha256": "0" * 64})
    with pytest.raises(ValueError, match="git checkout"):
        pc.code_state()
