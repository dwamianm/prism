"""Validate a completed PersonaMem pilot before joining predictions to labels."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import importlib
import inspect
import json
from pathlib import Path
import random
from statistics import mean

import duckdb
import prme
from prme.epistemic.inference import infer_source_type
from prme.models.relevance import RetrievalReceipt, make_receipt
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.storage.relevance import RelevanceRepository
from prme.types import NodeType, Scope

from benchmarks.diagnostics.packing_length import pack_length
from benchmarks.diagnostics.packing_reader import canonical, digest
from benchmarks.diagnostics.personamem_packing import (
    ALPHAS, ARMS, FORMAT, OPTIONS, SYSTEM, parse_response, reader_payload,
)
from benchmarks.diagnostics.product_packing import measure
from benchmarks.personamem import (
    cohort_identity, conversation_sources, history_path, question_and_reference,
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def complete_result(result, plan, plan_raw):
    """Reject partial or failed runs before accessing any prediction values."""
    require(result.get("complete") is True and result.get("errors") == 0
            and result.get("process_exit_code") == 0, "A complete native-exit-zero run is required")
    require(result["plan_sha256"] == digest(plan_raw), "Plan checksum changed")
    require(plan["registered_at"] < result["started_at"], "Study predates registration")
    ids = plan["cohort"]["selected_question_ids"]
    require([r["question_id"] for r in result["details"]] == ids, "Question coverage changed")
    require(len(ids) == len(set(ids)) == 96, "Expected exactly 96 unique questions")
    require(all(set(r["arms"]) == set(ARMS) for r in result["details"]), "Arm coverage changed")


def verify_code(plan):
    root = Path(prme.__file__).resolve().parent
    hashes = {str(p.relative_to(root)): digest(p.read_bytes()) for p in sorted(root.rglob("*.py"))}
    require(hashes == plan["runtime"]["source_sha256"], "Use the registered PRME source runtime")
    for name, expected in plan["runtime"]["harness_sha256"].items():
        module = importlib.import_module("benchmarks.diagnostics.personamem_packing" if name == "__main__" else name)
        require(digest(Path(inspect.getfile(module)).read_bytes()) == expected, "Registered harness changed")
    require(plan["arms"] == list(ARMS) and plan["budget"] == 4096, "Protocol arms or budget changed")
    require(plan["reader"]["options"] == OPTIONS and plan["reader"]["system"] == SYSTEM
            and plan["reader"]["format"] == FORMAT, "Reader contract changed")


def validate_source(node, turn):
    require(node.content == turn.content and node.metadata == {"source_turn": turn.id}
            and node.user_id == "evaluation" and node.scope == Scope.PERSONAL
            and node.node_type == NodeType.NOTE and node.session_id is None and node.event_time is None
            and node.source_type == infer_source_type(NodeType.NOTE, turn.role), "Source fidelity changed")


def verify_pack(path, turns, captures):
    """Read durable sources and receipts directly, without opening/migrating an engine."""
    before = digest(path.read_bytes())
    sources = {t.id: t for t in turns}
    with duckdb.connect(str(path), read_only=True) as conn:
        events = conn.execute("SELECT id, content, role, user_id, scope, session_id, event_time, metadata FROM events").fetchall()
        require(len(events) == len(sources), "Durable event coverage changed")
        event_by_id, seen = {}, set()
        for eid, content, role, owner, scope, session, event_time, metadata in events:
            meta = json.loads(metadata)
            turn = sources[meta["source_turn"]]
            require(meta == {"source_turn": turn.id} and turn.id not in seen and content == turn.content
                    and role == turn.role and owner == "evaluation" and scope == "personal"
                    and session is None and event_time is None, "Durable event fidelity changed")
            seen.add(turn.id)
            event_by_id[str(eid)] = turn
        nodes = conn.execute("SELECT id, content, user_id, scope, session_id, event_time, metadata, node_type, source_type, evidence_refs FROM nodes").fetchall()
        require(len(nodes) == len(sources), "Durable graph coverage changed")
        nodes_by_id, seen = {}, set()
        for nid, content, owner, scope, session, event_time, metadata, kind, source_type, refs in nodes:
            meta = json.loads(metadata)
            turn = sources[meta["source_turn"]]
            require(meta == {"source_turn": turn.id} and turn.id not in seen and content == turn.content
                    and owner == "evaluation" and scope == "personal" and session is None and event_time is None
                    and kind == "note" and source_type == infer_source_type(NodeType.NOTE, turn.role).value,
                    "Durable graph source fidelity changed")
            evidence = json.loads(refs)
            require(len(evidence) == 1 and event_by_id[str(evidence[0])] == turn, "Durable event provenance changed")
            nodes_by_id[str(nid)] = turn
            seen.add(turn.id)
        for saved in captures.values():
            receipt = RetrievalReceipt.model_validate(saved["receipt"])
            values = conn.execute("SELECT payload FROM operations WHERE op_type = 'RETRIEVAL_REQUEST' AND target_id = ? AND actor_id = ?",
                                  [str(receipt.request_id), "evaluation"]).fetchall()
            require(len(values) == 1, "Missing or ambiguous durable receipt")
            durable = RelevanceRepository._read_receipt(values[0][0], request_id=str(receipt.request_id), user_id="evaluation")
            require(durable == receipt, "Saved receipt differs from durable receipt")
            for value in saved["candidates"]:
                candidate = RetrievalCandidate.model_validate(value)
                validate_source(candidate.node, nodes_by_id[str(candidate.node.id)])
    require(digest(path.read_bytes()) == before, "Read-only verification mutated the pack")
    return before


def cluster_comparison(rows, arm, control, *, samples=2000, seed=42):
    """Paired persona resampling; every question in a sampled persona stays together."""
    groups = {}
    for row in rows:
        groups.setdefault(row["persona_id"], []).append(int(row["correct"][arm]) - int(row["correct"][control]))
    require(bool(groups), "Cannot compare an empty cohort")
    clusters = [groups[key] for key in sorted(groups)]
    rng = random.Random(seed)
    draws = []
    for _ in range(samples):
        selected = [rng.choice(clusters) for _ in clusters]
        draws.append(sum(sum(c) for c in selected) / sum(len(c) for c in selected))
    draws.sort()
    return {"arm": arm, "control": control, "questions": len(rows), "personas": len(groups),
            "delta_accuracy": mean([v for group in clusters for v in group]),
            "ci95_percentile": [draws[int(.025 * samples)], draws[min(int(.975 * samples), samples - 1)]],
            "bootstrap_samples": samples, "seed": seed}


def summarize(rows):
    def metrics(group):
        return {arm: {"correct": sum(r["correct"][arm] for r in group), "total": len(group),
                      "accuracy": mean(r["correct"][arm] for r in group),
                      "invalid_answers": sum(r["answers"][arm] is None for r in group)} for arm in ARMS}
    categories = {}
    for field in ("pref_type", "who", "updated", "conversation_scenario"):
        categories[field] = {value: metrics([r for r in rows if r[field] == value]) for value in sorted({r[field] for r in rows})}
    return {"arms": metrics(rows), "categories": categories,
            "primary": cluster_comparison(rows, "quarter", "density"),
            "secondary": [cluster_comparison(rows, arm, control) for arm, control in
                          (("score", "density"), ("quarter", "score"), ("density", "no_memory"),
                           ("quarter", "no_memory"), ("score", "no_memory"))]}


def verify(plan_path, result_path, data, artifacts):
    plan_raw, result_raw = plan_path.read_bytes(), result_path.read_bytes()
    plan, result = json.loads(plan_raw), json.loads(result_raw)
    complete_result(result, plan, plan_raw)
    verify_code(plan)
    selected, identity = cohort_identity(data / "benchmark/text/benchmark.csv", data)
    require(identity == plan["cohort"], "Dataset/cohort changed")
    require(json.loads((data / "dataset-identity.json").read_bytes()) == plan["dataset"], "Dataset revision changed")
    prepared_raw, references_raw = (artifacts / "prepared.json").read_bytes(), (artifacts / "references.json").read_bytes()
    require(digest(prepared_raw) == result["prepared_sha256"] and digest(references_raw) == result["references_sha256"],
            "Prepared artifacts changed")
    prepared, references = json.loads(prepared_raw), json.loads(references_raw)
    pairs = [question_and_reference(row) for row in selected]
    require([r["question"] for r in prepared] == json.loads(canonical([asdict(q) for q, _ in pairs])), "Reader questions changed")
    require(references == [ref for _, ref in pairs], "Reference labels changed")
    require([r["persona_id"] for r in result["captures"]] == identity["selected_persona_ids"], "Persona coverage changed")
    prepared_by_id = {r["question"]["id"]: r for r in prepared}
    pack_hashes, context_count = {}, 0
    config = PackingConfig.model_validate(plan["runtime"]["provenance"]["engine_config"]["packing"])
    for index, capture_ref in enumerate(result["captures"]):
        require(capture_ref["path"] == f"capture-{index:03d}.json", "Capture path changed")
        raw = (artifacts / capture_ref["path"]).read_bytes()
        require(digest(raw) == capture_ref["sha256"], "Capture checksum changed")
        saved = json.loads(raw)
        rows = [row for row in selected if row["persona_id"] == capture_ref["persona_id"]]
        turns = conversation_sources((data / history_path(rows[0])).read_bytes())
        sources = {t.id: t for t in turns}
        require(saved["source_count"] == len(turns) and saved["sources_sha256"] == digest(canonical([vars(t) for t in turns])),
                "Captured source identity changed")
        require(saved["source_memories_unchanged"] is True and saved["repeated_control_identical"] is True
                and saved["extraction_calls"] == 0, "Capture invariants failed")
        questions = [question_and_reference(r)[0] for r in rows]
        require(set(saved["captures"]) == {q.id for q in questions}, "Captured question coverage changed")
        for question in questions:
            capture = saved["captures"][question.id]
            contexts = capture["contexts"]
            require(contexts == prepared_by_id[question.id]["contexts"] and set(contexts) == set(ARMS), "Prepared contexts changed")
            require(capture["candidate_sha256"] == digest(canonical(capture["candidates"])), "Candidates changed")
            candidates = [RetrievalCandidate.model_validate(v) for v in capture["candidates"]]
            for candidate in candidates:
                validate_source(candidate.node, sources[candidate.node.metadata["source_turn"]])
            receipt = RetrievalReceipt.model_validate(capture["receipt"])
            require(receipt.query == question.query and receipt.user_id == "evaluation" and receipt.scopes == (Scope.PERSONAL,)
                    and receipt.reference_time == datetime.fromisoformat(saved["reference_time"])
                    and receipt.packing == config, "Receipt request changed")
            require(receipt.replay_ranking() == tuple(c.node.id for c in candidates), "Candidate ranking changed")
            before = canonical([c.model_dump(mode="json") for c in candidates])
            for arm, alpha in ALPHAS.items():
                bundle = pack_length(candidates, config, alpha)
                expected = {"context": bundle.render(), "sha256": digest(bundle.render().encode()),
                            "tokens": bundle.tokens_used, "measurement": measure(bundle, set(), config)}
                require(contexts[arm] == expected, "Context or accounting did not reproduce")
                if arm == "density":
                    rebuilt = make_receipt(request_id=receipt.request_id, user_id=receipt.user_id, query=receipt.query,
                        reference_time=receipt.reference_time, scopes=receipt.scopes, scoring=receipt.scoring,
                        packing=config, candidates=candidates, bundle=bundle, min_score=receipt.min_score,
                        result_limit=receipt.result_limit, retrieval_mode=receipt.retrieval_mode,
                        time_from=receipt.time_from, time_to=receipt.time_to,
                        ranking_policy=receipt.ranking_policy, execution=receipt.execution)
                    require(rebuilt == receipt, "Product receipt did not reproduce")
                context_count += 1
            require(canonical([c.model_dump(mode="json") for c in candidates]) == before, "Repacking mutated candidates")
            require(contexts["no_memory"] == {"context": "", "sha256": digest(b""), "tokens": 0}, "No-memory control changed")
        pack = artifacts / f"pack-{index:03d}" / "memory.duckdb"
        pack_hashes[str(index)] = verify_pack(pack, turns, saved["captures"])
        print(f"Verified {index + 1}/24 persona captures and durable packs", flush=True)

    predictions = {}
    for detail, prepared_row in zip(result["details"], prepared, strict=True):
        question = prepared_row["question"]
        require(detail["persona_id"] == question["persona_id"], "Prediction owner changed")
        order = sorted(ARMS, key=lambda arm: digest(canonical([question["id"], arm])))
        require(detail["arm_order"] == order, "Reader order changed")
        answers = {}
        for arm, saved in detail["arms"].items():
            context = prepared_row["contexts"][arm]
            body = reader_payload(question, context["context"], plan["reader"]["model"])
            require(saved["request_sha256"] == digest(canonical(body)) and saved["context_sha256"] == context["sha256"],
                    "Reader request changed")
            require(saved["response"]["model"] == plan["reader"]["model"], "Reader response model changed")
            answer = parse_response(saved["response"])
            require(answer == saved["answer"], "Saved answer differs from raw response")
            answers[arm] = answer
        predictions[question["id"]] = answers
    repeats = result["reader_repeats"]
    require([r["question_id"] for r in repeats] == identity["selected_question_ids"][::4], "Reader repeat coverage changed")
    disagreements = []
    for repeat in repeats:
        qid = repeat["question_id"]
        expected_arm = sorted(ARMS, key=lambda arm: digest(canonical([qid, arm])))[0]
        require(repeat["arm"] == expected_arm and repeat["response"]["model"] == plan["reader"]["model"], "Repeat identity changed")
        same = parse_response(repeat["response"]) == predictions[qid][expected_arm]
        require(repeat["same_answer"] is same, "Repeat comparison changed")
        if not same:
            disagreements.append({"question_id": qid, "arm": expected_arm})
    # Correctness is first computed here, after completion and all validation.
    scored = [{**ref, "correct_answer": ref["correct"], "answers": predictions[ref["question_id"]],
               "correct": {arm: predictions[ref["question_id"]][arm] == ref["correct"] for arm in ARMS}}
              for ref in references]
    return {"source_output_sha256": digest(result_raw), "plan_sha256": digest(plan_raw),
            "study_commit": plan["runtime"]["provenance"]["commit"], "native_study_exit_code": result["process_exit_code"],
            "questions": len(scored), "personas": len(pack_hashes), "contexts_reproduced": context_count,
            "raw_responses_verified": 4 * len(scored), "durable_pack_sha256": pack_hashes,
            "reader_repeats": {"total": len(repeats), "disagreements": disagreements,
                               "invalid_answers": sum(parse_response(r["response"]) is None for r in repeats)},
            "comparison": summarize(scored), "details": scored, "limits": plan["limits"],
            "default_promotion": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "result", "data", "artifacts", "verification"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    require(not args.verification.exists(), "Refusing to overwrite previous verification")
    report = verify(args.plan, args.result, args.data, args.artifacts)
    report["verifier_sha256"] = digest(Path(__file__).read_bytes())
    args.verification.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
