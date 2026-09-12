"""Reproducible raw-turn retrieval comparisons on LongMemEval.

Run: python -m benchmarks.retrieval_eval --help
This measures source evidence retrieval, not official answer accuracy. Every
method sees the same raw turns; labels and answers never enter the memory pack.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import math
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from prme import MemoryEngine, PRMEConfig
from prme.types import NodeType
from benchmarks.evidence import (
    evidence_metrics, longmemeval_sources, pack_sources,
    reciprocal_rank_fusion, select_questions,
)
from benchmarks.longmemeval import _parse_haystack_date


METHODS = ("prme", "bm25", "vector", "rrf", "recent", "none")


def provenance(config: PRMEConfig) -> dict:
    def git(*args):
        result = subprocess.run(["git", *args], capture_output=True, check=False)
        return result.stdout if result.returncode == 0 else b""

    def redact(value):
        if isinstance(value, dict):
            return {
                k: "<redacted>" if k.endswith("_key") or k in {"password", "database_url"}
                else redact(v) for k, v in value.items()
            }
        if isinstance(value, list):
            return [redact(v) for v in value]
        return value

    # Include untracked code in the working-tree fingerprint. A clean source
    # commit is still required for a publication run.
    worktree = git("diff", "HEAD")
    for name in git("ls-files", "--others", "--exclude-standard").decode().splitlines():
        path = Path(name)
        if path.is_file() and path.suffix in {".py", ".toml", ".lock"}:
            worktree += name.encode() + hashlib.sha256(path.read_bytes()).digest()
    return {
        "commit": git("rev-parse", "HEAD").decode().strip() or None,
        "dirty": bool(worktree),
        "worktree_sha256": hashlib.sha256(worktree).hexdigest(),
        "python": platform.python_version(), "platform": platform.platform(),
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("prme", "duckdb", "usearch", "tantivy", "fastembed", "tiktoken", "dateparser")
        },
        "engine_config": redact(config.model_dump(mode="json")),
    }


async def evaluate_question(
    question: dict, config: PRMEConfig, *, budgets, count_tokens, k: int,
    reference_time: datetime | None = None,
    capture_candidates: Path | None = None,
) -> dict:
    turns, gold = longmemeval_sources(question)
    by_id = {turn.id: turn for turn in turns}
    user_id = "evaluation"
    with tempfile.TemporaryDirectory(prefix="prme-evidence-") as tmp:
        root = Path(tmp)
        (root / "lexical").mkdir()
        local = config.model_copy(update={
            "db_path": str(root / "memory.duckdb"),
            "vector_path": str(root / "vectors.usearch"),
            "lexical_path": str(root / "lexical"),
        })
        async with MemoryEngine.open(local) as engine:
            start = time.perf_counter()
            for turn in turns:
                await engine.store(
                    turn.content, user_id=user_id, role=turn.role,
                    session_id=turn.session_id, node_type=NodeType.NOTE,
                    metadata={"source_turn": turn.id},
                    event_time=_parse_haystack_date(turn.date) if turn.date else None,
                )
            ingestion_ms = (time.perf_counter() - start) * 1000
            nodes = await engine.query_nodes(user_id=user_id, limit=len(turns) + 1)
            node_sources = {
                str(n.id): n.metadata["source_turn"] for n in nodes
                if n.metadata and "source_turn" in n.metadata
            }
            if len(node_sources) != len(turns):
                raise RuntimeError("Raw-turn ingestion did not preserve every source")

            ranked = {}
            latencies = {}
            start = time.perf_counter()
            response = await engine.retrieve(
                question["question"], user_id=user_id, reference_time=reference_time,
            )
            latencies["prme"] = (time.perf_counter() - start) * 1000
            snapshot_ref = None
            if capture_candidates is not None:
                # Record the actual public response before any evaluator uses
                # its evidence labels. Keep raw text out of the summary report.
                snapshot = {
                    "question_id": question["question_id"],
                    "packing_config": local.packing.model_dump(mode="json"),
                    "candidates": [c.model_dump(mode="json") for c in response.results],
                    "control": {"context": response.bundle.render(),
                                "tokens": response.bundle.tokens_used},
                }
                raw_snapshot = json.dumps(snapshot, ensure_ascii=False, allow_nan=False).encode()
                filename = hashlib.sha256(question["question_id"].encode()).hexdigest() + ".json"
                capture_candidates.mkdir(parents=True, exist_ok=True)
                (capture_candidates / filename).write_bytes(raw_snapshot)
                snapshot_ref = {"filename": filename, "sha256": hashlib.sha256(raw_snapshot).hexdigest()}
            ranked["prme"] = [
                node_sources[str(c.node.id)] for c in response.results
                if str(c.node.id) in node_sources
            ][:k]
            for method, index in (("bm25", engine._lexical_index), ("vector", engine._vector_index)):
                start = time.perf_counter()
                if method == "bm25":
                    hits = await index.search(question["question"], user_id, limit=k)
                else:
                    hits = await index.search(question["question"], user_id, k=k)
                latencies[method] = (time.perf_counter() - start) * 1000
                ranked[method] = [node_sources[h["node_id"]] for h in hits if h["node_id"] in node_sources]
            ranked["rrf"] = reciprocal_rank_fusion([ranked["bm25"], ranked["vector"]])[:k]
            latencies["rrf"] = latencies["bm25"] + latencies["vector"]
            ranked["recent"] = [t.id for t in sorted(
                turns, key=lambda t: (
                    _parse_haystack_date(t.date) if t.date else datetime.min.replace(tzinfo=timezone.utc),
                    int(t.id.split(":")[0][1:]), int(t.id.split(":t")[1]),
                ), reverse=True,
            )][:k]
            latencies["recent"] = None
            ranked["none"] = []
            latencies["none"] = None

            methods = {}
            for method in METHODS:
                ids = list(dict.fromkeys(ranked[method]))
                packing = {}
                for budget in budgets:
                    _, packed, tokens = pack_sources(
                        [by_id[s] for s in ids], token_budget=budget, count_tokens=count_tokens,
                    )
                    packing[str(budget)] = {
                        "tokens": tokens, "source_ids": packed,
                        "evidence_recall": len(set(packed) & gold) / len(gold) if gold else None,
                    }
                methods[method] = {
                    "ranked_source_ids": ids, "metrics": evidence_metrics(ids, gold),
                    "retrieval_ms": latencies[method], "packing": packing,
                }
    return {
        "question_id": question["question_id"], "category": question["question_type"],
        "abstention": question["question_id"].endswith("_abs"),
        "source_count": len(turns), "evidence_source_ids": sorted(gold),
        "ingestion_ms": ingestion_ms, "methods": methods,
        "reference_time": response.metadata.reference_time.isoformat(),
        **({"candidate_snapshot": snapshot_ref} if snapshot_ref is not None else {}),
    }


def summarize(details: list[dict], budgets) -> dict:
    result = {}
    measured = [d for d in details if "error" not in d]
    for method in METHODS:
        rows = [d["methods"][method] for d in measured]
        metric_names = list(rows[0]["metrics"]) if rows else []
        scores = {}
        for metric in metric_names:
            values = [r["metrics"][metric] for r in rows if r["metrics"][metric] is not None]
            scores[metric] = statistics.mean(values) if values else None
        packing = {}
        for budget in budgets:
            values = [r["packing"][str(budget)]["evidence_recall"] for r in rows]
            values = [v for v in values if v is not None]
            packing[str(budget)] = statistics.mean(values) if values else None
        latencies = sorted(r["retrieval_ms"] for r in rows if r["retrieval_ms"] is not None)
        result[method] = {
            "metrics": scores, "packed_evidence_recall": packing,
            "retrieval_p50_ms": statistics.median(latencies) if latencies else None,
            "retrieval_p95_ms": latencies[max(0, math.ceil(len(latencies) * .95) - 1)] if latencies else None,
            "evidence_labeled_queries": sum(r["metrics"]["mrr"] is not None for r in rows),
        }
    return result


def category_summary(details: list[dict], selected: list[dict], budgets) -> dict:
    """Keep failed and unattempted questions visible in category coverage."""
    result = {}
    for category in sorted({q["question_type"] for q in selected}):
        rows = [d for d in details if d["category"] == category]
        total = sum(q["question_type"] == category for q in selected)
        measured = sum("error" not in d for d in rows)
        result[category] = {
            "selected_queries": total, "measured_queries": measured,
            "errors": sum("error" in d for d in rows), "coverage": measured / total,
            "methods": summarize(rows, budgets),
        }
    return result


async def run(args) -> dict:
    import tiktoken

    raw = args.dataset.read_bytes()
    questions = json.loads(raw)
    if "oracle" in args.dataset.name and args.variant != "oracle":
        raise ValueError("An oracle dataset cannot be labeled as a long-history variant")
    selected = select_questions(questions, split=args.split, seed=args.seed, limit=args.limit)
    requested = getattr(args, "question_ids", None)
    if requested:
        if args.limit:
            raise ValueError("Use question IDs or a prefix limit, not both")
        unknown = set(requested) - {q["question_id"] for q in selected}
        if unknown:
            raise ValueError("Requested question IDs are absent from the selected split")
        selected = [q for q in selected if q["question_id"] in set(requested)]
    if not selected:
        raise ValueError("No questions selected")
    encoding = tiktoken.get_encoding(args.tokenizer)
    # A named, disclosed raw-turn profile: no inferred QA composites or
    # asynchronous maintenance. Config variants can be added as ablations.
    config = PRMEConfig(
        database_url=None, enable_qa_pairing=False,
        enable_query_reformulation=False, enable_store_supersedence=False,
        enable_surprise_gating=False, enable_reranker=False,
        organizer={"opportunistic_enabled": False},
    )
    details = []
    report = {
        "schema_version": 1, "run_id": getattr(args, "run_id", None) or str(uuid4()),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "kind": "source-evidence-retrieval", "profile": "raw-turns-static",
        "provenance": provenance(config),
        "dataset": {"name": args.dataset.name, "sha256": hashlib.sha256(raw).hexdigest(),
                    "variant": args.variant, "total_questions": len(questions),
                    "split": args.split, "split_seed": args.seed,
                    "selected_question_ids": [q["question_id"] for q in selected]},
        "tokenizer": args.tokenizer, "budgets": args.budgets, "candidate_limit": args.k,
        "rrf_constant": 60,
        "query_clock": args.clock,
        "concurrency": args.concurrency,
        "candidate_snapshots_captured": getattr(args, "capture_candidates", None) is not None,
        "limitations": [
            "Evidence retrieval, not answer accuracy or abstention accuracy.",
            "Shared whole-turn evaluation packer, not the PRME product packer.",
            "Methods use sequential warm shared indexes within each question; RRF latency sums its components.",
            "Concurrent questions and other machine workloads affect timing; these are not standalone latency SLO measurements.",
            "Raw NOTE ingestion with QA pairing and opportunistic maintenance disabled.",
            "Each question uses an isolated temporary local memory pack; configured storage paths are overridden.",
            "Query clock is explicitly selected; question time is not a knowledge-at cutoff.",
        ],
        "details": details,
    }
    start = time.perf_counter()
    semaphore = asyncio.Semaphore(args.concurrency)

    async def evaluate_one(question):
        async with semaphore:
            try:
                return await evaluate_question(
                    question, config, budgets=args.budgets, k=args.k,
                    count_tokens=lambda text: len(encoding.encode(text, disallowed_special=())),
                    reference_time=_parse_haystack_date(question["question_date"]) if args.clock == "question" else None,
                    capture_candidates=getattr(args, "capture_candidates", None),
                )
            except Exception as exc:
                return {"question_id": question["question_id"],
                        "category": question["question_type"], "error": type(exc).__name__}

    order = {q["question_id"]: i for i, q in enumerate(selected)}
    tasks = [asyncio.create_task(evaluate_one(q)) for q in selected]
    for future in asyncio.as_completed(tasks):
        details.append(await future)
        details.sort(key=lambda detail: order[detail["question_id"]])
        errors = sum("error" in d for d in details)
        report.update(
            elapsed_seconds=time.perf_counter() - start,
            complete=len(details) == len(selected) and errors == 0,
            errors=errors, coverage=(len(details) - errors) / len(selected),
            summary=summarize(details, args.budgets),
            categories=category_summary(details, selected, args.budgets),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Preserve a reviewable partial report if evaluation is interrupted.
        temp = args.output.with_suffix(args.output.suffix + ".tmp")
        temp.write_text(json.dumps(report, indent=2, allow_nan=False))
        temp.replace(args.output)
        print(f"Evaluated {len(details)}/{len(selected)}; errors={errors}", flush=True)
    return report


def supervise(output: Path, command: list[str], run_id: str) -> dict:
    """Record the native worker's actual exit, rejecting stale output files."""
    completed = subprocess.run(command, check=False)
    try:
        report = json.loads(output.read_bytes())
    except (OSError, ValueError):
        report = {}
    if not isinstance(report, dict) or report.get("run_id") != run_id:
        report = {"run_id": run_id, "complete": False,
                  "benchmark_error": "Worker did not produce a report for this run"}
    report["process_exit_code"] = completed.returncode
    report["complete"] = bool(report.get("complete") and completed.returncode == 0)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    temp.write_text(json.dumps(report, indent=2, allow_nan=False))
    temp.replace(output)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--variant", choices=["oracle", "s", "m", "custom"], required=True)
    parser.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    parser.add_argument("--seed", default="prme-evidence-v1")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--question-id", dest="question_ids", action="append",
                        help="Select an exact question within the chosen split; repeat for multiple IDs")
    parser.add_argument("--k", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--budgets", nargs="+", type=int, default=[2048, 4096, 8192])
    parser.add_argument("--tokenizer", default="cl100k_base")
    parser.add_argument("--clock", choices=["question", "wall"], default="question")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture-candidates", type=Path,
                        help="Save source-bearing public response snapshots for offline product-packing replay")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.k < 1 or args.concurrency < 1 or args.limit < 0 or any(b < 1 for b in args.budgets):
        parser.error("k, concurrency, and budgets must be positive; limit must be nonnegative")
    if args.worker:
        report = asyncio.run(run(args))
    else:
        run_id = str(uuid4())
        report = supervise(args.output, [sys.executable, "-m", "benchmarks.retrieval_eval",
                                        *sys.argv[1:], "--worker", "--run-id", run_id], run_id)
    raise SystemExit(0 if report["complete"] else 1)


if __name__ == "__main__":
    main()
