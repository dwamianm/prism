"""A passing workflow cannot hide a failed native/interpreter shutdown."""

import sys

import pytest

from benchmarks.diagnostics._process import checked_report


@pytest.mark.parametrize("exit_code", [0, 134])
def test_diagnostic_success_requires_clean_process_exit(exit_code):
    code = "import pathlib, sys; pathlib.Path(sys.argv[-1]).write_text('{\"passed\": true}'); sys.exit(int(sys.argv[1]))"
    report = checked_report([sys.executable, "-c", code, str(exit_code)], timeout=5)
    assert report["workflow_assertions_passed"]
    assert report["process_exit_code"] == exit_code
    assert report["passed"] == (exit_code == 0)


def test_missing_report_and_timeout_cannot_pass():
    assert not checked_report([sys.executable, "-c", "pass"], timeout=5)["passed"]
    report = checked_report([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.1)
    assert report["passed"] is False and report["error_type"] == "TimeoutExpired"



def test_relationship_diagnostic_checks_all_claims_and_actual_subject_edges():
    from types import SimpleNamespace
    from benchmarks.diagnostics.entity_references import assess_claims
    from prme.models import MemoryNode
    from prme.types import EdgeType, EpistemicType, NodeType

    source = "Alice might use a database."
    fact = MemoryNode(content=source, user_id="probe", node_type=NodeType.FACT,
                      epistemic_type=EpistemicType.HYPOTHETICAL,
                      metadata={"subject_link_status": "resolved"})
    edge = SimpleNamespace(edge_type=EdgeType.HAS_FACT, target_id=fact.id)
    assert assess_claims("conditional", source, [fact], [edge])["passed"]
    assert not assess_claims("conditional", source, [fact], [])["passed"]
    preference = fact.model_copy(update={"node_type": NodeType.PREFERENCE})
    report = assess_claims("conditional", source, [fact, preference], [edge])
    assert not report["passed"] and not report["expected_claim_kinds"]
    asserted = fact.model_copy(update={"epistemic_type": EpistemicType.ASSERTED})
    report = assess_claims("conditional", source, [fact, asserted], [edge])
    assert not report["passed"] and not report["epistemic_qualifications_preserved"]


def test_classification_probe_requires_each_expected_object_and_its_kind():
    from types import SimpleNamespace
    from benchmarks.diagnostics.entity_references import assess_claims
    from prme.models import MemoryNode
    from prme.types import EdgeType, EpistemicType, NodeType

    source = "Maya uses Redis but prefers SQLite."
    use = MemoryNode(content=source, user_id="probe", node_type=NodeType.FACT,
                     epistemic_type=EpistemicType.ASSERTED,
                     metadata={"object": "Redis", "subject_link_status": "resolved"})
    pref = MemoryNode(content=source, user_id="probe", node_type=NodeType.PREFERENCE,
                      epistemic_type=EpistemicType.ASSERTED,
                      metadata={"object": "SQLite", "subject_link_status": "resolved"})
    edges = [SimpleNamespace(edge_type=EdgeType.HAS_FACT, target_id=n.id) for n in [use, pref]]
    expected = {"expected_kinds": {"Redis": "fact", "SQLite": "preference"}, "allowed_epistemic": ["asserted", "observed"], "expected_claim_count": 2}
    assert assess_claims("mixed", source, [use, pref], edges, **expected)["passed"]
    assert not assess_claims("mixed", source, [use], edges, **expected)["passed"]
    assert not assess_claims("mixed", source, [use, pref, pref], edges, **expected)["passed"]
    wrong = pref.model_copy(update={"node_type": NodeType.FACT})
    assert not assess_claims("mixed", source, [use, wrong], edges, **expected)["passed"]



def test_namesake_probe_accepts_full_mentions_but_requires_correct_graph_roles():
    from benchmarks.diagnostics.entity_references import assess_claims
    from prme.models import MemoryNode, MemoryEdge
    from prme.types import EdgeType, NodeType

    source = "Jordan, the engineer, lives in Jordan, the country."
    subject = MemoryNode(content="Jordan, the engineer", user_id="probe", node_type=NodeType.ENTITY,
                         metadata={"entity_type": "person"})
    obj = MemoryNode(content="Jordan, the country", user_id="probe", node_type=NodeType.ENTITY,
                     metadata={"entity_type": "location"})
    fact = MemoryNode(content=source, user_id="probe", node_type=NodeType.FACT,
                      metadata={"object": obj.content, "subject_link_status": "resolved"})
    edges = [MemoryEdge(user_id="probe", source_id=subject.id, target_id=fact.id, edge_type=EdgeType.HAS_FACT),
             MemoryEdge(user_id="probe", source_id=fact.id, target_id=obj.id, edge_type=EdgeType.MENTIONS)]
    expected = {"expected_entity_types": {"subject": "person", "object": "location"}}
    assert assess_claims("namesake", source, [subject, obj, fact], edges, **expected)["passed"]
    wrong = edges[1].model_copy(update={"target_id": subject.id})
    assert not assess_claims("namesake", source, [subject, obj, fact], [edges[0], wrong], **expected)["passed"]
