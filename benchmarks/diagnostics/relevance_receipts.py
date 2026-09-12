"""Real-embedding public-client relevance persistence and retry workflow.

One authored workflow; does not fit weights or measure learned retrieval quality.
"""
import argparse
import hashlib
import inspect
import json
from pathlib import Path
import sys
import tempfile
import time

from prme import MemoryClient, PRMEConfig, RelevanceSubmission
from prme.storage.relevance import RelevanceRepository
from benchmarks.diagnostics._process import checked_report


def run():
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="prme-live-relevance-") as directory:
        root = Path(directory)
        config = PRMEConfig(database_url=None, encryption_enabled=False,
            db_path=str(root / "memory.duckdb"), vector_path=str(root / "vectors.usearch"),
            lexical_path=str(root / "lexical"), organizer={"opportunistic_enabled": False},
            embedding={"provider": "fastembed", "model_name": "BAAI/bge-small-en-v1.5", "dimension": 384, "api_key": None},
            extraction={"provider": "ollama", "model": "unused"})
        with MemoryClient(config=config) as client:
            client.store("The Aster service uses PostgreSQL.", user_id="alice")
            client.store("The Boreal service uses Redis.", user_id="bob")
            response = client.retrieve("What database does Aster use?", user_id="alice", min_score=0)
            assert response.metadata.receipt_persisted
            receipt = client.get_retrieval_receipt(str(response.metadata.request_id), user_id="alice")
            assert receipt.replay_ranking() == tuple(c.node.id for c in response.results)
            node = next(n for n in response.results if "PostgreSQL" in n.node.content)
            submission = RelevanceSubmission(request_id=receipt.request_id, labels={node.node.id: True},
                                             method="structured_evaluation")
            record = client.record_relevance(submission, user_id="alice")
            learning = client.evaluate_learning(user_id="alice")
            assert learning.decision == "insufficient_data"
            assert learning.feedback_ids == (record.feedback_id,)
            assert client.record_relevance(submission, user_id="alice") == record
            assert client.get_retrieval_receipt(str(receipt.request_id), user_id="bob") is None
            assert client.list_relevance(user_id="bob") == []
            client.archive(str(node.node.id), user_id="alice")
        with MemoryClient(config=config) as client:
            restored = client.get_retrieval_receipt(str(receipt.request_id), user_id="alice")
            assert restored == receipt
            assert restored.replay_ranking() == tuple(c.node.id for c in response.results)
            assert client.get_relevance(str(record.feedback_id), user_id="alice") == record
            assert client.record_relevance(submission, user_id="alice") == record
            assert client.list_relevance(user_id="alice") == [record]
            assert client.evaluate_learning(user_id="alice") == learning
            assert client._engine._config.scoring.version_id == receipt.scoring.version_id
        return {"passed": True, "embedding_model": "BAAI/bge-small-en-v1.5",
                "candidate_count": len(receipt.candidates), "receipt_bytes": len(receipt.model_dump_json().encode()),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "repository_sha256": hashlib.sha256(Path(inspect.getfile(RelevanceRepository)).read_bytes()).hexdigest(),
                "package_path": str(Path(inspect.getfile(RelevanceRepository)).resolve()),
                "checks": ["real embeddings through public sync client", "returned candidate snapshot and explicit labels",
                           "owner isolation", "retry identity survives archival and restart", "collection leaves weights unchanged",
                           "exact ranking replay before and after archival and restart",
                           "public learning evaluation rejects insufficient evidence and reproduces after restart"],
                "limits": "One authored persistence workflow; no learned profile or retrieval accuracy claim."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        report = run() if args.worker else checked_report(
            [sys.executable, "-m", "benchmarks.diagnostics.relevance_receipts", "--worker"], timeout=180)
    except Exception as exc:
        report = {"passed": False, "error_type": type(exc).__name__, "limits": "Incomplete workflow."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    if not args.worker:
        print(json.dumps(report))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
