"""Fixed-reader ablation of singly annotated sources in balanced contexts.

This is a development diagnostic over previously examined LongMemEval-derived
questions. Selection uses source annotations and context membership only; every
qualifying question is retained. Baseline reader/judge outcomes are reused from
the complete balanced study and counterfactual outcomes receive no retries.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from benchmarks.diagnostics import balanced_packing_answer as base
from benchmarks.diagnostics import reader_judge
from benchmarks.diagnostics.hindsight_capture import canonical, digest, write
from prme.retrieval.config import PackingConfig
from prme.retrieval.credit import ablate_context
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context
from prme.retrieval.tokenization import count_tokens


EXPECTED_CASES = 37
ARM = "source_ablation"


def source_paths(root: Path) -> dict[str, Path]:
    paths = base.base.source_paths(root)
    prior = root / "data/benchmarks/balanced-qwen35b-all-v2"
    paths.update({
        "prior_registration": (
            root / "benchmarks/results/research/2026-09-13/"
            "balanced-qwen35b-all-v2-registration.json"
        ),
        "prior_reader": prior / "reader.json",
        "prior_reader_state": prior / "reader-state.json",
        "prior_judge": prior / "judge.json",
        "prior_results": prior / "results.json",
    })
    return paths


def source_identity(paths: dict[str, Path]) -> dict[str, str]:
    return {
        key: digest(path.read_bytes())
        for key, path in paths.items()
        if key not in {"snapshots"}
    }


def prepare(root: Path) -> dict:
    """Reproduce all qualifying baselines, then remove their sole gold source."""
    paths = source_paths(root)
    prepared_baseline = base.prepare(root)
    baseline_by_id = {row["case_id"]: row for row in prepared_baseline["rows"]}
    references = {
        row["question_id"]: row
        for row in json.loads(paths["references"].read_bytes())["references"]
    }
    composition = json.loads(paths["composition"].read_bytes())
    rows = []
    for result in composition["details"]:
        question_id = result["question_id"]
        reference = references[question_id]
        evidence = reference["evidence_source_ids"]
        retained = result["arms"]["head1_quarter:4096"]["content_source_ids"]
        if len(evidence) != 1 or evidence[0] not in retained:
            continue

        snapshot_ref = result["snapshot"]
        snapshot_raw = (paths["snapshots"] / snapshot_ref["filename"]).read_bytes()
        if digest(snapshot_raw) != snapshot_ref["sha256"]:
            raise ValueError("Candidate snapshot checksum mismatch")
        snapshot = json.loads(snapshot_raw)
        candidates = [
            RetrievalCandidate.model_validate(candidate)
            for candidate in snapshot["candidates"]["parser"]
        ]
        config = PackingConfig.model_validate(
            snapshot["arms"]["parser:density:4096"]["packing"]
        ).model_copy(update={"multipath_ordering": "balanced"})
        bundle = pack_context(candidates, config)
        baseline = baseline_by_id[reference["case_id"]]
        if (
            bundle.render() != baseline["contexts"]["balanced"]["context"]
            or digest(bundle.render().encode())
            != baseline["contexts"]["balanced"]["sha256"]
        ):
            raise ValueError("Balanced baseline does not reproduce")
        targets = [
            candidate.node.id
            for candidates_in_section in bundle.sections.values()
            for candidate in candidates_in_section
            if (candidate.node.metadata or {}).get("source_turn") == evidence[0]
        ]
        if len(targets) != 1:
            raise ValueError("Singly annotated source does not map to one packed entry")
        ablation = ablate_context(bundle, targets)
        context = ablation.counterfactual.render()
        if (
            count_tokens(context, config.tokenizer)
            != ablation.counterfactual.tokens_used
            or str(targets[0]) in context
            or ablation.baseline_context_sha256
            != baseline["contexts"]["balanced"]["sha256"]
        ):
            raise ValueError("Context ablation does not reproduce its manifest")
        rows.append({
            "case_id": question_id,
            "question": baseline["question"],
            "question_date": baseline["question_date"],
            "category": reference["category"],
            "source_id": evidence[0],
            "node_id": str(targets[0]),
            "baseline_context_sha256": ablation.baseline_context_sha256,
            "context": context,
            "context_sha256": ablation.counterfactual_context_sha256,
            "tokens": ablation.counterfactual.tokens_used,
        })
    rows.sort(key=lambda row: row["case_id"])
    if len(rows) != EXPECTED_CASES or len({row["case_id"] for row in rows}) != EXPECTED_CASES:
        raise ValueError(f"Expected exactly {EXPECTED_CASES} qualifying questions")
    return {
        "kind": "balanced-gold-source-context-ablation",
        "schema_version": 1,
        "selection": "exactly one annotated source retained as content in balanced 4K context",
        "source_identity": source_identity(paths),
        "rows": rows,
    }


def reader_payload(row: dict, declaration: dict) -> dict:
    if digest(row["context"].encode()) != row["context_sha256"]:
        raise ValueError("Prepared ablation context checksum mismatch")
    adapted = {
        "question": row["question"],
        "question_date": row["question_date"],
        "contexts": {ARM: {
            "context": row["context"],
            "sha256": row["context_sha256"],
            "tokens": row["tokens"],
        }},
    }
    return base.reader_payload(adapted, ARM, declaration)


def register(root: Path, directory: Path, registration: Path, base_url: str) -> None:
    if directory.exists() or registration.exists():
        raise ValueError("Fresh registration and study directory required")
    prepared = prepare(root)
    paths = source_paths(root)
    prior = json.loads(paths["prior_registration"].read_bytes())
    reader = base.reader_declaration(base_url)
    judge = reader_judge.declaration(
        base.base.JUDGE_MODEL, base_url, prior["judge"]["controls_sha256"]
    )
    if reader != prior["reader"] or judge != prior["judge"]:
        raise ValueError("Reader or judge differs from the complete baseline study")
    reader_judge.validate_calibration(
        json.loads(paths["controls"].read_bytes()),
        json.loads(paths["calibration"].read_bytes()),
        judge,
    )
    directory.mkdir(parents=True)
    prepared_path = directory / "prepared.json"
    write(prepared_path, prepared)
    write(registration, {
        "kind": "balanced-gold-source-context-ablation",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "implementation_sha256": digest(Path(__file__).read_bytes()),
        "reader_implementation_sha256": digest(Path(base.__file__).read_bytes()),
        "prepared_sha256": digest(prepared_path.read_bytes()),
        "source_identity": prepared["source_identity"],
        "case_ids": [row["case_id"] for row in prepared["rows"]],
        "reader": reader,
        "judge": judge,
        "limits": [
            "Development diagnostic over previously examined questions and outcomes; not an independent holdout.",
            "Selection uses only frozen source annotations and balanced-context membership; all qualifying questions are retained.",
            "This exact packed-context ablation is narrower than memory-bank deletion and re-retrieval.",
            "Annotated sources are application-verified evidence, not model-reported citations.",
            "The study does not establish that a non-flipping source is irrelevant; other context or model priors may be sufficient.",
            "One local reader and one custom calibrated local judge are used, with no outcome retries.",
        ],
    })


def verify_registration(root: Path, directory: Path, registration: Path,
                        base_url: str) -> tuple[dict, dict]:
    declared = json.loads(registration.read_bytes())
    prepared_path = directory / "prepared.json"
    prepared = json.loads(prepared_path.read_bytes())
    paths = source_paths(root)
    if (
        declared["implementation_sha256"] != digest(Path(__file__).read_bytes())
        or declared["reader_implementation_sha256"] != digest(Path(base.__file__).read_bytes())
        or declared["prepared_sha256"] != digest(prepared_path.read_bytes())
        or declared["source_identity"] != source_identity(paths)
        or prepared != prepare(root)
        or declared["case_ids"] != [row["case_id"] for row in prepared["rows"]]
        or declared["reader"] != base.reader_declaration(base_url)
        or declared["judge"] != reader_judge.declaration(
            base.base.JUDGE_MODEL,
            base_url,
            declared["judge"]["controls_sha256"],
        )
    ):
        raise ValueError("Registration, inputs, or local models changed")
    reader_judge.validate_calibration(
        json.loads(paths["controls"].read_bytes()),
        json.loads(paths["calibration"].read_bytes()),
        declared["judge"],
    )
    return declared, prepared


def run_reader(prepared: dict, declaration: dict, state_path: Path,
               base_url: str) -> dict:
    jobs = [
        (row, reader_payload(row, declaration))
        for row in prepared["rows"]
    ]
    identity = {"prepared_sha256": digest(canonical(prepared)), "reader": declaration}
    with base.base.runtime.exclusive_state(state_path):
        state = json.loads(state_path.read_bytes()) if state_path.exists() else {
            "identity": identity,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "generations": {},
            "failed_attempts": [],
            "complete": False,
        }
        if state["identity"] != identity or state["failed_attempts"]:
            raise ValueError("Reader study cannot resume after changed input or a failed call")
        wanted = {digest(canonical(body)) for _, body in jobs}
        if not set(state["generations"]) <= wanted:
            raise ValueError("Reader state contains unrelated responses")
        for saved in state["generations"].values():
            if digest(canonical(saved["response"])) != saved["response_sha256"]:
                raise ValueError("Saved reader response changed")
            base.base._answer(saved["response"], declaration["model"])
        write(state_path, state)
        for index, (_, body) in enumerate(jobs):
            key = digest(canonical(body))
            if key not in state["generations"]:
                response = None
                try:
                    if base.base.runtime.model_digest(base_url, declaration["model"]) != declaration["model_digest"]:
                        raise ValueError("Reader model changed before generation")
                    response = base.base._request(base_url, body)
                    base.base._answer(response, declaration["model"])
                    if base.base.runtime.model_digest(base_url, declaration["model"]) != declaration["model_digest"]:
                        raise ValueError("Reader model changed during generation")
                    state["generations"][key] = {
                        "response": response,
                        "response_sha256": digest(canonical(response)),
                    }
                except Exception as exc:
                    state["failed_attempts"].append({
                        "prompt_sha256": key,
                        "error_type": type(exc).__name__,
                        "at": datetime.now(timezone.utc).isoformat(),
                        "response_sha256": digest(canonical(response)) if response else None,
                    })
                    write(state_path, state)
                    raise
                write(state_path, state)
            print(f"Reader {index + 1}/{len(jobs)}", flush=True)
        state["complete"] = True
        write(state_path, state)
    return {
        "complete": True,
        "identity": identity,
        "state_sha256": digest(state_path.read_bytes()),
        "rows": [
            {
                "case_id": row["case_id"],
                "context_sha256": row["context_sha256"],
                "prompt_sha256": digest(canonical(body)),
                "answer": base.base._answer(
                    state["generations"][digest(canonical(body))]["response"],
                    declaration["model"],
                ),
            }
            for row, body in jobs
        ],
    }


def baseline_outcomes(paths: dict[str, Path], registration: dict) -> dict[str, bool]:
    prior = json.loads(paths["prior_results"].read_bytes())
    reader = json.loads(paths["prior_reader"].read_bytes())
    judge = json.loads(paths["prior_judge"].read_bytes())
    state = json.loads(paths["prior_reader_state"].read_bytes())
    if (
        not prior["complete"]
        or prior["registration_sha256"] != digest(paths["prior_registration"].read_bytes())
        or prior["reader_report_sha256"] != digest(paths["prior_reader"].read_bytes())
        or prior["judge_report_sha256"] != digest(paths["prior_judge"].read_bytes())
        or reader["identity"]["reader"] != registration["reader"]
        or judge["identity"]["declaration"] != registration["judge"]
        or state["complete"] is not True
    ):
        raise ValueError("Complete matching baseline outcomes are required")
    outcomes = {
        row["case_id"]: row["answers"]["balanced"]
        for row in prior["details"]
    }
    if len(outcomes) != 119 or any(type(value) is not bool for value in outcomes.values()):
        raise ValueError("Baseline outcome coverage is incomplete")
    return outcomes


def summarize(prepared: dict, reader: dict, judge: dict,
              baseline: dict[str, bool], limits: list[str]) -> dict:
    verdicts = {row["id"]: row["correct"] for row in judge["judgments"]}
    details = []
    for row in prepared["rows"]:
        counterfactual = verdicts[row["case_id"]]
        before = baseline[row["case_id"]]
        tier, value = {
            (True, False): ("load_bearing", 1.0),
            (True, True): ("source_non_flipping", 0.6),
            (False, True): ("misleading_or_distracting", -1.0),
            (False, False): ("wrong_noncuring", 0.0),
        }[(before, counterfactual)]
        details.append({
            "case_id": row["case_id"],
            "category": row["category"],
            "node_id": row["node_id"],
            "source_id": row["source_id"],
            "baseline_correct": before,
            "counterfactual_correct": counterfactual,
            "tier": tier,
            "value": value,
        })
    counts = Counter(row["tier"] for row in details)
    return {
        "complete": True,
        "questions": len(details),
        "baseline_correct": sum(row["baseline_correct"] for row in details),
        "counterfactual_correct": sum(row["counterfactual_correct"] for row in details),
        "transitions": dict(sorted(counts.items())),
        "categories": {
            category: {
                "questions": len(rows),
                "baseline_correct": sum(row["baseline_correct"] for row in rows),
                "counterfactual_correct": sum(row["counterfactual_correct"] for row in rows),
                "load_bearing": sum(row["tier"] == "load_bearing" for row in rows),
            }
            for category in sorted({row["category"] for row in details})
            for rows in [[row for row in details if row["category"] == category]]
        },
        "details": details,
        "reader_state_sha256": reader["state_sha256"],
        "judge_unique_calls": judge["unique_calls"],
        "limits": limits,
    }


def run(root: Path, directory: Path, registration_path: Path,
        base_url: str) -> dict:
    registration, prepared = verify_registration(
        root, directory, registration_path, base_url
    )
    for name in ("reader.json", "judge.json", "results.json"):
        if (directory / name).exists():
            raise ValueError("Fresh study outcomes required")
    reader = run_reader(
        prepared, registration["reader"], directory / "reader-state.json", base_url
    )
    write(directory / "reader.json", reader)
    references = {
        row["question_id"]: row
        for row in json.loads(source_paths(root)["references"].read_bytes())["references"]
    }
    answers = {row["case_id"]: row["answer"] for row in reader["rows"]}
    judge_cases = [
        {
            "id": row["case_id"],
            "question": row["question"],
            "category": row["category"],
            "reference": str(references[row["case_id"]]["answer"]),
            "hypothesis": answers[row["case_id"]],
        }
        for row in prepared["rows"]
    ]
    judge_cases.sort(key=lambda row: row["id"])
    write(directory / "judge-inputs.json", {"cases": judge_cases})
    judge = reader_judge.run_cases(
        judge_cases,
        registration["judge"],
        directory / "judge-state.json",
        base_url,
    )
    if judge["prior_failed_attempts"]:
        raise ValueError("Judge study contains failed calls")
    write(directory / "judge.json", judge)
    result = summarize(
        prepared,
        reader,
        judge,
        baseline_outcomes(source_paths(root), registration),
        registration["limits"],
    )
    result.update(
        registration_sha256=digest(registration_path.read_bytes()),
        reader_report_sha256=digest((directory / "reader.json").read_bytes()),
        judge_report_sha256=digest((directory / "judge.json").read_bytes()),
    )
    write(directory / "results.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("register", "run"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--registration", required=True, type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    root = args.root.resolve()
    directory = args.directory.resolve()
    registration = args.registration.resolve()
    if args.mode == "register":
        register(root, directory, registration, args.base_url)
    else:
        result = run(root, directory, registration, args.base_url)
        print(json.dumps({
            "complete": result["complete"],
            "questions": result["questions"],
            "baseline_correct": result["baseline_correct"],
            "counterfactual_correct": result["counterfactual_correct"],
            "transitions": result["transitions"],
        }))


if __name__ == "__main__":
    main()
