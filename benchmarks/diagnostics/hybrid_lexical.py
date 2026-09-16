"""Prospective development factorial study of lexical queries and product packing.

One raw-source pack per question; every arm calls MemoryEngine.retrieve(). Query
preprocessing is injected only into the native content-query parser, preserving
all real backend scope filters. The prototype does not change package defaults.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib.metadata import version
import json
from pathlib import Path
import sys
import tempfile
import time
from uuid import uuid4

from prme import MemoryEngine, PRMEConfig
from prme.config import EmbeddingConfig
from prme.retrieval.config import PackingConfig
from prme.epistemic.inference import infer_source_type
from prme.types import NodeType, Scope
from benchmarks.compare_evidence import paired_statistics
from benchmarks.diagnostics.lexical_queries import literal_query, STOP_SHA
from benchmarks.diagnostics.packing_reader import canonical, digest, write
from benchmarks.diagnostics.product_packing import measure
from benchmarks.evidence import longmemeval_sources, select_questions
from benchmarks.longmemeval import _parse_haystack_date
from benchmarks.retrieval_eval import provenance, supervise

POLICIES = ("parser", "literal_stopwords")
ORDERS = ("density", "score")
BUDGETS = (2048, 4096, 8192)


async def embedding_identity():
    from prme.storage.embedding import FastEmbedProvider

    provider = FastEmbedProvider()
    await provider.embed(["Authored model identity preflight."])
    root = Path(provider._model.model._model_dir)
    return {"model": provider.model_name, "version": provider.model_version,
            "dependencies": {name: version(name) for name in ["fastembed", "onnxruntime", "numpy"]},
            "assets_sha256": {str(path.relative_to(root)): digest(path.read_bytes())
                              for path in sorted(root.rglob("*")) if path.is_file()}}


class ForbiddenExtraction:
    provider_name = "forbidden-in-raw-study"

    def __init__(self):
        self.calls = 0

    async def extract(self, *args, **kwargs):
        self.calls += 1
        raise RuntimeError("Raw-source study must not call an extraction model")


def raw_config():
    return PRMEConfig(database_url=None, encryption_enabled=False,
        enable_qa_pairing=False, enable_query_reformulation=False,
        enable_store_supersedence=False, enable_surprise_gating=False, enable_reranker=False,
        embedding=EmbeddingConfig(provider="fastembed", model_name="BAAI/bge-small-en-v1.5", dimension=384),
        packing=PackingConfig(multipath_ordering="density"), organizer={"opportunistic_enabled": False})


class QueryProxy:
    def __init__(self, index):
        self.native = index._index
        self.index = index

    def __getattr__(self, name):
        return getattr(self.native, name)

    def parse_query_lenient(self, text, fields):
        if fields != ["content"]:
            raise ValueError("Experimental parser may only transform content queries")
        return literal_query(self.index, text, stopwords=True), []


@contextmanager
def policy_scope(engine, policy, ordering):
    if policy not in POLICIES or ordering not in ORDERS:
        raise ValueError("Unknown experimental arm")
    index = engine._lexical_index
    pipeline = engine._retrieval_pipeline
    original, packing, identity = index._index, pipeline._packing_config, pipeline._feature_identity
    try:
        if policy == "literal_stopwords":
            index._index = QueryProxy(index)
        pipeline._packing_config = packing.model_copy(update={"multipath_ordering": ordering})
        pipeline._feature_identity = {**identity, "experimental_lexical_query": {
            "policy": policy, "stopwords_sha256": STOP_SHA if policy != "parser" else None,
            "harness_sha256": digest(Path(__file__).read_bytes())}}
        yield
    finally:
        index._index, pipeline._packing_config, pipeline._feature_identity = original, packing, identity


def candidate_bytes(response):
    return canonical([candidate.model_dump(mode="json") for candidate in response.results])


async def capture(turns, query, reference_time, config, *, case_identity, budgets=BUDGETS):
    """Adapter sees no answers, evidence labels or original dataset session IDs."""
    with tempfile.TemporaryDirectory(prefix="prme-hybrid-lexical-") as tmp:
        root = Path(tmp)
        local = config.model_copy(update={"db_path": str(root / "memory.duckdb"),
            "vector_path": str(root / "vectors.usearch"), "lexical_path": str(root / "lexical")})
        (root / "lexical").mkdir()
        async with MemoryEngine.open(local) as engine:
            guard = ForbiddenExtraction()
            engine._pipeline._extraction_provider = guard
            start = time.perf_counter()
            for turn in turns:
                await engine.store(turn.content, user_id="evaluation", role=turn.role,
                    session_id=turn.session_id, metadata={"source_turn": turn.id},
                    event_time=_parse_haystack_date(turn.date) if turn.date else None)
            ingestion_ms = (time.perf_counter() - start) * 1000
            if engine.materialization_debt:
                raise ValueError("All sources must be durably indexed before retrieval")
            nodes = await engine.query_nodes(user_id="evaluation", limit=len(turns)+1)
            sources = {node.metadata["source_turn"]: node for node in nodes}
            if len(nodes) != len(turns) or len(sources) != len(turns):
                raise ValueError("Raw sources were lost or duplicated")
            for turn in turns:
                node = sources[turn.id]
                if (node.content != turn.content or node.session_id != turn.session_id or node.user_id != "evaluation"
                        or node.metadata != {"source_turn": turn.id} or node.node_type != NodeType.NOTE
                        or node.scope != Scope.PERSONAL or node.source_type != infer_source_type(NodeType.NOTE, turn.role)
                        or node.event_time != (_parse_haystack_date(turn.date) if turn.date else None)):
                    raise ValueError("Raw source identity/content changed")
            original_nodes = canonical([node.model_dump(mode="json") for node in sorted(nodes, key=lambda n: str(n.id))])
            arms = [(p, o, b) for p in POLICIES for o in ORDERS for b in budgets]
            arms.sort(key=lambda arm: digest(canonical([case_identity, *arm])))
            captures, candidates = {}, {}
            for policy, ordering, budget in [*arms, arms[0]]:
                with policy_scope(engine, policy, ordering):
                    response = await engine.retrieve(query, user_id="evaluation", reference_time=reference_time,
                                                     token_budget=budget, include_cross_scope=False)
                    receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id="evaluation")
                    if (receipt is None or receipt.packing.multipath_ordering != ordering
                            or receipt.execution.features["experimental_lexical_query"]["policy"] != policy):
                        raise ValueError("Receipt does not identify the executed arm")
                    candidate_raw = candidate_bytes(response)
                    if policy in candidates and candidate_raw != canonical(candidates[policy]):
                        raise ValueError("Packing budget/order or prior retrieval changed ranked candidates")
                    candidates[policy] = json.loads(candidate_raw)
                    cfg = engine._retrieval_pipeline._packing_config.model_copy(update={"token_budget": budget})
                    # Measure with no gold inside this adapter. Credit is joined outside it.
                    measurement = measure(response.bundle, set(), cfg)
                    key = f"{policy}:{ordering}:{budget}"
                    value = {"measurement": measurement, "context": response.bundle.render(),
                             "candidate_sha256": digest(candidate_raw),
                             "execution": receipt.execution.model_dump(mode="json"),
                             "packing": receipt.packing.model_dump(mode="json")}
                    if key in captures and captures[key] != value:
                        raise ValueError("Repeated control changed after other arms")
                    captures[key] = value
            current = await engine.query_nodes(user_id="evaluation", limit=len(turns)+1)
            if canonical([node.model_dump(mode="json") for node in sorted(current, key=lambda n: str(n.id))]) != original_nodes:
                raise ValueError("Retrieval arms mutated source memories")
            if guard.calls:
                raise ValueError("An extraction call was attempted")
            return {"arms": captures, "candidates": candidates, "ingestion_ms": ingestion_ms,
                    "arm_order": [list(arm) for arm in arms], "repeated_control_identical": True,
                    "source_memories_unchanged": True, "extraction_calls": guard.calls}


def summarize(details):
    def group(rows):
        result = {}
        for ordering in ORDERS:
            result[ordering] = {}
            for budget in BUDGETS:
                key = f"{ordering}:{budget}"
                pairs = [(r["arms"]["parser:"+key]["evidence_recall"],
                          r["arms"]["literal_stopwords:"+key]["evidence_recall"]) for r in rows]
                result[ordering][str(budget)] = paired_statistics([(a,b) for a,b in pairs if a is not None and b is not None])
        return result
    return {"overall": group(details), "categories": {cat: group([r for r in details if r["category"] == cat])
                                                      for cat in sorted({r["category"] for r in details})}}


async def run(args):
    plan = json.loads(args.plan.read_bytes())
    config = raw_config()
    observed = provenance(config)
    if (observed != plan["provenance"] or digest(Path(__file__).read_bytes()) != plan["runner_sha256"]
            or plan["policies"] != list(POLICIES) or plan["orders"] != list(ORDERS)
            or plan["budgets"] != list(BUDGETS) or plan["stopwords_sha256"] != STOP_SHA):
        raise ValueError("Runtime differs from the frozen study plan")
    model_identity = await embedding_identity()
    if model_identity != plan["embedding_identity"]:
        raise ValueError("Embedding assets differ from the frozen study plan")
    raw = args.dataset.read_bytes()
    if digest(raw) != plan["dataset"]["sha256"] or plan["dataset"]["split"] != "dev":
        raise ValueError("Only the registered development dataset is allowed")
    data = json.loads(raw)
    by_id = {q["question_id"]: q for q in data}
    selected = plan["dataset"]["selected_question_ids"]
    if len(by_id) != len(data) or not selected or len(set(selected)) != len(selected) or not set(selected) <= by_id.keys():
        raise ValueError("Invalid registered cohort")
    if selected != [q["question_id"] for q in select_questions(data, split="dev", seed=plan["dataset"]["split_seed"])]:
        raise ValueError("Study must include the complete development split")
    if plan["registered_at"] >= datetime.now(timezone.utc).isoformat():
        raise ValueError("Registration must predate execution")
    report = {"run_id": args.run_id, "complete": False, "started_at": datetime.now(timezone.utc).isoformat(),
              "plan_sha256": digest(args.plan.read_bytes()), "provenance": observed,
              "embedding_identity": model_identity, "details": [], "errors": 0, "limits": plan["limits"]}
    write(args.output, report)
    semaphore = asyncio.Semaphore(plan["concurrency"])
    async def one(qid):
        async with semaphore:
            question = by_id[qid]
            try:
                turns, gold = longmemeval_sources(question)
                result = await capture(turns, question["question"], _parse_haystack_date(question["question_date"]),
                                       config, case_identity=qid)
                filename = digest(qid.encode()) + ".json"
                snapshot = {"question_id": qid, "sources_sha256": digest(canonical([vars(t) for t in turns])), **result}
                write(args.snapshots / filename, snapshot)
                arms = {}
                for key, arm in result["arms"].items():
                    measurement = dict(arm["measurement"])
                    retained = set(measurement["content_source_ids"])
                    measurement.update(evidence_recall=len(retained & gold)/len(gold) if gold else None,
                                       all_evidence_retained=gold <= retained if gold else None)
                    arms[key] = measurement
                return {"question_id": qid, "category": question["question_type"], "evidence_source_ids": sorted(gold),
                        "source_count": len(turns), "arms": arms, "ingestion_ms": result["ingestion_ms"],
                        "snapshot": {"filename": filename, "sha256": digest((args.snapshots / filename).read_bytes())}}
            except Exception as exc:
                return {"question_id": qid, "category": question["question_type"], "error_type": type(exc).__name__}
    order = {qid:i for i,qid in enumerate(selected)}
    tasks = [asyncio.create_task(one(qid)) for qid in selected]
    for task in asyncio.as_completed(tasks):
        report["details"].append(await task)
        report["details"].sort(key=lambda row: order[row["question_id"]])
        report["errors"] = sum("error_type" in row for row in report["details"])
        write(args.output, report)
        print(f"Evaluated {len(report['details'])}/{len(selected)}; errors={report['errors']}", flush=True)
    if await embedding_identity() != model_identity or provenance(config) != observed:
        raise ValueError("Runtime or embedding assets changed during the study")
    report["complete"] = report["errors"] == 0
    if report["complete"]:
        report["comparison"] = summarize(report["details"])
    write(args.output, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["plan", "dataset", "snapshots", "output"]:
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-id", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        asyncio.run(run(args))
        return
    if args.output.exists() or args.snapshots.exists():
        raise ValueError("Refusing to overwrite prior study artifacts")
    run_id = str(uuid4())
    result = supervise(args.output, [sys.executable, "-m", __spec__.name, *sys.argv[1:], "--worker", "--run-id", run_id], run_id)
    raise SystemExit(0 if result["complete"] else 1)


if __name__ == "__main__":
    main()
