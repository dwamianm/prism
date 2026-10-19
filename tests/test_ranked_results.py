"""The ranked results of a chat probe, by kind, with what ranks above the answer."""
from datetime import datetime, timezone
from uuid import uuid4

from benchmarks.diagnostics.ranked_results import kinds_above_answer, result_kind, result_rows
from prme.models.nodes import MemoryNode
from prme.retrieval.models import RetrievalCandidate
from prme.types import LifecycleState, NodeType

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
PROBE = {"id": "pet", "question": "What pet does Dana have?", "all_of": ["Biscuit", "golden"]}


def result(content, node_type=NodeType.FACT, grounding=None, score=0.5):
    node = MemoryNode(
        id=uuid4(), user_id="owner", node_type=node_type, content=content, lifecycle_state=LifecycleState.STABLE,
        created_at=NOW, updated_at=NOW, last_reinforced_at=NOW,
        metadata={"grounding_method": grounding} if grounding else None,
    )
    return RetrievalCandidate(node=node, paths=["VECTOR", "LEXICAL"], path_count=2, composite_score=score,
                              semantic_score=0.6, lexical_score=0.4)


RESULTS = [
    result("Dana has pet Biscuit.", grounding="claim_statement_v1", score=0.9),
    result("Dana", NodeType.ENTITY, score=0.8),
    result("Dana has partner Sam.", grounding="claim_statement_v1", score=0.7),
    result("Our golden retriever, Biscuit, is three.", grounding="claim_sentences_v1", score=0.6),
    result("Biscuit is golden.", NodeType.NOTE, score=0.5),
]


def test_a_kind_is_the_record_type_and_for_a_claim_its_grounding():
    assert [result_kind(item) for item in RESULTS] == [
        "fact/claim_statement_v1", "entity", "fact/claim_statement_v1", "fact/claim_sentences_v1", "note"]
    assert result_kind(result("A claim with no grounding")) == "fact/unknown"


def test_the_kinds_above_the_answer_are_counted_and_an_entity_is_never_the_answer():
    # The statement and the entity name Biscuit or Dana but only the sentence holds both answer terms.
    assert kinds_above_answer(PROBE, RESULTS) == {"fact/claim_statement_v1": 2, "entity": 1}
    assert kinds_above_answer(PROBE, RESULTS[3:]) == {}
    assert kinds_above_answer(PROBE, RESULTS[:3]) == {}


def test_the_rows_mark_the_answer_and_stop_at_the_limit():
    rows = result_rows(PROBE, RESULTS, limit=4)
    assert [row[0] for row in rows] == [1, 2, 3, 4]
    assert [row[1] for row in rows] == ["", "", "", "answer"]
    assert rows[3][2:9] == ["fact/claim_sentences_v1", "0.6000", "0.600", "0.400", "0.00", "None/None", "VECTOR,LEXICAL"]
