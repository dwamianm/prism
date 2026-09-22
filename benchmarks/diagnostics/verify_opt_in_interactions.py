"""Authenticate retained study artifacts without scoring an incomplete matrix."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import duckdb

from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new
from benchmarks.diagnostics.run_opt_in_interactions import MODEL, validate_registration
from benchmarks.integrations import run_longmemeval_s_baseline as base
from prme.retrieval.tokenization import count_tokens
from prme.storage.relevance import RelevanceRepository


def verify(args):
    root = Path.cwd()
    reg = json.loads(args.registration.read_text())
    execution = json.loads((args.execution_dir / "execution.json").read_text())
    cases = base._load_dataset(args.dataset)
    validate_registration(reg, root, cases)
    assert execution["registration_sha256"] == reg["registration_sha256"]
    assert file_sha(args.dataset) == base.DATASET_SHA256
    expected = [case["question_id"] for case in cases]
    lookup = {case["question_id"]: case for case in cases}
    official_prompt = base._load_official_prompt_function(args.official_root)
    artifacts = []
    receipt_count = 0
    provider_usage = Counter()
    provider_failure_rows = []
    for arm, saved_arm in execution["arms"].items():
        assert [row["question_id"] for row in saved_arm["rows"]] == expected
        for row in saved_arm["rows"]:
            folder = args.execution_dir / arm / row["question_id"]
            if row["status"] == "not_started":
                assert not folder.exists()
                continue
            assert json.loads((folder / "result.json").read_text()) == row
            case = lookup[row["question_id"]]
            capture_path = folder / "capture.json"
            capture = json.loads(capture_path.read_text())
            assert base._tree_identity(folder / "pack") == capture["final_pack"]
            with duckdb.connect(str(folder / "pack/memory.duckdb"), read_only=True) as connection:
                for retrieval in capture["retrievals"]:
                    request_id = retrieval["metadata"]["request_id"]
                    rows = connection.execute(
                        "SELECT payload FROM operations WHERE op_type='RETRIEVAL_REQUEST' "
                        "AND target_id=? AND actor_id=?", [request_id, base.USER_ID],
                    ).fetchall()
                    assert len(rows) == 1
                    receipt = RelevanceRepository._read_receipt(rows[0][0], request_id=request_id, user_id=base.USER_ID)
                    assert receipt.model_dump(mode="json") == retrieval["receipt"]
                    digest = hashlib.sha256(retrieval["context"].encode()).hexdigest()
                    assert digest == retrieval["context_sha256"] == receipt.context_sha256
                    assert count_tokens(retrieval["context"], "cl100k_base") == retrieval["context_tokens"] <= 3996
                    assert tuple(str(x) for x in receipt.replay_ranking()) == tuple(r["node_id"] for r in retrieval["returned"])
                    receipt_count += 1
            reader = None
            for role, limit in (("reader", 1024), ("judge", 64)):
                path = folder / f"{role}.json"
                if not path.exists():
                    assert row["status"] == "failed"
                    continue
                value = json.loads(path.read_text())
                assert value["request_sha256"] == sha(value["request"])
                request = value["request"]
                assert request["model"] == MODEL and request["think"] is False and request["stream"] is False
                assert request["options"] == {"temperature": 0, "seed": 42, "num_ctx": 65536, "num_predict": limit}
                if role == "reader":
                    expected_prompt = base._reader_prompt(capture["retrievals"][0]["context"], case["question_date"], case["question"])
                    reader = value
                else:
                    expected_prompt = official_prompt(case["question_type"], case["question"], case["answer"],
                        reader["response"]["message"]["content"].strip(), abstention=case["question_id"].endswith("_abs"))
                assert request["messages"] == [{"role": "user", "content": expected_prompt}]
                provider_usage[f"{role}_logical_calls"] += 1
                provider_usage["http_attempts"] += len(value["attempts"])
                provider_usage["transient_http_errors"] += sum(a["http_status"] >= 400 for a in value["attempts"])
                response = value.get("response", {})
                provider_usage["reported_input_tokens"] += response.get("prompt_eval_count", 0)
                provider_usage["reported_output_tokens"] += response.get("eval_count", 0)
                if value["status"] == "complete":
                    assert response["done"] and response["done_reason"] == "stop"
                    assert file_sha(path) == row[f"{role}_sha256"]
                    if role == "judge":
                        # Authenticate the saved official-rule verdict, but never
                        # aggregate or publish an incomplete answer score.
                        assert row["correct"] == ("yes" in response["message"]["content"].lower())
                else:
                    provider_failure_rows.append({"arm": arm, "question_id": row["question_id"], "role": role,
                        "done_reason": response.get("done_reason"), "output_tokens": response.get("eval_count"),
                        "artifact_sha256": file_sha(path), "attempts": value["attempts"]})
            if row["status"] == "complete":
                assert file_sha(capture_path) == row["capture_sha256"]
            artifacts.append({"arm": arm, "question_id": row["question_id"], "status": row["status"],
                "capture_sha256": file_sha(capture_path), "input_pack_sha256": capture["input_pack"]["tree_sha256"],
                "final_pack_sha256": capture["final_pack"]["tree_sha256"], "result_sha256": file_sha(folder / "result.json")})
    # Authenticate all 500 masters again after execution, including unused ones.
    identity = json.loads((args.master_root / "identity.json").read_text())
    master_rows = []
    for case in cases:
        qid = case["question_id"]
        saved = json.loads((args.master_root / "captures" / f"{qid}.json").read_text())
        base._validate_saved_capture(saved, identity={"registration_sha256": identity["registration_sha256"],
            "case_sha256": base._case_identity(case)}, pack_path=args.master_root / "packs" / qid)
        master_rows.append({"question_id": qid, "pack_sha256": saved["pack"]["tree_sha256"]})
    result = {"kind": "opt-in-interactions-artifact-verification", "benchmark_complete": execution["complete"],
        "artifact_verification_complete": True, "registration_sha256": reg["registration_sha256"],
        "execution_sha256": file_sha(args.execution_dir / "execution.json"),
        "verifier_sha256": file_sha(Path(__file__)),
        "retained_case_artifacts": len(artifacts), "authenticated_receipts": receipt_count,
        "unchanged_master_packs": len(master_rows), "master_manifest_sha256": sha(master_rows),
        "retained_artifacts": artifacts, "retained_manifest_sha256": sha(artifacts),
        "provider_failure_rows": provider_failure_rows,
        "partial_operational_usage": dict(provider_usage),
        "answer_scores_computed": False, "paired_confidence_intervals_computed": False,
        "boundary": "Authenticates retained artifacts and consumed resources only; incomplete execution is not an answer-quality result."}
    write_new(args.output, result)
    print(json.dumps({k: v for k, v in result.items() if k not in {"retained_artifacts", "provider_failure_rows"}}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "execution-dir", "dataset", "master-root", "official-root", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    verify(parser.parse_args())


if __name__ == "__main__":
    main()
