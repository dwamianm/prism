"""Verify a complete registered hybrid study and reproduce every saved context."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.diagnostics.hybrid_lexical import summarize
from benchmarks.diagnostics.packing_reader import canonical, digest
from benchmarks.diagnostics.product_packing import measure
from benchmarks.evidence import longmemeval_sources, select_questions
from benchmarks.longmemeval import _parse_haystack_date
from prme.epistemic.inference import infer_source_type
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context
from prme.types import NodeType, Scope


def verify(plan_path: Path, output: Path, dataset: Path, snapshots: Path):
    plan = json.loads(plan_path.read_bytes())
    result = json.loads(output.read_bytes())
    assert (
        result["complete"]
        and result["errors"] == 0
        and result["process_exit_code"] == 0
    )
    assert result["plan_sha256"] == digest(plan_path.read_bytes())
    assert result["provenance"] == plan["provenance"]
    assert result["embedding_identity"] == plan["embedding_identity"]
    assert plan["registered_at"] < result["started_at"]
    assert digest(dataset.read_bytes()) == plan["dataset"]["sha256"]
    data = json.loads(dataset.read_bytes())
    by_id = {q["question_id"]: q for q in data}
    assert len(by_id) == len(data)
    selected = plan["dataset"]["selected_question_ids"]
    assert selected == [
        q["question_id"]
        for q in select_questions(data, split="dev", seed=plan["dataset"]["split_seed"])
    ]
    assert [row["question_id"] for row in result["details"]] == selected
    expected_arms = {
        f"{p}:{o}:{b}"
        for p in plan["policies"]
        for o in plan["orders"]
        for b in plan["budgets"]
    }
    checked, snapshot_refs = 0, []
    for row in result["details"]:
        qid = row["question_id"]
        question = by_id[qid]
        turns, gold = longmemeval_sources(question)
        sources = {t.id: t for t in turns}
        assert row["source_count"] == len(sources) == len(turns)
        assert row["evidence_source_ids"] == sorted(gold)
        assert row["category"] == question["question_type"]
        ref = row["snapshot"]
        assert ref["filename"] == digest(qid.encode()) + ".json"
        raw = (snapshots / ref["filename"]).read_bytes()
        assert digest(raw) == ref["sha256"]
        snapshot_refs.append(ref)
        saved = json.loads(raw)
        assert saved["question_id"] == qid
        assert saved["sources_sha256"] == digest(canonical([vars(t) for t in turns]))
        assert (
            saved["repeated_control_identical"] and saved["source_memories_unchanged"]
        )
        assert saved["extraction_calls"] == 0
        assert set(saved["arms"]) == set(row["arms"]) == expected_arms
        assert len(saved["arm_order"]) == len(expected_arms)
        assert {f"{p}:{o}:{b}" for p, o, b in saved["arm_order"]} == expected_arms
        candidates = {}
        for policy, values in saved["candidates"].items():
            candidates[policy] = [RetrievalCandidate.model_validate(v) for v in values]
            for c in candidates[policy]:
                turn = sources[c.node.metadata["source_turn"]]
                assert (
                    c.node.content == turn.content
                    and c.node.session_id == turn.session_id
                )
                assert c.node.user_id == "evaluation" and c.node.scope == Scope.PERSONAL
                assert c.node.node_type == NodeType.NOTE and c.node.metadata == {
                    "source_turn": turn.id
                }
                assert c.node.source_type == infer_source_type(NodeType.NOTE, turn.role)
                assert c.node.event_time == (
                    _parse_haystack_date(turn.date) if turn.date else None
                )
        assert set(candidates) == set(plan["policies"])
        for key, arm in saved["arms"].items():
            policy, order, budget = key.split(":")
            expected_config = {
                **plan["provenance"]["engine_config"]["packing"],
                "multipath_ordering": order,
                "token_budget": int(budget),
            }
            assert arm["packing"] == expected_config
            config = PackingConfig.model_validate(arm["packing"])
            assert arm["candidate_sha256"] == digest(
                canonical(saved["candidates"][policy])
            )
            feature = arm["execution"]["features"]["experimental_lexical_query"]
            assert feature == {
                "policy": policy,
                "stopwords_sha256": plan["stopwords_sha256"]
                if policy != "parser"
                else None,
                "harness_sha256": plan["runner_sha256"],
            }
            before = canonical([c.model_dump(mode="json") for c in candidates[policy]])
            bundle = pack_context(candidates[policy], config)
            assert bundle.render() == arm["context"]
            assert measure(bundle, set(), config) == arm["measurement"]
            assert measure(bundle, gold, config) == row["arms"][key]
            assert (
                canonical([c.model_dump(mode="json") for c in candidates[policy]])
                == before
            )
            checked += 1
        print(
            f"Verified {len(snapshot_refs)}/{len(selected)} saved questions", flush=True
        )
    assert summarize(result["details"]) == result["comparison"]
    return {
        "study_commit": plan["provenance"]["commit"],
        "source_output_sha256": digest(output.read_bytes()),
        "plan_sha256": digest(plan_path.read_bytes()),
        "verified_snapshots_sha256": digest(canonical(snapshot_refs)),
        "questions": len(selected),
        "contexts_reproduced": checked,
        "labelled_questions": sum(
            bool(r["evidence_source_ids"]) for r in result["details"]
        ),
        "native_study_exit_code": result["process_exit_code"],
        "errors": result["errors"],
        "source_model_and_runtime_match_registration": True,
        "all_contexts_and_metrics_reproduced": True,
        "comparison": result["comparison"],
        "limits": result["limits"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["plan", "output", "dataset", "snapshots", "verification"]:
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    assert not args.verification.exists()
    result = verify(args.plan, args.output, args.dataset, args.snapshots)
    result["verifier_sha256"] = digest(Path(__file__).read_bytes())
    args.verification.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
