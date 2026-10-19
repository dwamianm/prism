"""Stage "store": the memories an application stores one call at a time, and the questions asked of them.

``POST /v1/store`` stores one node per call and runs no extraction, so the
whole graph of a developer who follows the quick start is what ``store()``
made. This stage builds that pack from ``fixtures/fast-cycle/store-v1.json``
(75 memories of one made-up user, 17 questions with answer terms): a fresh
engine over the gate's configuration (current defaults, isolated from the
environment, organizer off), the real local embedding model, and ``store()``
called as the route calls it (``store_with_receipt``) with each memory's role,
speaker, node type, scope and metadata.

Per question it keeps the rank of the answer among the results ``retrieve()``
returns (the chat probe's ``answer_rank``), whether a record that holds only a
stale value ranks above the answer, the context tokens used and whether the
word and graph channels found a candidate. Over the pack it keeps the nodes and
edges made per call and how many of the fixture's key entities exist as nodes.
No model is involved, so a run is repeatable on one machine.

The default pack is a local DuckDB pack. ``database_url`` runs the same
stage on PostgreSQL, the hosted backend, in a fresh owner so earlier runs
cannot answer for this one. The two backends search words differently
(PostgreSQL full-text against Tantivy), so a report names its backend and
``compare`` refuses to judge one against the other.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
import uuid

from pydantic import SecretStr

from benchmarks.diagnostics.chat_probe import _name, _records, answer_rank, context_report
from benchmarks.diagnostics.extracted_packs import _drain_sources
from benchmarks.diagnostics.product_packing import gate_config
from prme import MemoryEngine
from prme.types import NodeType, Scope

KIND = "prme-fast-cycle-store"
SCHEMA_VERSION = 1
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "fast-cycle"
DEFAULT_FIXTURE = FIXTURES / "store-v1.json"
ACTIVE = frozenset({"tentative", "stable"})


def load_fixture(path: Path = DEFAULT_FIXTURE) -> dict:
    fixture = json.loads(path.read_text())
    if fixture.get("kind") != KIND or fixture.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path} is not a fast cycle store fixture")
    for memory in fixture["memories"]:
        NodeType(memory["node_type"]), Scope(memory["scope"])
        if not memory["content"].strip() or memory["role"] not in {"user", "assistant", "participant"}:
            raise ValueError(f"{path}: memory {memory['content'][:40]!r} needs text and a role")
    ids = [question["id"] for question in fixture["key"]["questions"]]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: question IDs must be unique")
    for question in fixture["key"]["questions"]:
        if bool(question.get("all_of")) == bool(question.get("any_of")):
            raise ValueError(f"{path}: question {question['id']} needs all_of or any_of")
    return fixture


def fixture_digest(path: Path = DEFAULT_FIXTURE) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stale_above_answer(question: dict, ranked: list[dict]) -> bool:
    """Whether a record with the question's stale value ranks above its answer, or the answer is missing.

    The answer's rank is the first record that holds the answer, so every
    record above it is a record without the answer; one that holds a stale
    value is the old fact the reader would see first. An entity's text is only
    its name, so it never counts. A question with no stale value is never true.
    """
    stale = [term.casefold() for term in question.get("stale", [])]
    rank = answer_rank(question, ranked)
    above = ranked if rank is None else ranked[:rank - 1]
    return bool(stale) and any(result["type"] != "entity" and any(term in result["content"].casefold() for term in stale)
                               for result in above)


def question_report(question: dict, response, originals: dict[str, str] | None = None) -> dict:
    """What one question's ``retrieve()`` response holds.

    ``originals`` maps the retrieval content a memory was stored with to the text it was sent as. Answers and
    stale values are read from the text as sent, so a sentence added to a memory cannot answer for it.
    """
    originals = originals or {}
    ranked = [{"content": originals.get(candidate.node.content, candidate.node.content),
               "type": candidate.node.node_type.value} for candidate in response.results]
    included = [{"content": originals.get(candidate.node.content, candidate.node.content),
                 "type": candidate.node.node_type.value} for section in response.bundle.sections.values()
                for candidate in section]
    context = response.bundle.render()
    for stored, sent in originals.items():
        context = context.replace(stored, sent)
    report = context_report(question, included, context, response.bundle.tokens_used,
                            response.bundle.token_budget, ranked)
    channels = response.metadata.candidates_generated
    return {**report, "stale_above_answer": stale_above_answer(question, ranked),
            "lexical_candidates": channels.get("LEXICAL", 0), "graph_candidates": channels.get("GRAPH", 0)}


def store_measures(questions: list[dict], nodes: list[dict], edges: list[dict], calls: int, key_entities: list[dict]) -> dict:
    """The stage's measures, by name, from the question reports and the finished pack."""
    ranks = [question["answer_rank"] for question in questions]
    entities = {_name(node["content"]) for node in nodes if node["type"] == "entity" and node["state"] in ACTIVE}
    count = len(questions)
    return {
        "nodes_per_store_call": len(nodes) / calls,
        "edges_per_store_call": len(edges) / calls,
        "context_tokens_mean": sum(question["tokens_used"] for question in questions) / count,
        "key_entities_found": sum(any(_name(name) in entities for name in entity["names"]) for entity in key_entities),
        "questions_with_a_graph_candidate": sum(question["graph_candidates"] > 0 for question in questions),
        "questions_with_a_word_candidate": sum(question["lexical_candidates"] > 0 for question in questions),
        "answers_in_top1": sum(rank == 1 for rank in ranks),
        "answers_in_top3": sum(rank is not None and rank <= 3 for rank in ranks),
        "answers_in_context": sum(question["answer_in_context"] for question in questions),
        "mean_reciprocal_rank": sum(1 / rank for rank in ranks if rank) / count,
        "stale_above_answer": sum(question["stale_above_answer"] for question in questions),
        "stale_in_context": sum(bool(question["stale_in_context"]) for question in questions),
        **{f"answer_rank:{question['id']}": question["answer_rank"] for question in questions},
    }


def store_config(pack: Path, overrides: dict | None = None, database_url: str | None = None):
    """The gate's configuration over ``pack``, or over PostgreSQL when ``database_url`` is given."""
    config = gate_config(pack, overrides)
    return config.model_copy(update={"database_url": SecretStr(database_url)}) if database_url else config


async def run(work: Path, *, fixture_path: Path = DEFAULT_FIXTURE, overrides: dict | None = None,
              database_url: str | None = None, retrieval_contents: list[str | None] | None = None) -> dict:
    """Store the fixture's memories, ask its questions and return the stage's measures and details.

    ``retrieval_contents`` gives each memory's ``retrieval_content`` (None keeps the memory as written); the
    contextual stage uses it. Answers are always read from the memory as sent.
    """
    fixture = load_fixture(fixture_path)
    if retrieval_contents is not None and len(retrieval_contents) != len(fixture["memories"]):
        raise ValueError("retrieval_contents needs one entry for each memory")
    originals = {} if retrieval_contents is None else {
        stored: memory["content"] for stored, memory in zip(retrieval_contents, fixture["memories"]) if stored}
    pack = work / "pack"
    (pack / "lexical_index").mkdir(parents=True, exist_ok=True)
    # A shared PostgreSQL database keeps earlier runs, so each run is a new owner.
    owner = f"{fixture['owner']}-{uuid.uuid4().hex[:8]}" if database_url else fixture["owner"]
    started = time.perf_counter()
    async with MemoryEngine.open(store_config(pack, overrides, database_url)) as engine:
        for number, memory in enumerate(fixture["memories"]):
            await engine.store_with_receipt(
                memory["content"], user_id=owner, role=memory["role"], speaker=memory.get("speaker"),
                retrieval_content=None if retrieval_contents is None else retrieval_contents[number],
                node_type=NodeType(memory["node_type"]), scope=Scope(memory["scope"]),
                metadata={"source": "synthetic-demo", "session": memory["session"]})
        stored = time.perf_counter() - started
        # Search indexes are written after the node; wait for them as a reader later would.
        await _drain_sources(engine, owner)
        nodes, edges, _ = await _records(engine, owner)
        questions = []
        asked = time.perf_counter()
        for question in fixture["key"]["questions"]:
            questions.append(question_report(question, await engine.retrieve(question["question"], user_id=owner),
                                             originals))
        asked = time.perf_counter() - asked
    measures = store_measures(questions, nodes, edges, len(fixture["memories"]), fixture["key"]["entities"])
    return {"backend": "postgres" if database_url else "duckdb", "fixture": fixture["name"],
            "fixture_sha256": fixture_digest(fixture_path), "settings": overrides or {},
            "calls": len(fixture["memories"]), "nodes": len(nodes), "edges": len(edges),
            "store_seconds": stored, "retrieve_seconds": asked, "measures": measures,
            "questions": [{"id": question["id"], "question": question["question"], **{
                key: report[key] for key in ("answer_rank", "answer_in_context", "stale_in_context",
                                             "stale_above_answer", "tokens_used", "lexical_candidates",
                                             "graph_candidates", "top_results")}}
                          for question, report in zip(fixture["key"]["questions"], questions)]}
