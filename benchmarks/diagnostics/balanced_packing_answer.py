"""Full development answer comparison for density and balanced packing.

This extends the focused assistant-memory assay to every question and category
in the existing 119-question cohort. It reuses the same bounded 35B reader and
calibrated judge implementation. The cohort has been examined previously, so
results remain development evidence and cannot serve as an independent holdout.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from benchmarks.diagnostics import assistant_packing_answer as base
from benchmarks.diagnostics import reader_judge
from benchmarks.diagnostics.hindsight_capture import canonical, digest, write
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context
from prme.retrieval.tokenization import count_tokens


def prepare(root: Path) -> dict:
    """Reproduce all density/balanced contexts without exposing references."""
    paths = base.source_paths(root)
    raw = paths["composition"].read_bytes()
    composition = json.loads(raw)
    completion = json.loads(paths["composition_completion"].read_bytes())
    verification_raw = paths["composition_verification"].read_bytes()
    verification = json.loads(verification_raw)
    verifier = json.loads(paths["verifier_completion"].read_bytes())
    if (
        not composition["complete"]
        or composition["errors"]
        or composition["contexts_evaluated"] != 1428
        or completion["native_exit_code"] != 0
        or completion["output_sha256"] != digest(raw)
        or not verification["all_credit_and_summaries_recomputed"]
        or verifier["native_exit_code"] != 0
        or verifier["report_sha256"] != digest(verification_raw)
    ):
        raise ValueError("A complete independently verified composition study is required")

    cases = json.loads(paths["inputs"].read_bytes())["cases"]
    references = json.loads(paths["references"].read_bytes())["references"]
    by_case = {case["case_id"]: case for case in cases}
    by_question = {row["question_id"]: row for row in references}
    if len(cases) != 119 or len(by_case) != 119 or len(by_question) != 119:
        raise ValueError("The complete fixed development cohort is required")

    rows = []
    for result in composition["details"]:
        question_id = result["question_id"]
        reference = by_question[question_id]
        case = by_case[reference["case_id"]]
        snapshot_ref = result["snapshot"]
        snapshot_raw = (paths["snapshots"] / snapshot_ref["filename"]).read_bytes()
        if digest(snapshot_raw) != snapshot_ref["sha256"]:
            raise ValueError("Candidate snapshot checksum mismatch")
        snapshot = json.loads(snapshot_raw)
        candidates = [
            RetrievalCandidate.model_validate(candidate)
            for candidate in snapshot["candidates"]["parser"]
        ]
        baseline_config = PackingConfig.model_validate(
            snapshot["arms"]["parser:density:4096"]["packing"]
        )
        contexts = {}
        for arm, source_arm in (
            ("density", "density:4096"),
            ("balanced", "head1_quarter:4096"),
        ):
            saved = result["arms"][source_arm]
            config = baseline_config.model_copy(update={"multipath_ordering": arm})
            bundle = pack_context(candidates, config)
            context = bundle.render()
            if (
                context != saved["context"]
                or digest(context.encode()) != saved["context_sha256"]
                or bundle.tokens_used != saved["tokens"]
                or count_tokens(context, config.tokenizer) != saved["tokens"]
            ):
                raise ValueError(f"Current {arm} packing differs from the verified context")
            contexts[arm] = {
                "context": context,
                "sha256": saved["context_sha256"],
                "tokens": saved["tokens"],
            }
        contexts["empty"] = {"context": "", "sha256": digest(b""), "tokens": 0}
        rows.append({
            "case_id": case["case_id"],
            "question": case["question"],
            "question_date": case["question_date"],
            "contexts": contexts,
        })
    if len(rows) != 119 or len({row["case_id"] for row in rows}) != len(rows):
        raise ValueError("Expected all 119 unique development questions")
    return {
        "kind": "balanced-packing-answer-inputs",
        "schema_version": 1,
        "budget": base.BUDGET,
        "source_identity": base._source_identity(paths),
        "rows": rows,
    }


def register(root: Path, directory: Path, registration: Path, base_url: str) -> None:
    if directory.exists() or registration.exists():
        raise ValueError("Fresh registration and study directory required")
    prepared = prepare(root)
    paths = base.source_paths(root)
    controls_sha = digest(paths["controls"].read_bytes())
    prior = json.loads(paths["prior_reader_registration"].read_bytes())
    judge = prior["judge_declaration"]
    if judge != reader_judge.declaration(
        base.JUDGE_MODEL, base_url, judge["controls_sha256"],
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
        "kind": "balanced-packing-answer-development",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "implementation_sha256": digest(Path(__file__).read_bytes()),
        "reader_implementation_sha256": digest(Path(base.__file__).read_bytes()),
        "prepared_sha256": digest(prepared_path.read_bytes()),
        "source_identity": prepared["source_identity"],
        "case_ids": [row["case_id"] for row in prepared["rows"]],
        "arms": base.ARMS,
        "reader": reader,
        "judge": judge,
        "controls_sha256": controls_sha,
        "calibration_sha256": digest(paths["calibration"].read_bytes()),
        "limits": [
            "All 119 questions come from a previously examined development cohort.",
            "One local reader and a custom calibrated local judge; no independent holdout.",
            "Every category and result is retained; no answer-based selection or outcome retry.",
            "Complete coverage and zero provider failures are required.",
            "This can support a policy decision but not a competitive leadership claim.",
        ],
    })


def verify_registration(root: Path, directory: Path, registration: Path,
                        base_url: str) -> tuple[dict, dict]:
    declared = json.loads(registration.read_bytes())
    prepared_path = directory / "prepared.json"
    prepared = json.loads(prepared_path.read_bytes())
    paths = base.source_paths(root)
    if (
        declared["implementation_sha256"] != digest(Path(__file__).read_bytes())
        or declared["reader_implementation_sha256"] != digest(Path(base.__file__).read_bytes())
        or declared["prepared_sha256"] != digest(prepared_path.read_bytes())
        or declared["source_identity"] != base._source_identity(paths)
        or declared["source_identity"] != prepared["source_identity"]
        or declared["case_ids"] != [row["case_id"] for row in prepared["rows"]]
        or declared["arms"] != list(base.ARMS)
        or declared["reader"] != base.reader_declaration(base_url)
        or declared["judge"] != reader_judge.declaration(
            base.JUDGE_MODEL, base_url, declared["judge"]["controls_sha256"]
        )
        or declared["calibration_sha256"] != digest(paths["calibration"].read_bytes())
        or prepared != prepare(root)
    ):
        raise ValueError("Registration, sources, models, or prepared contexts changed")
    return declared, prepared


def summarize(reader: dict, judge: dict, mapping: list[dict], references: dict,
              limits: list[str]) -> dict:
    verdicts = {row["id"]: row["correct"] for row in judge["judgments"]}
    outcomes: dict[str, dict[str, bool]] = {}
    for row in mapping:
        outcomes.setdefault(row["case_id"], {})[row["arm"]] = verdicts[row["id"]]
    details = [{
        "case_id": case_id,
        "category": references[case_id]["scoring_category"],
        "answers": values,
    } for case_id, values in sorted(outcomes.items())]

    def summary(rows: list[dict]) -> dict:
        totals = {arm: sum(row["answers"][arm] for row in rows) for arm in base.ARMS}
        paired = {}
        for before, after in (
            ("empty", "density"), ("empty", "balanced"), ("density", "balanced"),
        ):
            pairs = [(row["answers"][before], row["answers"][after]) for row in rows]
            paired[f"{after}_vs_{before}"] = {
                "wins": sum(not first and second for first, second in pairs),
                "losses": sum(first and not second for first, second in pairs),
                "ties": sum(first == second for first, second in pairs),
            }
        return {"questions": len(rows), "correct": totals, "paired": paired}

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
    reader = base.run_reader(
        prepared, registration["reader"], directory / "reader-state.json", base_url,
    )
    write(directory / "reader.json", reader)

    reference_rows = json.loads(
        base.source_paths(root)["references"].read_bytes()
    )["references"]
    references = {}
    for row in reference_rows:
        references[row["case_id"]] = {
            **row,
            "scoring_category": (
                "abstention" if row["question_id"].endswith("_abs") else row["category"]
            ),
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
        mapping.append({"id": identity, "case_id": row["case_id"], "arm": row["arm"]})
    judge_cases.sort(key=lambda row: row["id"])
    mapping.sort(key=lambda row: row["id"])
    write(directory / "judge-inputs.json", {"cases": judge_cases})
    write(directory / "judge-mapping.json", {"mapping": mapping})
    judge_state = directory / "judge-state.json"
    if judge_state.exists() and json.loads(judge_state.read_bytes())["failed_attempts"]:
        raise ValueError("Judge study cannot resume after a failed call")
    judge = reader_judge.run_cases(
        judge_cases, registration["judge"], judge_state, base_url,
    )
    if judge["prior_failed_attempts"]:
        raise ValueError("Judge study contains failed calls")
    write(directory / "judge.json", judge)
    result = summarize(reader, judge, mapping, references, registration["limits"])
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
