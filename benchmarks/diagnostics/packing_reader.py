"""Prepare and run a paired development reader study on frozen product contexts.

Gold references are exported separately and never read by the generation worker.
Predictions are not correctness judgments or a benchmark leadership claim.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import sys
import urllib.request
from unittest.mock import patch

from benchmarks.diagnostics._process import checked_report
from benchmarks.llm_judge import GENERATION_SYSTEM_PROMPT
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context
from prme.retrieval.tokenization import count_tokens

ARMS = ("density", "score")
OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 65536, "num_predict": 1024}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(canonical(value) + b"\n")
    temporary.replace(path)


def prepare(report: dict, snapshots: Path, dataset_raw: bytes, *, budget: int = 4096) -> tuple[dict, list]:
    if (not report.get("complete") or report.get("errors") or report.get("process_exit_code") != 0
            or report["dataset"]["split"] != "dev"):
        raise ValueError("A complete, normally exited development capture is required")
    if digest(dataset_raw) != report["dataset"]["sha256"]:
        raise ValueError("Dataset hash differs from the retrieval capture")
    if budget not in report["budgets"]:
        raise ValueError("Reader budget must be one of the captured study budgets")
    selected = report["dataset"]["selected_question_ids"]
    rows = report["details"]
    ids = [row["question_id"] for row in rows]
    if (not selected or len(set(selected)) != len(selected) or len(set(ids)) != len(ids)
            or set(ids) != set(selected) or any("error" in row for row in rows)):
        raise ValueError("Every selected question needs one successful capture")
    dataset = json.loads(dataset_raw)
    questions = {row["question_id"]: row for row in dataset}
    if len(questions) != len(dataset) or not set(selected) <= questions.keys():
        raise ValueError("Dataset question identities are ambiguous or missing")
    config = PackingConfig.model_validate(report["provenance"]["engine_config"]["packing"])
    prepared, references = [], []
    for row in rows:
        qid = row["question_id"]
        filename = digest(qid.encode()) + ".json"
        ref = row["candidate_snapshot"]
        if ref["filename"] != filename:
            raise ValueError("Snapshot filename differs from question identity")
        raw = (snapshots / filename).read_bytes()
        if digest(raw) != ref["sha256"]:
            raise ValueError("Snapshot hash mismatch")
        saved = json.loads(raw)
        if saved["question_id"] != qid or saved["packing_config"] != config.model_dump(mode="json"):
            raise ValueError("Snapshot identity/configuration mismatch")
        candidates = [RetrievalCandidate.model_validate(value) for value in saved["candidates"]]
        before = canonical([value.model_dump(mode="json") for value in candidates])
        control = pack_context(candidates, config)
        if control.render() != saved["control"]["context"] or control.tokens_used != saved["control"]["tokens"]:
            raise ValueError("Original product context does not reproduce")
        contexts = {}
        current = config.model_copy(update={"token_budget": budget})
        for arm in ARMS:
            if arm == "score":
                with patch("prme.retrieval.packing.compute_str", lambda candidate: candidate.composite_score):
                    bundle = pack_context(candidates, current)
            else:
                bundle = pack_context(candidates, current)
            context = bundle.render()
            tokens = count_tokens(context, config.tokenizer)
            if tokens != bundle.tokens_used or tokens > max(0, budget-current.overhead_tokens):
                raise ValueError("Packed context violates the measured token budget")
            contexts[arm] = {"context": context, "sha256": digest(context.encode()), "tokens": tokens}
        if before != canonical([value.model_dump(mode="json") for value in candidates]):
            raise ValueError("Packing mutated the frozen candidates")
        question = questions[qid]
        prepared.append({"question_id": qid, "question": question["question"],
                         "question_date": question["question_date"], "contexts": contexts,
                         "candidate_snapshot_sha256": ref["sha256"]})
        references.append({key: question[key] for key in ("question_id", "question", "question_date", "question_type", "answer")})
    return {"schema_version": 1, "kind": "development-packing-reader-inputs", "budget": budget,
            "dataset": report["dataset"], "source_report_canonical_sha256": digest(canonical(report)),
            "packing_module_sha256": digest(Path(inspect.getfile(pack_context)).read_bytes()),
            "generation_system_prompt": GENERATION_SYSTEM_PROMPT,
            "rows": prepared, "limitations": ["Development cohort; not independent confirmation.",
                "Predictions require separate independent judging.", "No production packing default is changed."]}, references


def payload(row: dict, arm: str, prepared: dict, model: str) -> dict:
    context = row["contexts"][arm]
    if digest(context["context"].encode()) != context["sha256"]:
        raise ValueError("Prepared context checksum mismatch")
    messages = [{"role": "system", "content": prepared["generation_system_prompt"]},
                {"role": "user", "content": "QUESTION DATE:\n" + row["question_date"] +
                 "\n\nMEMORY:\n" + context["context"] + "\n\nQUESTION:\n" + row["question"]}]
    # Conservative byte-based headroom, not a claim of exact model tokenization.
    # Never knowingly rely on the server truncating a submitted context.
    if sum(len(message["content"].encode()) for message in messages) + 4096 + OPTIONS["num_predict"] > OPTIONS["num_ctx"]:
        raise ValueError("Prompt exceeds conservative reader context headroom")
    return {"model": model, "messages": messages, "stream": False, "think": False, "options": dict(OPTIONS)}


def request(base_url: str, endpoint: str, body=None):
    req = urllib.request.Request(base_url.rstrip("/") + endpoint,
        data=None if body is None else canonical(body), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as response:
        return json.load(response)


def model_digest(base_url: str, model: str) -> str:
    matches = [value["digest"] for value in request(base_url, "/api/tags")["models"] if value["name"] == model]
    if len(matches) != 1:
        raise ValueError("Reader model must resolve to exactly one installed tag")
    return matches[0]


def validate_response(response: dict) -> str:
    if response.get("done") is not True or response.get("done_reason") != "stop":
        raise ValueError("Reader response is incomplete or truncated")
    message = response.get("message", {})
    answer = message.get("content")
    if not isinstance(answer, str) or not answer.strip() or message.get("tool_calls"):
        raise ValueError("Reader did not return a final text answer")
    if not isinstance(response.get("prompt_eval_count"), int) or not isinstance(response.get("eval_count"), int):
        raise ValueError("Reader response is missing token observations")
    if response["prompt_eval_count"] + response["eval_count"] > OPTIONS["num_ctx"]:
        raise ValueError("Reader token observations exceed the requested context")
    return answer.strip()


@contextmanager
def exclusive_state(path: Path):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def run(prepared_path: Path, state_path: Path, *, model: str, base_url: str) -> dict:
    raw = prepared_path.read_bytes()
    prepared = json.loads(raw)
    if prepared.get("kind") != "development-packing-reader-inputs" or prepared["dataset"]["split"] != "dev":
        raise ValueError("Reader requires frozen development inputs")
    pinned = model_digest(base_url, model)
    identity = {"prepared_sha256": digest(raw), "runner_sha256": digest(Path(__file__).read_bytes()),
                "model": model, "model_digest": pinned, "options": dict(OPTIONS),
                "ollama_version": request(base_url, "/api/version")["version"]}
    if prepared.get("reader") != {key: value for key, value in identity.items() if key != "prepared_sha256"}:
        raise ValueError("Reader runtime differs from the prepared declaration")
    ids = [row["question_id"] for row in prepared["rows"]]
    selected = prepared["dataset"]["selected_question_ids"]
    if len(ids) != len(selected) or len(set(ids)) != len(ids) or set(ids) != set(selected):
        raise ValueError("Prepared cohort differs from the selected questions")
    jobs = []
    for row in prepared["rows"]:
        # Fixed per-question order, independent of answers and quality outcomes.
        order = ARMS if int(digest(row["question_id"].encode()), 16) % 2 else tuple(reversed(ARMS))
        for arm in order:
            body = payload(row, arm, prepared, model)
            jobs.append((row, arm, body, digest(canonical(body))))
    if not jobs or len({(row["question_id"], arm) for row, arm, _, _ in jobs}) != len(jobs):
        raise ValueError("Prepared reader jobs are empty or duplicated")
    with exclusive_state(state_path):
        state = json.loads(state_path.read_text()) if state_path.exists() else {
            "identity": identity, "started_at": datetime.now(timezone.utc).isoformat(),
            "generations": {}, "failed_attempts": [], "complete": False}
        if state["identity"] != identity:
            raise ValueError("Resume identity differs from the frozen reader study")
        wanted = {key for _, _, _, key in jobs}
        if not set(state["generations"]) <= wanted:
            raise ValueError("State contains unrelated generations")
        for saved in state["generations"].values():
            if digest(canonical(saved["response"])) != saved["response_sha256"]:
                raise ValueError("Saved response checksum mismatch")
            validate_response(saved["response"])
        write(state_path, state)
        for index, (_, _, body, key) in enumerate(jobs):
            if key not in state["generations"]:
                response = None
                try:
                    if model_digest(base_url, model) != pinned:
                        raise ValueError("Reader model changed")
                    response = request(base_url, "/api/chat", body)
                    validate_response(response)
                    if response.get("model") != model or model_digest(base_url, model) != pinned:
                        raise ValueError("Reader model identity changed during generation")
                    state["generations"][key] = {"response": response, "response_sha256": digest(canonical(response))}
                except Exception as exc:
                    state["failed_attempts"].append({"prompt_sha256": key, "error_type": type(exc).__name__,
                        "at": datetime.now(timezone.utc).isoformat(), "response": response,
                        "response_sha256": digest(canonical(response)) if response is not None else None})
                    write(state_path, state)
                    raise
                write(state_path, state)
            print(f"Reader {index+1}/{len(jobs)}; unique generations={len(state['generations'])}", flush=True)
        state["complete"] = True
        write(state_path, state)
        rows = [{"question_id": row["question_id"], "arm": arm, "context_sha256": row["contexts"][arm]["sha256"],
                 "prompt_sha256": key, "hypothesis": validate_response(state["generations"][key]["response"])}
                for row, arm, _, key in jobs]
        return {"passed": True, "complete": True, "identity": identity, "started_at": state["started_at"], "question_count": len(prepared["rows"]),
                "logical_predictions": len(jobs), "unique_generations": len(state["generations"]),
                "reused_predictions": len(jobs)-len(state["generations"]), "prior_failed_attempts": state["failed_attempts"],
                "rows": rows, "limits": "Development predictions from one local reader; independent judging is still required."}


def export_predictions(result: dict, directory: Path) -> None:
    if not result.get("passed") or not result.get("complete") or result.get("process_exit_code") != 0:
        raise ValueError("Only complete predictions with verified process exit zero may be exported")
    ids_by_arm = {}
    for arm in ARMS:
        ids = [row["question_id"] for row in result["rows"] if row["arm"] == arm]
        if len(ids) != result["question_count"] or len(set(ids)) != len(ids):
            raise ValueError("Every arm must cover every question exactly once")
        ids_by_arm[arm] = set(ids)
    if ids_by_arm["density"] != ids_by_arm["score"] or len(result["rows"]) != 2*result["question_count"]:
        raise ValueError("Prediction arms differ or include unknown rows")
    directory.mkdir(parents=True, exist_ok=True)
    for arm in ARMS:
        lines = [canonical({"question_id": row["question_id"], "hypothesis": row["hypothesis"]})
                 for row in result["rows"] if row["arm"] == arm]
        (directory / f"{arm}.jsonl").write_bytes(b"\n".join(lines) + b"\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--snapshots", type=Path)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--references", type=Path)
    parser.add_argument("--prepared", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--predictions-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.source:
        if not all((args.snapshots, args.dataset, args.references)):
            parser.error("Preparation requires --snapshots, --dataset and --references")
        source_raw = args.source.read_bytes()
        prepared, references = prepare(json.loads(source_raw), args.snapshots, args.dataset.read_bytes())
        prepared["source_report_file_sha256"] = digest(source_raw)
        prepared["prepared_at"] = datetime.now(timezone.utc).isoformat()
        prepared["reader"] = {"model": args.model, "model_digest": model_digest(args.base_url, args.model),
            "options": dict(OPTIONS), "runner_sha256": digest(Path(__file__).read_bytes()),
            "ollama_version": request(args.base_url, "/api/version")["version"]}
        write(args.output, prepared)
        write(args.references, references)
        print(f"Prepared {len(prepared['rows'])} paired questions", flush=True)
        return
    if not args.prepared or not args.state:
        parser.error("Generation requires --prepared and --state")
    try:
        result = run(args.prepared, args.state, model=args.model, base_url=args.base_url) if args.worker else checked_report(
            [sys.executable, "-m", "benchmarks.diagnostics.packing_reader", "--worker", "--prepared", str(args.prepared),
             "--state", str(args.state), "--model", args.model, "--base-url", args.base_url], timeout=21600)
    except Exception as exc:
        result = {"passed": False, "complete": False, "error_type": type(exc).__name__}
    write(args.output, result)
    if result["passed"] and not args.worker and args.predictions_dir:
        export_predictions(result, args.predictions_dir)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
