"""Post-hoc localization of the failed monotonic compact answer trial.

This diagnostic uses only questions whose answer correctness changed in the
registered development trial. It serializes exactly the control records at
their original representations in compact form, without admitting extras. The
cohort is observed and can never promote a product policy.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
import tempfile
from typing import Any

from benchmarks.diagnostics import packing_reader as reader_runtime
from benchmarks.diagnostics import reader_judge as judge_runtime
from benchmarks.diagnostics.longmemeval_s_compact import (
    _canonical,
    _clone_pack,
    _sha256,
    _sha256_file,
    _source_changes_since,
    _write,
)
from benchmarks.integrations.run_longmemeval_s_baseline import (
    DATASET_SHA256,
    USER_ID,
    _load_dataset,
    _pack_config,
    _parse_date,
)
from benchmarks.llm_judge import GENERATION_SYSTEM_PROMPT
from prme import MemoryEngine
from prme.retrieval.context_formatter import build_context_guidance
from prme.retrieval.packing import pack_context
from prme.retrieval.query_analysis import analyze_query


ARMS = ("control", "same_set_compact")
READER_OPTIONS = {
    "temperature": 0,
    "seed": 42,
    "num_ctx": 65536,
    "num_predict": 1024,
}


def _git_revision(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _answer_result(path: Path, execution_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    result = json.loads(path.read_text())
    execution = json.loads(execution_path.read_text())
    claimed = result.get("result_sha256")
    computed = _sha256(
        _canonical({key: value for key, value in result.items() if key != "result_sha256"})
    )
    if (
        result.get("kind") != "longmemeval-s-monotonic-answer-result"
        or result.get("split") != "dev"
        or result.get("questions") != 119
        or result.get("metrics", {}).get("gate", {}).get("passed") is not False
        or result.get("execution_sha256") != _sha256_file(execution_path)
        or execution.get("metrics") != result.get("metrics")
        or execution.get("reader", {}).get("complete") is not True
        or execution.get("reader", {}).get("failed_attempts")
        or execution.get("judge", {}).get("complete") is not True
        or execution.get("judge", {}).get("prior_failed_attempts")
        or claimed != computed
    ):
        raise ValueError("failed answer result or execution is incomplete")
    return result, execution


def _transition_ids(execution: dict[str, Any]) -> list[str]:
    verdicts: dict[str, dict[str, bool]] = {}
    for row in execution["judge"]["judgments"]:
        question_id, arm = row["id"].rsplit(":", 1)
        if arm not in ("control", "monotonic") or type(row.get("correct")) is not bool:
            raise ValueError("prior judgment identity differs")
        verdicts.setdefault(question_id, {})[arm] = row["correct"]
    changed = sorted(
        question_id
        for question_id, values in verdicts.items()
        if set(values) == {"control", "monotonic"}
        and values["control"] != values["monotonic"]
    )
    if len(verdicts) != 119 or len(changed) != 20:
        raise ValueError("expected the 20 observed answer transitions")
    return changed


def _model_identity(model: str, base_url: str) -> dict[str, Any]:
    resolved = (
        model.removesuffix(":cloud")
        if model.endswith(":cloud")
        else model.removesuffix("-cloud")
        if model.endswith("-cloud")
        else model
    )
    return {
        "provider": "ollama",
        "model": model,
        "model_digest": reader_runtime.model_digest(base_url, model),
        "ollama_version": reader_runtime.request(base_url, "/api/version")["version"],
        "accepted_response_models": sorted({model, resolved}),
        "options": dict(READER_OPTIONS),
        "system_prompt_sha256": _sha256(GENERATION_SYSTEM_PROMPT.encode()),
    }


def _validate_calibration(
    controls_path: Path, declaration_path: Path, calibration_path: Path
) -> dict[str, Any]:
    controls = json.loads(controls_path.read_text())
    declaration = json.loads(declaration_path.read_text())
    calibration = json.loads(calibration_path.read_text())
    judge_runtime.validate_calibration(controls, calibration, declaration)
    return declaration


async def _prepare_case(
    case: dict[str, Any],
    *,
    baseline_root: Path,
    source_cases_root: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    question_id = case["question_id"]
    frozen = json.loads((source_cases_root / f"{question_id}.json").read_text())
    baseline_capture = json.loads(
        (baseline_root / "captures" / f"{question_id}.json").read_text()
    )
    with tempfile.TemporaryDirectory(prefix="prme-lme-compact-localize-") as temporary:
        pack = Path(temporary) / question_id
        clone_method = await asyncio.to_thread(
            _clone_pack, baseline_root / "packs" / question_id, pack
        )
        config = _pack_config(pack)
        config = config.model_copy(
            update={
                "organizer": config.organizer.model_copy(
                    update={"opportunistic_enabled": False}
                )
            }
        )
        engine = await MemoryEngine.create(config)
        try:
            reference_time = _parse_date(case["question_date"])
            response = await engine.retrieve(
                case["question"], user_id=USER_ID, reference_time=reference_time
            )
            candidate_ids = [str(candidate.node.id) for candidate in response.results]
            candidate_scores = [candidate.composite_score for candidate in response.results]
            if (
                response.bundle.render() != baseline_capture["context"]
                or candidate_ids != [row["node_id"] for row in baseline_capture["returned"]]
                or candidate_scores
                != [row["composite_score"] for row in baseline_capture["returned"]]
                or response.bundle.render() != frozen["arms"]["control"]["context"]
            ):
                raise ValueError(f"baseline replay differs for {question_id}")
            analysis = await analyze_query(case["question"], reference_time=reference_time)
            guidance = build_context_guidance(
                case["question"],
                query_analysis=analysis,
                reference_time=reference_time,
                mode=config.packing.context_guidance_mode,
            )
            control = pack_context(
                response.results,
                config.packing,
                coverage_notice=response.bundle.coverage_notice,
                context_guidance=guidance,
            )
            if control.render() != response.bundle.render():
                raise ValueError(f"control repack differs for {question_id}")
            required = tuple(
                (candidate.node.id, candidate.representation)
                for values in control.sections.values()
                for candidate in values
                if candidate.representation is not None
            )
            required_ids = {node_id for node_id, _representation in required}
            selected = [
                candidate for candidate in response.results if candidate.node.id in required_ids
            ]
            candidate = pack_context(
                selected,
                config.packing.model_copy(update={"context_format": "compact"}),
                coverage_notice=response.bundle.coverage_notice,
                context_guidance=guidance,
                _required=required,
                _require_guidance=control.context_guidance is not None,
            )
        finally:
            await engine.close()
    control_rows = [item for values in control.sections.values() for item in values]
    candidate_rows = [item for values in candidate.sections.values() for item in values]
    control_pairs = {(item.node.id, item.representation) for item in control_rows}
    candidate_pairs = {(item.node.id, item.representation) for item in candidate_rows}
    if (
        candidate.context_format != "compact"
        or control_pairs != candidate_pairs
        or control.included_count != candidate.included_count
        or candidate.tokens_used > max(0, config.packing.token_budget - config.packing.overhead_tokens)
        or candidate.context_guidance != control.context_guidance
    ):
        raise ValueError(f"same-set compact invariant failed for {question_id}")
    neutral = {
        "question_id": question_id,
        "question": case["question"],
        "question_date": case["question_date"],
        "contexts": {
            "control": {
                "context": control.render(),
                "sha256": _sha256(control.render().encode()),
                "tokens": control.tokens_used,
            },
            "same_set_compact": {
                "context": candidate.render(),
                "sha256": _sha256(candidate.render().encode()),
                "tokens": candidate.tokens_used,
            },
        },
    }
    reference = {
        key: case[key]
        for key in ("question_id", "question", "question_date", "question_type", "answer")
    }
    audit = {
        "question_id": question_id,
        "clone_method": clone_method,
        "records": control.included_count,
        "control_tokens": control.tokens_used,
        "same_set_compact_tokens": candidate.tokens_used,
        "candidate_ids_sha256": _sha256(_canonical(candidate_ids)),
        "candidate_scores_sha256": _sha256(_canonical(candidate_scores)),
    }
    return neutral, reference, audit


async def _prepare(
    *,
    dataset_path: Path,
    transition_ids: list[str],
    baseline_root: Path,
    source_cases_root: Path,
    prepared_path: Path,
    references_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if _sha256_file(dataset_path) != DATASET_SHA256:
        raise ValueError("LongMemEval-S dataset checksum differs")
    cases = {case["question_id"]: case for case in _load_dataset(dataset_path)}
    if not set(transition_ids) <= set(cases):
        raise ValueError("transition questions are missing from the dataset")
    values = []
    for question_id in transition_ids:
        values.append(
            await _prepare_case(
                cases[question_id],
                baseline_root=baseline_root,
                source_cases_root=source_cases_root,
            )
        )
        print(f"Prepared {len(values)}/{len(transition_ids)}", flush=True)
    neutral_rows = [value[0] for value in values]
    references = [value[1] for value in values]
    prepared = {
        "schema_version": 1,
        "kind": "longmemeval-s-compact-localization-inputs",
        "dataset_sha256": DATASET_SHA256,
        "selection": "all 20 observed correctness transitions from the failed development trial",
        "question_ids": transition_ids,
        "generation_system_prompt": GENERATION_SYSTEM_PROMPT,
        "rows": neutral_rows,
        "audits": [value[2] for value in values],
    }
    if any("answer" in row for row in neutral_rows):
        raise ValueError("neutral reader inputs contain reference answers")
    _write(prepared_path, prepared)
    _write(references_path, references)
    return prepared, references


def _protocol() -> dict[str, Any]:
    return {
        "status": "post-hoc localization; observed transitions; cannot promote a policy",
        "arms": list(ARMS),
        "control": "fresh auditable generation over the original selected records",
        "candidate": "compact serialization of exactly the same records and representations; no extras",
        "reader_order": "counterbalanced by question-ID hash parity",
        "one_generation_per_arm": True,
        "no_selective_retries": True,
        "interpretation": {
            "candidate_matches_control": "additional records are the more likely cause",
            "candidate_repeats_losses": "compact serialization contributes to the failure",
            "mixed": "both effects remain plausible",
        },
    }


def _source(
    *,
    revision: str,
    answer_registration: Path,
    answer_result: Path,
    answer_execution: Path,
    prepared_path: Path,
    references_path: Path,
    controls_path: Path,
    declaration_path: Path,
    calibration_path: Path,
) -> dict[str, Any]:
    return {
        "prme_revision": revision,
        "runner_sha256": _sha256_file(Path(__file__).resolve()),
        "answer_registration_sha256": _sha256_file(answer_registration),
        "answer_result_sha256": _sha256_file(answer_result),
        "answer_execution_sha256": _sha256_file(answer_execution),
        "prepared_sha256": _sha256_file(prepared_path),
        "references_sha256": _sha256_file(references_path),
        "controls_sha256": _sha256_file(controls_path),
        "judge_declaration_sha256": _sha256_file(declaration_path),
        "judge_calibration_sha256": _sha256_file(calibration_path),
    }


async def create_registration(
    *,
    dataset_path: Path,
    baseline_root: Path,
    source_cases_root: Path,
    answer_registration: Path,
    answer_result: Path,
    answer_execution: Path,
    prepared_path: Path,
    references_path: Path,
    controls_path: Path,
    declaration_path: Path,
    calibration_path: Path,
    reader_model: str,
    base_url: str,
    project_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    for path in (prepared_path, references_path, output_path):
        if path.exists():
            raise ValueError(f"fresh output required: {path}")
    result, execution = _answer_result(answer_result, answer_execution)
    transition_ids = _transition_ids(execution)
    prepared, references = await _prepare(
        dataset_path=dataset_path,
        transition_ids=transition_ids,
        baseline_root=baseline_root,
        source_cases_root=source_cases_root,
        prepared_path=prepared_path,
        references_path=references_path,
    )
    declaration = _validate_calibration(
        controls_path, declaration_path, calibration_path
    )
    revision = _git_revision(project_root)
    value = {
        "schema_version": 1,
        "kind": "longmemeval-s-compact-localization-registration",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "Post-hoc causal localization on observed answer transitions. The result "
            "can choose the next experiment but cannot support product promotion."
        ),
        "source": _source(
            revision=revision,
            answer_registration=answer_registration,
            answer_result=answer_result,
            answer_execution=answer_execution,
            prepared_path=prepared_path,
            references_path=references_path,
            controls_path=controls_path,
            declaration_path=declaration_path,
            calibration_path=calibration_path,
        ),
        "dataset": {
            "name": "LongMemEval-S cleaned",
            "sha256": DATASET_SHA256,
            "questions": len(transition_ids),
            "question_ids": transition_ids,
            "question_ids_sha256": _sha256(_canonical(transition_ids)),
        },
        "prior_result_sha256": result["result_sha256"],
        "models": {
            "reader": _model_identity(reader_model, base_url),
            "judge": declaration,
        },
        "protocol": _protocol(),
        "limitations": [
            "All selected questions and their prior outcomes were observed before registration.",
            "Ollama cloud manifests do not prove immutable remote weights.",
            "One generation per arm does not estimate model variance.",
            "The custom judge is not the official LongMemEval judge.",
        ],
    }
    if len(prepared["rows"]) != len(references) or len(references) != len(transition_ids):
        raise ValueError("prepared/reference coverage differs")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(value, indent=2) + "\n")
    return value


def _validate_registration(
    registration: dict[str, Any],
    *,
    answer_registration: Path,
    answer_result: Path,
    answer_execution: Path,
    prepared_path: Path,
    references_path: Path,
    controls_path: Path,
    declaration_path: Path,
    calibration_path: Path,
    base_url: str,
    project_root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result, execution = _answer_result(answer_result, answer_execution)
    transition_ids = _transition_ids(execution)
    revision = registration.get("source", {}).get("prme_revision")
    reader = registration.get("models", {}).get("reader", {})
    expected_reader = _model_identity(str(reader.get("model", "")), base_url)
    expected_source = _source(
        revision=revision,
        answer_registration=answer_registration,
        answer_result=answer_result,
        answer_execution=answer_execution,
        prepared_path=prepared_path,
        references_path=references_path,
        controls_path=controls_path,
        declaration_path=declaration_path,
        calibration_path=calibration_path,
    )
    declaration = _validate_calibration(
        controls_path, declaration_path, calibration_path
    )
    if (
        registration.get("schema_version") != 1
        or registration.get("kind")
        != "longmemeval-s-compact-localization-registration"
        or not isinstance(revision, str)
        or registration.get("source") != expected_source
        or registration.get("prior_result_sha256") != result["result_sha256"]
        or registration.get("dataset", {}).get("question_ids") != transition_ids
        or registration.get("dataset", {}).get("questions") != len(transition_ids)
        or registration.get("dataset", {}).get("question_ids_sha256")
        != _sha256(_canonical(transition_ids))
        or registration.get("models", {}).get("reader") != expected_reader
        or registration.get("models", {}).get("judge") != declaration
        or registration.get("protocol") != _protocol()
        or any(
            not path.startswith("benchmarks/results/research/")
            for path in _source_changes_since(revision, project_root)
        )
    ):
        raise ValueError("registered compact-localization inputs differ")
    prepared = json.loads(prepared_path.read_text())
    references = json.loads(references_path.read_text())
    if (
        prepared.get("kind") != "longmemeval-s-compact-localization-inputs"
        or prepared.get("question_ids") != transition_ids
        or len(prepared.get("rows", [])) != len(transition_ids)
        or [row.get("question_id") for row in prepared["rows"]] != transition_ids
        or [row.get("question_id") for row in references] != transition_ids
        or any("answer" in row for row in prepared["rows"])
        or any(set(row.get("contexts", {})) != set(ARMS) for row in prepared["rows"])
    ):
        raise ValueError("prepared compact-localization inputs differ")
    return prepared, references


def _reader_payload(
    row: dict[str, Any], arm: str, model: str
) -> dict[str, Any]:
    context = row["contexts"][arm]
    if context["sha256"] != _sha256(context["context"].encode()):
        raise ValueError("prepared context checksum differs")
    messages = [
        {"role": "system", "content": GENERATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "QUESTION DATE:\n"
                + row["question_date"]
                + "\n\nMEMORY:\n"
                + context["context"]
                + "\n\nQUESTION:\n"
                + row["question"]
            ),
        },
    ]
    if (
        sum(len(message["content"].encode()) for message in messages)
        + 4096
        + READER_OPTIONS["num_predict"]
        > READER_OPTIONS["num_ctx"]
    ):
        raise ValueError("reader prompt exceeds conservative context headroom")
    return {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": dict(READER_OPTIONS),
    }


def _reader_jobs(
    prepared: dict[str, Any], model: str
) -> list[tuple[dict[str, Any], str, dict[str, Any], str]]:
    jobs = []
    for row in prepared["rows"]:
        order = (
            ARMS
            if int(_sha256(row["question_id"].encode()), 16) % 2
            else tuple(reversed(ARMS))
        )
        for arm in order:
            body = _reader_payload(row, arm, model)
            jobs.append((row, arm, body, _sha256(_canonical(body))))
    return jobs


def _reader_run(
    prepared: dict[str, Any],
    state_path: Path,
    *,
    registration_sha256: str,
    model: str,
    model_digest: str,
    base_url: str,
) -> dict[str, Any]:
    identity = {
        "registration_sha256": registration_sha256,
        "prepared_sha256": _sha256(_canonical(prepared)),
        "model": model,
        "model_digest": model_digest,
        "options": dict(READER_OPTIONS),
    }
    jobs = _reader_jobs(prepared, model)
    with reader_runtime.exclusive_state(state_path):
        state = (
            json.loads(state_path.read_text())
            if state_path.exists()
            else {
                "identity": identity,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "generations": {},
                "failed_attempts": [],
                "complete": False,
            }
        )
        if state["identity"] != identity:
            raise ValueError("reader resume identity differs")
        wanted = {key for _row, _arm, _body, key in jobs}
        if not set(state["generations"]) <= wanted:
            raise ValueError("reader state contains unrelated generations")
        for saved in state["generations"].values():
            if saved["response_sha256"] != _sha256(_canonical(saved["response"])):
                raise ValueError("saved reader response checksum differs")
            reader_runtime.validate_response(saved["response"])
        _write(state_path, state)
        for index, (_row, _arm, body, key) in enumerate(jobs):
            if key not in state["generations"]:
                response = None
                try:
                    if reader_runtime.model_digest(base_url, model) != model_digest:
                        raise ValueError("reader model changed")
                    response = reader_runtime.request(base_url, "/api/chat", body)
                    answer = reader_runtime.validate_response(response)
                    if (
                        not answer
                        or not judge_runtime.matches_response_model(
                            model, response.get("model")
                        )
                        or reader_runtime.model_digest(base_url, model) != model_digest
                    ):
                        raise ValueError("reader response identity changed")
                    state["generations"][key] = {
                        "response": response,
                        "response_sha256": _sha256(_canonical(response)),
                    }
                except Exception as exc:
                    state["failed_attempts"].append(
                        {
                            "prompt_sha256": key,
                            "error_type": type(exc).__name__,
                            "at": datetime.now(timezone.utc).isoformat(),
                            "response": response,
                            "response_sha256": (
                                _sha256(_canonical(response))
                                if response is not None
                                else None
                            ),
                        }
                    )
                    _write(state_path, state)
                    raise
                _write(state_path, state)
            print(f"Reader {index + 1}/{len(jobs)}", flush=True)
        state["complete"] = True
        _write(state_path, state)
        rows = [
            {
                "question_id": row["question_id"],
                "arm": arm,
                "context_sha256": row["contexts"][arm]["sha256"],
                "prompt_sha256": key,
                "hypothesis": reader_runtime.validate_response(
                    state["generations"][key]["response"]
                ),
            }
            for row, arm, _body, key in jobs
        ]
        return {
            "complete": True,
            "identity": identity,
            "started_at": state["started_at"],
            "questions": len(prepared["rows"]),
            "logical_predictions": len(jobs),
            "unique_generations": len(state["generations"]),
            "failed_attempts": state["failed_attempts"],
            "rows": rows,
        }


def _judge_cases(
    predictions: dict[str, Any], references: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_id = {row["question_id"]: row for row in references}
    cases = []
    for row in predictions["rows"]:
        reference = by_id[row["question_id"]]
        cases.append(
            {
                "id": row["question_id"] + ":" + row["arm"],
                "question_id": row["question_id"],
                "arm": row["arm"],
                "category": (
                    "abstention"
                    if row["question_id"].endswith("_abs")
                    else reference["question_type"]
                ),
                "question": reference["question"],
                "reference": str(reference["answer"]),
                "hypothesis": row["hypothesis"],
            }
        )
    expected = {
        (question_id, arm) for question_id in by_id for arm in ARMS
    }
    if {(row["question_id"], row["arm"]) for row in cases} != expected:
        raise ValueError("judge case coverage differs")
    return cases


def _metrics(
    cases: list[dict[str, Any]], judgments: dict[str, Any]
) -> dict[str, Any]:
    verdicts = {row["id"]: row["correct"] for row in judgments["judgments"]}
    if set(verdicts) != {row["id"] for row in cases}:
        raise ValueError("judge verdict coverage differs")
    grouped: dict[str, dict[str, Any]] = {}
    for case in cases:
        row = grouped.setdefault(
            case["question_id"], {"category": case["category"]}
        )
        row[case["arm"]] = verdicts[case["id"]]
    if any(set(row) != {"category", *ARMS} for row in grouped.values()):
        raise ValueError("paired judgment coverage differs")

    def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
        control = sum(row["control"] for row in rows)
        candidate = sum(row["same_set_compact"] for row in rows)
        wins = sum(row["same_set_compact"] and not row["control"] for row in rows)
        losses = sum(row["control"] and not row["same_set_compact"] for row in rows)
        return {
            "questions": len(rows),
            "control_correct": control,
            "same_set_compact_correct": candidate,
            "paired_wins": wins,
            "paired_losses": losses,
            "paired_ties": len(rows) - wins - losses,
        }

    values = list(grouped.values())
    return {
        "overall": summarize(values),
        "categories": {
            category: summarize(
                [row for row in values if row["category"] == category]
            )
            for category in sorted({row["category"] for row in values})
        },
        "complete_reader_execution": True,
        "complete_judge_execution": judgments.get("complete") is True,
        "reader_failed_attempts": 0,
        "judge_failed_attempts": len(judgments.get("prior_failed_attempts", [])),
    }


def evaluate(
    *,
    registration_path: Path,
    answer_registration: Path,
    answer_result: Path,
    answer_execution: Path,
    prepared_path: Path,
    references_path: Path,
    controls_path: Path,
    declaration_path: Path,
    calibration_path: Path,
    base_url: str,
    project_root: Path,
    output_dir: Path,
    summary_path: Path,
) -> dict[str, Any]:
    if summary_path.exists():
        raise ValueError("summary output already exists")
    registration = json.loads(registration_path.read_text())
    prepared, references = _validate_registration(
        registration,
        answer_registration=answer_registration,
        answer_result=answer_result,
        answer_execution=answer_execution,
        prepared_path=prepared_path,
        references_path=references_path,
        controls_path=controls_path,
        declaration_path=declaration_path,
        calibration_path=calibration_path,
        base_url=base_url,
        project_root=project_root,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    registration_sha256 = _sha256_file(registration_path)
    reader = registration["models"]["reader"]
    predictions = _reader_run(
        prepared,
        output_dir / "reader-state.json",
        registration_sha256=registration_sha256,
        model=reader["model"],
        model_digest=reader["model_digest"],
        base_url=base_url,
    )
    _write(output_dir / "reader.json", predictions)
    if predictions["failed_attempts"]:
        raise ValueError("reader execution contains failed attempts")
    cases = _judge_cases(predictions, references)
    judgments = judge_runtime.run_cases(
        cases,
        registration["models"]["judge"],
        output_dir / "judge-state.json",
        base_url,
    )
    _write(output_dir / "judge.json", judgments)
    metrics = _metrics(cases, judgments)
    execution = {
        "registration_sha256": registration_sha256,
        "reader": predictions,
        "judge": judgments,
        "metrics": metrics,
    }
    _write(output_dir / "execution.json", execution)
    result = {
        "schema_version": 1,
        "kind": "longmemeval-s-compact-localization-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": registration_sha256,
        "questions": len(prepared["rows"]),
        "metrics": metrics,
        "execution_sha256": _sha256_file(output_dir / "execution.json"),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "claim_boundary": registration["claim_boundary"],
    }
    result["result_sha256"] = _sha256(_canonical(result))
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register")
    run = commands.add_parser("evaluate")
    for command in (register, run):
        command.add_argument("--answer-registration", type=Path, required=True)
        command.add_argument("--answer-result", type=Path, required=True)
        command.add_argument("--answer-execution", type=Path, required=True)
        command.add_argument("--prepared", type=Path, required=True)
        command.add_argument("--references", type=Path, required=True)
        command.add_argument("--controls", type=Path, required=True)
        command.add_argument("--judge-declaration", type=Path, required=True)
        command.add_argument("--judge-calibration", type=Path, required=True)
        command.add_argument("--base-url", default="http://127.0.0.1:11434")
        command.add_argument("--project-root", type=Path, default=Path.cwd())
    register.add_argument("--dataset", type=Path, required=True)
    register.add_argument("--baseline-root", type=Path, required=True)
    register.add_argument("--source-cases-root", type=Path, required=True)
    register.add_argument("--reader-model", required=True)
    register.add_argument("--output", type=Path, required=True)
    run.add_argument("--registration", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--summary", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    shared = {
        "answer_registration": args.answer_registration.resolve(),
        "answer_result": args.answer_result.resolve(),
        "answer_execution": args.answer_execution.resolve(),
        "prepared_path": args.prepared.resolve(),
        "references_path": args.references.resolve(),
        "controls_path": args.controls.resolve(),
        "declaration_path": args.judge_declaration.resolve(),
        "calibration_path": args.judge_calibration.resolve(),
        "base_url": args.base_url,
        "project_root": args.project_root.resolve(),
    }
    if args.command == "register":
        value = asyncio.run(
            create_registration(
                dataset_path=args.dataset.resolve(),
                baseline_root=args.baseline_root.resolve(),
                source_cases_root=args.source_cases_root.resolve(),
                reader_model=args.reader_model,
                output_path=args.output.resolve(),
                **shared,
            )
        )
        print(json.dumps({"dataset": value["dataset"], "protocol": value["protocol"]}, indent=2))
        return
    value = evaluate(
        registration_path=args.registration.resolve(),
        output_dir=args.output_dir.resolve(),
        summary_path=args.summary.resolve(),
        **shared,
    )
    print(json.dumps({"metrics": value["metrics"], "result_sha256": value["result_sha256"]}, indent=2))


if __name__ == "__main__":
    main()
