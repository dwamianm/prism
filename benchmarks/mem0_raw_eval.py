"""Pinned Mem0 OSS raw-turn evidence comparison, without extraction or a reader.

The reference PRME run is frozen and already completed. This is exploratory
development evidence, not a default-product or answer-quality comparison.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

from benchmarks.compare_evidence import paired_statistics
from benchmarks.evidence import (
    evidence_metrics,
    longmemeval_sources,
    pack_sources,
    select_questions,
)
from benchmarks.longmemeval import _parse_haystack_date
from benchmarks.retrieval_eval import supervise


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def assets(directory):
    return {
        str(path.relative_to(directory)): sha(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def validate_rows(rows, by_id, memory_ids):
    """Never turn an unknown, altered or duplicate result into source credit."""
    ids = []
    for row in rows:
        source = row.get("metadata", {}).get("source_turn")
        if source not in by_id or source in ids:
            raise ValueError("Unknown or duplicate source result")
        turn = by_id[source]
        if (
            row["id"] != memory_ids[source]
            or row["memory"] != turn.content
            or row.get("role") != turn.role
            or row.get("user_id") != "evaluation"
            or row["metadata"].get("source_session") != turn.session_id
            or row["metadata"].get("source_date") != turn.date
        ):
            raise ValueError("Returned source identity or content differs")
        ids.append(source)
    return ids


def raw_question(memory, turns, query, *, k):
    """Adapter accepts neutral source records and query only, never labels."""
    by_id = {turn.id: turn for turn in turns}
    if len(by_id) != len(turns) or any(
        t.role not in {"user", "assistant"} for t in turns
    ):
        raise ValueError("Unique user/assistant source turns are required")
    memory_ids = {}
    start = time.perf_counter()
    for turn in turns:
        metadata = {
            "source_turn": turn.id,
            "source_session": turn.session_id,
            "source_date": turn.date,
        }
        if turn.date:
            metadata["created_at"] = _parse_haystack_date(turn.date).isoformat()
        rows = memory.add(
            [{"role": turn.role, "content": turn.content}],
            user_id="evaluation",
            metadata=metadata,
            infer=False,
        )["results"]
        if len(rows) != 1 or rows[0]["memory"] != turn.content:
            raise ValueError("Raw write failed to preserve a source")
        memory_ids[turn.id] = rows[0]["id"]
    ingestion_ms = (time.perf_counter() - start) * 1000
    all_rows = memory.get_all(filters={"user_id": "evaluation"}, top_k=len(turns) + 1)[
        "results"
    ]
    if set(validate_rows(all_rows, by_id, memory_ids)) != set(by_id):
        raise ValueError("Not every source survived ingestion")
    start = time.perf_counter()
    rows = memory.search(
        query, filters={"user_id": "evaluation"}, top_k=k, threshold=0, rerank=False
    )["results"]
    retrieval_ms = (time.perf_counter() - start) * 1000
    ids = validate_rows(rows, by_id, memory_ids)
    if len(ids) > k:
        raise ValueError("Candidate limit exceeded")
    return {
        "ranked_source_ids": ids,
        "ingestion_ms": ingestion_ms,
        "retrieval_ms": retrieval_ms,
    }


def validate_inputs(plan, dataset, baseline):
    if plan["dataset_sha256"] != sha(dataset) or plan["baseline_sha256"] != sha(
        baseline
    ):
        raise ValueError("Frozen input hash mismatch")
    reference = json.loads(baseline.read_bytes())
    if (
        not reference.get("complete")
        or reference.get("errors")
        or reference.get("process_exit_code") != 0
    ):
        raise ValueError("Complete native-exited PRME reference required")
    selected = select_questions(
        json.loads(dataset.read_bytes()), split="dev", seed="prme-evidence-v1"
    )
    ids = [q["question_id"] for q in selected]
    if (
        ids != plan["selected_question_ids"]
        or ids != reference["dataset"]["selected_question_ids"]
    ):
        raise ValueError("Development cohort mismatch")
    if (
        reference["profile"] != "raw-turns-static"
        or reference["query_clock"] != "question"
        or reference["candidate_limit"] != plan["candidate_limit"]
        or reference["budgets"] != plan["budgets"]
        or reference["tokenizer"] != plan["tokenizer"]
    ):
        raise ValueError("Reference protocol mismatch")
    return selected, reference


def compare(reference, details, *, budgets):
    old = {r["question_id"]: r for r in reference["details"]}
    if len(details) != len(old) or {r["question_id"] for r in details} != set(old):
        raise ValueError("Complete paired coverage required")
    for row in details:
        if "error" in row or any(
            row[k] != old[row["question_id"]][k]
            for k in ["category", "source_count", "evidence_source_ids"]
        ):
            raise ValueError("Source labels or coverage mismatch")

    def summarize(rows):
        result = {}
        for budget in budgets:
            pairs = [
                (
                    row["packing"][str(budget)]["evidence_recall"],
                    old[row["question_id"]]["methods"]["prme"]["packing"][str(budget)][
                        "evidence_recall"
                    ],
                )
                for row in rows
            ]
            result[str(budget)] = paired_statistics(
                [(a, b) for a, b in pairs if a is not None and b is not None]
            )
        return result

    return {
        "direction": "before=Mem0 raw; after=PRME raw; shared whole-turn evaluator packer",
        "overall": summarize(details),
        "categories": {
            category: summarize([r for r in details if r["category"] == category])
            for category in sorted({r["category"] for r in details})
        },
    }


def run(args):
    plan = json.loads(args.plan.read_bytes())
    selected, reference = validate_inputs(plan, args.dataset, args.baseline)
    if sha(__file__) != plan["runner_sha256"]:
        raise ValueError("Runner differs from declaration")
    helpers = {
        name: sha(Path(__file__).parent / name)
        for name in [
            "evidence.py",
            "compare_evidence.py",
            "longmemeval.py",
            "retrieval_eval.py",
        ]
    }
    if helpers != plan["helper_sha256"]:
        raise ValueError("Evaluator helpers differ from declaration")
    if datetime.fromisoformat(plan["registered_at"]) >= datetime.now(timezone.utc):
        raise ValueError("Plan must predate execution")
    for name, expected in plan["versions"].items():
        if version(name) != expected:
            raise ValueError("Dependency version mismatch")
    import tiktoken

    encoding = tiktoken.get_encoding(plan["tokenizer"])

    def count_tokens(text):
        return len(encoding.encode(text, disallowed_special=()))

    report = {
        "run_id": args.run_id,
        "complete": False,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "plan_sha256": sha(args.plan),
        "details": [],
        "errors": 0,
        "limits": plan["limits"],
    }
    with tempfile.TemporaryDirectory(prefix="prme-mem0-raw-") as directory:
        root = Path(directory)
        os.environ["MEM0_TELEMETRY"] = "false"
        os.environ["MEM0_DIR"] = str(root / "settings")
        from mem0 import Memory
        import mem0
        from mem0.utils.spacy_models import get_nlp_full, get_nlp_lemma
        import spacy

        if (
            assets(spacy.util.get_package_path("en_core_web_sm"))
            != plan["nlp_assets_sha256"]
        ):
            raise ValueError("NLP model assets differ")
        if get_nlp_full() is None or get_nlp_lemma() is None:
            raise ValueError("Recommended NLP pipelines failed to load")

        installed = Path(inspect.getfile(mem0)).parent
        if (
            subprocess.check_output(
                ["git", "-C", str(args.upstream), "rev-parse", "HEAD"], text=True
            ).strip()
            != plan["mem0_commit"]
        ):
            raise ValueError("Competitor revision differs")
        if subprocess.check_output(
            ["git", "-C", str(args.upstream), "status", "--porcelain"], text=True
        ).strip():
            raise ValueError("Competitor checkout must be clean")
        paths = list((args.upstream / "mem0").rglob("*.py"))
        if not paths or any(
            sha(p) != sha(installed / p.relative_to(args.upstream / "mem0"))
            for p in paths
        ):
            raise ValueError("Installed competitor source differs")

        llm_calls = 0

        def no_inference(*args, **kwargs):
            nonlocal llm_calls
            llm_calls += 1
            raise AssertionError("Raw evaluation forbids LLM calls")

        for index, question in enumerate(selected):
            turns, gold = longmemeval_sources(question)
            row = {
                "question_id": question["question_id"],
                "category": question["question_type"],
                "source_count": len(turns),
                "evidence_source_ids": sorted(gold),
                "sources_sha256": hashlib.sha256(
                    json.dumps([asdict(t) for t in turns]).encode()
                ).hexdigest(),
            }
            try:
                with tempfile.TemporaryDirectory(dir=root) as pack:
                    config = json.loads(json.dumps(plan["mem0_config"]))
                    config["vector_store"]["config"]["path"] = str(
                        Path(pack) / "qdrant"
                    )
                    config["history_db_path"] = str(Path(pack) / "history.db")
                    start = time.perf_counter()
                    memory = Memory.from_config(config)
                    row["startup_ms"] = (time.perf_counter() - start) * 1000
                    memory.llm.generate_response = no_inference
                    try:
                        memory.embedding_model.embed(
                            "model identity preflight", "search"
                        )
                        model_dir = Path(
                            memory.embedding_model.dense_model.model._model_dir
                        )
                        if assets(model_dir) != plan["model_assets_sha256"]:
                            raise ValueError("Embedding asset mismatch")
                        sparse = memory.vector_store._get_bm25_encoder()
                        if sparse is None or not memory.vector_store._has_bm25_slot:
                            raise ValueError("BM25 path is unavailable")
                        if (
                            assets(Path(sparse.model._model_dir))
                            != plan["sparse_assets_sha256"]
                        ):
                            raise ValueError("BM25 asset mismatch")
                        keyword_search = memory.vector_store.keyword_search

                        def checked_keyword_search(*args, **kwargs):
                            result = keyword_search(*args, **kwargs)
                            if result is None:
                                raise RuntimeError(
                                    "BM25 query failed; refusing silent fallback"
                                )
                            return result

                        memory.vector_store.keyword_search = checked_keyword_search
                        embed = memory.embedding_model.embed
                        embedding_calls = {}

                        def counted_embed(text, memory_action=None, *args, **kwargs):
                            action = memory_action or "unspecified"
                            counts = embedding_calls.setdefault(
                                action, {"calls": 0, "characters": 0}
                            )
                            counts["calls"] += 1
                            counts["characters"] += len(text)
                            return embed(text, memory_action, *args, **kwargs)

                        memory.embedding_model.embed = counted_embed
                        row.update(
                            raw_question(
                                memory,
                                turns,
                                question["question"],
                                k=plan["candidate_limit"],
                            )
                        )
                        row["embedding_calls"] = embedding_calls
                        if llm_calls:
                            raise RuntimeError("Unexpected LLM call in raw mode")
                    finally:
                        try:
                            memory.close()
                        finally:
                            memory.vector_store.client.close()
                by_id = {turn.id: turn for turn in turns}
                row["metrics"] = evidence_metrics(row["ranked_source_ids"], gold)
                row["packing"] = {}
                for budget in plan["budgets"]:
                    context, ids, tokens = pack_sources(
                        [by_id[s] for s in row["ranked_source_ids"]],
                        token_budget=budget,
                        count_tokens=count_tokens,
                    )
                    row["packing"][str(budget)] = {
                        "tokens": tokens,
                        "source_ids": ids,
                        "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
                        "evidence_recall": len(set(ids) & gold) / len(gold)
                        if gold
                        else None,
                    }
            except Exception as exc:
                row["error"] = type(exc).__name__
                report["errors"] += 1
            report["details"].append(row)
            atomic_json(args.output, report)
            print(
                f"Evaluated {index + 1}/{len(selected)}; errors={report['errors']}",
                flush=True,
            )
    report["complete"] = report["errors"] == 0 and len(report["details"]) == len(
        selected
    )
    if report["complete"]:
        report["comparison"] = compare(
            reference, report["details"], budgets=plan["budgets"]
        )
    atomic_json(args.output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["plan", "dataset", "baseline", "upstream", "output"]:
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        report = run(args)
    else:
        if args.output.exists():
            parser.error(
                "Refusing to overwrite an existing run; preserve failed attempts"
            )
        run_id = str(uuid4())
        report = supervise(
            args.output,
            [
                sys.executable,
                "-m",
                "benchmarks.mem0_raw_eval",
                *sys.argv[1:],
                "--worker",
                "--run-id",
                run_id,
            ],
            run_id,
        )
    raise SystemExit(0 if report["complete"] else 1)


if __name__ == "__main__":
    main()
