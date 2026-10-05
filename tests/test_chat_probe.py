"""A scripted chat built through ingest() and measured against its answer key."""
import json

import pytest

from benchmarks.diagnostics import chat_probe
from benchmarks.diagnostics.chat_probe import (
    DEFAULT_CONVERSATION, chat_turns, compare_markdown, context_report, graph_report, load_conversation,
)
from prme.ingestion.schema import ExtractionResult

HOME = "I live in Denver with my partner Sam."
NURSE = "Sam is a nurse."
ECHO = "Nice, Sam sounds great."
# Claims the scripted model finds, by message; any other text yields none.
CLAIMS = {
    HOME: [("I", "lives_in", "Denver", "location"), ("I", "has_partner", "Sam", "person")],
    NURSE: [("Sam", "works_as", "nurse", None)],
}


def _conversation(tmp_path, **changes):
    conversation = {
        "kind": "prme-chat-probe", "schema_version": 1, "name": "tiny", "owner": "tiny-owner",
        "user_name": "Dana", "reference_time": "2026-03-01T12:00:00+00:00",
        "sessions": [
            {"id": "a", "start": "2026-02-01T10:00:00+00:00",
             "turns": [{"role": "user", "text": HOME}, {"role": "assistant", "text": ECHO}]},
            {"id": "b", "start": "2026-02-08T10:00:00+00:00", "turns": [{"role": "user", "text": NURSE}]},
        ],
        "key": {
            "entities": [{"id": "dana", "names": ["Dana"], "owner": True}, {"id": "sam", "names": ["Sam"]},
                         {"id": "denver", "names": ["Denver"]}, {"id": "rachel", "names": ["Rachel"]}],
            "links": [{"id": "partner", "between": ["dana", "sam"], "label": "Dana's partner is Sam"},
                      {"id": "sister", "between": ["dana", "rachel"], "label": "Rachel is Dana's sister"}],
            "repeats": [{"id": "sam-nurse", "label": "Sam is a nurse", "terms": ["sam", "nurse"],
                         "user_mentions": 1}],
            "probes": [{"id": "job", "question": "What does Sam do?", "all_of": ["nurse"]},
                       {"id": "home", "question": "Where does Dana live?", "any_of": ["Denver"],
                        "stale": ["Boston"]}],
        },
        **changes,
    }
    path = tmp_path / "tiny.json"
    path.write_text(json.dumps(conversation))
    return path


class ScriptedExtraction:
    def __init__(self):
        self.calls = []

    async def extract(self, content, *, role=None):
        self.calls.append((content, role))
        claims = CLAIMS.get(content, [])
        return ExtractionResult.model_validate({
            "entities": [{"name": name, "entity_type": kind or "person"}
                         for name, kind in {(claim[0], None) for claim in claims if claim[0] != "I"}
                         | {(claim[2], claim[3]) for claim in claims if claim[3]}],
            "facts": [{"subject": subject, "predicate": predicate, "object": obj, "object_entity_type": kind,
                       "polarity": "positive", "evidence_quote": content}
                      for subject, predicate, obj, kind in claims]})


@pytest.fixture
def mock_embeddings(monkeypatch):
    from tests.test_durable_ingestion import MockEmbeddingProvider

    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())


def test_the_committed_conversation_loads_and_every_turn_has_its_own_time_and_key():
    conversation = load_conversation(DEFAULT_CONVERSATION)
    turns = chat_turns(conversation)
    assert len(turns) == 54 and len({turn["key"] for turn in turns}) == len(turns)
    assert all(earlier["event_time"] < later["event_time"] for earlier, later in zip(turns, turns[1:]))
    assert {turn["role"] for turn in turns} == {"user", "assistant"}
    assert all(turn["speaker"] is None for turn in turns)
    named = chat_turns(conversation, speaker=True)
    assert {turn["speaker"] for turn in named if turn["role"] == "user"} == {"Dana"}
    assert {turn["speaker"] for turn in named if turn["role"] == "assistant"} == {None}
    assert turns[0]["metadata"] == {"chat_probe": "chat-v1", "turn_key": "s1-intro#0"}


def test_a_key_must_name_one_owner_and_only_listed_entities(tmp_path):
    path = _conversation(tmp_path)
    broken = json.loads(path.read_text())
    broken["key"]["links"].append({"id": "x", "between": ["dana", "nobody"], "label": "x"})
    path.write_text(json.dumps(broken))
    with pytest.raises(ValueError, match="names an entity"):
        load_conversation(path)
    broken["key"]["links"].pop()
    broken["key"]["entities"][0]["owner"] = False
    path.write_text(json.dumps(broken))
    with pytest.raises(ValueError, match="exactly one owner"):
        load_conversation(path)


def _node(node_id, kind, content, state="tentative", refs=(), **metadata):
    return {"id": node_id, "type": kind, "state": state, "content": content, "metadata": metadata,
            "evidence_refs": list(refs)}


def test_graph_report_counts_repeated_text_pronoun_subjects_and_links(tmp_path):
    conversation = load_conversation(_conversation(tmp_path))
    nodes = [
        _node("note-1", "note", HOME, refs=["e1"]),
        _node("i", "entity", "I", identity_status="unresolved_reference"),
        _node("sam", "entity", "Sam"), _node("denver", "entity", "Denver"), _node("rachel", "entity", "Rachel"),
        _node("summer", "entity", "summer"),
        _node("c1", "fact", HOME, refs=["e1"], subject="I", predicate="lives_in", object="Denver"),
        _node("c2", "fact", HOME, refs=["e1"], subject="I", predicate="has_partner", object="Sam"),
        _node("c3", "fact", NURSE, refs=["e3"], subject="Sam", predicate="works_as", object="nurse"),
        _node("c4", "fact", ECHO, refs=["e2"], subject="Sam", predicate="works_as", object="nurse"),
        _node("old", "fact", "Sam was a teacher.", state="superseded", refs=["e3"]),
    ]
    edges = [{"source": "i", "target": "c1", "type": "has_fact"}, {"source": "c1", "target": "denver", "type": "mentions"},
             {"source": "i", "target": "c2", "type": "has_fact"}, {"source": "c2", "target": "sam", "type": "mentions"},
             {"source": "sam", "target": "c3", "type": "has_fact"}, {"source": "sam", "target": "c4", "type": "has_fact"}]
    events = {"e1": {"role": "user", "key": "a#0"}, "e2": {"role": "assistant", "key": "a#1"},
              "e3": {"role": "user", "key": "b#0"}}
    report = graph_report(nodes, edges, events, conversation)
    claims = report["claims"]
    assert (claims["active"], claims["distinct_texts"], claims["extra_copies_of_a_text"]) == (4, 3, 1)
    assert (claims["same_claim_groups"], claims["extra_copies_of_a_claim"]) == (1, 1)
    assert claims["text_is_a_whole_message"] == 2 and claims["from_assistant_turns_only"] == 1
    assert claims["user_claim_subjects"] == {"first_person_pronoun": 2, "other_entity": 1}
    entities = report["entities"]
    assert entities["with_no_link"] == ["Rachel", "summer"] and entities["pronouns"] == ["I"]
    assert {name: entry["status"] for name, entry in entities["key"].items()} == {
        "dana": "missing", "sam": "found", "denver": "found", "rachel": "found"}
    links = {link["id"]: link for link in report["links"]}
    assert links["partner"]["status"] == "via_pronoun"
    assert links["partner"]["claims"] == [{"claim": "I → has_partner → Sam", "state": "tentative"}]
    assert links["sister"]["status"] == "not_linked"
    [repeat] = report["repeats"]
    assert (repeat["claims_stating_it"], repeat["from_assistant_only"], repeat["records_showing_its_text"]) == (2, 1, 1)


def test_context_report_reads_answers_only_from_record_lines():
    probe = {"id": "job", "question": "What does Sam do?", "all_of": ["nurse", "Denver"], "stale": ["teacher"]}
    context = ("Lines starting with \"- \" are records; nurse Denver.\n[stable_facts]\n"
               "- \"Sam is a nurse.\"\n- \"Sam is a nurse.\"\n- \"Sam was a teacher.\"\n")
    included = [{"content": "Sam is a nurse.", "type": "fact"}] * 2 + [{"content": "Sam was a teacher.", "type": "fact"}]
    report = context_report(probe, included, context, 30, 4096)
    assert report["answer_in_context"] is False and report["terms_found"] == ["nurse"]
    assert report["stale_in_context"] == ["teacher"]
    assert (report["records"], report["distinct_texts"], report["repeated_records"], report["repeated_lines"]) == (
        3, 2, 1, 1)
    assert context_report({**probe, "all_of": None, "any_of": ["Denver", "nurse"]}, included, context, 30,
                          4096)["answer_in_context"] is True


async def test_build_ingests_both_sides_measures_the_pack_and_rebuilds_from_the_cache(
        tmp_path, mock_embeddings, monkeypatch):
    monkeypatch.setattr(chat_probe, "model_identity", lambda model, cloud=False: {
        "provider": "ollama", "model": model, "server_version": "0.0.0"})
    path = _conversation(tmp_path)
    scripted = ScriptedExtraction()

    def prepare(engine):
        engine._pipeline._extraction_provider.extract = scripted.extract

    root = tmp_path / "builds"
    report = await chat_probe.build("defaults", conversation_path=path, root=root, model="local-model",
                                    prepare=prepare)
    assert [(content, role) for content, role in scripted.calls] == [
        (HOME, "user"), (ECHO, "assistant"), (NURSE, "user")]
    folder = root / "tiny" / "defaults"
    rows = [json.loads(line) for line in (folder / "turns.jsonl").read_text().splitlines()]
    assert [(row["key"], row["role"], row["cache_hits"]) for row in rows] == [
        ("a#0", "user", 0), ("a#1", "assistant", 0), ("b#0", "user", 0)]
    build = json.loads((folder / "build.json").read_text())
    assert build["totals"]["turns"] == 3 and build["speaker"] is False and build["model"]["model"] == "local-model"
    graph = report["graph"]
    assert graph["claims"]["active"] == 3 and graph["claims"]["user_claim_subjects"]["first_person_pronoun"] == 2
    assert {link["id"]: link["status"] for link in graph["links"]} == {
        "partner": "via_pronoun", "sister": "entity_missing"}
    probes = {probe["id"]: probe for probe in report["probes"]}
    assert probes["job"]["answer_in_context"] and probes["home"]["answer_in_context"]
    assert (folder / "contexts" / "job.txt").read_text().startswith("Q: What does Sam do?")
    assert (folder / "report.md").read_text().startswith("# Chat probe: tiny / defaults")
    assert json.loads((folder / "report.json").read_text())["summary"] == report["summary"]

    with pytest.raises(ValueError, match="--replace"):
        await chat_probe.build("defaults", conversation_path=path, root=root, model="local-model", prepare=prepare)
    # The same settings again repeat the cached extraction without asking the model.
    again = await chat_probe.build("defaults", conversation_path=path, root=root, model="local-model",
                                   prepare=prepare, replace=True, organize=True)
    assert len(scripted.calls) == 3
    assert [json.loads(line)["cache_hits"] for line in (folder / "turns.jsonl").read_text().splitlines()] == [1, 1, 1]
    assert again["graph"]["claims"]["active"] == report["graph"]["claims"]["active"]
    assert "organize_result" in json.loads((folder / "build.json").read_text())
    measured = await chat_probe.measure(folder, load_conversation(path))
    assert measured["summary"] == again["summary"]
    table = compare_markdown([report, again])
    assert "| Measure | defaults | defaults |" in table and "| Dana's partner is Sam | via_pronoun | via_pronoun |" in table


async def test_a_folder_that_is_not_a_build_is_never_replaced(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_probe, "model_identity", lambda model, cloud=False: {"provider": "ollama"})
    path = _conversation(tmp_path)
    folder = tmp_path / "builds" / "tiny" / "mine"
    folder.mkdir(parents=True)
    (folder / "notes.txt").write_text("keep")
    with pytest.raises(ValueError, match="not a chat probe build"):
        await chat_probe.build("mine", conversation_path=path, root=tmp_path / "builds", replace=True)
    assert (folder / "notes.txt").read_text() == "keep"
    with pytest.raises(ValueError, match="plain folder name"):
        await chat_probe.build("../escape", conversation_path=path, root=tmp_path / "builds")
