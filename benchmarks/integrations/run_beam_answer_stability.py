"""Repeat BEAM answers over two frozen retrieval artifacts in interleaved order.

This diagnostic runner makes no retrieval calls. It verifies a registered
dataset, upstream checkout, model manifests, and two complete BEAM question
artifacts, then gives their exact saved result lists to the pinned upstream
answerer and judge. Repeated, alternating arm order measures reader/judge
variance separately from retrieval generation.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
from pathlib import Path
import subprocess
from typing import Any
from urllib.request import ProxyHandler, build_opener


REGISTRATION_KIND = "beam-answer-stability-registration"
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


def _verify_registration(
    registration: dict[str, Any],
    *,
    project_root: Path,
    upstream_root: Path,
    dataset: Path,
    baseline_artifact: Path,
    candidate_artifact: Path,
) -> None:
    if registration.get("schema_version") != REGISTRATION_SCHEMA:
        raise ValueError("unsupported answer-stability registration schema")
    if registration.get("kind") != REGISTRATION_KIND:
        raise ValueError("unexpected answer-stability registration kind")
    if registration.get("status") != "preregistered":
        raise ValueError("answer-stability trial must be preregistered")
    source = registration.get("source")
    if not isinstance(source, dict):
        raise ValueError("registration is missing source identity")
    if source.get("prme_revision") != _git_revision(project_root):
        raise ValueError("PRME revision differs from registration")
    if source.get("upstream_revision") != _git_revision(upstream_root):
        raise ValueError("upstream revision differs from registration")
    files = source.get("files")
    expected = {
        "runner_sha256": _digest(Path(__file__)),
        "dataset_sha256": _digest(dataset),
        "baseline_artifact_sha256": _digest(baseline_artifact),
        "candidate_artifact_sha256": _digest(candidate_artifact),
    }
    if files != expected:
        raise ValueError("registered answer-stability files differ")
    protocol = registration.get("protocol")
    if not isinstance(protocol, dict):
        raise ValueError("registration is missing protocol")
    repeats = protocol.get("repeats")
    order = protocol.get("arm_order")
    if (
        not isinstance(repeats, int)
        or isinstance(repeats, bool)
        or repeats < 2
        or not isinstance(order, list)
        or len(order) != repeats
        or any(
            item not in (["baseline", "candidate"], ["candidate", "baseline"])
            for item in order
        )
    ):
        raise ValueError("invalid registered repetition order")
    _verify_models(registration)


class _FrozenResultsRetriever:
    def __init__(self, artifact: dict[str, Any]):
        retrieval = artifact.get("retrieval")
        if not isinstance(retrieval, dict):
            raise ValueError("frozen question artifact has no retrieval")
        results = retrieval.get("search_results")
        if not isinstance(results, list) or not results:
            raise ValueError("frozen question artifact has no search results")
        self.query = artifact.get("question")
        self.user_id = artifact.get("user_id")
        self.results = results

    async def search(
        self,
        query: str,
        user_id: str,
        top_k: int = 50,
        rerank: bool = False,
        score_debug: bool = False,
    ) -> list[dict[str, Any]]:
        if query != self.query or user_id != self.user_id:
            raise ValueError("answer-stability request differs from frozen artifact")
        if top_k != 50 or rerank or score_debug:
            raise ValueError("answer-stability retrieval settings differ")
        return [dict(item) for item in self.results]


def _summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    by_arm: dict[str, dict[int, float]] = {"baseline": {}, "candidate": {}}
    for sample in samples:
        arm = sample["arm"]
        repeat = sample["repeat"]
        if arm not in by_arm or repeat in by_arm[arm]:
            raise ValueError("samples must contain one result per arm and repeat")
        by_arm[arm][repeat] = sample["evaluation"]["cutoff_results"]["top_50"]["score"]
    repeat_ids = set(by_arm["baseline"])
    if repeat_ids != set(by_arm["candidate"]):
        raise ValueError("baseline and candidate repeats differ")
    ordered_scores = {
        arm: [scores[repeat] for repeat in sorted(scores)]
        for arm, scores in by_arm.items()
    }
    arms = {
        arm: {
            "scores": scores,
            "mean_score": round(sum(scores) / len(scores), 6),
            "pass_count": sum(score >= 0.5 for score in scores),
            "repeats": len(scores),
        }
        for arm, scores in ordered_scores.items()
    }
    paired = []
    for repeat in sorted(repeat_ids):
        before = by_arm["baseline"][repeat]
        after = by_arm["candidate"][repeat]
        paired.append(
            {
                "repeat": repeat,
                "baseline_score": before,
                "candidate_score": after,
                "score_delta": round(after - before, 6),
                "pass_transition": f"{before >= 0.5}->{after >= 0.5}",
            }
        )
    return {
        "arms": arms,
        "candidate_mean_delta": round(
            arms["candidate"]["mean_score"] - arms["baseline"]["mean_score"],
            6,
        ),
        "paired": paired,
    }


async def _run(args: argparse.Namespace) -> None:
    from benchmarks.beam.run import extract_probing_questions, process_question
    from benchmarks.common.llm_client import LLMClient

    registration = _load_object(args.registration)
    _verify_registration(
        registration,
        project_root=args.project_root,
        upstream_root=args.upstream_root,
        dataset=args.dataset,
        baseline_artifact=args.baseline_artifact,
        candidate_artifact=args.candidate_artifact,
    )
    output = args.output
    samples_dir = output / "samples"
    if output.exists() and not args.resume:
        raise ValueError("output exists; use --resume or choose a fresh directory")
    output.mkdir(parents=True, exist_ok=args.resume)
    samples_dir.mkdir(exist_ok=True)

    protocol = registration["protocol"]
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    conversation = dataset[protocol["conversation_index"]]
    questions = extract_probing_questions(conversation)
    question_index = protocol["question_index"]
    question = questions[question_index]
    artifacts = {
        "baseline": _load_object(args.baseline_artifact),
        "candidate": _load_object(args.candidate_artifact),
    }
    expected_question_id = (
        f"{protocol['chat_size']}_{protocol['conversation_index']}_q"
        f"{question_index}_{question['question_type']}"
    )
    question_text = question.get("question_text", question.get("question", ""))
    for artifact in artifacts.values():
        retrieval = artifact.get("retrieval", {})
        if (
            artifact.get("question_id") != expected_question_id
            or artifact.get("question") != question_text
            or retrieval.get("search_query") != question_text
            or retrieval.get("total_results")
            != len(retrieval.get("search_results", ()))
        ):
            raise ValueError(
                "frozen artifact question identity differs from registration"
            )

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

    samples: list[dict[str, Any]] = []
    logger = logging.getLogger("beam-answer-stability")
    for repeat, order in enumerate(protocol["arm_order"]):
        for arm in order:
            path = samples_dir / f"repeat-{repeat:02d}-{arm}.json"
            if path.exists():
                sample = _load_object(path)
            else:
                artifact = artifacts[arm]
                evaluation = await process_question(
                    question_data=question,
                    qi=question_index,
                    chat_size=protocol["chat_size"],
                    conv_idx=protocol["conversation_index"],
                    user_id=artifact["user_id"],
                    mem0=_FrozenResultsRetriever(artifact),
                    answerer=answerer,
                    judge_llm=judge,
                    cutoffs=[50],
                    top_k=50,
                    predict_only=False,
                    logger=logger,
                    conversation_meta=conversation,
                )
                sample = {"repeat": repeat, "arm": arm, "evaluation": evaluation}
                path.write_text(
                    json.dumps(sample, indent=2, ensure_ascii=False, allow_nan=False)
                    + "\n",
                    encoding="utf-8",
                )
            samples.append(sample)
            score = sample["evaluation"]["cutoff_results"]["top_50"]["score"]
            print(f"repeat={repeat} arm={arm} score={score}")

    result = {
        "schema_version": 1,
        "kind": "beam-answer-stability-execution",
        "registration": args.registration.name,
        "registration_sha256": _digest(args.registration),
        "protocol": protocol,
        "models": models,
        "summary": _summarize(samples),
        "samples": samples,
    }
    result_path = output / "beam-answer-stability-result.json"
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
    parser.add_argument("--baseline-artifact", required=True, type=Path)
    parser.add_argument("--candidate-artifact", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
