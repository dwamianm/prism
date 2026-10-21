"""The graph oracle's gold graphs, strategies, store round trip and reports, with no model and no network."""
import copy
from contextlib import asynccontextmanager
import json
import os

import pytest

from benchmarks.diagnostics.chat_probe import load_conversation
from benchmarks.diagnostics.extracted_packs import _drain_sources
from benchmarks.diagnostics.product_packing import gate_config
from benchmarks.fast_cycle import store as fast_store
from benchmarks.graph_oracle import gold as gold_module
from benchmarks.graph_oracle import run as oracle_run
from benchmarks.graph_oracle.fidelity import fidelity
from benchmarks.graph_oracle.gold import GOLD_NAMES, RELATIONS, load_gold, mentions, relation_cues, target_cues
from benchmarks.graph_oracle.graph import CLAIM, DIRECT, MemorySource, load_graph, write_graph
from benchmarks.graph_oracle.strategies import Params, fuse, generic_scores, graph_first, graph_order, run_oracle, walk_plan
from prme import MemoryEngine
from tests.test_chat_probe import mock_embeddings  # noqa: F401


def _source_questions(gold):
    """The questions of the corpus a gold file annotates: id to (text, terms)."""
    raw = json.loads(gold.path.read_text())["corpus"]
    path = gold_module.ROOT / raw["path"]
    if raw["kind"] == "conversation":
        probes = load_conversation(path)["key"]["probes"]
    else:
        probes = fast_store.load_fixture(path)["key"]["questions"]
    return {probe["id"]: (probe["question"], {key: probe[key] for key in ("all_of", "any_of") if probe.get(key)})
            for probe in probes}


# --- The gold files ------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", GOLD_NAMES)
def test_a_gold_file_annotates_every_question_of_its_corpus_and_only_adds_marked_ones(name):
    gold = load_gold(name)
    source = _source_questions(gold)
    kept = {question.id: question for question in gold.questions if question.origin == "source"}
    assert set(kept) == set(source)
    for question_id, (text, terms) in source.items():
        assert kept[question_id].text == text and kept[question_id].terms == terms
    added = [question for question in gold.questions if question.origin == "added"]
    assert added and all(question.kind in ("two_hop", "three_hop") for question in added)


def test_the_corpora_are_the_three_the_issue_names():
    counts = {name: (len(load_gold(name).memories), len(load_gold(name).questions)) for name in GOLD_NAMES}
    assert counts == {"defects-v1": (14, 8), "store-v1": (75, 20), "chat-v1": (54, 21)}
    assert len(load_gold("defects-v1").questions) - 1 == 7


@pytest.mark.parametrize("name", GOLD_NAMES)
def test_every_answer_is_a_memory_and_most_hold_the_answer_terms(name):
    gold = load_gold(name)
    for question in gold.questions:
        holding = {memory.id for memory in gold.memories if gold_module.term_hits(question.terms, memory.text)}
        if question.id == "niece-born":
            # The date comes from the session's date, which no turn's text holds.
            assert not holding
        else:
            assert holding & set(question.answers), question.id


@pytest.mark.parametrize("name", GOLD_NAMES)
def test_the_gold_graph_stays_inside_the_closed_vocabulary_and_marks_what_it_infers(name):
    gold = load_gold(name)
    assert {claim.r for claim in gold.claims} <= set(RELATIONS) and len(RELATIONS) == 17
    inferred = [claim for claim in gold.claims if claim.inferred]
    assert inferred and all(claim.r == "located_in" and not claim.ev for claim in inferred)
    assert all(claim.ev for claim in gold.claims if not claim.inferred)


def _break(gold, claim_index=0, **changes):
    broken = copy.copy(gold)
    broken.claims = list(gold.claims)
    broken.claims[claim_index] = gold_module.Claim(**{**gold.claims[claim_index].__dict__, **changes})
    return broken


@pytest.mark.parametrize("changes, message", [
    ({"r": "likes"}, "not in the vocabulary"),
    ({"o": "nobody"}, "unknown entity"),
    ({"ev": ()}, "needs evidence"),
    ({"ev": ("s9-nowhere#0",)}, "unknown memory"),
    ({"inferred": True, "ev": ()}, "only located_in may be inferred"),
    ({"polarity": "maybe"}, "bad status or polarity"),
    ({"supersedes": "missing"}, "supersedes an unknown claim"),
])
def test_a_gold_file_that_breaks_a_rule_is_rejected(changes, message):
    with pytest.raises(ValueError, match=message):
        gold_module.validate(_break(load_gold("defects-v1"), **changes))


def test_a_literal_claim_needs_an_attribute_and_only_has_takes_one():
    gold = load_gold("store-v1")
    literal = next(index for index, claim in enumerate(gold.claims) if claim.r == "has")
    with pytest.raises(ValueError, match="only has takes a literal"):
        gold_module.validate(_break(gold, literal, attr=""))
    with pytest.raises(ValueError, match="only has takes a literal"):
        gold_module.validate(_break(gold, 0, o=None))


# --- Reading text -----------------------------------------------------------------------------------


def test_a_longer_name_wins_over_the_shorter_name_inside_it_and_exact_case_names_stay_exact():
    chat = load_gold("chat-v1")
    assert mentions(chat, "Sam is a nurse at Denver Health.") == ["sam", "denver_health"]
    assert mentions(chat, "I live in Denver.") == ["denver"]
    store = load_gold("store-v1")
    assert "go" in mentions(store, "Alex likes code examples in Go and TypeScript") and "go" not in mentions(store, "let's go home")
    assert mentions(store, "WHICH CITIES WILL ALEX VISIT IN JAPAN") == ["alex", "japan"]


def test_a_question_word_that_names_a_subtype_asks_for_it_and_cue_words_point_at_relations():
    store = load_gold("store-v1")
    assert target_cues(store, "which cities will Alex visit?")["subtype"] == {"city"}
    assert target_cues(store, "What is Alex's dog called?")["subtype"] == {"dog"}
    assert target_cues(store, "Who is on call?")["type"] == {"person"}
    assert {"allergic_to"} <= relation_cues("What is Jordan allergic to?")
    assert {"manager_of"} <= relation_cues("Who is Alex's manager?")
    assert relation_cues("How many rows does the table have?") == set()


# --- Combining lists -------------------------------------------------------------------------------


def test_the_graph_list_keeps_text_order_for_equal_scores_and_leaves_out_memories_it_does_not_reach():
    base = ["a", "b", "c", "d"]
    assert graph_order({"c": 2.0, "d": 2.0, "a": 1.0, "b": 0.0}, base) == ["c", "d", "a"]
    assert graph_first({"c": 2.0}, base) == ["c", "a", "b", "d"]


def test_fusion_with_no_weight_is_the_text_order_and_a_high_weight_lifts_a_graph_memory():
    base = ["a", "b", "c", "d"]
    assert fuse({"d": 1.0}, base, 0.0) == base
    assert fuse({"d": 1.0}, base, 0.01)[0] == "a"
    assert fuse({"d": 1.0}, base, 100.0)[0] == "d"
    assert fuse({}, base, 5.0) == base


def test_the_settings_flag_parses_json_values_and_refuses_an_unknown_name():
    params = oracle_run.parse_params(["hops=3", "cue_filter=false", 'relations=["visits"]', 'weights={"part_of": 0.5}'])
    assert params.hops == 3 and params.cue_filter is False
    assert params.relations == frozenset({"visits"}) and params.weights == (("part_of", 0.5),)
    with pytest.raises(ValueError, match="not a setting"):
        oracle_run.parse_params(["speed=2"])


# --- The graph in a store ----------------------------------------------------------------------------


@asynccontextmanager
async def _stored(tmp_path, name, database_url=None):
    """A pack with the corpus stored (mock embeddings), its gold graph written and read back."""
    gold = load_gold(name)
    pack = tmp_path / "pack"
    (pack / "lexical_index").mkdir(parents=True)
    config = gate_config(pack)
    if database_url:
        from uuid import uuid4

        from pydantic import SecretStr

        config = config.model_copy(update={"database_url": SecretStr(database_url)})
        gold.owner = f"{gold.owner}-{uuid4().hex[:8]}"  # a shared database keeps earlier runs, so each run is a new owner
    async with MemoryEngine.open(config) as engine:
        node_ids = {}
        for memory in gold.memories:
            receipt = await engine.store_with_receipt(memory.text, user_id=gold.owner, role=memory.role,
                                                      node_type=memory.node_type, scope=memory.scope, extract=False)
            node_ids[memory.id] = str(receipt.node.id)
        await _drain_sources(engine, gold.owner)
        handles = await write_graph(engine, gold, gold.owner, node_ids)
        yield engine, gold, handles, await load_graph(engine, gold.owner, handles)


async def test_both_shapes_read_back_as_the_claims_that_were_written(mock_embeddings, tmp_path):  # noqa: F811
    async with _stored(tmp_path, "defects-v1") as (engine, gold, handles, data):
        written = {claim.id: claim for claim in gold.claims}
        for shape in (DIRECT, CLAIM):
            read = {edge.id: edge for edge in data.edges if edge.shape in (shape, "both")}
            assert set(read) == set(written)
            for claim_id, edge in read.items():
                claim = written[claim_id]
                assert (edge.s, edge.r, edge.o, edge.polarity, edge.status, set(edge.evidence)) == (
                    claim.s, claim.r, claim.o, claim.polarity, claim.status, set(claim.ev)), (shape, claim_id)
        # A memory has a mentions edge to every entity it names, and nothing else names an entity.
        assert all(data.mentions[memory.id] == set(mentions(gold, memory.text)) for memory in gold.memories
                   if mentions(gold, memory.text))


async def test_generic_finds_the_cities_memory_first_from_the_three_spellings_in_both_shapes(mock_embeddings, tmp_path):  # noqa: F811
    async with _stored(tmp_path, "defects-v1") as (engine, gold, handles, data):
        for text in (gold.questions[0].text, *gold.questions[0].variants):
            for params in (Params(), Params(shape=CLAIM, hops=4)):
                result = await generic_scores(text, gold, MemorySource(data, params.shape), params)
                assert graph_order(result.scores, [])[0] == "s1-trip#2", (text, params.shape)
        # Without a name in the question there is no seed, so the graph adds nothing.
        empty = await generic_scores("what happened yesterday?", gold, MemorySource(data), Params())
        assert empty.seeds == [] and empty.scores == {}


async def test_a_negative_or_superseded_claim_does_not_carry_the_walk(mock_embeddings, tmp_path):  # noqa: F811
    async with _stored(tmp_path, "defects-v1") as (engine, gold, handles, data):
        text = "which cities will alex visit?"
        result = await generic_scores(text, gold, MemorySource(data), Params())
        # A dropped city is still reached through the country's containment, but less than a chosen one.
        assert result.entities["tokyo"] > result.entities["hiroshima"] > 0
        allowed = await generic_scores(text, gold, MemorySource(data), Params(negative=1.0))
        assert allowed.entities["hiroshima"] == allowed.entities["tokyo"]


async def test_the_oracle_follows_a_spec_and_a_changed_spec_changes_the_memories(mock_embeddings, tmp_path):  # noqa: F811
    async with _stored(tmp_path, "defects-v1") as (engine, gold, handles, data):
        base = [memory.id for memory in gold.memories]
        cities = gold.questions[0].spec
        result = run_oracle(gold, data, cities, base)
        assert result.answered == ["s1-trip#2"] and result.targets == ["kanazawa", "kyoto", "osaka", "tokyo"]
        dropped = run_oracle(gold, data, {**cities, "target": {"subtype": "city"},
                                           "path": [{**cities["path"][0], "polarity": "negative"}]}, base)
        assert dropped.targets == ["hiroshima"]


# --- The whole run -------------------------------------------------------------------------------------


async def test_the_oracle_ranks_every_gold_question_first_and_a_run_writes_its_report(mock_embeddings, tmp_path):  # noqa: F811
    report = await oracle_run.run(tmp_path / "out", heldout=False, ablations=True, work=tmp_path / "work")
    gold_items = [item for item in report["items"] if item["group"] == "gold"]
    assert len(gold_items) == 53 and all(item["ranks"]["oracle"] == 1 for item in gold_items)
    assert report["summary"]["oracle"]["top1"] == 53 and report["summary"]["baseline"]["n"] == 53
    assert report["store_and_memory_traversals_differ"] == 0
    # Two hops cost three reads of the store: one for each hop and one for the last frontier.
    assert report["milliseconds"]["store_reads_per_question"]["max"] == 3
    assert {row["group"] for row in report["ablations"]} >= {"hop limit", "hub damping of the owner", "drop one relation",
                                                              "claim nodes or direct edges", "question cues"}
    written = json.loads((tmp_path / "out" / "report.json").read_text())
    assert written["kind"] == oracle_run.KIND and "## Strategies over all gold questions" in (tmp_path / "out" / "report.md").read_text()
    cities = [item for item in gold_items if item["id"].split("#")[0] in oracle_run.CITIES]
    assert len(cities) == 6 and all(item["ranks"]["generic"] == 1 for item in cities)
    comparison = oracle_run.compare_markdown(report, report)
    assert "Questions whose rank changed" in comparison and "| baseline | top3 |" in comparison


# --- Extraction fidelity -----------------------------------------------------------------------------


def _as_candidate(gold, drop=(), rename=None):
    rename = rename or {}
    entities = [{"id": f"x-{entity.id}", "names": list(entity.names)} for entity in gold.entities.values()]
    relations = [{"s": f"x-{claim.s}", "r": claim.r, **({"o": f"x-{claim.o}"} if claim.o else {"attr": claim.attr}),
                  "polarity": claim.polarity, "status": claim.status}
                 for claim in gold.claims if claim.id not in drop and not claim.inferred]
    return {"entities": entities, "relations": relations}


def test_a_candidate_equal_to_the_gold_graph_scores_one_and_missing_or_wrong_claims_lower_it():
    gold = load_gold("defects-v1")
    perfect = fidelity(gold, _as_candidate(gold))
    assert (perfect["entity_recall"], perfect["entity_precision"], perfect["relation_recall"],
            perfect["relation_precision"], perfect["polarity_accuracy"], perfect["status_accuracy"]) == (1.0,) * 6
    assert perfect["inferred_containment_found"] == 0.0
    missing = _as_candidate(gold, drop={gold.claims[0].id})
    assert fidelity(gold, missing)["relation_recall"] < 1.0 and fidelity(gold, missing)["relation_precision"] == 1.0
    wrong = _as_candidate(gold)
    wrong["relations"].append({"s": "x-alex", "r": "owns", "o": "x-japan"})
    wrong["entities"].append({"id": "x-ghost", "names": ["Nobody Atall"]})
    scored = fidelity(gold, wrong)
    assert scored["relation_precision"] < 1.0 and scored["entity_precision"] < 1.0 and scored["entity_recall"] == 1.0
    flipped = _as_candidate(gold)
    flipped["relations"][-1]["polarity"] = "negative"
    assert fidelity(gold, flipped)["polarity_accuracy"] < 1.0
    contained = _as_candidate(gold)
    contained["relations"].append({"s": "x-tokyo", "r": "located_in", "o": "x-japan"})
    assert fidelity(gold, contained)["inferred_containment_found"] > 0


# --- Blind validation --------------------------------------------------------------------------------


def test_the_blind_file_is_rebuilt_exactly_from_the_committed_candidates_and_decisions():
    from benchmarks.graph_oracle import blind

    frozen = blind.load_blind()
    rebuilt = blind.freeze(json.loads(blind.CANDIDATES_FILE.read_text()), json.loads(blind.REVIEW_FILE.read_text()))
    assert rebuilt == frozen
    review = frozen["review"]
    assert review["kept"] == len(frozen["questions"]) == 86 and review["candidates"] == 147
    assert sum(review["dropped"].values()) == review["candidates"] - review["kept"]
    assert {question["type"] for question in frozen["questions"]} == set(blind.TYPES)
    # Every answer is a memory of its own corpus, and no question repeats an existing one word for word.
    assert len({question["id"] for question in frozen["questions"]}) == 86
    asked = {" ".join(sorted(blind._words(item["question"]))) for name in blind.CORPORA
             for item in json.loads((gold_module.FIXTURES / f"{name}.json").read_text())["questions"]}
    assert not asked & {" ".join(sorted(blind._words(question["question"]))) for question in frozen["questions"]}


def test_the_paraphrases_belong_to_the_frozen_file_and_are_three_for_every_question():
    from benchmarks.graph_oracle import blind

    paraphrases = blind.load_paraphrases()
    assert set(paraphrases) == {question["id"] for question in blind.load_blind()["questions"]}
    assert all(len(texts) == 3 and all(texts) for texts in paraphrases.values())
    with pytest.raises(ValueError, match="not the paraphrases"):
        blind.load_paraphrases(blind_path=gold_module.FIXTURES / "defects-v1.json")


def test_the_generator_only_uses_a_loopback_server_and_sees_no_gold_graph():
    from benchmarks.graph_oracle import blind

    with pytest.raises(ValueError, match="loopback"):
        blind.check_endpoint("http://example.com/v1")
    blind.check_endpoint(blind.ENDPOINT)
    prompt = blind.SYSTEM.format(count=3, type="lookup", instruction=blind.INSTRUCTIONS["lookup"])
    assert "relation" not in prompt.lower() and "gold" not in prompt.lower() and "graph" not in prompt.lower()


def test_cue_words_derived_from_the_definitions_cover_every_relation_and_leave_out_example_names():
    cues = gold_module.derive_cues()
    assert set(cues) == set(RELATIONS)
    assert {"employ", "work"} <= cues["works_at"] and {"allergy", "allergic"} <= cues["allergic_to"]
    assert "live" in cues["lives_in"] and "run" in cues["friend_of"] and "mentor" in cues["manager_of"]
    assert not any({"tokyo", "japan", "brooklyn", "role", "status", "polarity"} & words for words in cues.values())
    assert relation_cues("Where do I live now?", "derived") == {"lives_in"}
    assert relation_cues("What is the plan?", "derived") == set()
    small = gold_module.derive_cues("## The relation vocabulary\n| `owns` | a, b | owns, keeps |\n")
    assert set(small) == {"owns"} and {"own", "keep"} <= small["owns"]


async def test_the_owner_is_seeded_by_first_person_words_only_when_asked(mock_embeddings, tmp_path):  # noqa: F811
    async with _stored(tmp_path, "chat-v1") as (engine, gold, handles, data):
        question = "Where do I work now?"
        plain = await generic_scores(question, gold, MemorySource(data), Params(cue_source="derived"))
        seeded = await generic_scores(question, gold, MemorySource(data), Params(cue_source="derived", first_person_seed=True))
        assert plain.seeds == [] and plain.scores == {}
        assert seeded.seeds == ["dana"] and set(graph_order(seeded.scores, [])[:2]) <= {
            "s4-offer#0", "s4-offer#1", "s5-race#4", "s5-race#5"}


def _table(strategy, baseline, counts):
    """A by type table with the given top 3 counts: counts maps type to (strategy, baseline, n)."""
    entry = lambda top3, n: {"n": n, "top3": top3, "top5": top3, "top1": 0, "mrr": 0.0, "missing": 0, "recall5": 0.0}  # noqa: E731
    types = {kind: {strategy: entry(s, n), "baseline": entry(b, n)} for kind, (s, b, n) in counts.items()}
    total = [sum(values[index] for values in counts.values()) for index in range(3)]
    blind = {**types, "all": {strategy: entry(total[0], total[2]), "baseline": entry(total[1], total[2])}}
    return {"blind": blind, "paraphrase": {"all": {strategy: entry(0, 1), "baseline": entry(0, 1)}}}


def test_the_decision_rule_checks_the_four_conditions_of_blind_md():
    from benchmarks.graph_oracle import blind_run

    counts = {"lookup": (10, 10, 12), "list": (8, 5, 10), "negation": (4, 5, 6)}
    table = _table("g", "baseline", counts)
    table["paraphrase"]["all"] = {"g": {"n": 100, "top3": 70, "top1": 0, "top5": 0, "mrr": 0.0, "missing": 0, "recall5": 0.0},
                                  "baseline": {"n": 100, "top3": 60, "top1": 0, "top5": 0, "mrr": 0.0, "missing": 0, "recall5": 0.0}}
    result = blind_run.verdict(table, "g")
    # 22 of 28 against 20 of 28 is 7.1 points, the worst type loses 1, lookups tie, paraphrases are 70 percent against 78.6.
    assert [item["pass"] for item in result["conditions"].values()] == [False, True, True, False] and not result["passes"]
    counts["list"] = (9, 5, 10)
    passing = blind_run.verdict(_with_paraphrase(_table("g", "baseline", counts), 78), "g")
    assert passing["passes"], passing
    counts["negation"] = (3, 5, 6)
    assert not blind_run.verdict(_with_paraphrase(_table("g", "baseline", counts), 78), "g")["conditions"][
        "2 loses on no type by more than 1 question"]["pass"]
    counts.update(negation=(4, 5, 6), lookup=(9, 10, 12))
    assert not blind_run.verdict(_with_paraphrase(_table("g", "baseline", counts), 78), "g")["conditions"][
        "3 ties or beats the baseline on lookups"]["pass"]


def _with_paraphrase(table, top3):
    table["paraphrase"]["all"] = {"g": {"n": 100, "top3": top3, "top1": 0, "top5": 0, "mrr": 0.0, "missing": 0, "recall5": 0.0},
                                  "baseline": {"n": 100, "top3": 60, "top1": 0, "top5": 0, "mrr": 0.0, "missing": 0, "recall5": 0.0}}
    return table


async def test_a_blind_run_writes_its_report_and_a_latency_run_measures_a_small_synthetic_pack(mock_embeddings, tmp_path):  # noqa: F811
    from benchmarks.graph_oracle import blind_run, latency

    report = await blind_run.run(tmp_path / "blind", work=tmp_path / "work")
    assert report["blind_questions"] == 86 and report["blind_sha256"] == blind_run.digest(
        gold_module.FIXTURES / "blind-v1.json")
    assert set(report["table"]) == {"original", "blind", "paraphrase"} and set(report["verdict"]) >= {blind_run.PRIMARY}
    assert report["table"]["original"]["all"]["baseline"]["n"] == 53 and report["table"]["blind"]["all"]["baseline"]["n"] == 86
    assert report["paraphrases_scored"] + report["paraphrases_unchanged_and_left_out"] == 258
    assert "## Verdict by the rule in BLIND.md" in (tmp_path / "blind" / "report.md").read_text()
    result = await latency.run(tmp_path / "latency", backend="duckdb", turns=[200])
    row = result["results"][0]
    assert row["turns"] == 200 and row["owner_mentions"] > 30 and row["naive_reads"]["store_reads"]["max"] == 3
    # The owner-aware reads fetch the owner by claim edge types, so they read fewer edges when the owner is a seed.
    assert row["owner_aware_reads"]["edges_read"]["mean"] < row["naive_reads"]["edges_read"]["mean"]
    # Claims alone are a fraction of what the frontier's edges hold, which is mostly mentions.
    assert row["claims_only_reads"]["edges_read"]["mean"] < row["owner_aware_reads"]["edges_read"]["mean"]


# --- The question shape router ----------------------------------------------------------------------


@pytest.mark.parametrize("question, expected", [
    ("Which cities will Maya visit in Peru?", "list"), ("What programming languages does Alex use?", "list"),
    ("Who are the members of the team?", "list"), ("What are the steps in the checklist?", "list"),
    ("Which of the cafes is open late?", "list"), ("How many books has Ana read?", "count"),
    ("What is the number of rooms?", "count"), ("Which kind of tea does Sam like?", "subtype"),
    ("Who is the manager of Jordan's partner?", "chain"), ("Dana's sister's mother lives where?", "chain"),
    ("What is Ben's dog called?", "other"), ("What does Sam do for a living?", "other"), ("When is the launch?", "other"),
    ("Does Alex like tea?", "other"), ("What time did Dana finish the race?", "other"),
    ("Where does Dana's sister live?", "other"), ("What is the name of the dog?", "other"),
])
def test_the_router_reads_the_shape_of_a_question_from_its_words(question, expected):
    from benchmarks.graph_oracle import router

    assert router.shape(question) == expected
    assert router.route(question) == ("graph" if expected != "other" else "text")


def test_the_router_accuracy_counts_agreement_with_the_route_a_type_tag_implies():
    from benchmarks.graph_oracle import router

    asked = [{"question": "Which cities will Maya visit?", "type": "list"},
             {"question": "How many books has Ana read?", "type": "aggregation"},
             {"question": "What is Ben's dog called?", "type": "lookup"},
             {"question": "Which cities does Ben like?", "type": "lookup"},
             {"question": "When is the launch?", "type": "temporal"}]
    result = router.accuracy(asked)
    assert result["accuracy"] == 0.8 and result["lookups"] == 2 and result["lookups_routed_to_graph"] == 1
    assert result["lookup_routed_share"] == 0.5 and result["by_type"]["temporal"]["routed_to_graph"] == 0
    assert router.expected_route("multi-hop") == "graph" and router.expected_route("negation") == "text"


# --- The routing gate -------------------------------------------------------------------------------


def test_the_new_memory_set_has_a_gold_graph_that_covers_every_memory_and_its_blind_file_rebuilds():
    from benchmarks.graph_oracle import blind

    new = load_gold("draw2-v1")
    assert len(new.memories) == 46 and new.questions == [] and {memory.id for memory in new.memories} <= {
        memory_id for claim in new.claims for memory_id in claim.ev}
    frozen = blind.load_blind(blind.BLIND2_FILE)
    rebuilt = blind.freeze(json.loads(blind.CANDIDATES2_FILE.read_text()), json.loads(blind.REVIEW2_FILE.read_text()),
                           corpora=blind.CORPORA_DRAW2, name="blind-v2", description=blind.DESCRIPTION_V2)
    assert rebuilt == frozen and len(frozen["questions"]) == 65 and {q["corpus"] for q in frozen["questions"]} == {"draw2-v1"}
    assert sum(frozen["review"]["dropped"].values()) == frozen["review"]["candidates"] - frozen["review"]["kept"]


@pytest.mark.parametrize("params", [Params(), Params(cue_source="derived"), Params(cue_filter=False)],
                         ids=["hand", "derived", "no-filter"])
async def test_the_sql_walk_scores_every_question_as_the_python_walk_does(mock_embeddings, tmp_path, params):  # noqa: F811
    import math

    from benchmarks.graph_oracle.graph import StoreSource
    from benchmarks.graph_oracle.sqlwalk import SqlWalk

    async with _stored(tmp_path, "defects-v1") as (engine, gold, handles, data):
        walk = SqlWalk(engine, "duckdb", gold.owner, handles, gold, params)
        asked = [question.text for question in gold.questions] + [v for question in gold.questions for v in question.variants]
        for text in asked:
            python = await generic_scores(text, gold, StoreSource(engine, gold.owner, handles), params)
            sql = await walk.scores(text)
            positive = lambda result: {key: value for key, value in result.scores.items() if value > 0}  # noqa: E731
            assert sql.seeds == python.seeds and sql.calls <= 1
            assert set(positive(sql)) == set(positive(python)) and all(
                math.isclose(value, positive(python)[key], rel_tol=1e-9) for key, value in positive(sql).items()), text


async def test_the_sql_walk_caps_the_edges_it_reads_per_entity_and_leaves_the_owner_unexpanded(mock_embeddings, tmp_path):  # noqa: F811
    from benchmarks.graph_oracle.sqlwalk import SqlWalk

    async with _stored(tmp_path, "store-v1") as (engine, gold, handles, data):
        capped = SqlWalk(engine, "duckdb", gold.owner, handles, gold, Params(), claim_cap=2, seed_cap=2, mention_cap=2)
        full = SqlWalk(engine, "duckdb", gold.owner, handles, gold, Params())
        text = "which cities will Alex and jordan visit in Japan?"
        small, large = await capped.scores(text), await full.scores(text)
        assert 0 < small.edges_read < large.edges_read and small.calls == 1
        seeds, _, cued = walk_plan(text, gold, Params())
        statement = full.sql(seeds, cued)
        assert statement.count("row_number()") == 4 and "LIMIT 64" in statement and "edge_type = 'mentions'" in statement


def test_the_gate_conditions_are_computed_from_the_new_draws_rows():
    from benchmarks.graph_oracle import routed_run

    def row(kind, baseline, routed, route="text"):
        return {"type": kind, "ranks": {"baseline": baseline, "routed[derived]": routed}, "route": route}

    rows = ([row("lookup", 2, 2)] * 5 + [row("temporal", 1, 1)] * 5 + [row("correction", 4, 4)] * 2
            + [row("list", 6, 1, "graph")] * 7 + [row("multi-hop", 5, 2, "graph")] * 2 + [row("negation", 1, 1)] * 3)
    good = {"accuracy": 0.93, "lookup_routed_share": 0.0}
    result = routed_run.conditions(rows, good, 29.4)
    assert [item["pass"] for item in result.values()] == [True] * 6
    result = routed_run.conditions(rows, {"accuracy": 0.81, "lookup_routed_share": 0.07}, 80.0)
    assert [item["pass"] for item in result.values()] == [True, True, True, True, False, False]
    rows[0] = row("lookup", 2, 1, "graph")
    assert not routed_run.conditions(rows, good, 29.4)["3 lookups, temporal and correction equal the baseline question by question"]["pass"]
    assert not routed_run.conditions(rows[:5] + rows[5:12], good, None)["6 the SQL walk at 10,000 memories at most 50 ms p95 on PostgreSQL"]["pass"]


async def test_a_routed_run_sends_only_the_routed_questions_to_the_graph(mock_embeddings, tmp_path):  # noqa: F811
    from benchmarks.graph_oracle import routed_run

    report = await routed_run.run(tmp_path / "routed", work=tmp_path / "work")
    assert report["questions"] == 65 and set(report["routing"]) == {"spent_blind_set", "new_draw"}
    assert report["routing"]["spent_blind_set"]["n"] == 86
    for row in report["rows"]:
        if row["route"] == "text":
            assert row["ranks"]["routed[derived]"] == row["ranks"]["routed[hand]"] == row["ranks"]["baseline"]
        else:
            assert row["ranks"]["routed[derived]"] == row["ranks"]["graph[derived]"]
    assert "## Verdict against GATE.md" in (tmp_path / "routed" / "report.md").read_text()
    assert report["sql_walk_p95_ms_postgres_10000"] is None and not report["verdict"]["routed[derived]"]["passes"]


@pytest.mark.skipif(not os.environ.get("PRME_TEST_DATABASE_URL"), reason="needs PRME_TEST_DATABASE_URL")
async def test_the_sql_walk_on_postgresql_scores_every_question_as_the_python_walk_does(mock_embeddings, tmp_path):  # noqa: F811
    import math

    from benchmarks.graph_oracle.graph import StoreSource
    from benchmarks.graph_oracle.sqlwalk import SqlWalk

    for name in ("defects-v1", "chat-v1"):
        async with _stored(tmp_path / name, name, os.environ["PRME_TEST_DATABASE_URL"]) as (engine, gold, handles, data):
            for params in (Params(), Params(cue_source="derived")):
                walk = SqlWalk(engine, "postgres", gold.owner, handles, gold, params)
                for question in gold.questions:
                    python = await generic_scores(question.text, gold, StoreSource(engine, gold.owner, handles), params)
                    sql = await walk.scores(question.text)
                    keep = lambda result: {key: value for key, value in result.scores.items() if value > 0}  # noqa: E731
                    assert set(keep(sql)) == set(keep(python)) and all(
                        math.isclose(value, keep(python)[key], rel_tol=1e-9) for key, value in keep(sql).items()), question.id


# --- The scale test -----------------------------------------------------------------------------------


def test_the_combined_core_keeps_every_fixture_with_prefixed_ids_and_shared_names():
    from benchmarks.graph_oracle import scale

    golds, core = scale.load_core()
    assert len(core.memories) == 75 + 14 + 54 + 46 and sorted(core.owners) == [
        "chat-v1:dana", "defects-v1:alex", "draw2-v1:noor", "store-v1:alex"]
    assert all(memory.id.split(":")[0] in golds for memory in core.memories)
    assert len(core.claims) == sum(len(gold.claims) for gold in golds.values())
    assert all(set(claim.ev) <= {memory.id for memory in core.memories} for claim in core.claims)
    shared = scale.shared_names(golds)
    assert shared["alex"] == ["defects-v1", "store-v1"] and "dana" in shared and "northwind" not in shared or "denver" in shared
    # A question that names a shared name seeds every entity that has it.
    assert {"defects-v1:alex", "store-v1:alex"} <= set(mentions(core, "Which cities will Alex visit?"))


def test_look_alikes_are_deterministic_renamed_copies_that_never_repeat_a_core_text():
    from benchmarks.graph_oracle import scale

    golds, core = scale.load_core()
    first, clones, dropped = scale.make_clones(core, 400)
    again, _, _ = scale.make_clones(core, 400)
    assert [(memory.text, clone) for memory, clone in first] == [(memory.text, clone) for memory, clone in again]
    assert len(first) == 400 and len(clones) >= 3 and dropped > 0
    core_texts = {memory.text for memory in core.memories}
    assert not core_texts & {memory.text for memory, _ in first} and len({memory.text for memory, _ in first}) == 400
    renamed = " ".join(memory.text for memory, _ in first[:150])
    assert "Alex" not in renamed and "Tokyo" not in renamed and "Jordan" not in renamed
    clone = clones[0]
    stored = {memory.id for memory in clone.memories}
    assert all(set(claim.ev) <= stored for claim in clone.claims) and any(claim.inferred for claim in clone.claims)
    assert all(claim.ev for claim in clone.claims if not claim.inferred)


async def test_the_embedding_cache_embeds_each_text_once_and_keeps_vectors_in_a_file(tmp_path):
    from benchmarks.graph_oracle.scale import EmbeddingCache

    class Model:
        model_name, model_version, dimension = "fake", "1", 2

        def __init__(self):
            self.calls = []

        async def embed(self, texts):
            self.calls.append(list(texts))
            return [[float(len(text)), 1.0] for text in texts]

    model = Model()
    cache = EmbeddingCache(None, tmp_path / "vectors.pkl").wrap(model)
    assert await cache.embed(["a", "bb", "a"]) == [[1.0, 1.0], [2.0, 1.0], [1.0, 1.0]]
    await cache.embed(["bb", "ccc"])
    assert model.calls == [["a", "bb"], ["ccc"]] and cache.computed == 3 and cache.hits == 2
    cache.save()
    again = EmbeddingCache(None, tmp_path / "vectors.pkl").wrap(Model())
    assert await again.embed(["a", "ccc"]) == [[1.0, 1.0], [3.0, 1.0]] and again.computed == 0


def test_the_scale_conditions_are_computed_from_the_rows_at_each_size():
    from benchmarks.graph_oracle import scale_run

    def rows(baseline_hits, routed_hits, kind="list", route="graph", total=100):
        return [{"id": f"{kind}{number}", "set": "x", "type": kind, "route": route,
                 "ranks": {"baseline": 1 if number < baseline_hits else 9, "routed[derived]": 1 if number < routed_hits else 9,
                           "routed[hand]": 1, "graph[derived]": 1, "tagged[derived]": 1}} for number in range(total)]

    def sizes(core, grown):
        return {"core": rows(*core), "500": rows(*grown), "2000": rows(*grown)}

    # The advantage is 10 points at the core size and 30 at 2,000: it grows by 20.
    result = scale_run.conditions(sizes((60, 70), (50, 80)))
    assert result["1 advantage at 2,000 at least 8 points larger than at the core size"] == {"value": 20.0, "pass": True}
    assert result["2 no type loses more than 1 question at any size"]["pass"]
    flat = scale_run.conditions(sizes((60, 70), (50, 62)))
    assert not flat["1 advantage at 2,000 at least 8 points larger than at the core size"]["pass"]
    losing = scale_run.conditions({"core": rows(60, 70), "500": rows(60, 58), "2000": rows(60, 70)})
    assert losing["2 no type loses more than 1 question at any size"] == {"value": 2, "pass": False}
    broken = sizes((60, 70), (50, 80))
    broken["core"] = rows(60, 62, "lookup", "text")
    assert not scale_run.conditions(broken)["3 routed-to-text lookups, temporal and corrections equal the baseline"]["pass"]


async def test_the_sql_walk_matches_the_python_walk_in_the_combined_core(mock_embeddings, tmp_path):  # noqa: F811
    import math

    from benchmarks.graph_oracle import scale, scale_run
    from benchmarks.graph_oracle.graph import StoreSource
    from benchmarks.graph_oracle.sqlwalk import SqlWalk
    from prme.models.nodes import MemoryNode
    from prme.types import NodeType, Scope

    golds, core = scale.load_core()
    pack = tmp_path / "pack"
    (pack / "lexical_index").mkdir(parents=True)
    async with MemoryEngine.open(gate_config(pack)) as engine:
        ids = {memory.id: await engine._graph_store.create_node(MemoryNode(
            user_id="scale", node_type=NodeType.NOTE, content=memory.text, scope=Scope.PERSONAL, confidence=0.9))
            for memory in core.memories}
        handles = await write_graph(engine, core, "scale", ids)
        asked = [item["text"] for item in scale_run.build_items(golds)][::6]
        for params in (Params(), Params(cue_source="derived")):
            walk = SqlWalk(engine, "duckdb", "scale", handles, core, params)
            for text in asked:
                python = await generic_scores(text, core, StoreSource(engine, "scale", handles), params)
                sql = await walk.scores(text)
                keep = lambda result: {key: value for key, value in result.scores.items() if value > 0}  # noqa: E731
                assert sql.seeds == python.seeds and set(keep(sql)) == set(keep(python)) and all(
                    math.isclose(value, keep(python)[key], rel_tol=1e-9) for key, value in keep(sql).items()), text


async def test_a_scale_run_pads_both_kinds_and_reports_each_size(mock_embeddings, tmp_path, monkeypatch):  # noqa: F811
    from benchmarks.graph_oracle import scale_run

    monkeypatch.setattr(scale_run, "conversation_pad", lambda count: [f"An unrelated remark number {number} about the weather" for number in range(count)])
    report = await scale_run.run(tmp_path / "scale", sizes=(200, 215), backends=("duckdb",), every=12)
    assert report["questions"] == 17 and report["sizes"] == ["core", "200", "215"] and set(report["kinds"]) == {"conversation", "lookalike"}
    for kind, result in report["kinds"].items():
        assert [stage["memories"] for stage in result["stages"].values()] == [189, 200, 215]
        assert result["stages"]["core"]["summary"]["all"]["baseline"]["n"] == 17
        assert set(result["conditions"]) >= {"1 advantage at 2,000 at least 8 points larger than at the core size"}
        assert all(row["ranks"]["routed[derived]"] == row["ranks"]["baseline"] for rows in result["rows"].values()
                   for row in rows if row["route"] == "text")
    assert report["kinds"]["lookalike"]["padding"]["clones"] >= 1 and "## lookalike" in (tmp_path / "scale" / "report.md").read_text()
    # A second run reuses the text stores' lists.
    again = await scale_run.run(tmp_path / "scale", sizes=(200, 215), backends=("duckdb",), every=12)
    assert again["kinds"]["conversation"]["stages"]["215"]["summary"] == report["kinds"]["conversation"]["stages"]["215"]["summary"]
