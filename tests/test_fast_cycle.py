"""The fast cycle's fixtures, measures, rules and comparator, with no model and no network."""
import copy
import json
import re
from types import SimpleNamespace

import pytest

from benchmarks.diagnostics import chat_probe
from benchmarks.fast_cycle import compare, contextual, cycle, ingest, store
from prme.types import NodeType
from tests.test_chat_probe import CLAIMS, HOME, NURSE, ScriptedExtraction, _conversation, mock_embeddings  # noqa: F401

RULES = compare.load_rules()


# --- The store fixture ---------------------------------------------------------------------------------


def test_the_store_fixture_has_the_memories_and_the_three_questions_of_the_issues():
    fixture = store.load_fixture()
    questions = {question["id"]: question for question in fixture["key"]["questions"]}
    assert len(fixture["memories"]) == 75 and len(questions) == 17
    assert questions["rust"]["question"] == "what coding language is alex learning?"
    assert questions["japan-cities"]["question"] == "which cities will Alex and jordan visit in Japan?"
    assert questions["japan-cities"]["all_of"] == ["Tokyo", "Kyoto", "Kanazawa", "Osaka"]
    assert questions["cutover"]["question"] == "When is the production cutover?"
    assert questions["cutover"]["any_of"] == ["2026-10-31"] and questions["cutover"]["stale"] == ["2026-10-24"]


def test_every_answer_and_stale_term_of_the_store_fixture_is_in_a_memory():
    fixture = store.load_fixture()
    texts = [memory["content"].casefold() for memory in fixture["memories"]]
    for question in fixture["key"]["questions"]:
        terms = [term.casefold() for term in question.get("all_of") or question["any_of"]]
        holds = all if question.get("all_of") else any
        assert any(holds(term in text for term in terms) for text in texts), question["id"]
        for stale in question.get("stale", []):
            # The stale value is held by a memory that does not hold the answer, or the question cannot test it.
            assert any(stale.casefold() in text and not holds(term in text for term in terms)
                       for text in texts), (question["id"], stale)


def test_a_store_fixture_question_needs_all_of_or_any_of(tmp_path):
    fixture = store.load_fixture()
    fixture["key"]["questions"][0]["any_of"] = ["Rust"]
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture))
    with pytest.raises(ValueError, match="needs all_of or any_of"):
        store.load_fixture(path)


# --- The store measures --------------------------------------------------------------------------------


def _result(content, kind="fact"):
    return {"content": content, "type": kind}


CUTOVER = {"id": "cutover", "question": "When is the production cutover?", "any_of": ["2026-10-31"],
           "stale": ["2026-10-24"]}


def test_a_stale_value_above_the_answer_is_counted_only_above_it():
    old, new, other = (_result("Cutover is 2026-10-24."), _result("Cutover moved to 2026-10-31."),
                       _result("Alex is on call."))
    assert store.stale_above_answer(CUTOVER, [old, new, other])
    assert not store.stale_above_answer(CUTOVER, [new, old, other])
    # An answer that no result holds leaves the stale record as what the reader sees first.
    assert store.stale_above_answer(CUTOVER, [old, other])
    assert not store.stale_above_answer(CUTOVER, [other])
    # A record that holds both values is the answer; the entity named like the old value is not a record.
    assert not store.stale_above_answer(CUTOVER, [_result("2026-10-24", "entity"), _result("from 2026-10-24 to 2026-10-31")])
    assert not store.stale_above_answer({**CUTOVER, "stale": []}, [old, new])


def test_the_store_measures_count_ranks_context_and_graph_findings():
    def question(rank, tokens, *, stale_above=False, stale=False, context=True, words=1, graph=0):
        return {"answer_rank": rank, "tokens_used": tokens, "stale_above_answer": stale_above, "answer_in_context": context,
                "stale_in_context": ["x"] if stale else [], "lexical_candidates": words, "graph_candidates": graph}

    questions = [question(1, 100), question(3, 200, stale_above=True, stale=True), question(None, 300, context=False),
                 question(26, 400, words=0, graph=2)]
    for index, row in enumerate(questions):
        row["id"] = f"q{index}"
    nodes = [{"type": "note", "state": "stable", "content": "a"}] * 4 + [
        {"type": "entity", "state": "stable", "content": "Alex Rivera"},
        {"type": "entity", "state": "superseded", "content": "Japan"}]
    edges = [{"source": "1", "target": "2", "type": "mentions"}]
    entities = [{"id": "alex", "names": ["Alex", "Alex Rivera"]}, {"id": "japan", "names": ["Japan"]},
                {"id": "sam", "names": ["Sam"]}]
    measures = store.store_measures(questions, nodes, edges, 4, entities)
    assert measures["nodes_per_store_call"] == 1.5 and measures["edges_per_store_call"] == 0.25
    assert measures["context_tokens_mean"] == 250
    assert measures["key_entities_found"] == 1  # A superseded entity is not found.
    assert (measures["answers_in_top1"], measures["answers_in_top3"], measures["answers_in_context"]) == (1, 2, 3)
    assert measures["mean_reciprocal_rank"] == pytest.approx((1 + 1 / 3 + 1 / 26) / 4)
    assert (measures["stale_above_answer"], measures["stale_in_context"]) == (1, 1)
    assert (measures["questions_with_a_word_candidate"], measures["questions_with_a_graph_candidate"]) == (3, 1)
    assert measures["answer_rank:q2"] is None and measures["answer_rank:q3"] == 26


async def test_the_store_stage_stores_one_node_per_call_and_answers_every_question(tmp_path, mock_embeddings):  # noqa: F811
    result = await store.run(tmp_path / "store")
    measures = result["measures"]
    assert result["backend"] == "duckdb" and result["calls"] == 75
    assert measures["nodes_per_store_call"] == 1.0 and measures["edges_per_store_call"] == 0.0
    assert measures["key_entities_found"] == 0 and measures["questions_with_a_graph_candidate"] == 0
    assert all(f"answer_rank:{question['id']}" in measures for question in store.load_fixture()["key"]["questions"])
    assert measures["answers_in_context"] == 17
    assert [row["id"] for row in result["questions"]][:2] == ["rust", "japan-cities"]
    assert result["fixture_sha256"] == store.fixture_digest()


# --- The ingest stage ----------------------------------------------------------------------------------


def test_replayed_turns_count_the_tokens_recorded_for_them_and_live_turns_their_own():
    rows = [{"key": "a#0", "calls": 1, "prompt_tokens": 100, "completion_tokens": 20, "cache_hits": 0},
            {"key": "a#1", "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cache_hits": 1}]
    recorded = {"a#1": {"calls": 1, "prompt_tokens": 80, "completion_tokens": 10}}
    total, live = ingest.turn_tokens(rows, recorded)
    assert total == 210 and live == {"a#0": {"calls": 1, "prompt_tokens": 100, "completion_tokens": 20}}
    # A replayed turn with no record leaves the total unknown instead of understating it.
    assert ingest.turn_tokens(rows, {})[0] is None


def test_correction_measures_count_old_values_that_survive_and_where_they_show():
    cases = [
        {"facts": {"new_value_active": True, "first_context_line_shows": "old", "context_lines_with_only_the_old_value": 2,
                   "active_records_with_only_the_old_value": 3, "extracted_claims_with_only_the_old_value": 3,
                   "of_them_superseded": 1}},
        {"facts": {"new_value_active": True, "first_context_line_shows": "new", "context_lines_with_only_the_old_value": 0,
                   "active_records_with_only_the_old_value": 1, "extracted_claims_with_only_the_old_value": 1,
                   "of_them_superseded": 1}},
        {"facts": {"active_records_per_value": {"black": 1}}},  # A conflict has no old value.
    ]
    assert ingest.correction_measures(cases) == {
        "old_value_shown_first": 1, "old_only_context_lines": 2, "old_records_still_active": 4,
        "old_claims_not_retired": 2, "new_value_active": 2}


async def test_the_ingest_stage_measures_a_chat_and_a_rerun_replays_the_cache_exactly(
        tmp_path, mock_embeddings, monkeypatch):  # noqa: F811
    monkeypatch.setattr(chat_probe, "model_identity", lambda model, cloud=False: {
        "provider": "ollama", "model": model, "server_version": "0.0.0"})
    path = _conversation(tmp_path)
    scripted = ScriptedExtraction()

    def prepare(engine):
        engine._pipeline._extraction_provider.extract = scripted.extract

    committed = tmp_path / "committed-cache"
    options = {"conversations": [(path, False)], "overrides": {"extraction_batch_target_tokens": 0},
               "model": "local-model", "prepare": prepare, "cache_dir": committed}
    first = await ingest.run(tmp_path / "first", use_cache=False, **options)
    assert [call[0] for call in scripted.calls] == [HOME, chat_probe_echo(), NURSE]
    # The run wrote into its own copy; the committed cache is untouched until it is updated.
    assert not committed.exists()
    assert ingest.update_cache(tmp_path / "first" / "extraction-cache", committed) == 3
    assert ingest.cache_size(committed)[0] == 3

    second = await ingest.run(tmp_path / "second", **options)
    assert len(scripted.calls) == 3 and second["cache_hits"] == 3
    # The scripted model reports no calls or tokens; the replay counts its requests, and it has no recorded tokens.
    cost = {"extraction_requests", "extraction_tokens"}
    assert {key: value for key, value in second["measures"]["tiny"].items() if key not in cost} == {
        key: value for key, value in first["measures"]["tiny"].items() if key not in cost}
    assert second["measures"]["tiny"]["extraction_requests"] == 3 and second["measures"]["tiny"]["extraction_tokens"] is None
    assert ingest.update_cache(tmp_path / "second" / "extraction-cache", committed) == 0
    measures = second["measures"]["tiny"]
    assert measures["claims_per_turn"] == 1 and measures["user_claims_first_person"] == 2
    assert measures["key_links_linked"] == 0 and measures["key_links_via_pronoun"] == 1
    assert measures["key_links_not_linked"] == 1  # Rachel is not in the chat.
    assert measures["answers_in_top3"] == 2 and measures["nodes_per_turn"] > 1
    assert measures["answer_rank:job"] is not None


def chat_probe_echo():
    return "Nice, Sam sounds great."


# --- The rules and the baseline -----------------------------------------------------------------------


def test_every_rule_names_a_group_a_direction_and_a_live_tolerance():
    for name, rule in RULES["measures"].items():
        assert rule["group"] in compare.GROUPS and rule["good"] in {"higher", "lower", None}, name
        assert isinstance(rule["gate"], bool) and set(rule["live"]) <= {"abs", "rel"}, name
        assert not (rule["gate"] and rule["good"] is None), name


def test_the_committed_baseline_covers_every_measure_and_matches_the_committed_fixtures():
    baseline = json.loads(cycle.DEFAULT_BASELINE.read_text())
    assert baseline["kind"] == compare.REPORT_KIND and set(baseline["stages"]) == {"store", "ingest"}
    assert baseline["stages"]["store"]["fixture_sha256"] == store.fixture_digest()
    assert baseline["stages"]["ingest"]["mode"] == "replay"
    for name, chat in baseline["stages"]["ingest"]["conversations"].items():
        path = ingest.CONVERSATION_DIR / f"{name}.json"
        assert chat["conversation_sha256"] == chat_probe.conversation_digest(path), name
    # Each measure has a rule, and the grouped view the comparison reads is what the stages hold.
    assert compare.grouped(RULES, baseline["stages"]) == baseline["measures"]
    names = {compare.split_id(key)[1] for group in baseline["measures"].values() for key in group}
    assert names <= set(RULES["measures"])


# --- The comparator ------------------------------------------------------------------------------------


def _results(*, store_values=None, ingest_values=None, mode="replay", backend="duckdb"):
    """Stage results as the stages return them, with a few measures each."""
    replay = mode == "replay"
    return {
        "store": {"fixture": "store-v1", "fixture_sha256": "f" * 64, "backend": backend, "calls": 75, "seconds": 9.0,
                  "questions": [{"id": "rust", "question": "what coding language is alex learning?"}],
                  "measures": {"nodes_per_store_call": 1.0, "answers_in_top3": 14, "answer_rank:rust": 3,
                               "edges_per_store_call": 0.0, **(store_values or {})}},
        "ingest": {"mode": mode, "model": "m", "live_calls": 0 if replay else 14, "cache_hits": 14 if replay else 0,
                   "seconds": 100.0, "working_cache": "/not/in/the/report",
                   "conversations": {"chat": {"conversation_sha256": "c" * 64, "turns": 14, "seconds": 60.0,
                                              "questions": {}}},
                   "measures": {"chat": {"claims_repeating_text": 10, "user_claims_named_owner": 0,
                                         "extraction_requests": 14, **(ingest_values or {})}}}}


def _report(label="candidate", **options):
    return cycle.build_report(label, _results(**options), {}, RULES)


def _judge(baseline, report, **options):
    result = compare.compare(baseline, report, RULES, **options)
    return result, {row["id"]: row["verdict"] for row in result["rows"]}


def test_identical_reports_pass_and_a_better_measure_is_listed_without_failing():
    result, verdicts = _judge(_report("main"), _report())
    assert result["status"] == "PASS" and verdicts == {} and result["unchanged"] == 7
    result, verdicts = _judge(_report("main"), _report(store_values={"answers_in_top3": 16, "answer_rank:rust": 1},
                                                       ingest_values={"claims_repeating_text": 4}))
    assert result["status"] == "PASS"
    assert verdicts == {"store/answers_in_top3": "better", "store/answer_rank:rust": "better",
                        "chat/claims_repeating_text": "better"}


def test_a_gated_measure_that_gets_worse_fails_and_a_watched_one_does_not():
    report = _report(store_values={"answers_in_top3": 13, "answer_rank:rust": 9, "edges_per_store_call": 2.0})
    result, verdicts = _judge(_report("main"), report)
    assert result["status"] == "FAIL" and result["failed"] == ["store/answers_in_top3"]
    assert verdicts["store/answer_rank:rust"] == "worse (watched)"
    assert verdicts["store/edges_per_store_call"] == "moved"
    text = compare.render(result, _report("main"), report)
    assert "FAIL: 1 measure(s) failed" in text and "FAIL: Answers ranked in the top 3 [store]: 14 to 13 (-1)" in text


def test_a_replay_is_exact_and_a_live_run_allows_the_rules_noise():
    rules = copy.deepcopy(RULES)
    rules["measures"]["claims_repeating_text"]["live"] = {"abs": 2, "rel": 0.1}
    noisy = _report(ingest_values={"claims_repeating_text": 12})
    assert compare.compare(_report("main"), noisy, rules)["status"] == "FAIL"
    live = compare.compare(_report("main"), _report(ingest_values={"claims_repeating_text": 12}, mode="live"), rules)
    assert live["status"] == "PASS" and live["rows"][0]["verdict"] == "worse, within tolerance"
    assert live["rows"][0]["tolerance"] == 2
    too_far = compare.compare(_report("main"), _report(ingest_values={"claims_repeating_text": 14}, mode="live"), rules)
    assert too_far["status"] == "FAIL"
    # The store stage uses no model, so a live ingest stage does not loosen it.
    store_worse = _report(store_values={"nodes_per_store_call": 1.01}, mode="live")
    assert compare.compare(_report("main"), store_worse, rules)["failed"] == ["store/nodes_per_store_call"]


def test_accepting_a_measure_names_it_and_does_not_fail_on_it():
    report = _report(store_values={"nodes_per_store_call": 3.0})
    result, verdicts = _judge(_report("main"), report, accept=frozenset({"nodes_per_store_call"}))
    assert result["status"] == "PASS" and verdicts == {"store/nodes_per_store_call": "worse, accepted"}


def test_an_answer_that_is_no_longer_found_is_worse_than_any_rank():
    result, verdicts = _judge(_report("main"), _report(store_values={"answer_rank:rust": None}))
    assert verdicts == {"store/answer_rank:rust": "worse (watched)"} and result["status"] == "PASS"
    result, verdicts = _judge(_report("main", store_values={"answer_rank:rust": None}), _report())
    assert verdicts == {"store/answer_rank:rust": "better"}


def test_reports_of_different_fixtures_or_backends_cannot_be_compared(capsys, tmp_path):
    other = _report(backend="postgres")
    result = compare.compare(_report("main"), other, RULES)
    assert result["status"] == "not comparable" and "backend differs" in result["reasons"][0]
    changed = _report()
    changed["stages"]["ingest"]["conversations"]["chat"]["conversation_sha256"] = "d" * 64
    assert compare.compare(_report("main"), changed, RULES)["reasons"] == ["the chat chat differs"]
    paths = []
    for name, report in (("base", _report("main")), ("other", other)):
        paths.append(tmp_path / f"{name}.json")
        paths[-1].write_text(json.dumps(report))
    assert cycle.main(["compare", str(paths[0]), str(paths[1])]) == 2
    assert "NOT COMPARABLE" in capsys.readouterr().out


def test_a_report_missing_a_stage_is_compared_on_the_stage_it_has(capsys, tmp_path):
    partial = _report()
    del partial["stages"]["ingest"]
    partial["measures"] = compare.grouped(RULES, partial["stages"])
    result = compare.compare(_report("main"), partial, RULES)
    assert result["status"] == "PASS"
    assert result["skipped"] and all(measure.startswith("chat/") and where == "baseline"
                                     for measure, where in result["skipped"])
    text = compare.render(result, _report("main"), partial)
    assert "Partial: ingest is in only one of the reports" in text
    assert "Not compared: 3 measure(s) of chat only in the baseline." in text
    base, bad = tmp_path / "base.json", tmp_path / "bad.json"
    base.write_text(json.dumps(_report("main")))
    bad.write_text(json.dumps(_report(store_values={"answers_in_top3": 1})))
    assert cycle.main(["compare", str(base), str(bad)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_the_markdown_report_groups_the_measures_and_times_each_stage():
    report = _report()
    assert "working_cache" not in json.dumps(report)
    text = cycle.report_markdown(report, RULES)
    assert text.index("## Storage") < text.index("## Meaning") < text.index("## Results")
    assert "| total | 109.0 |" in text and "| ingest: chat | 60.0 |" in text
    assert "| what coding language is alex learning? | store | 3 |" in text
    assert "| Extraction requests | chat | 14 | lower |" in text


def test_run_writes_both_reports_and_exits_with_the_verdict(tmp_path, monkeypatch, capsys):
    stages = {}

    async def fake_store(work, **options):
        return stages["store"]

    async def fake_ingest(work, **options):
        return stages["ingest"]

    monkeypatch.setattr(store, "run", fake_store)
    monkeypatch.setattr(ingest, "run", fake_ingest)
    monkeypatch.setattr(cycle, "_quiet_logs", lambda: None)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(_report("main")))
    command = ["run", "--label", "candidate", "--out", str(tmp_path / "out"), "--baseline", str(baseline)]

    stages.update(_results())
    assert cycle.main(command) == 0
    saved = json.loads((tmp_path / "out" / "report.json").read_text())
    assert saved["label"] == "candidate" and set(saved["measures"]) == set(compare.GROUPS)
    # The times are measured around each stage, whatever the stage reported.
    assert set(saved["seconds"]) == {"store", "ingest", "total"}
    assert saved["seconds"]["total"] == pytest.approx(saved["seconds"]["store"] + saved["seconds"]["ingest"])
    assert (tmp_path / "out" / "report.md").read_text().startswith("# Fast cycle: candidate")
    assert "PASS: 0 measure(s) failed" in capsys.readouterr().out

    stages.update(_results(store_values={"answers_in_top3": 12}))
    assert cycle.main(command) == 1
    assert "FAIL: Answers ranked in the top 3 [store]: 14 to 12 (-2)" in capsys.readouterr().out
    assert cycle.main([*command, "--accept", "answers_in_top3"]) == 0
    assert cycle.main(command[:-2] + ["--baseline", "none"]) == 0
    with pytest.raises(SystemExit):
        cycle.main([*command[:-2], "--baseline", str(tmp_path / "missing.json")])


# --- The store-contextual stage ------------------------------------------------------------------------

CONTEXTUAL_FIXTURE = store.FIXTURES / "store-contextual-v1.json"


def test_the_second_store_fixture_has_its_own_story_and_every_answer_and_stale_term_is_in_a_memory():
    fixture = store.load_fixture(CONTEXTUAL_FIXTURE)
    memories = fixture["memories"]
    assert len(memories) == 30 and len(fixture["key"]["questions"]) == 10
    assert len({memory["session"] for memory in memories}) == 3
    # A different story from the first fixture, so a lift on one is not the other's wording.
    first = " ".join(memory["content"] for memory in store.load_fixture()["memories"]).casefold()
    assert not any(name.casefold() in first for entity in fixture["key"]["entities"] for name in entity["names"])
    texts = [memory["content"].casefold() for memory in memories]
    for question in fixture["key"]["questions"]:
        terms = [term.casefold() for term in question.get("all_of") or question["any_of"]]
        holds = all if question.get("all_of") else any
        assert any(holds(term in text for term in terms) for text in texts), question["id"]
        for stale in question.get("stale", []):
            assert any(stale.casefold() in text and not holds(term in text for term in terms)
                       for text in texts), (question["id"], stale)


def test_a_prompt_gets_only_the_five_memories_before_it_in_its_session_and_names_nothing_of_a_fixture():
    memories = [{"session": "a", "content": f"a{number}"} for number in range(7)]
    memories.insert(3, {"session": "b", "content": "b0"})
    assert contextual.context_window(memories, 0) == [] and contextual.context_window(memories, 3) == []
    assert contextual.context_window(memories, 4) == ["a0", "a1", "a2"]
    assert contextual.context_window(memories, 7) == ["a1", "a2", "a3", "a4", "a5"]
    text = contextual.build_prompt(["first", "second"], "the memory")
    assert "1. first\n2. second" in text and text.endswith("the memory\n\nSentence:")
    # The overfitting guard: no prompt names a person, place or answer of either fixture.
    words = set()
    for path in (store.DEFAULT_FIXTURE, CONTEXTUAL_FIXTURE):
        fixture = store.load_fixture(path)
        words |= {name.casefold() for entity in fixture["key"]["entities"] for name in entity["names"]}
        words |= {term.casefold() for question in fixture["key"]["questions"]
                  for term in question.get("all_of", []) + question.get("any_of", []) + question.get("stale", [])}
    for name, prompt in contextual.PROMPTS.items():
        assert not [word for word in words if re.search(rf"\b{re.escape(word)}\b", prompt.casefold())], name


def test_a_reply_is_one_short_sentence_or_nothing():
    clean = contextual.clean_sentence
    assert clean("  Maya and Luis's kitchen\nrenovation.  ") == "Maya and Luis's kitchen renovation."
    assert clean('"Sentence: The choir."') == "The choir."
    assert clean("NONE") is None and clean("none.") is None and clean("  ") is None
    assert clean("x" * (contextual.MAX_SENTENCE_CHARS + 1)) is None
    assert contextual.retrieval_content("About the trip.", "They chose three cities.") == "About the trip. They chose three cities."
    assert contextual.retrieval_content(None, "They chose three cities.") is None


def test_the_committed_replies_cover_both_fixtures_and_both_prompts_so_a_rerun_asks_no_model():
    import asyncio

    for path in (store.DEFAULT_FIXTURE, CONTEXTUAL_FIXTURE):
        fixture = store.load_fixture(path)
        for prompt in contextual.PROMPTS:
            for number in (1, 2, 3):
                cache = contextual.Cache(contextual.cache_path(fixture, number))
                assert all(contextual._cached(fixture, number, cache, prompt, contextual.MODEL))
                records = asyncio.run(contextual.generate(fixture, run=number, cache=cache, live=False, prompt=prompt))
                assert len(records) == len(fixture["memories"]) and not any(record["asked"] for record in records)
                # The first memory of each session has nothing to situate it by, so it is stored as written.
                assert all(record["sentence"] is None for record in records if record["skipped"])
    empty = contextual.Cache(contextual.CACHE_DIR / "none.json")
    with pytest.raises(RuntimeError, match="No cached reply"):
        asyncio.run(contextual.generate(store.load_fixture(), run=9, cache=empty, live=False))


def test_replies_added_by_a_run_are_copied_to_the_committed_cache_once(tmp_path):
    committed, working = tmp_path / "committed", tmp_path / "working"
    for folder, entries in ((committed, {"a": {"raw": "one"}}), (working, {"a": {"raw": "other"}, "b": {"raw": "two"}})):
        cache = contextual.Cache(folder / "store-v1-run1.json")
        cache.entries = entries
        cache.save()
    assert contextual.update_cache(working, committed) == 1
    assert contextual.update_cache(working, committed) == 0
    assert contextual.Cache(committed / "store-v1-run1.json").entries == {"a": {"raw": "one"}, "b": {"raw": "two"}}


def test_answers_are_read_from_the_text_as_sent_not_from_the_sentence_added_to_it():
    node = NodeType.FACT
    sentence = "Tokyo, Kyoto, Kanazawa and Osaka were chosen. They dropped Hiroshima."
    other = SimpleNamespace(node=SimpleNamespace(content=f"{sentence} Alex booked flights.", node_type=node))
    answer = SimpleNamespace(node=SimpleNamespace(content="Tokyo, Kyoto, Kanazawa, and Osaka.", node_type=node))
    response = SimpleNamespace(
        results=[other, answer],
        bundle=SimpleNamespace(sections={"facts": [other]}, tokens_used=30, token_budget=100,
                               render=lambda: f"- [fact] {sentence} Alex booked flights."),
        metadata=SimpleNamespace(candidates_generated={"LEXICAL": 2}))
    question = {"id": "cities", "question": "which cities?", "all_of": ["Tokyo", "Kyoto", "Kanazawa", "Osaka"]}
    assert store.question_report(question, response)["answer_rank"] == 1
    sent = store.question_report(question, response, {f"{sentence} Alex booked flights.": "Alex booked flights."})
    assert sent["answer_rank"] == 2 and not sent["answer_in_context"]
    assert sent["tokens_used"] == 30 and sent["top_results"][0] == "fact: Alex booked flights."


async def test_the_contextual_stage_stores_with_the_sentences_and_asks_no_model_from_the_cache(
        tmp_path, mock_embeddings):  # noqa: F811
    from prme import MemoryEngine

    seen = []
    original = MemoryEngine.store_with_receipt

    async def spy(self, content, **options):
        seen.append((content, options.get("retrieval_content")))
        return await original(self, content, **options)

    MemoryEngine.store_with_receipt = spy
    try:
        result = await contextual.run(tmp_path / "ctx", cache_dir=contextual.CACHE_DIR)
    finally:
        MemoryEngine.store_with_receipt = original
    info = result["contextual"]
    assert info["model_calls"] == 0 and info["replayed"] == 70 and info["no_context"] == 5
    assert result["calls"] == 75 and len(seen) == 75
    fixture = store.load_fixture()
    for (content, retrieval), memory in zip(seen, fixture["memories"]):
        # The source text is sent as written; the sentence rides in front of it as retrieval content.
        assert content == memory["content"] and (retrieval is None or retrieval.endswith(f" {content}"))
    assert sum(retrieval is not None for _, retrieval in seen) == info["sentences"]
    assert result["measures"]["nodes_per_store_call"] == 1.0


def test_the_contextual_stage_is_opt_in_and_its_measures_are_reported_beside_the_stores():
    assert "store-contextual" not in cycle.STAGES and cycle.OPT_IN_STAGES == ("store-contextual",)
    with pytest.raises(SystemExit):
        cycle.main(["run", "--label", "x", "--out", "/nonexistent", "--stages", "nothing"])
    results = _results()
    results["store-contextual"] = {**results["store"], "seconds": 5.0, "working_cache": "/not/in/the/report",
                                   "measures": {**results["store"]["measures"], "answers_in_top3": 15,
                                                "answer_rank:rust": 2},
                                   "contextual": {"model": "m", "prompt": "situate", "run": 1, "sentences": 61,
                                                  "model_calls": 0, "replayed": 70, "memories": 75}}
    report = cycle.build_report("candidate", results, {}, RULES)
    assert report["measures"]["results"]["store-contextual/answers_in_top3"] == 15
    text = cycle.report_markdown(report, RULES)
    assert "| what coding language is alex learning? | store-contextual | 2 |" in text
    assert "before 61 of 75 memories: 0 model calls and 70 replayed" in text
    # Against a baseline without the stage, its measures are listed as not compared and nothing fails.
    result = compare.compare(_report("main"), report, RULES)
    assert result["status"] == "PASS" and {where for _, where in result["skipped"]} == {"report"}
    assert all(measure.startswith("store-contextual/") for measure, _ in result["skipped"])
