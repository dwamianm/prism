"""PV-04 qualifier component checks; backend acceptance is recorded separately."""

import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest

from prme.ingestion.admitted_statements import (
    project_admitted_graph, project_admitted_node, project_direct_node,
)
from prme.ingestion.planning import PlanningGraph, PlanningIndexes
from prme.models import Event, MemoryNode
from prme.privacy.capabilities import SanitizedContent
from prme.privacy.policy import PrivacyDenied
from prme.types import EpistemicType, NodeType, Scope

FIXTURE_PATH = Path(__file__).parent / "fixtures/privacy/source-separation-v1.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text())
NOW = datetime.fromisoformat(FIXTURE["clock"])
EVENT_ID = UUID("30000000-0000-0000-0000-000000000001")


def source_for(row):
    return SanitizedContent(
        fields_json=json.dumps({"content": row["source"], "speaker": row.get("speaker")}),
        source_id=UUID("40000000-0000-0000-0000-000000000001"),
        representation_id=UUID("50000000-0000-0000-0000-000000000001"),
        private_checksum="not-used-by-this-pure-projection",
        sanitizer_version="prme-sanitizer-v1", registry_version="prme-recognizers-v1",
    )


def node_for(row):
    metadata = {name: row[name] for name in (
        "subject", "predicate", "object", "polarity", "condition", "speaker_reference", "quantity",
    ) if name in row}
    metadata.update(
        extraction_kind="relationship" if row.get("relationship") else "fact",
        evidence_quote=row["source"],
        resolution={"original": FIXTURE["source_only_marker"], "replacement": FIXTURE["source_only_marker"]},
        unknown_extension=FIXTURE["source_only_marker"],
    )
    if row.get("condition"):
        metadata["condition_state"] = "unknown"
    effective = datetime.fromisoformat(row.get("effective_time", FIXTURE["clock"]))
    return MemoryNode(
        node_type=NodeType.FACT, content=row["source"], user_id=FIXTURE["owner"],
        scope=Scope.PERSONAL, metadata=metadata, evidence_refs=[EVENT_ID],
        event_time=effective, valid_from=effective, created_at=NOW,
        epistemic_type=EpistemicType.CONDITIONAL if row.get("condition") else EpistemicType.ASSERTED,
    )


@pytest.mark.parametrize("row", FIXTURE["qualifiers"], ids=lambda row: row["id"])
def test_frozen_qualifier_projection(row):
    node = node_for(row)
    projected = project_admitted_node(node, source_for(row))
    assert projected.content == row["expected"]
    assert projected.event_time == node.event_time and projected.valid_from == node.valid_from
    assert projected.evidence_refs == [EVENT_ID]
    assert projected.epistemic_type == node.epistemic_type
    assert projected.metadata["polarity"] == row["polarity"]
    assert FIXTURE["source_only_marker"] not in projected.model_dump_json()
    assert "evidence_quote" not in projected.metadata and "resolution" not in projected.metadata
    if row.get("condition"):
        assert projected.metadata["condition"] == row["condition"]
    if row.get("quantity"):
        assert projected.metadata["quantity"] == row["quantity"]
    assert node.content == row["source"] and "evidence_quote" in node.metadata
    if row["id"] == "Q04":
        from prme.ingestion.grounding import validate_private_grounding
        from prme.ingestion.pipeline import IngestionPipeline
        from prme.ingestion.schema import ExtractedFact, ExtractionResult
        def extraction(text, reference):
            return ExtractionResult(facts=[ExtractedFact(
                subject=row["subject"], predicate=row["predicate"], object=row["object"],
                polarity=row["polarity"], evidence_quote=text, temporal_ref=reference,
            )])
        original = extraction(row["source"], "October 21")
        checked = validate_private_grounding(original, row["source"], reference_time=NOW,
                                             resolve_date=IngestionPipeline._resolve_temporal)
        assert checked.facts[0].temporal_ref == row["effective_time"]
        assert original.facts[0].temporal_ref == "October 21"
        # Controls within frozen Q04: no arbitrary model timestamp, borrowed
        # start cue, incomplete/ambiguous calendar date or competing source date.
        for text, reference in (
            (row["source"], None), (row["source"], "21"),
            (row["source"], "2026-10-21"), (row["source"], "October"),
            ("Starting November 2, Jordan uses TeaDB. Alex uses TeaDB on October 21.", "October 21"),
            (row["source"].replace("October 21", "10/11"), "10/11"),
            (row["source"] + " It changes on 11/01.", "October 21"),
        ):
            with pytest.raises(PrivacyDenied, match="privacy_evidence_ambiguous"):
                validate_private_grounding(extraction(text, reference), text, reference_time=NOW,
                                           resolve_date=IngestionPipeline._resolve_temporal)


def test_unknown_polarity_and_missing_condition_do_not_become_truth():
    row = FIXTURE["qualifiers"][0]
    node = node_for(row)
    for change in ({"polarity": "unknown"}, {"condition": None}):
        metadata = {**node.metadata, **change}
        with pytest.raises(PrivacyDenied):
            project_admitted_node(node.model_copy(update={"metadata": metadata}))


@pytest.mark.parametrize("change", [
    {"evidence_quote": "Alex raised approximately 12.50 USD."},
    {"quantity": {"value": "1250", "unit": "USD", "source_text": "12.50 USD", "grounding": "object_decimal_v1"}},
])
def test_optional_quantity_rechecks_original_passage_and_exact_admitted_object(change):
    row = FIXTURE["qualifiers"][6]
    node = node_for(row)
    projected = project_admitted_node(node.model_copy(update={"metadata": {**node.metadata, **change}}))
    assert "quantity" not in projected.metadata
    assert projected.content == row["expected"]


async def test_projection_updates_actual_plan_and_both_index_input_collections():
    row = FIXTURE["qualifiers"][0]
    event = Event(id=EVENT_ID, content=row["source"], user_id=FIXTURE["owner"], timestamp=NOW, role="user")
    graph = PlanningGraph(None, event)
    indexes = PlanningIndexes(graph)
    node = node_for(row)
    await graph.create_node(node)
    await indexes.index(str(node.id), node.content, node.user_id)
    await indexes.index(str(node.id), node.content, node.user_id, node.node_type.value, node.scope.value)
    project_admitted_graph(graph, indexes, source_for(row))
    assert graph.nodes[node.id].content == row["expected"]
    assert indexes.vectors[node.id] == row["expected"]
    assert indexes.lexical[node.id].content == row["expected"]


def test_entity_projection_keeps_owner_barrier_without_description_copy():
    node = MemoryNode(
        node_type=NodeType.ENTITY, content="Alex", user_id=FIXTURE["owner"],
        metadata={"entity_type": "person", "description": FIXTURE["source_only_marker"],
                  "owner_name": {"event_id": str(EVENT_ID), "basis": "speaker"}},
    )
    projected = project_admitted_node(node)
    assert projected.metadata["owner_name"] == node.metadata["owner_name"]
    assert FIXTURE["source_only_marker"] not in projected.model_dump_json()


def test_direct_text_and_explicit_operational_binding_remain_admitted():
    node = MemoryNode(
        node_type=NodeType.NOTE, content=FIXTURE["admitted_marker"], user_id=FIXTURE["owner"],
        metadata={"unclassified_source": FIXTURE["source_only_marker"],
                  "prme_value_bindings_v1": [{"reference": "value", "kind": "fixture",
                                            "presentation": FIXTURE["admitted_marker"], "lookup": "opaque-destination"}]},
    )
    projected = project_direct_node(node)
    assert projected.content == FIXTURE["admitted_marker"]
    assert projected.metadata["prme_value_bindings_v1"][0]["lookup"] == "opaque-destination"
    assert FIXTURE["source_only_marker"] not in projected.model_dump_json()
