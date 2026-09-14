"""Reproducible raw-turn retrieval comparisons on LongMemEval.

Run: python -m benchmarks.retrieval_eval --help
This measures source evidence retrieval, not official answer accuracy. Every
method sees the same raw turns; labels and answers never enter the memory pack.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
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
import urllib.request
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


def _extraction_identity(provider: str, model: str, base_url: str) -> dict:
    """Resolve immutable local model identity before any extraction calls."""
    identity = {"provider": provider, "model": model, "base_url": base_url}
    if provider != "ollama":
        return {**identity, "model_digest": None}
    native_url = base_url.rstrip("/")
    if native_url.endswith("/v1"):
        native_url = native_url[:-3]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(native_url + "/api/tags", timeout=10) as response:
        inventory = json.load(response)
    matches = [
        item["digest"] for item in inventory.get("models", ())
        if item.get("name") == model
    ]
    if len(matches) != 1:
        raise ValueError("Extraction model must resolve to exactly one Ollama tag")
    return {**identity, "model_digest": matches[0]}


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False))
    temporary.replace(path)


def _sources_for_nodes(
    node_ids: list[str], node_sources: dict[str, list[str]], *, limit: int
) -> list[str]:
    """Expand ranked nodes to unique source turns through durable provenance."""
    ranked: list[str] = []
    for node_id in node_ids:
        ranked.extend(node_sources.get(node_id, []))
    return list(dict.fromkeys(ranked))[:limit]


async def _ingest_turns(engine, turns, *, user_id: str, profile: str) -> tuple[dict, dict[str, list[str]]]:
    """Ingest source-only turns and return extraction coverage plus node lineage."""
    if profile not in {"raw", "extracted"}:
        raise ValueError("ingestion profile must be raw or extracted")
    source_order = {turn.id: position for position, turn in enumerate(turns)}
    event_sources: dict[str, str] = {}
    started = time.perf_counter()
    for turn in turns:
        kwargs = {
            "user_id": user_id,
            "role": turn.role,
            "session_id": turn.session_id,
            "metadata": {"source_turn": turn.id},
            "event_time": _parse_haystack_date(turn.date) if turn.date else None,
        }
        if profile == "extracted":
            event_id = await engine.ingest(
                turn.content, wait_for_extraction=True, **kwargs
            )
            status = await engine.extraction_status(event_id, user_id=user_id)
            if status is None or status.status != "complete":
                raise RuntimeError("Extraction did not reach its durable completion boundary")
        else:
            event_id = await engine.store(turn.content, node_type=NodeType.NOTE, **kwargs)
        event_sources[event_id] = turn.id

    node_sources: dict[str, list[str]] = {}
    materialized_sources: set[str] = set()
    node_types: Counter[str] = Counter()
    for event_id, source_id in event_sources.items():
        nodes = await engine.get_event_nodes(event_id, user_id=user_id)
        if nodes:
            materialized_sources.add(source_id)
        for node in nodes:
            node_types[node.node_type.value] += 1
            node_sources.setdefault(str(node.id), []).append(source_id)
    for sources in node_sources.values():
        sources.sort(key=source_order.__getitem__)
    if profile == "raw" and len(materialized_sources) != len(turns):
        raise RuntimeError("Raw-turn ingestion did not preserve every source")
    return {
        "profile": profile,
        "source_events": len(turns),
        "materialized_sources": len(materialized_sources),
        "sources_without_nodes": sorted(set(source_order) - materialized_sources),
        "materialized_nodes": sum(node_types.values()),
        "node_types": dict(sorted(node_types.items())),
        "duration_ms": (time.perf_counter() - started) * 1000,
    }, node_sources


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
    ingestion_profile: str = "raw",
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
            ingestion, node_sources = await _ingest_turns(
                engine, turns, user_id=user_id, profile=ingestion_profile
            )

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
            ranked["prme"] = _sources_for_nodes(
                [str(c.node.id) for c in response.results], node_sources, limit=k
            )
            for method, index in (("bm25", engine._lexical_index), ("vector", engine._vector_index)):
                start = time.perf_counter()
                if method == "bm25":
                    hits = await index.search(question["question"], user_id, limit=k)
                else:
                    hits = await index.search(question["question"], user_id, k=k)
                latencies[method] = (time.perf_counter() - start) * 1000
                ranked[method] = _sources_for_nodes(
                    [h["node_id"] for h in hits], node_sources, limit=k
                )
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
        "ingestion_ms": ingestion["duration_ms"], "ingestion": ingestion,
        "methods": methods,
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
    # Both profiles disable inferred QA composites and asynchronous
    # maintenance so the declared ingestion path is the measured intervention.
    ingestion_profile = getattr(args, "ingestion_profile", "raw")
    extraction = {
        "provider": getattr(args, "extraction_provider", "ollama"),
        "model": getattr(args, "extraction_model", "qwen3.5:4b"),
        "base_url": getattr(args, "extraction_base_url", "http://127.0.0.1:11434/v1"),
        "timeout": getattr(args, "extraction_timeout", 120.0),
        "max_retries": getattr(args, "extraction_max_retries", 2),
        "temperature": 0,
    }
    config = PRMEConfig(
        database_url=None, enable_qa_pairing=False,
        enable_query_reformulation=False, enable_store_supersedence=False,
        enable_surprise_gating=False, enable_reranker=False,
        organizer={"opportunistic_enabled": False},
        extraction=extraction,
    )
    details = []
    report = {
        "schema_version": 1, "run_id": getattr(args, "run_id", None) or str(uuid4()),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "kind": "source-evidence-retrieval",
        "profile": (
            "raw-turns-static" if ingestion_profile == "raw"
            else "llm-extracted-source-lineage"
        ),
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
            (
                "Raw NOTE ingestion with QA pairing and opportunistic maintenance disabled."
                if ingestion_profile == "raw"
                else "Public ingest() with synchronous LLM extraction; source metrics expand "
                     "retrieved node provenance and do not prove the extracted text answers the question."
            ),
            "Each question uses an isolated temporary local memory pack; configured storage paths are overridden.",
            "Query clock is explicitly selected; question time is not a knowledge-at cutoff.",
        ],
        "details": details,
    }
    extraction_identity = None
    if ingestion_profile == "extracted":
        extraction_identity = _extraction_identity(
            extraction["provider"], extraction["model"], extraction["base_url"]
        )
        report["extraction"] = extraction_identity
        if extraction_identity["model_digest"] is None:
            report["limitations"].append(
                "The configured extraction provider does not expose an immutable model digest."
            )
    report.update(
        registered_at=datetime.now(timezone.utc).isoformat(),
        elapsed_seconds=0,
        complete=False,
        errors=0,
        coverage=0,
        summary={},
        categories={},
    )
    # Persist the full source/model/config declaration before any evaluation
    # task can make an extraction call or observe an outcome.
    _write_report(args.output, report)
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
                    ingestion_profile=ingestion_profile,
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
        # Preserve a reviewable partial report if evaluation is interrupted.
        _write_report(args.output, report)
        print(f"Evaluated {len(details)}/{len(selected)}; errors={errors}", flush=True)
    if extraction_identity is not None:
        if _extraction_identity(
            extraction["provider"], extraction["model"], extraction["base_url"]
        ) != extraction_identity:
            report["complete"] = False
            report["benchmark_error"] = "Extraction model identity changed during the run"
            _write_report(args.output, report)
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
    _write_report(output, report)
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
    parser.add_argument("--ingestion-profile", choices=["raw", "extracted"], default="raw")
    parser.add_argument("--extraction-provider", default="ollama")
    parser.add_argument("--extraction-model", default="qwen3.5:4b")
    parser.add_argument("--extraction-base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--extraction-timeout", type=float, default=120.0)
    parser.add_argument("--extraction-max-retries", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture-candidates", type=Path,
                        help="Save source-bearing public response snapshots for offline product-packing replay")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.k < 1 or args.concurrency < 1 or args.limit < 0 or any(b < 1 for b in args.budgets):
        parser.error("k, concurrency, and budgets must be positive; limit must be nonnegative")
    if args.extraction_timeout <= 0 or args.extraction_max_retries < 0:
        parser.error("extraction timeout must be positive and retries nonnegative")
    if args.ingestion_profile == "extracted" and args.capture_candidates is not None:
        parser.error("candidate snapshot replay currently supports raw source nodes only")
    if args.worker:
        report = asyncio.run(run(args))
    else:
        run_id = str(uuid4())
        report = supervise(args.output, [sys.executable, "-m", "benchmarks.retrieval_eval",
                                        *sys.argv[1:], "--worker", "--run-id", run_id], run_id)
    raise SystemExit(0 if report["complete"] else 1)


if __name__ == "__main__":
    main()
