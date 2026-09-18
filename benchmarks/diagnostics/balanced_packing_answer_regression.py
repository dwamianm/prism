"""Answer confirmation for density and balanced packing on the 381-case partition.

The partition was previously examined for source retention, so this is not an
independent benchmark holdout. Answer outcomes are registered before generation
and every fixed density/balanced result is retained.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from benchmarks.diagnostics import balanced_packing_answer as base
from benchmarks.diagnostics import reader_judge
from benchmarks.diagnostics.hindsight_capture import canonical, digest, write
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context
from prme.retrieval.tokenization import count_tokens

ARMS = ("density", "balanced")
BUDGET = 4096


def source_paths(root: Path) -> dict[str, Path]:
    research = root / "benchmarks/results/research/2026-09-12"
    return {
        "source": root / "data/benchmarks/packing-confirmation-1f5375a-source.json",
        "comparison": (
            root
            / "benchmarks/results/packing/2026-09-12/"
            "product-packing-confirmation-1f5375a.json"
        ),
        "regression": root / "data/benchmarks/packing-regression.json",
        "completion": research / "packing-regression-completion.json",
        "verification": research / "packing-regression-verification.json",
        "verifier": research / "packing-regression-verifier-completion.json",
        "dataset": root / "data/benchmarks/longmemeval/longmemeval_s_cleaned.json",
        "contexts": root / "data/benchmarks/packing-regression-contexts",
        "snapshots": root / "data/benchmarks/packing-confirmation-1f5375a-candidates",
        "controls": root / "benchmarks/fixtures/reader_judge_controls.json",
        "calibration": root / "data/benchmarks/public-context-judge-calibration-f511aaa.json",
        "prior_registration": (
            root
            / "benchmarks/results/research/2026-09-13/"
            "balanced-qwen35b-all-v2-registration.json"
        ),
    }


def source_identity(paths: dict[str, Path], regression: dict) -> dict[str, str]:
    identity = {
        key: digest(path.read_bytes())
        for key, path in paths.items()
        if key not in ("contexts", "snapshots")
    }
    identity["context_manifest"] = digest(canonical([
        row["context_file"] for row in regression["details"]
    ]))
    identity["candidate_manifest"] = digest(canonical([
        row["candidate_snapshot"] for row in regression["details"]
    ]))
    return identity


def prepare(root: Path) -> dict:
    """Verify and expose only fixed questions and density/balanced contexts."""
    paths = source_paths(root)
    source_raw = paths["source"].read_bytes()
    source = json.loads(source_raw)
    comparison = json.loads(paths["comparison"].read_bytes())
    regression_raw = paths["regression"].read_bytes()
    regression = json.loads(regression_raw)
    completion = json.loads(paths["completion"].read_bytes())
    verification_raw = paths["verification"].read_bytes()
    verification = json.loads(verification_raw)
    verifier = json.loads(paths["verifier"].read_bytes())
    dataset_raw = paths["dataset"].read_bytes()
    cases = json.loads(dataset_raw)
    selected = source["dataset"]["selected_question_ids"]
    if (
        not source["complete"]
        or source["errors"]
        or source["process_exit_code"] != 0
        or not comparison["complete"]
        or not comparison["baseline_reproduction_passed"]
        or comparison["input_sha256"] != digest(source_raw)
        or not regression["complete"]
        or regression["errors"]
        or regression["contexts_evaluated"] != 4572
        or completion["native_exit_code"] != 0
        or completion["output_sha256"] != digest(regression_raw)
        or not verification["complete"]
        or verification["questions"] != 381
        or verification["contexts_verified"] != 4572
        or verification["output_sha256"] != digest(regression_raw)
        or verifier["native_exit_code"] != 0
        or verifier["verification_sha256"] != digest(verification_raw)
        or digest(dataset_raw) != source["dataset"]["sha256"]
        or len(selected) != 381
        or len(set(selected)) != 381
    ):
        raise ValueError("Complete verified 381-question source evidence is required")
    by_question = {case["question_id"]: case for case in cases}
    by_source = {row["question_id"]: row for row in source["details"]}
    if len(cases) != 500 or not set(selected) <= set(by_question):
        raise ValueError("The complete fixed LongMemEval dataset is required")

    rows = []
    for result in regression["details"]:
        question_id = result["question_id"]
        if question_id not in by_source:
            raise ValueError("Regression question is absent from the source capture")
        context_ref = result["context_file"]
        context_raw = (paths["contexts"] / context_ref["filename"]).read_bytes()
        if digest(context_raw) != context_ref["sha256"]:
            raise ValueError("Saved context checksum mismatch")
        saved_contexts = json.loads(context_raw)["contexts"]

        snapshot_ref = result["candidate_snapshot"]
        source_ref = by_source[question_id]["candidate_snapshot"]
        if snapshot_ref != source_ref:
            raise ValueError("Candidate snapshot identity changed")
        snapshot_raw = (paths["snapshots"] / snapshot_ref["filename"]).read_bytes()
        if digest(snapshot_raw) != snapshot_ref["sha256"]:
            raise ValueError("Candidate snapshot checksum mismatch")
        snapshot = json.loads(snapshot_raw)
        candidates = [
            RetrievalCandidate.model_validate(candidate)
            for candidate in snapshot["candidates"]
        ]
        baseline = PackingConfig.model_validate(snapshot["packing_config"])
        contexts = {}
        for arm, source_arm in (
            ("density", "density:4096"),
            ("balanced", "head1_quarter:4096"),
        ):
            config = baseline.model_copy(update={
                "token_budget": BUDGET,
                "multipath_ordering": arm,
            })
            bundle = pack_context(candidates, config)
            context = bundle.render()
            expected = saved_contexts[source_arm]
            measured = result["arms"][source_arm]
            if (
                context != expected
                or digest(context.encode()) != measured["context_sha256"]
                or bundle.tokens_used != measured["tokens"]
                or count_tokens(context, config.tokenizer) != measured["tokens"]
            ):
                raise ValueError(f"Current {arm} policy differs from saved context")
            contexts[arm] = {
                "context": context,
                "sha256": measured["context_sha256"],
                "tokens": measured["tokens"],
            }
        case = by_question[question_id]
        rows.append({
            "case_id": question_id,
            "question": case["question"],
            "question_date": case["question_date"],
            "contexts": contexts,
        })
    if (
        len(rows) != 381
        or [row["case_id"] for row in rows] != selected
        or len({row["case_id"] for row in rows}) != 381
    ):
        raise ValueError("Expected the fixed ordered 381-question partition")
    return {
        "kind": "balanced-packing-answer-regression-inputs",
        "schema_version": 1,
        "budget": BUDGET,
        "source_identity": source_identity(paths, regression),
        "rows": rows,
    }


def run_reader(prepared: dict, declared: dict, state_path: Path,
               base_url: str) -> dict:
    jobs = []
    for row in prepared["rows"]:
        for arm in sorted(
            ARMS,
            key=lambda value: digest(canonical([row["case_id"], value])),
        ):
            body = base.reader_payload(row, arm, declared)
            jobs.append((row, arm, body, digest(canonical(body))))
    identity = {"prepared_sha256": digest(canonical(prepared)), "reader": declared}
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
        wanted = {key for _, _, _, key in jobs}
        if not set(state["generations"]) <= wanted:
            raise ValueError("Reader state contains unrelated responses")
        for saved in state["generations"].values():
            if digest(canonical(saved["response"])) != saved["response_sha256"]:
                raise ValueError("Saved reader response changed")
            base.base._answer(saved["response"], declared["model"])
        write(state_path, state)
        for index, (_, _, body, key) in enumerate(jobs):
            if key not in state["generations"]:
                response = None
                try:
                    if (
                        base.base.runtime.model_digest(base_url, declared["model"])
                        != declared["model_digest"]
                    ):
                        raise ValueError("Reader model changed before generation")
                    response = base.base._request(base_url, body)
                    base.base._answer(response, declared["model"])
                    if (
                        base.base.runtime.model_digest(base_url, declared["model"])
                        != declared["model_digest"]
                    ):
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
                        "response_sha256": (
                            digest(canonical(response)) if response is not None else None
                        ),
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
                "arm": arm,
                "prompt_sha256": key,
                "context_sha256": row["contexts"][arm]["sha256"],
                "answer": base.base._answer(
                    state["generations"][key]["response"], declared["model"]
                ),
            }
            for row, arm, _, key in jobs
        ],
    }


def register(root: Path, directory: Path, registration: Path, base_url: str) -> None:
    if directory.exists() or registration.exists():
        raise ValueError("Fresh registration and study directory required")
    prepared = prepare(root)
    paths = source_paths(root)
    prior = json.loads(paths["prior_registration"].read_bytes())
    judge = prior["judge"]
    if judge != reader_judge.declaration(
        base.base.JUDGE_MODEL,
        base_url,
        judge["controls_sha256"],
    ):
        raise ValueError("The calibrated judge runtime has changed")
    reader_judge.validate_calibration(
        json.loads(paths["controls"].read_bytes()),
        json.loads(paths["calibration"].read_bytes()),
        judge,
    )
    reader = base.reader_declaration(base_url)
    if reader["model_digest"] == judge["model_digest"]:
        raise ValueError("Reader and judge models must differ")
    directory.mkdir(parents=True)
    prepared_path = directory / "prepared.json"
    write(prepared_path, prepared)
    write(registration, {
        "kind": "balanced-packing-answer-regression",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "implementation_sha256": digest(Path(__file__).read_bytes()),
        "reader_implementation_sha256": digest(Path(base.__file__).read_bytes()),
        "prepared_sha256": digest(prepared_path.read_bytes()),
        "source_identity": prepared["source_identity"],
        "case_ids": [row["case_id"] for row in prepared["rows"]],
        "arms": ARMS,
        "reader": reader,
        "judge": judge,
        "limits": [
            "The 381-question partition was examined for source retention and is not an independent benchmark holdout.",
            "Answer outcomes from this partition were not used before registration.",
            "One local reader and one custom calibrated local judge are used.",
            "Every fixed density and balanced result is retained; no outcome retry or answer-based selection.",
            "Complete coverage and zero provider failures are required.",
        ],
    })


def verify_registration(root: Path, directory: Path, registration: Path,
                        base_url: str) -> tuple[dict, dict]:
    declared = json.loads(registration.read_bytes())
    prepared_path = directory / "prepared.json"
    prepared = json.loads(prepared_path.read_bytes())
    paths = source_paths(root)
    regression = json.loads(paths["regression"].read_bytes())
    if (
        declared["implementation_sha256"] != digest(Path(__file__).read_bytes())
        or declared["reader_implementation_sha256"] != digest(Path(base.__file__).read_bytes())
        or declared["prepared_sha256"] != digest(prepared_path.read_bytes())
        or declared["source_identity"] != source_identity(paths, regression)
        or declared["source_identity"] != prepared["source_identity"]
        or declared["case_ids"] != [row["case_id"] for row in prepared["rows"]]
        or declared["arms"] != list(ARMS)
        or declared["reader"] != base.reader_declaration(base_url)
        or declared["judge"] != reader_judge.declaration(
            base.base.JUDGE_MODEL,
            base_url,
            declared["judge"]["controls_sha256"],
        )
        or prepared != prepare(root)
    ):
        raise ValueError("Registration, sources, models, or prepared contexts changed")
    reader_judge.validate_calibration(
        json.loads(paths["controls"].read_bytes()),
        json.loads(paths["calibration"].read_bytes()),
        declared["judge"],
    )
    return declared, prepared


def summarize(reader: dict, judge: dict, mapping: list[dict], references: dict,
              limits: list[str]) -> dict:
    verdicts = {row["id"]: row["correct"] for row in judge["judgments"]}
    outcomes: dict[str, dict[str, bool]] = {}
    for row in mapping:
        outcomes.setdefault(row["case_id"], {})[row["arm"]] = verdicts[row["id"]]
    details = [
        {
            "case_id": case_id,
            "category": references[case_id]["scoring_category"],
            "answers": values,
        }
        for case_id, values in sorted(outcomes.items())
    ]

    def summary(rows: list[dict]) -> dict:
        pairs = [
            (row["answers"]["density"], row["answers"]["balanced"])
            for row in rows
        ]
        return {
            "questions": len(rows),
            "correct": {
                arm: sum(row["answers"][arm] for row in rows) for arm in ARMS
            },
            "balanced_vs_density": {
                "wins": sum(not before and after for before, after in pairs),
                "losses": sum(before and not after for before, after in pairs),
                "ties": sum(before == after for before, after in pairs),
            },
        }

    categories = sorted({row["category"] for row in details})
    return {
        "complete": True,
        "overall": summary(details),
        "categories": {
            category: summary([row for row in details if row["category"] == category])
            for category in categories
        },
        "details": details,
        "reader_state_sha256": reader["state_sha256"],
        "judge_unique_calls": judge["unique_calls"],
        "limits": limits,
    }


def run(root: Path, directory: Path, registration_path: Path,
        base_url: str) -> dict:
    registration, prepared = verify_registration(
        root, directory, registration_path, base_url,
    )
    for name in ("reader.json", "judge.json", "results.json"):
        if (directory / name).exists():
            raise ValueError("Fresh study outcomes required")
    reader = run_reader(
        prepared,
        registration["reader"],
        directory / "reader-state.json",
        base_url,
    )
    write(directory / "reader.json", reader)

    cases = json.loads(source_paths(root)["dataset"].read_bytes())
    references = {
        row["question_id"]: {
            **row,
            "scoring_category": (
                "abstention"
                if row["question_id"].endswith("_abs")
                else row["question_type"]
            ),
        }
        for row in cases
    }
    questions = {row["case_id"]: row["question"] for row in prepared["rows"]}
    judge_cases, mapping = [], []
    for row in reader["rows"]:
        identity = digest(canonical([row["case_id"], row["arm"]]))
        reference = references[row["case_id"]]
        judge_cases.append({
            "id": identity,
            "question": questions[row["case_id"]],
            "category": reference["scoring_category"],
            "reference": str(reference["answer"]),
            "hypothesis": row["answer"],
        })
        mapping.append({
            "id": identity,
            "case_id": row["case_id"],
            "arm": row["arm"],
        })
    judge_cases.sort(key=lambda row: row["id"])
    mapping.sort(key=lambda row: row["id"])
    write(directory / "judge-inputs.json", {"cases": judge_cases})
    write(directory / "judge-mapping.json", {"mapping": mapping})
    judge_state = directory / "judge-state.json"
    if judge_state.exists() and json.loads(judge_state.read_bytes())["failed_attempts"]:
        raise ValueError("Judge study cannot resume after a failed call")
    judge = reader_judge.run_cases(
        judge_cases,
        registration["judge"],
        judge_state,
        base_url,
    )
    if judge["prior_failed_attempts"]:
        raise ValueError("Judge study contains failed calls")
    write(directory / "judge.json", judge)
    result = summarize(
        reader,
        judge,
        mapping,
        references,
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
        print(json.dumps({"complete": result["complete"], **result["overall"]}))


if __name__ == "__main__":
    main()
