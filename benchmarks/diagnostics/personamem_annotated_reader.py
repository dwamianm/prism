"""Post-hoc reader control using annotation-selected, actually retained sources."""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import importlib
from importlib.metadata import version
import inspect
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from benchmarks.diagnostics.packing_reader import canonical, digest, write, request
from benchmarks.diagnostics.personamem_packing import reader_identity, reader_payload, parse_response
from benchmarks.personamem import cohort_identity, conversation_sources, history_path, question_and_reference
from benchmarks.retrieval_eval import supervise
from prme.retrieval.tokenization import count_tokens

ARMS = ("annotated_source", "no_memory")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def annotated_context(turns, snippet):
    """Annotations select existing role/text matches; their text is never copied."""
    require(isinstance(snippet, list) and bool(snippet), "Nonempty annotated snippet required")
    require(all(isinstance(m, dict) and m.get("role") in ("user", "assistant")
                and isinstance(m.get("content"), str) for m in snippet), "Unsupported snippet format")
    wanted = {(m["role"], m["content"]) for m in snippet}
    selected = [t for t in turns if (t.role, t.content) in wanted]
    text = "\n\n".join(f"[{t.role.upper()} source={t.id}]\n{t.content}" for t in selected)
    tokens = count_tokens(text)
    require(tokens <= 4096, "Do not clip or substitute over-budget annotated sources")
    return {"context": text, "tokens": tokens, "sha256": digest(text.encode()),
            "source_ids": [t.id for t in selected], "source_roles": [t.role for t in selected]}


def inputs(data, pilot_plan, verification):
    plan = json.loads(pilot_plan.read_bytes())
    verified = json.loads(verification.read_bytes())
    require(verified["native_study_exit_code"] == 0 and verified["questions"] == 96
            and verified["contexts_reproduced"] == 288 and verified["plan_sha256"] == digest(pilot_plan.read_bytes()),
            "A completed independently verified pilot is required")
    rows, identity = cohort_identity(data / "benchmark/text/benchmark.csv", data)
    require(identity == plan["cohort"], "Pilot cohort changed")
    prepared, references = [], []
    for row in rows:
        question, reference = question_and_reference(row)
        turns = conversation_sources((data / history_path(row)).read_bytes())
        context = annotated_context(turns, json.loads(row["related_conversation_snippet"]))
        prepared.append({"question": asdict(question), "contexts": {"annotated_source": context,
            "no_memory": {"context": "", "tokens": 0, "sha256": digest(b""), "source_ids": [], "source_roles": []}}})
        references.append(reference)
    require([r["question"]["id"] for r in prepared] == [r["question_id"] for r in verified["details"]], "Pilot cases changed")
    return prepared, references, identity, plan["reader"]


def runtime():
    names = ["benchmarks.diagnostics.personamem_annotated_reader", "benchmarks.diagnostics.personamem_packing",
             "benchmarks.personamem", "benchmarks.diagnostics.packing_reader", "prme.retrieval.tokenization"]
    return {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
            "python": sys.version, "tiktoken": version("tiktoken"),
            "modules": {name: digest(Path(inspect.getfile(importlib.import_module(name))).read_bytes()) for name in names}}


def registered_inputs(args):
    prepared, references, identity, reader = inputs(args.data, args.pilot_plan, args.verification)
    observed = {"pilot_plan_sha256": digest(args.pilot_plan.read_bytes()),
        "pilot_verification_sha256": digest(args.verification.read_bytes()), "cohort": identity,
        "prepared_sha256": digest(canonical(prepared)), "references_sha256": digest(canonical(references)),
        "reader": reader_identity(args.base_url, reader["model"]), "runtime": runtime()}
    require(observed["reader"] == reader, "Use exactly the completed pilot's reader and prompt contract")
    for row in prepared:
        for context in row["contexts"].values():
            reader_payload(row["question"], context["context"], reader["model"])
    return prepared, references, observed


def run(args):
    plan = json.loads(args.plan.read_bytes())
    prepared, references, observed = registered_inputs(args)
    started = datetime.now(timezone.utc).isoformat()
    require(plan["inputs"] == observed and plan["registered_at"] < started, "Registered diagnostic inputs changed")
    report = {"run_id": args.run_id, "complete": False, "errors": 0, "started_at": started,
              "plan_sha256": digest(args.plan.read_bytes()), "details": [], "reader_repeats": []}
    args.artifacts.mkdir(parents=True, exist_ok=False)
    try:
        write(args.artifacts / "prepared.json", prepared)
        write(args.artifacts / "references.json", references)
        model = observed["reader"]["model"]
        for i, row in enumerate(prepared):
            question = row["question"]
            order = sorted(ARMS, key=lambda arm: digest(canonical([question["id"], arm])))
            outputs = {}
            for arm in order:
                context = row["contexts"][arm]
                body = reader_payload(question, context["context"], model)
                response = request(args.base_url, "/api/chat", body)
                outputs[arm] = {"answer": parse_response(response), "request_sha256": digest(canonical(body)),
                                "context_sha256": context["sha256"], "response": response}
            if i % 4 == 0:
                arm = order[0]
                response = request(args.base_url, "/api/chat", reader_payload(question, row["contexts"][arm]["context"], model))
                report["reader_repeats"].append({"question_id": question["id"], "arm": arm,
                    "same_answer": parse_response(response) == outputs[arm]["answer"], "response": response})
            report["details"].append({"question_id": question["id"], "persona_id": question["persona_id"],
                                      "arm_order": order, "arms": outputs})
            print(f"Answered {i + 1}/96 diagnostic questions", flush=True)
        require(registered_inputs(args)[2] == observed, "Diagnostic inputs changed during execution")
        report["complete"] = True
    except BaseException as exc:
        report["errors"] = 1
        report["error_type"] = type(exc).__name__
        raise
    finally:
        write(args.output, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["register", "run"])
    for name in ("data", "pilot-plan", "verification", "plan"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("output", "artifacts"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--base-url", default="http://localhost:11434")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.mode == "register":
        require(not args.plan.exists(), "Refusing to overwrite previous registration")
        prepared, _, observed = registered_inputs(args)
        require(not observed["runtime"]["dirty"], "Commit the diagnostic before registration")
        write(args.plan, {"registered_at": datetime.now(timezone.utc).isoformat(), "inputs": observed,
            "arms": ARMS, "maximum_memory_tokens": max(r["contexts"]["annotated_source"]["tokens"] for r in prepared),
            "empty_annotated_contexts": sum(not r["contexts"]["annotated_source"]["context"] for r in prepared),
            "protocol": ["Post-hoc diagnostic on the completed pilot cohort; not a new holdout.",
                "Annotations only select exact role/text matches in actual source history, in original order.",
                "No initial oracle persona, unmatched annotation text or answer label enters context.",
                "Retain every case and full matched passages; no clipping, exclusion or correctness-based retry.",
                "Unchanged reader, prompt, options and option ordering; fresh no-memory control.",
                "Inspect no new answer quality before all 192 answers complete with native exit zero.",
                "Invalid final JSON counts incorrect. Report all categories and paired persona-cluster intervals.",
                "Annotation selection and shorter source-only formatting are confounded; no product retrieval or leaderboard claim."],
            "statistics": "paired persona-cluster bootstrap, 2000 samples, seed 42; compare annotated_source with no_memory",
            "default_promotion": False})
        return
    require(args.output is not None and args.artifacts is not None, "Output and artifacts required")
    if args.worker:
        run(args)
        return
    require(not args.output.exists() and not args.artifacts.exists(), "Refusing to overwrite previous diagnostic")
    run_id = str(uuid4())
    result = supervise(args.output, [sys.executable, "-m", __spec__.name, *sys.argv[1:], "--worker", "--run-id", run_id], run_id)
    raise SystemExit(0 if result["complete"] else 1)


if __name__ == "__main__":
    main()
