"""Replay BEAM answer quality from a frozen PRME pack with one retrieval change.

Run this file directly so the pinned upstream checkout can provide its
``benchmarks`` package while this repository provides ``prme`` through
``PYTHONPATH``. The runner never ingests or extracts. It copies the registered
pack, retrieves the same public questions for the pack's existing owner, and
uses the pinned upstream answer and rubric-judge functions.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterable
from datetime import datetime
import hashlib
import json
import logging
from pathlib import Path
import shutil
import subprocess
from typing import Any
from urllib.request import ProxyHandler, build_opener


REGISTRATION_KIND = "beam-retrieval-ablation-registration"
REGISTRATION_SCHEMA = 1


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _git_revision(root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()


def _verify_file(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or _digest(path) != expected:
        raise ValueError(f"{label} differs from registration: {path}")


def _verify_registration(
    registration: dict[str, Any],
    *,
    project_root: Path,
    upstream_root: Path,
    dataset: Path,
    source_pack: Path,
    baseline_result: Path,
) -> None:
    if registration.get("schema_version") != REGISTRATION_SCHEMA:
        raise ValueError("unsupported retrieval-ablation registration schema")
    if registration.get("kind") != REGISTRATION_KIND:
        raise ValueError("unexpected retrieval-ablation registration kind")
    if registration.get("status") != "preregistered":
        raise ValueError("retrieval ablation must be preregistered")
    source = registration.get("source")
    if not isinstance(source, dict):
        raise ValueError("registration is missing source identity")
    if source.get("prme_revision") != _git_revision(project_root):
        raise ValueError("PRME revision differs from registration")
    if source.get("upstream_revision") != _git_revision(upstream_root):
        raise ValueError("upstream revision differs from registration")
    files = source.get("files")
    if not isinstance(files, dict):
        raise ValueError("registration is missing file hashes")
    _verify_file(Path(__file__), files["runner_sha256"], "ablation runner")
    _verify_file(dataset, files["dataset_sha256"], "BEAM dataset")
    _verify_file(baseline_result, files["baseline_result_sha256"], "baseline result")
    for relative, expected in files.get("pack_sha256", {}).items():
        _verify_file(source_pack / relative, expected, f"source pack {relative}")


def _verify_models(registration: dict[str, Any]) -> None:
    models = registration.get("models")
    if not isinstance(models, dict) or set(models) != {"answerer", "judge"}:
        raise ValueError("registration must bind answerer and judge")
    inventories: dict[str, dict[str, str]] = {}
    for role in ("answerer", "judge"):
        identity = models[role]
        if not isinstance(identity, dict):
            raise ValueError(f"invalid {role} identity")
        base_url = identity.get("base_url")
        if not isinstance(base_url, str) or not base_url.endswith("/v1"):
            raise ValueError(f"invalid {role} base URL")
        native_url = base_url[:-3]
        inventory = inventories.get(native_url)
        if inventory is None:
            opener = build_opener(ProxyHandler({}))
            with opener.open(native_url + "/api/tags", timeout=10) as response:
                payload = json.load(response)
            inventory = {
                item["name"]: item["digest"]
                for item in payload.get("models", ())
                if isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and isinstance(item.get("digest"), str)
            }
            inventories[native_url] = inventory
        if inventory.get(identity.get("model")) != identity.get("model_digest"):
            raise ValueError(f"{role} model digest differs from registration")


class _FrozenPackRetriever:
    def __init__(self, engine: Any, *, user_id: str, max_per_evidence: int):
        self.engine = engine
        self.user_id = user_id
        self.max_per_evidence = max_per_evidence
        self.event_times: dict[str, datetime] = {}
        self.reference_time: datetime | None = None

    async def initialize(self) -> None:
        events = await self.engine.get_events(self.user_id, limit=1000)
        if not events:
            raise ValueError("registered pack owner has no events")
        for event in events:
            observed = event.event_time or event.created_at
            self.event_times[str(event.id)] = observed
            if self.reference_time is None or observed > self.reference_time:
                self.reference_time = observed

    def _source_time(self, node: Any) -> datetime:
        observed = [
            self.event_times[str(reference)]
            for reference in node.evidence_refs
            if str(reference) in self.event_times
        ]
        return max(observed) if observed else (node.event_time or node.created_at)

    async def search(
        self,
        query: str,
        user_id: str,
        top_k: int = 50,
        rerank: bool = False,
        score_debug: bool = False,
    ) -> list[dict[str, Any]]:
        if user_id != self.user_id:
            raise ValueError("retrieval owner differs from registered pack owner")
        if rerank or score_debug:
            raise ValueError("registered ablation does not enable debug reranking")
        response = await self.engine.retrieve(
            query,
            user_id=user_id,
            reference_time=self.reference_time,
            limit=top_k,
            max_per_source=1,
            max_per_evidence=self.max_per_evidence,
            include_cross_scope=False,
        )
        return [
            {
                "id": str(candidate.node.id),
                "memory": candidate.node.content,
                "score": candidate.composite_score,
                "created_at": self._source_time(candidate.node).isoformat(),
            }
            for candidate in response.results
        ]


def _build_config(pack: Path):
    from prme import PRMEConfig
    from prme.config import EmbeddingConfig, OrganizerConfig

    return PRMEConfig(
        database_url=None,
        encryption_enabled=False,
        enable_qa_pairing=False,
        enable_query_reformulation=False,
        enable_store_supersedence=False,
        enable_surprise_gating=False,
        enable_reranker=False,
        duckdb_threads=1,
        db_path=str(pack / "memory.duckdb"),
        vector_path=str(pack / "vectors.usearch"),
        lexical_path=str(pack / "lexical"),
        embedding=EmbeddingConfig(
            provider="fastembed",
            model_name="BAAI/bge-small-en-v1.5",
            dimension=384,
        ),
        organizer=OrganizerConfig(opportunistic_enabled=False),
    )


def _correct(score: float) -> bool:
    return score >= 0.5


def _paired_comparison(
    baseline: Iterable[dict[str, Any]], candidate: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    baseline_by_id = {item["question_id"]: item for item in baseline}
    candidate_by_id = {item["question_id"]: item for item in candidate}
    if set(baseline_by_id) != set(candidate_by_id):
        raise ValueError("baseline and candidate question identities differ")
    wins = losses = ties = 0
    score_delta = 0.0
    transitions: list[dict[str, Any]] = []
    for question_id in sorted(baseline_by_id):
        before = baseline_by_id[question_id]["cutoff_results"]["top_50"]["score"]
        after = candidate_by_id[question_id]["cutoff_results"]["top_50"]["score"]
        before_pass = _correct(before)
        after_pass = _correct(after)
        if after_pass and not before_pass:
            wins += 1
        elif before_pass and not after_pass:
            losses += 1
        else:
            ties += 1
        score_delta += after - before
        transitions.append(
            {
                "question_id": question_id,
                "baseline_score": before,
                "candidate_score": after,
                "pass_transition": f"{before_pass}->{after_pass}",
            }
        )
    return {
        "pass_level_wins": wins,
        "pass_level_losses": losses,
        "pass_level_ties": ties,
        "mean_score_delta": round(score_delta / len(transitions), 5),
        "transitions": transitions,
    }


async def _run(args: argparse.Namespace) -> None:
    from benchmarks.beam.run import (
        compute_beam_metrics,
        extract_probing_questions,
        process_question,
    )
    from benchmarks.common.llm_client import LLMClient
    from prme import MemoryEngine

    registration = _load_object(args.registration)
    _verify_registration(
        registration,
        project_root=args.project_root,
        upstream_root=args.upstream_root,
        dataset=args.dataset,
        source_pack=args.source_pack,
        baseline_result=args.baseline_result,
    )
    _verify_models(registration)
    protocol = registration["protocol"]
    if protocol.get("max_per_evidence") != 1 or protocol.get("top_k") != 50:
        raise ValueError("unsupported registered treatment")
    output = args.output
    work_pack = output / "work-pack"
    questions_dir = output / "questions"
    if output.exists() and not args.resume:
        raise ValueError("output exists; use --resume or choose a fresh directory")
    output.mkdir(parents=True, exist_ok=args.resume)
    questions_dir.mkdir(exist_ok=True)
    if not work_pack.exists():
        shutil.copytree(args.source_pack, work_pack)

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    conversation = dataset[protocol["conversation_index"]]
    questions = extract_probing_questions(conversation)
    if len(questions) != 20:
        raise ValueError("registered BEAM conversation must contain 20 questions")
    baseline = _load_object(args.baseline_result)
    baseline_evaluations = baseline.get("evaluations")
    if not isinstance(baseline_evaluations, list):
        raise ValueError("baseline result has no evaluations")

    models = registration["models"]
    answerer = LLMClient(
        model=models["answerer"]["model"],
        provider="openai",
        api_key="ollama",
        base_url=models["answerer"]["base_url"],
        rpm=protocol["rpm"],
        timeout=protocol["model_timeout_seconds"],
    )
    judge = LLMClient(
        model=models["judge"]["model"],
        provider="openai",
        api_key="ollama",
        base_url=models["judge"]["base_url"],
        rpm=protocol["rpm"],
        timeout=protocol["model_timeout_seconds"],
    )
    logger = logging.getLogger("beam-retrieval-ablation")
    logging.basicConfig(level=logging.INFO)
    existing: dict[str, dict[str, Any]] = {}
    for path in questions_dir.glob("*.json"):
        item = _load_object(path)
        if isinstance(item.get("question_id"), str):
            existing[item["question_id"]] = item

    async with MemoryEngine.open(_build_config(work_pack)) as engine:
        retriever = _FrozenPackRetriever(
            engine,
            user_id=protocol["user_id"],
            max_per_evidence=protocol["max_per_evidence"],
        )
        await retriever.initialize()
        evaluations: list[dict[str, Any]] = []
        for index, question in enumerate(questions):
            question_id = (
                f"{protocol['chat_size']}_{protocol['conversation_index']}_q"
                f"{index}_{question['question_type']}"
            )
            item = existing.get(question_id)
            if item is None:
                item = await process_question(
                    question_data=question,
                    qi=index,
                    chat_size=protocol["chat_size"],
                    conv_idx=protocol["conversation_index"],
                    user_id=protocol["user_id"],
                    mem0=retriever,
                    answerer=answerer,
                    judge_llm=judge,
                    cutoffs=[50],
                    top_k=50,
                    predict_only=False,
                    logger=logger,
                    conversation_meta=conversation,
                )
                path = questions_dir / f"{question_id}.json"
                path.write_text(
                    json.dumps(item, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                    encoding="utf-8",
                )
                print(
                    f"[{index + 1}/20] {question_id}: "
                    f"{item['cutoff_results']['top_50']['score']}"
                )
            evaluations.append(item)

    metrics = compute_beam_metrics(evaluations, [50])
    result = {
        "schema_version": 1,
        "kind": "beam-retrieval-ablation-execution",
        "registration": args.registration.name,
        "registration_sha256": _digest(args.registration),
        "baseline_result": args.baseline_result.name,
        "protocol": protocol,
        "models": models,
        "metrics_by_cutoff": metrics,
        "paired_baseline_comparison": _paired_comparison(
            baseline_evaluations, evaluations
        ),
        "evaluations": evaluations,
    }
    result_path = output / "beam-retrieval-ablation-result.json"
    result_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(result_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", required=True, type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--upstream-root", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--source-pack", required=True, type=Path)
    parser.add_argument("--baseline-result", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
