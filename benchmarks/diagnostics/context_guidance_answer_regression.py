"""Confirm non-displacing context guidance on the 381-question partition."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from benchmarks.diagnostics import balanced_packing_answer as reader_base
from benchmarks.diagnostics import balanced_packing_answer_regression as baseline
from benchmarks.diagnostics import context_ablation_answer as reader_run
from benchmarks.diagnostics import context_guidance_answer as development
from benchmarks.diagnostics import reader_judge
from benchmarks.diagnostics.hindsight_capture import digest, write
from benchmarks.diagnostics.product_packing import measure
from prme.retrieval.config import PackingConfig
from prme.retrieval.context_formatter import build_context_guidance
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context


EXPECTED_CASES = 70


def source_paths(root: Path) -> dict[str, Path]:
    paths = baseline.source_paths(root)
    prior = root / "data/benchmarks/balanced-qwen35b-regression-v1"
    paths.update(
        {
            "baseline_registration": (
                root
                / "benchmarks/results/research/2026-09-13/"
                "balanced-qwen35b-regression-registration.json"
            ),
            "baseline_results": (
                root
                / "benchmarks/results/research/2026-09-13/"
                "balanced-qwen35b-regression-results.json"
            ),
            "baseline_reader": prior / "reader.json",
            "baseline_reader_state": prior / "reader-state.json",
            "baseline_judge": prior / "judge.json",
        }
    )
    return paths


def source_identity(paths: dict[str, Path]) -> dict[str, str]:
    return {
        key: digest(path.read_bytes())
        for key, path in paths.items()
        if key not in {"contexts", "snapshots"}
    }


def _question_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y/%m/%d (%a) %H:%M").replace(
        tzinfo=timezone.utc
    )


def prepare(root: Path) -> dict:
    """Select every confirmation context where guidance fits unchanged."""
    paths = source_paths(root)
    prepared_baseline = baseline.prepare(root)
    baseline_by_id = {row["case_id"]: row for row in prepared_baseline["rows"]}
    dataset = {
        row["question_id"]: row
        for row in json.loads(paths["dataset"].read_bytes())
    }
    source = {
        row["question_id"]: row
        for row in json.loads(paths["source"].read_bytes())["details"]
    }
    regression = json.loads(paths["regression"].read_bytes())
    rows = []
    for result in regression["details"]:
        question_id = result["question_id"]
        case = dataset[question_id]
        guidance = build_context_guidance(
            case["question"], reference_time=_question_time(case["question_date"])
        )
        if guidance is None:
            continue

        snapshot_ref = result["candidate_snapshot"]
        snapshot_raw = (paths["snapshots"] / snapshot_ref["filename"]).read_bytes()
        if digest(snapshot_raw) != snapshot_ref["sha256"]:
            raise ValueError("Candidate snapshot checksum mismatch")
        snapshot = json.loads(snapshot_raw)
        candidates = [
            RetrievalCandidate.model_validate(candidate)
            for candidate in snapshot["candidates"]
        ]
        config = PackingConfig.model_validate(snapshot["packing_config"]).model_copy(
            update={"token_budget": baseline.BUDGET, "multipath_ordering": "balanced"}
        )
        control = pack_context(candidates, config)
        expected = baseline_by_id[question_id]["contexts"]["balanced"]
        if (
            control.render() != expected["context"]
            or digest(control.render().encode()) != expected["sha256"]
            or control.tokens_used != expected["tokens"]
        ):
            raise ValueError("Balanced baseline does not reproduce")

        guided = pack_context(candidates, config, context_guidance=guidance)
        if guided.context_guidance is None:
            continue
        if (
            guided.sections != control.sections
            or guided.included_count != control.included_count
            or guided.excluded_ids != control.excluded_ids
        ):
            raise ValueError("Context guidance changed record selection")
        gold = set(source[question_id]["evidence_source_ids"])
        control_measure = measure(control, gold, config)
        guided_measure = measure(guided, gold, config)
        if (
            control_measure["evidence_recall"] != guided_measure["evidence_recall"]
            or control_measure["all_evidence_retained"]
            != guided_measure["all_evidence_retained"]
        ):
            raise ValueError("Context guidance changed annotated-source retention")
        context = guided.render()
        rows.append(
            {
                "case_id": question_id,
                "question": case["question"],
                "question_date": case["question_date"],
                "category": result["category"],
                "guidance_kind": development._guidance_kind(guidance),
                "guidance": guidance,
                "baseline_context_sha256": expected["sha256"],
                "context": context,
                "context_sha256": digest(context.encode()),
                "tokens": guided.tokens_used,
                "source_retention": {
                    "baseline": control_measure["evidence_recall"],
                    "guided": guided_measure["evidence_recall"],
                },
            }
        )
    if (
        len(rows) != EXPECTED_CASES
        or len({row["case_id"] for row in rows}) != EXPECTED_CASES
    ):
        raise ValueError(f"Expected exactly {EXPECTED_CASES} confirmation questions")
    return {
        "kind": "balanced-context-guidance-confirmation",
        "schema_version": 1,
        "selection": "every 381-partition question whose guidance fits without record changes",
        "source_identity": source_identity(paths),
        "rows": rows,
    }


def register(root: Path, directory: Path, registration: Path, base_url: str) -> None:
    if directory.exists() or registration.exists():
        raise ValueError("Fresh registration and study directory required")
    prepared = prepare(root)
    paths = source_paths(root)
    prior_registration = json.loads(paths["baseline_registration"].read_bytes())
    reader = reader_base.reader_declaration(base_url)
    judge = reader_judge.declaration(
        reader_base.base.JUDGE_MODEL,
        base_url,
        prior_registration["judge"]["controls_sha256"],
    )
    if reader != prior_registration["reader"] or judge != prior_registration["judge"]:
        raise ValueError("Reader or judge differs from the complete baseline study")
    reader_judge.validate_calibration(
        json.loads(paths["controls"].read_bytes()),
        json.loads(paths["calibration"].read_bytes()),
        judge,
    )
    directory.mkdir(parents=True)
    prepared_path = directory / "prepared.json"
    write(prepared_path, prepared)
    write(
        registration,
        {
            "kind": "balanced-context-guidance-confirmation",
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "implementation_sha256": digest(Path(__file__).read_bytes()),
            "guidance_implementation_sha256": digest(
                Path(build_context_guidance.__code__.co_filename).read_bytes()
            ),
            "reader_implementation_sha256": digest(Path(reader_base.__file__).read_bytes()),
            "prepared_sha256": digest(prepared_path.read_bytes()),
            "source_identity": prepared["source_identity"],
            "case_ids": [row["case_id"] for row in prepared["rows"]],
            "guidance_kinds": dict(
                sorted(Counter(row["guidance_kind"] for row in prepared["rows"]).items())
            ),
            "reader": reader,
            "judge": judge,
            "gate": {
                "overall_gains_exceed_losses": True,
                "temporal_gains_exceed_losses": True,
                "no_personalization_accuracy_loss": True,
                "no_current_state_accuracy_loss": True,
                "unchanged_annotated_source_recall": True,
            },
            "limits": [
                "This partition's baseline source retention and answer outcomes were examined previously.",
                "Selection uses context fit only; baseline correctness and references cannot affect inclusion.",
                "Every included guidance prefix is token-counted and preserves exact record selection.",
                "Questions where guidance does not fit are unchanged and excluded from model calls.",
                "One fixed local reader and one calibrated local judge are used, with no outcome retries.",
                "This is a confirmation of product behavior, not an independent competitive benchmark.",
            ],
        },
    )


def verify_registration(
    root: Path, directory: Path, registration: Path, base_url: str
) -> tuple[dict, dict]:
    declared = json.loads(registration.read_bytes())
    prepared_path = directory / "prepared.json"
    prepared = json.loads(prepared_path.read_bytes())
    paths = source_paths(root)
    if (
        declared["implementation_sha256"] != digest(Path(__file__).read_bytes())
        or declared["guidance_implementation_sha256"]
        != digest(Path(build_context_guidance.__code__.co_filename).read_bytes())
        or declared["reader_implementation_sha256"]
        != digest(Path(reader_base.__file__).read_bytes())
        or declared["prepared_sha256"] != digest(prepared_path.read_bytes())
        or declared["source_identity"] != source_identity(paths)
        or prepared != prepare(root)
        or declared["case_ids"] != [row["case_id"] for row in prepared["rows"]]
        or declared["reader"] != reader_base.reader_declaration(base_url)
        or declared["judge"]
        != reader_judge.declaration(
            reader_base.base.JUDGE_MODEL,
            base_url,
            declared["judge"]["controls_sha256"],
        )
    ):
        raise ValueError("Registration, inputs, implementation, or models changed")
    return declared, prepared


def baseline_outcomes(paths: dict[str, Path], registration: dict) -> dict[str, bool]:
    results = json.loads(paths["baseline_results"].read_bytes())
    reader = json.loads(paths["baseline_reader"].read_bytes())
    judge = json.loads(paths["baseline_judge"].read_bytes())
    state = json.loads(paths["baseline_reader_state"].read_bytes())
    if (
        not results["complete"]
        or results["registration_sha256"]
        != digest(paths["baseline_registration"].read_bytes())
        or results["reader_report_sha256"] != digest(paths["baseline_reader"].read_bytes())
        or results["judge_report_sha256"] != digest(paths["baseline_judge"].read_bytes())
        or reader["identity"]["reader"] != registration["reader"]
        or judge["identity"]["declaration"] != registration["judge"]
        or state["complete"] is not True
    ):
        raise ValueError("Complete matching baseline outcomes are required")
    outcomes = {
        row["case_id"]: row["answers"]["balanced"] for row in results["details"]
    }
    if len(outcomes) != 381 or any(type(value) is not bool for value in outcomes.values()):
        raise ValueError("Baseline outcome coverage is incomplete")
    return outcomes


def run(root: Path, directory: Path, registration_path: Path, base_url: str) -> dict:
    registration, prepared = verify_registration(
        root, directory, registration_path, base_url
    )
    for name in ("reader.json", "judge.json", "results.json"):
        if (directory / name).exists():
            raise ValueError("Fresh study outcomes required")
    reader = reader_run.run_reader(
        prepared,
        registration["reader"],
        directory / "reader-state.json",
        base_url,
    )
    write(directory / "reader.json", reader)
    references = {
        row["question_id"]: row
        for row in json.loads(source_paths(root)["dataset"].read_bytes())
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
    result = development.summarize(
        prepared,
        reader,
        judge,
        baseline_outcomes(source_paths(root), registration),
        registration,
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
        print(
            json.dumps(
                {
                    "complete": result["complete"],
                    "quality_gate_passed": result["quality_gate_passed"],
                    "overall": result["overall"],
                    "guidance_kinds": result["guidance_kinds"],
                }
            )
        )


if __name__ == "__main__":
    main()
