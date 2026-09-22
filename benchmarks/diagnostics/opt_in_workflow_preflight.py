"""Authored live Jev workflow and temporal-protocol readiness, outside the matrix."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from dotenv import dotenv_values

from benchmarks.diagnostics.register_opt_in_interactions import (
    canonical, clean_config, file_sha, sha, write_new,
)
from benchmarks.integrations.run_longmemeval_s_baseline import _tree_identity
from prme import MemoryEngine, PRMEConfig, NodeType
from prme.integrations.typesafe import JevProductAdvisorConfig
from prme.retrieval.temporal_relation_providers import (
    JevTemporalRelationGate, OllamaTemporalResolver,
)
from prme.retrieval.temporal_relations import (
    EvidenceRecord, TemporalRelationConfig, TemporalRelation, ValidatedOperand,
)
from uuid import UUID


def registration():
    same = {"name": "Adobe Acrobat Standard 7.0 Windows", "manufacturer": "Adobe", "price": "299.00"}
    return {
        "kind": "opt-in-explicit-workflow-preflight-registration",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "runner_sha256": file_sha(Path(__file__)),
        "production_revision": "a66ee854325890c6bc28f6b515efeb6ed7df4deb",
        "claim_boundary": "Three authored caller-selected software-product pairs and one authored temporal provider case. Operational checks only, no held-out accuracy or precision claim.",
        "pairs": [
            {"id": "accept", "left": same, "right": same, "review": "accepted", "expected_proposal": True},
            {"id": "reject", "left": same, "right": same, "review": "rejected", "expected_proposal": True},
            {"id": "different", "left": same, "right": {"name": "Microsoft Windows 11 Home", "manufacturer": "Microsoft", "price": "139.00"}, "review": None, "expected_proposal": False},
        ],
        "pair_selection": "Explicit fixed pairs above; no candidate enumeration, bulk proposals, or auto merge.",
        "reviewer": "benchmark:authored-source-review",
        "review_policy": "Accept exact duplicate product fields in accept fixture only; deliberately reject the second duplicate to exercise rejection. Never pretend this is a human review or held-out semantic judgment.",
        "temporal": {
            "query": "How many days passed between starting and finishing the frame?",
            "question_time": "2026-01-10T00:00:00+00:00",
            "records": [
                {"id": "00000000-0000-0000-0000-000000000001", "event_time": "2026-01-01T00:00:00+00:00", "text": "I started the frame on January 1, 2026."},
                {"id": "00000000-0000-0000-0000-000000000002", "event_time": "2026-01-05T00:00:00+00:00", "text": "I finished the frame on January 5, 2026."},
            ],
            "config": TemporalRelationConfig(enabled=True).model_dump(mode="json"),
            "gate_inputs": "Fixed authored event/date operands; no benchmark question, reference answer or calculated value sent to the gate.",
        },
        "failure_policy": "Keep all attempts and complete the fixed independent preflight cases; no retries outside the product's configured limits, no substitutions, no dataset execution if any prerequisite fails.",
    }


async def run(reg, output_dir, env_file):
    output_dir.mkdir(parents=True, exist_ok=False)
    key = os.environ.get("JEV_API_KEY") or dotenv_values(env_file).get("JEV_API_KEY")
    if not key:
        raise ValueError("Jev credential unavailable")
    rows = []
    for pair in reg["pairs"]:
        started = time.perf_counter()
        pack = output_dir / pair["id"]
        pack.mkdir()
        data = clean_config()
        data.update(db_path=str(pack / "memory.duckdb"), vector_path=str(pack / "vectors.usearch"), lexical_path=str(pack / "lexical_index"))
        data["organizer"]["opportunistic_enabled"] = False
        config = PRMEConfig(_env_file=None, **data)
        row = {"id": pair["id"], "status": "failed", "config_sha256": sha(data)}
        try:
            async with MemoryEngine.open(config) as engine:
                ids = []
                for product in (pair["left"], pair["right"]):
                    receipt = await engine.store_with_receipt(product["name"], user_id=pair["id"], node_type=NodeType.ENTITY, metadata={"entity_type": "product"})
                    ids.append(str(receipt.node_id))
                proposal = await engine.propose_product_alignment(*ids, pair["left"], pair["right"], user_id=pair["id"], config=JevProductAdvisorConfig(api_key=key))
                row["proposal"] = proposal.model_dump(mode="json")
                if proposal.proposal_published != pair["expected_proposal"]:
                    raise ValueError("Authored proposal expectation failed")
                if await engine._graph_store.find_shortest_path(*ids) is not None:
                    raise ValueError("Unreviewed proposal became traversable")
                row["inert_before_review"] = True
                if pair["review"] is not None:
                    reason = "Exact authored fields agree." if pair["review"] == "accepted" else "Authored rejection-path control; retain separate identities."
                    review = await engine.review_alias_proposal(proposal.proposal_operation_id, user_id=pair["id"], decision=pair["review"], reviewer_id=reg["reviewer"], reason=reason)
                    row["review"] = review.model_dump(mode="json")
                    repeat = await engine.review_alias_proposal(proposal.proposal_operation_id, user_id=pair["id"], decision=pair["review"], reviewer_id=reg["reviewer"], reason=reason)
                    if repeat.applied or repeat.operation_id != review.operation_id:
                        raise ValueError("Review retry was not idempotent")
                active = await engine.query_nodes(user_id=pair["id"], node_type=NodeType.ENTITY)
                if {str(node.id) for node in active} != set(ids):
                    raise ValueError("Explicit review retired an entity")
            async with MemoryEngine.open(config) as engine:
                connected = await engine._graph_store.find_shortest_path(*ids) is not None
                if connected != (pair["review"] == "accepted"):
                    raise ValueError("Restart traversal differs from explicit decision")
                row["inbox_after_restart"] = [x.model_dump(mode="json") for x in await engine.list_alias_proposals(user_id=pair["id"])]
                if await engine.list_alias_proposals(user_id="other-owner"):
                    raise ValueError("Cross-owner inbox disclosure")
            row.update(status="passed", both_entities_retained=True, restart_verified=True)
        except Exception as exc:
            row["exception_type"] = type(exc).__name__
        row["elapsed_seconds"] = time.perf_counter() - started
        row["pack"] = _tree_identity(pack)
        write_new(output_dir / f"{pair['id']}-result.json", row)
        rows.append(row)
    temporal = {"status": "failed"}
    try:
        spec = reg["temporal"]
        config = TemporalRelationConfig(**{**spec["config"], "gate_api_key": key})
        records = tuple(EvidenceRecord.model_validate(r) for r in spec["records"])
        resolved = await OllamaTemporalResolver(config).resolve(spec["query"], datetime.fromisoformat(spec["question_time"]), records)
        temporal["resolver"] = resolved.model_dump(mode="json")
        # Gate a fixed source-backed operand set so the preflight actually calls Jev
        # even if the answer-blind resolver legitimately returns unsupported.
        operands = tuple(ValidatedOperand(
            name=name, evidence_id=record.id, quote=record.text,
            time_expression=date, time_basis="text_expression",
            resolved_time=record.event_time,
        ) for record, name, date in zip(records, ("starting the frame", "finishing the frame"), ("January 1, 2026", "January 5, 2026")))
        relation = TemporalRelation(operation="elapsed_between", operands=operands, value="4 days", guidance="Authored preflight only")
        gate = await JevTemporalRelationGate(config).assess(spec["query"], relation)
        temporal["gate"] = gate.model_dump(mode="json")
        temporal["status"] = "passed"
    except Exception as exc:
        temporal["exception_type"] = type(exc).__name__
    result = {"kind": "opt-in-explicit-workflow-preflight-result", "registration_sha256": sha(reg), "dataset_calls": 0, "rows": rows, "temporal": temporal,
              "complete": True, "passed": all(r["status"] == "passed" for r in rows) and temporal["status"] == "passed"}
    write_new(output_dir / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["register", "run"])
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if args.command == "register":
        write_new(args.registration, registration())
    else:
        reg = json.loads(args.registration.read_text())
        if reg["runner_sha256"] != file_sha(Path(__file__)):
            raise ValueError("Runner changed after registration")
        result = asyncio.run(run(reg, args.output_dir, args.env_file))
        print(json.dumps({"complete": result["complete"], "passed": result["passed"], "row_status": [r["status"] for r in result["rows"]], "temporal_status": result["temporal"]["status"]}))
        if not result["passed"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
