"""Frozen disjoint confirmation for provenance-linked temporal relations.

The workflow is deliberately staged:

1. ``register`` freezes the 104-question cohort and answer-blind resolver inputs.
2. ``resolve`` performs model-assisted event alignment and deterministic arithmetic.
3. ``gate`` applies the fixed Jev 0.85 semantic threshold.
4. ``prepare`` builds bounded paired contexts, and ``reader`` generates answers.
5. ``judge`` is the first command that reads reference answers.

Every model stage has durable, identity-bound state and fails closed.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
from typing import Any

import httpx

from benchmarks.diagnostics import (
    longmemeval_s_temporal_relation_answer as answer_trial,
)
from benchmarks.diagnostics import longmemeval_s_temporal_relation_jev as jev_trial
from benchmarks.diagnostics import longmemeval_s_temporal_relation_probe as resolver
from benchmarks.diagnostics import paired_context_eval as paired
from benchmarks.diagnostics import packing_reader as reader_runtime
from benchmarks.diagnostics import reader_judge as judge_runtime
from benchmarks.diagnostics.longmemeval_s_compact import (
    _case_checksum,
    _sha256,
    _sha256_file,
    _source_changes_since,
    _write,
)
from benchmarks.integrations.run_longmemeval_s_baseline import (
    DATASET_SHA256,
    _load_dataset,
    _parse_date,
)
from benchmarks.llm_judge import GENERATION_SYSTEM_PROMPT
from prme.retrieval.packing import pack_context


CONFIRMATION_QUESTIONS = 104
ARMS = answer_trial.ARMS
JEV_THRESHOLD = answer_trial.JEV_THRESHOLD
READER_OPTIONS = answer_trial.READER_OPTIONS
RESOLVER_MODEL = "deepseek-v4.1-flash:cloud"
READER_MODEL = "deepseek-v4.1-flash:cloud"
MAX_SCHEMA_REPAIRS = 1
SCHEMA_REPAIR_PROMPT = (
    "Your previous JSON did not match the required schema. Return only one corrected "
    "JSON object. Every evidence_id must be one of the UUIDs from the supplied records. "
    "If the requested relation needs an unsupported or missing operand, return operation "
    "unsupported with an empty operands list. Do not calculate or answer the question."
)


def _git_revision(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _validate_calibration(
    controls_path: Path, declaration_path: Path, calibration_path: Path
) -> dict[str, Any]:
    controls = json.loads(controls_path.read_text())
    declaration = json.loads(declaration_path.read_text())
    calibration = json.loads(calibration_path.read_text())
    judge_runtime.validate_calibration(controls, calibration, declaration)
    return declaration


def _repair_body(
    body: dict[str, Any], invalid_response: dict[str, Any]
) -> dict[str, Any]:
    content = reader_runtime.validate_response(invalid_response)
    return {
        **body,
        "messages": [
            *body["messages"],
            {"role": "assistant", "content": content},
            {"role": "user", "content": SCHEMA_REPAIR_PROMPT},
        ],
        "format": resolver.RawResolution.model_json_schema(),
    }


def _load_resolver_seed(registration_path: Path, state_path: Path) -> dict[str, Any]:
    registration = json.loads(registration_path.read_text())
    state = json.loads(state_path.read_text())
    registration_sha256 = _sha256_file(registration_path)
    if (
        registration.get("schema_version") != 1
        or registration.get("kind")
        != "longmemeval-s-temporal-relation-confirmation-registration"
        or state.get("identity", {}).get("registration_sha256") != registration_sha256
        or state.get("complete") is not False
    ):
        raise ValueError("resolver seed identity differs")
    generations = state.get("generations", {})
    failures = state.get("failed_attempts", [])
    if not isinstance(generations, dict) or not isinstance(failures, list):
        raise ValueError("resolver seed shape differs")
    for saved in generations.values():
        if saved.get("response_sha256") != _sha256(
            paired.canonical(saved.get("response"))
        ):
            raise ValueError("resolver seed response checksum differs")
        resolver._parse_response(saved["response"])
    invalid: dict[str, dict[str, Any]] = {}
    for failed in failures:
        prompt_sha256 = failed.get("prompt_sha256")
        response = failed.get("response")
        if (
            not isinstance(prompt_sha256, str)
            or prompt_sha256 in invalid
            or response is None
            or failed.get("response_sha256") != _sha256(paired.canonical(response))
        ):
            raise ValueError("resolver seed failure differs")
        reader_runtime.validate_response(response)
        try:
            resolver._parse_response(response)
        except ValueError:
            invalid[prompt_sha256] = failed
        else:
            raise ValueError("resolver seed failure is schema-valid")
    if set(generations) & set(invalid):
        raise ValueError("resolver seed prompt is both valid and invalid")
    return {
        "registration": registration,
        "registration_sha256": registration_sha256,
        "state_sha256": _sha256_file(state_path),
        "identity": state["identity"],
        "generations": generations,
        "invalid": invalid,
        "summary": {
            "valid_generations": len(generations),
            "invalid_schema_attempts": len(invalid),
        },
    }


def _cohort(
    dataset_path: Path,
    development_registration_path: Path,
) -> tuple[list[dict[str, Any]], set[str]]:
    if _sha256_file(dataset_path) != DATASET_SHA256:
        raise ValueError("LongMemEval-S dataset checksum differs")
    development = json.loads(development_registration_path.read_text())
    if (
        development.get("kind") != "longmemeval-s-monotonic-answer-registration"
        or development.get("dataset", {}).get("split") != "dev"
        or development.get("dataset", {}).get("questions") != 119
        or development.get("dataset", {}).get("sha256") != DATASET_SHA256
    ):
        raise ValueError("development split registration differs")
    development_ids = set(development["dataset"]["question_ids"])
    cases = [
        case
        for case in _load_dataset(dataset_path)
        if case["question_id"] not in development_ids
        and case["question_type"] == "temporal-reasoning"
    ]
    if len(cases) != CONFIRMATION_QUESTIONS:
        raise ValueError("confirmation temporal cohort differs")
    return cases, development_ids


def _source_question_type(question_id: str, dataset_question_type: str) -> str:
    return "abstention" if question_id.endswith("_abs") else dataset_question_type


def _resolver_jobs(
    rows: list[dict[str, Any]], model: str
) -> list[
    tuple[dict[str, Any], dict[Any, resolver.EvidenceRecord], dict[str, Any], str]
]:
    jobs = []
    for row in rows:
        records = resolver.parse_records(row["control"]["context"])
        body = resolver._request_body(
            model=model,
            question=row["question"],
            question_date=row["question_date"],
            records=records,
        )
        jobs.append((row, records, body, _sha256(paired.canonical(body))))
    return jobs


def _prepare_registered_inputs(
    *,
    cases: list[dict[str, Any]],
    source_cases_root: Path,
    resolver_inputs_path: Path,
    references_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    rows = []
    references = []
    case_identities = []
    for case in cases:
        question_id = case["question_id"]
        saved = json.loads((source_cases_root / f"{question_id}.json").read_text())
        expected_source_type = _source_question_type(question_id, case["question_type"])
        if (
            saved.get("kind") != "longmemeval-s-monotonic-compact-case"
            or saved.get("question_id") != question_id
            or saved.get("case_sha256") != _case_checksum(saved)
            or saved.get("question_type") != expected_source_type
        ):
            raise ValueError(f"saved source case differs for {question_id}")
        control = saved["arms"]["control"]
        if control.get("context_sha256") != _sha256(control["context"].encode()):
            raise ValueError(f"saved control checksum differs for {question_id}")
        resolver.parse_records(control["context"])
        rows.append(
            {
                "question_id": question_id,
                "question": case["question"],
                "question_date": case["question_date"],
                "question_type": case["question_type"],
                "control": {
                    "context": control["context"],
                    "sha256": control["context_sha256"],
                    "tokens": control["tokens"],
                    "node_ids": control["node_ids"],
                },
            }
        )
        references.append(
            {
                key: case[key]
                for key in (
                    "question_id",
                    "question",
                    "question_date",
                    "question_type",
                    "answer",
                )
            }
        )
        case_identities.append(
            {"question_id": question_id, "case_sha256": saved["case_sha256"]}
        )
    value = {
        "schema_version": 1,
        "kind": "longmemeval-s-temporal-relation-confirmation-resolver-inputs",
        "dataset_sha256": DATASET_SHA256,
        "question_ids": [row["question_id"] for row in rows],
        "rows": rows,
    }
    if any("answer" in row for row in rows):
        raise ValueError("resolver inputs contain reference answers")
    _write(resolver_inputs_path, value)
    _write(references_path, references)
    return value, references, _sha256(paired.canonical(case_identities))


def _protocol(seed_summary: dict[str, int]) -> dict[str, Any]:
    return {
        "status": (
            "v2 frozen before resolver repair, remaining resolver calls, Jev, "
            "reader, or judge"
        ),
        "cohort": (
            "all temporal-reasoning questions in the 381-question complement of "
            "the frozen 119-question development split"
        ),
        "stages": ["resolve", "gate", "prepare", "reader", "judge"],
        "answer_isolation": (
            "resolver, Jev, prepare, and reader validate only the reference file hash "
            "and do not deserialize answers; judge is the first stage that reads answers"
        ),
        "resolver": {
            "model": RESOLVER_MODEL,
            "model_selects": "exact record IDs, verbatim quotes, temporal expressions",
            "code_validates": "citations, quotes, temporal licenses, complete operands",
            "code_computes": "calendar differences, dates, ordering, or explicit sums",
            "seeded_valid_generations": seed_summary["valid_generations"],
            "seeded_invalid_schema_attempts": seed_summary["invalid_schema_attempts"],
            "maximum_schema_repairs_per_question": MAX_SCHEMA_REPAIRS,
            "repair_uses_structured_output": True,
            "terminal_failed_questions": 0,
        },
        "jev": {
            "model": jev_trial.MODEL,
            "minimum_operand_probability": JEV_THRESHOLD,
            "threshold_fixed_from_development": True,
            "failed_calls": 0,
        },
        "packing": {
            "same_token_budget": True,
            "candidate_pool": "control records only",
            "cited_records_required": True,
            "only_uncited_records_may_be_evicted": True,
        },
        "answer_trial": {
            "reader_order": "counterbalanced by question-ID hash parity",
            "one_generation_per_distinct_context": True,
            "no_selective_reader_or_judge_retries": True,
            "judge_after_complete_reader": True,
        },
        "gate": {
            "complete_all_stages": True,
            "terminal_failed_questions": 0,
            "accepted_hints": ">= 10",
            "candidate_correct": "> control_correct",
            "paired_wins": "> paired_losses",
            "accepted_hint_losses": 0,
            "accepted_hint_wins": ">= 3",
        },
    }


def _source(
    *,
    revision: str,
    development_registration_path: Path,
    baseline_identity_path: Path,
    source_identity_path: Path,
    resolver_inputs_path: Path,
    references_path: Path,
    controls_path: Path,
    declaration_path: Path,
    calibration_path: Path,
    resolver_seed_registration_path: Path,
    resolver_seed_state_path: Path,
    source_cases_manifest_sha256: str,
) -> dict[str, Any]:
    return {
        "prme_revision": revision,
        "runner_sha256": _sha256_file(Path(__file__)),
        "resolver_runtime_sha256": _sha256_file(Path(resolver.__file__)),
        "jev_runtime_sha256": _sha256_file(Path(jev_trial.__file__)),
        "answer_runtime_sha256": _sha256_file(Path(answer_trial.__file__)),
        "paired_runtime_sha256": _sha256_file(Path(paired.__file__)),
        "packing_sha256": _sha256_file(Path(pack_context.__code__.co_filename)),
        "development_registration_sha256": _sha256_file(development_registration_path),
        "baseline_identity_sha256": _sha256_file(baseline_identity_path),
        "source_identity_sha256": _sha256_file(source_identity_path),
        "source_cases_manifest_sha256": source_cases_manifest_sha256,
        "resolver_inputs_sha256": _sha256_file(resolver_inputs_path),
        "references_sha256": _sha256_file(references_path),
        "controls_sha256": _sha256_file(controls_path),
        "judge_declaration_sha256": _sha256_file(declaration_path),
        "judge_calibration_sha256": _sha256_file(calibration_path),
        "resolver_seed_registration_sha256": _sha256_file(
            resolver_seed_registration_path
        ),
        "resolver_seed_state_sha256": _sha256_file(resolver_seed_state_path),
    }


async def create_registration(
    *,
    dataset_path: Path,
    development_registration_path: Path,
    baseline_identity_path: Path,
    source_identity_path: Path,
    source_cases_root: Path,
    resolver_inputs_path: Path,
    references_path: Path,
    controls_path: Path,
    declaration_path: Path,
    calibration_path: Path,
    resolver_seed_registration_path: Path,
    resolver_seed_state_path: Path,
    resolver_model: str,
    reader_model: str,
    base_url: str,
    project_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    for path in (resolver_inputs_path, references_path, output_path):
        if path.exists():
            raise ValueError(f"fresh output required: {path}")
    if resolver_model != RESOLVER_MODEL or reader_model != READER_MODEL:
        raise ValueError("confirmation models differ from the frozen protocol")
    cases, development_ids = _cohort(dataset_path, development_registration_path)
    resolver_inputs, references, case_manifest = _prepare_registered_inputs(
        cases=cases,
        source_cases_root=source_cases_root,
        resolver_inputs_path=resolver_inputs_path,
        references_path=references_path,
    )
    declaration = _validate_calibration(
        controls_path, declaration_path, calibration_path
    )
    revision = _git_revision(project_root)
    question_ids = resolver_inputs["question_ids"]
    resolver_identity = paired.model_identity(
        resolver_model,
        base_url,
        dict(resolver.MODEL_OPTIONS),
        resolver.SYSTEM_PROMPT,
    )
    reader_identity = paired.model_identity(
        reader_model,
        base_url,
        dict(READER_OPTIONS),
        GENERATION_SYSTEM_PROMPT,
    )
    resolver_seed = _load_resolver_seed(
        resolver_seed_registration_path, resolver_seed_state_path
    )
    seed_identity = resolver_seed["identity"]
    wanted = {
        key
        for _row, _records, _body, key in _resolver_jobs(
            resolver_inputs["rows"], resolver_model
        )
    }
    seeded = set(resolver_seed["generations"])
    invalid = set(resolver_seed["invalid"])
    if (
        resolver_seed["registration"].get("dataset", {}).get("question_ids")
        != question_ids
        or resolver_seed["registration"].get("models", {}).get("resolver")
        != resolver_identity
        or seed_identity.get("model") != resolver_identity["model"]
        or seed_identity.get("model_digest") != resolver_identity["model_digest"]
        or seed_identity.get("system_prompt_sha256")
        != resolver_identity["system_prompt_sha256"]
        or seed_identity.get("options") != resolver_identity["options"]
        or not seeded <= wanted
        or not invalid <= wanted
        or seeded & invalid
    ):
        raise ValueError("resolver seed is incompatible with confirmation inputs")
    value = {
        "schema_version": 2,
        "kind": "longmemeval-s-temporal-relation-confirmation-registration",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "status": "v2 registered before repair, Jev, reader, or judge calls",
        "claim_boundary": (
            "Disjoint LongMemEval-S temporal answer confirmation under the frozen "
            "PRME protocol; not an official leaderboard score or universal claim."
        ),
        "source": _source(
            revision=revision,
            development_registration_path=development_registration_path,
            baseline_identity_path=baseline_identity_path,
            source_identity_path=source_identity_path,
            resolver_inputs_path=resolver_inputs_path,
            references_path=references_path,
            controls_path=controls_path,
            declaration_path=declaration_path,
            calibration_path=calibration_path,
            resolver_seed_registration_path=resolver_seed_registration_path,
            resolver_seed_state_path=resolver_seed_state_path,
            source_cases_manifest_sha256=case_manifest,
        ),
        "dataset": {
            "name": "LongMemEval-S cleaned",
            "sha256": DATASET_SHA256,
            "split": "confirmation",
            "development_questions_excluded": len(development_ids),
            "questions": len(question_ids),
            "question_ids": question_ids,
            "question_ids_sha256": _sha256(paired.canonical(question_ids)),
        },
        "models": {
            "resolver": resolver_identity,
            "jev": {
                "provider": "typesafe_jev",
                "model": jev_trial.MODEL,
                "api_url": jev_trial.API_URL,
                "question_sha256": _sha256(paired.canonical(jev_trial.QUESTION)),
            },
            "reader": reader_identity,
            "judge": declaration,
        },
        "resolver_seed": {
            "registration_sha256": resolver_seed["registration_sha256"],
            "state_sha256": resolver_seed["state_sha256"],
            **resolver_seed["summary"],
            "reuse_validation": (
                "exact prompt hash, model digest, response checksum, schema, "
                "registration hash, and state hash"
            ),
        },
        "protocol": _protocol(resolver_seed["summary"]),
        "limitations": [
            "The confirmation is disjoint from the registered 119-question development split, but comes from the same benchmark dataset.",
            "The 500-pack retrieval baseline and structural source coverage were observed before confirmation.",
            "Version 2 reuses answer-blind valid resolver calls after version 1 aborted on schema-invalid JSON; no reference answer had been deserialized.",
            "The generic schema-repair policy was fixed after observing the invalid JSON shape but before any Jev, reader, judge, or answer result.",
            "Ollama cloud manifests do not prove immutable remote weights.",
            "One generation per distinct context does not estimate model variance.",
            "The custom judge is not the official LongMemEval judge.",
        ],
    }
    if len(references) != CONFIRMATION_QUESTIONS:
        raise ValueError("confirmation reference coverage differs")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(value, indent=2) + "\n")
    return value


def _validate_registration(
    registration_path: Path,
    *,
    development_registration_path: Path,
    baseline_identity_path: Path,
    source_identity_path: Path,
    resolver_inputs_path: Path,
    references_path: Path,
    controls_path: Path,
    declaration_path: Path,
    calibration_path: Path,
    resolver_seed_registration_path: Path,
    resolver_seed_state_path: Path,
    source_cases_root: Path,
    base_url: str,
    project_root: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    registration = json.loads(registration_path.read_text())
    revision = registration.get("source", {}).get("prme_revision")
    resolver_inputs = json.loads(resolver_inputs_path.read_text())
    resolver_seed = _load_resolver_seed(
        resolver_seed_registration_path, resolver_seed_state_path
    )
    question_ids = registration.get("dataset", {}).get("question_ids", [])
    case_identities = []
    for question_id in question_ids:
        saved = json.loads((source_cases_root / f"{question_id}.json").read_text())
        if saved.get("case_sha256") != _case_checksum(saved):
            raise ValueError(f"source case differs for {question_id}")
        case_identities.append(
            {"question_id": question_id, "case_sha256": saved["case_sha256"]}
        )
    expected_source = _source(
        revision=revision,
        development_registration_path=development_registration_path,
        baseline_identity_path=baseline_identity_path,
        source_identity_path=source_identity_path,
        resolver_inputs_path=resolver_inputs_path,
        references_path=references_path,
        controls_path=controls_path,
        declaration_path=declaration_path,
        calibration_path=calibration_path,
        resolver_seed_registration_path=resolver_seed_registration_path,
        resolver_seed_state_path=resolver_seed_state_path,
        source_cases_manifest_sha256=_sha256(paired.canonical(case_identities)),
    )
    declaration = _validate_calibration(
        controls_path, declaration_path, calibration_path
    )
    resolver_model = registration.get("models", {}).get("resolver", {})
    reader_model = registration.get("models", {}).get("reader", {})
    expected_jev = {
        "provider": "typesafe_jev",
        "model": jev_trial.MODEL,
        "api_url": jev_trial.API_URL,
        "question_sha256": _sha256(paired.canonical(jev_trial.QUESTION)),
    }
    if (
        registration.get("schema_version") != 2
        or registration.get("kind")
        != "longmemeval-s-temporal-relation-confirmation-registration"
        or not isinstance(revision, str)
        or registration.get("source") != expected_source
        or registration.get("dataset", {}).get("questions") != CONFIRMATION_QUESTIONS
        or registration.get("dataset", {}).get("question_ids_sha256")
        != _sha256(paired.canonical(question_ids))
        or registration.get("models", {}).get("resolver")
        != paired.model_identity(
            str(resolver_model.get("model", "")),
            base_url,
            dict(resolver.MODEL_OPTIONS),
            resolver.SYSTEM_PROMPT,
        )
        or registration.get("models", {}).get("jev") != expected_jev
        or registration.get("models", {}).get("reader")
        != paired.model_identity(
            str(reader_model.get("model", "")),
            base_url,
            dict(READER_OPTIONS),
            GENERATION_SYSTEM_PROMPT,
        )
        or registration.get("models", {}).get("judge") != declaration
        or registration.get("resolver_seed")
        != {
            "registration_sha256": resolver_seed["registration_sha256"],
            "state_sha256": resolver_seed["state_sha256"],
            **resolver_seed["summary"],
            "reuse_validation": (
                "exact prompt hash, model digest, response checksum, schema, "
                "registration hash, and state hash"
            ),
        }
        or registration.get("protocol") != _protocol(resolver_seed["summary"])
        or any(
            not path.startswith("benchmarks/results/research/")
            for path in _source_changes_since(revision, project_root)
        )
    ):
        raise ValueError("registered confirmation inputs differ")
    if (
        resolver_inputs.get("kind")
        != "longmemeval-s-temporal-relation-confirmation-resolver-inputs"
        or resolver_inputs.get("question_ids") != question_ids
        or [row.get("question_id") for row in resolver_inputs.get("rows", [])]
        != question_ids
        or any("answer" in row for row in resolver_inputs["rows"])
        or any(
            row.get("question_type") != "temporal-reasoning"
            for row in resolver_inputs["rows"]
        )
    ):
        raise ValueError("confirmation resolver inputs differ")
    jobs = _resolver_jobs(resolver_inputs["rows"], str(resolver_model.get("model")))
    wanted = {key for _row, _records, _body, key in jobs}
    seeded = set(resolver_seed["generations"])
    invalid = set(resolver_seed["invalid"])
    if (
        registration.get("models", {}).get("resolver")
        != resolver_seed["registration"].get("models", {}).get("resolver")
        or resolver_seed["registration"].get("dataset", {}).get("question_ids")
        != question_ids
        or not seeded <= wanted
        or not invalid <= wanted
        or seeded & invalid
    ):
        raise ValueError("registered resolver seed differs")
    return registration, resolver_inputs, resolver_seed


def run_resolver(
    *,
    registration_path: Path,
    resolver_inputs_path: Path,
    state_path: Path,
    output_path: Path,
    base_url: str,
    validation: dict[str, Any],
) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("fresh resolver output required")
    registration = validation["registration"]
    inputs = validation["resolver_inputs"]
    seed = validation["resolver_seed"]
    model = registration["models"]["resolver"]
    identity = {
        "kind": "longmemeval-s-temporal-relation-confirmation-resolver-state",
        "registration_sha256": _sha256_file(registration_path),
        "resolver_inputs_sha256": _sha256_file(resolver_inputs_path),
        "model": model["model"],
        "model_digest": model["model_digest"],
        "system_prompt_sha256": _sha256(resolver.SYSTEM_PROMPT.encode()),
        "options": resolver.MODEL_OPTIONS,
        "seed_registration_sha256": seed["registration_sha256"],
        "seed_state_sha256": seed["state_sha256"],
        "schema_repair_prompt_sha256": _sha256(SCHEMA_REPAIR_PROMPT.encode()),
        "response_schema_sha256": _sha256(
            paired.canonical(resolver.RawResolution.model_json_schema())
        ),
        "maximum_schema_repairs": MAX_SCHEMA_REPAIRS,
    }
    jobs = _resolver_jobs(inputs["rows"], model["model"])
    reuse = {
        "registration_sha256": seed["registration_sha256"],
        "state_sha256": seed["state_sha256"],
        **seed["summary"],
    }
    with reader_runtime.exclusive_state(state_path):
        state = (
            json.loads(state_path.read_text())
            if state_path.exists()
            else {
                "identity": identity,
                "reuse": reuse,
                "generations": dict(seed["generations"]),
                "attempts": {},
                "failed_attempts": [],
                "complete": False,
            }
        )
        if state.get("identity") != identity or state.get("reuse") != reuse:
            raise ValueError("confirmation resolver resume identity differs")
        wanted = {key for _row, _records, _body, key in jobs}
        if (
            not set(state["generations"]) <= wanted
            or not set(state.get("attempts", {})) <= wanted
            or any(
                state["generations"].get(key) != saved
                for key, saved in seed["generations"].items()
            )
        ):
            raise ValueError("confirmation resolver state contains unrelated calls")
        for saved in state["generations"].values():
            if saved["response_sha256"] != _sha256(paired.canonical(saved["response"])):
                raise ValueError("saved resolver response checksum differs")
            resolver._parse_response(saved["response"])
        bodies = {key: body for _row, _records, body, key in jobs}
        for key, attempts in state.get("attempts", {}).items():
            if not isinstance(attempts, list) or len(attempts) > 2:
                raise ValueError("saved resolver attempts differ")
            previous = (
                seed["invalid"][key]["response"] if key in seed["invalid"] else None
            )
            for attempt_index, attempt in enumerate(attempts):
                response = attempt.get("response")
                expected_kind = "repair" if previous is not None else "original"
                expected_body = (
                    _repair_body(bodies[key], previous)
                    if previous is not None
                    else bodies[key]
                )
                if (
                    response is None
                    or attempt.get("response_sha256")
                    != _sha256(paired.canonical(response))
                    or attempt.get("request_kind") != expected_kind
                    or attempt.get("request_sha256")
                    != _sha256(paired.canonical(expected_body))
                    or not judge_runtime.matches_response_model(
                        model["model"], response.get("model")
                    )
                ):
                    raise ValueError("saved resolver attempt checksum differs")
                reader_runtime.validate_response(response)
                if attempt.get("valid_schema") is True:
                    resolver._parse_response(response)
                    if (
                        attempt_index != len(attempts) - 1
                        or state["generations"].get(key, {}).get("response") != response
                    ):
                        raise ValueError("saved valid resolver attempt differs")
                    previous = None
                elif attempt.get("valid_schema") is False:
                    try:
                        resolver._parse_response(response)
                    except ValueError:
                        pass
                    else:
                        raise ValueError("saved invalid resolver attempt is valid")
                    previous = response
                else:
                    raise ValueError("saved resolver attempt validity differs")
        _write(state_path, state)
        print(
            f"Imported {len(seed['generations'])}/{len(jobs)} valid resolver calls",
            flush=True,
        )
        for index, (_row, _records, original_body, key) in enumerate(jobs):
            if key not in state["generations"]:
                attempts = state["attempts"].setdefault(key, [])
                if attempts:
                    previous = attempts[-1]["response"]
                    request_kind = "repair"
                    body = _repair_body(original_body, previous)
                elif key in seed["invalid"]:
                    previous = seed["invalid"][key]["response"]
                    request_kind = "repair"
                    body = _repair_body(original_body, previous)
                else:
                    request_kind = "original"
                    body = original_body
                while True:
                    response = None
                    try:
                        if (
                            reader_runtime.model_digest(base_url, model["model"])
                            != model["model_digest"]
                        ):
                            raise ValueError("resolver model changed")
                        response = reader_runtime.request(base_url, "/api/chat", body)
                        if (
                            not judge_runtime.matches_response_model(
                                model["model"], response.get("model")
                            )
                            or reader_runtime.model_digest(base_url, model["model"])
                            != model["model_digest"]
                        ):
                            raise ValueError("resolver response identity changed")
                        reader_runtime.validate_response(response)
                        try:
                            resolver._parse_response(response)
                        except ValueError as schema_error:
                            attempts.append(
                                {
                                    "request_kind": request_kind,
                                    "request_sha256": _sha256(paired.canonical(body)),
                                    "response": response,
                                    "response_sha256": _sha256(
                                        paired.canonical(response)
                                    ),
                                    "valid_schema": False,
                                    "error_type": type(schema_error).__name__,
                                }
                            )
                            _write(state_path, state)
                            if (
                                request_kind == "repair"
                                or sum(
                                    item["request_kind"] == "repair"
                                    for item in attempts
                                )
                                >= MAX_SCHEMA_REPAIRS
                            ):
                                raise
                            request_kind = "repair"
                            body = _repair_body(original_body, response)
                            continue
                        attempts.append(
                            {
                                "request_kind": request_kind,
                                "request_sha256": _sha256(paired.canonical(body)),
                                "response": response,
                                "response_sha256": _sha256(paired.canonical(response)),
                                "valid_schema": True,
                                "error_type": None,
                            }
                        )
                        state["generations"][key] = {
                            "response": response,
                            "response_sha256": _sha256(paired.canonical(response)),
                        }
                        _write(state_path, state)
                        break
                    except Exception as exc:
                        state["failed_attempts"].append(
                            {
                                "prompt_sha256": key,
                                "request_kind": request_kind,
                                "error_type": type(exc).__name__,
                                "response": response,
                                "response_sha256": (
                                    _sha256(paired.canonical(response))
                                    if response is not None
                                    else None
                                ),
                            }
                        )
                        _write(state_path, state)
                        raise
                print(f"Resolved {index + 1}/{len(jobs)}", flush=True)
        state["complete"] = True
        _write(state_path, state)

    rows = []
    for row, records, _body, key in jobs:
        raw = resolver._parse_response(state["generations"][key]["response"])
        relation, errors = resolver.compute_relation(
            raw, records, _parse_date(row["question_date"])
        )
        rows.append(
            {
                "question_id": row["question_id"],
                "resolution": raw.model_dump(mode="json"),
                "validation_errors": list(errors),
                "relation": relation.model_dump(mode="json") if relation else None,
            }
        )
    value = {
        "schema_version": 2,
        "kind": "longmemeval-s-temporal-relation-confirmation-resolver-result",
        "registration_sha256": _sha256_file(registration_path),
        "complete": state["complete"],
        "failed_attempts": state["failed_attempts"],
        "questions": len(rows),
        "validated_relations": sum(row["relation"] is not None for row in rows),
        "execution": {
            "seeded_valid_generations": len(seed["generations"]),
            "seeded_invalid_schema_attempts": len(seed["invalid"]),
            "fresh_original_calls": sum(
                attempt["request_kind"] == "original"
                for attempts in state["attempts"].values()
                for attempt in attempts
            ),
            "schema_repair_calls": sum(
                attempt["request_kind"] == "repair"
                for attempts in state["attempts"].values()
                for attempt in attempts
            ),
        },
        "rows": rows,
    }
    value["result_sha256"] = _sha256(paired.canonical(value))
    _write(output_path, value)
    return value


def _validate_self_hash(value: dict[str, Any]) -> bool:
    copy = dict(value)
    claimed = copy.pop("result_sha256", None)
    return isinstance(claimed, str) and claimed == _sha256(paired.canonical(copy))


async def run_jev_gate(
    *,
    registration_path: Path,
    resolver_inputs_path: Path,
    resolver_result_path: Path,
    state_path: Path,
    output_path: Path,
    env_file: Path | None,
    validation: dict[str, Any],
) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("fresh Jev output required")
    inputs = validation["resolver_inputs"]
    resolved = json.loads(resolver_result_path.read_text())
    if (
        resolved.get("kind")
        != "longmemeval-s-temporal-relation-confirmation-resolver-result"
        or resolved.get("registration_sha256") != _sha256_file(registration_path)
        or not resolved.get("complete")
        or resolved.get("failed_attempts")
        or not _validate_self_hash(resolved)
    ):
        raise ValueError("confirmation resolver result differs")
    by_input = {row["question_id"]: row for row in inputs["rows"]}
    items = [
        {
            "question_id": row["question_id"],
            "question": by_input[row["question_id"]]["question"],
            "relation": row["relation"],
        }
        for row in resolved["rows"]
        if row["relation"] is not None
    ]
    identity = {
        "kind": "longmemeval-s-temporal-relation-confirmation-jev-state",
        "registration_sha256": _sha256_file(registration_path),
        "resolver_result_sha256": _sha256_file(resolver_result_path),
        "model": jev_trial.MODEL,
        "api_url": jev_trial.API_URL,
        "question_sha256": _sha256(paired.canonical(jev_trial.QUESTION)),
        "threshold": JEV_THRESHOLD,
    }
    state = (
        json.loads(state_path.read_text())
        if state_path.exists()
        else {"identity": identity, "results": {}, "failures": []}
    )
    if state.get("identity") != identity:
        raise ValueError("confirmation Jev resume identity differs")
    wanted = {item["question_id"] for item in items}
    if not set(state["results"]) <= wanted:
        raise ValueError("confirmation Jev state contains unrelated calls")
    for item in items:
        saved = state["results"].get(item["question_id"])
        if saved is not None:
            jev_trial._validate_response(
                saved["response"], len(item["relation"]["operands"])
            )
    _write(state_path, state)
    key = jev_trial._api_key(env_file)
    semaphore = asyncio.Semaphore(jev_trial.CONCURRENCY)
    lock = asyncio.Lock()
    completed = len(state["results"])
    async with httpx.AsyncClient(timeout=jev_trial.TIMEOUT_SECONDS) as client:

        async def execute(item: dict[str, Any]) -> None:
            nonlocal completed
            question_id = item["question_id"]
            if question_id in state["results"]:
                return
            payload = jev_trial._request(item)
            try:
                async with semaphore:
                    value = await jev_trial._request_one(client, key, payload)
            except Exception as exc:
                async with lock:
                    state["failures"].append(
                        {"question_id": question_id, "error_type": type(exc).__name__}
                    )
                    _write(state_path, state)
                raise
            async with lock:
                state["results"][question_id] = value
                _write(state_path, state)
                completed += 1
                print(f"Jev gated {completed}/{len(items)}", flush=True)

        await asyncio.gather(*(execute(item) for item in items))

    score_rows: dict[str, dict[str, Any]] = {}
    for item in items:
        response = state["results"][item["question_id"]]["response"]
        probabilities = [
            float(response["answers"][f"operand_{index}"]["noul"])
            for index in range(len(item["relation"]["operands"]))
        ]
        score_rows[item["question_id"]] = {
            "operand_probabilities": probabilities,
            "minimum_probability": min(probabilities),
        }
    rows = []
    for row in resolved["rows"]:
        score = score_rows.get(row["question_id"])
        rows.append(
            {
                "question_id": row["question_id"],
                "has_validated_relation": row["relation"] is not None,
                "operand_probabilities": (
                    score["operand_probabilities"] if score else []
                ),
                "minimum_probability": (
                    score["minimum_probability"] if score else None
                ),
                "accepted": (
                    score is not None and score["minimum_probability"] >= JEV_THRESHOLD
                ),
            }
        )
    value = {
        "schema_version": 1,
        "kind": "longmemeval-s-temporal-relation-confirmation-jev-result",
        "registration_sha256": _sha256_file(registration_path),
        "resolver_result_sha256": _sha256_file(resolver_result_path),
        "complete": len(state["results"]) == len(items) and not state["failures"],
        "failures": state["failures"],
        "threshold": JEV_THRESHOLD,
        "validated_relations": len(items),
        "accepted_relations": sum(row["accepted"] for row in rows),
        "usage": {
            name: sum(
                saved["response"]["usage"][name] for saved in state["results"].values()
            )
            for name in ("input_tokens", "output_tokens")
        },
        "rows": rows,
    }
    value["result_sha256"] = _sha256(paired.canonical(value))
    _write(output_path, value)
    return value


async def prepare_paired_inputs(
    *,
    registration_path: Path,
    resolver_inputs_path: Path,
    resolver_result_path: Path,
    jev_result_path: Path,
    baseline_root: Path,
    source_cases_root: Path,
    output_path: Path,
    validation: dict[str, Any],
) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("fresh paired input output required")
    inputs = validation["resolver_inputs"]
    resolved = json.loads(resolver_result_path.read_text())
    gated = json.loads(jev_result_path.read_text())
    if (
        not _validate_self_hash(resolved)
        or not _validate_self_hash(gated)
        or gated.get("kind")
        != "longmemeval-s-temporal-relation-confirmation-jev-result"
        or gated.get("registration_sha256") != _sha256_file(registration_path)
        or gated.get("resolver_result_sha256") != _sha256_file(resolver_result_path)
        or not gated.get("complete")
        or gated.get("failures")
        or gated.get("threshold") != JEV_THRESHOLD
    ):
        raise ValueError("confirmation Jev result differs")
    by_resolution = {row["question_id"]: row for row in resolved["rows"]}
    by_gate = {row["question_id"]: row for row in gated["rows"]}
    values = []
    for row in inputs["rows"]:
        question_id = row["question_id"]
        gate = by_gate[question_id]
        resolution = by_resolution[question_id]
        selection = {
            "relation": resolution["relation"],
            "minimum_probability": gate["minimum_probability"],
            "accepted": gate["accepted"],
        }
        public_case = {
            "question_id": question_id,
            "question": row["question"],
            "question_date": row["question_date"],
            "question_type": row["question_type"],
            "answer": "reference withheld until judge stage",
        }
        neutral, _withheld_reference, audit = await answer_trial._prepare_case(
            public_case,
            selection,
            baseline_root=baseline_root,
            source_cases_root=source_cases_root,
        )
        values.append((neutral, audit))
        print(f"Prepared {len(values)}/{len(inputs['rows'])}", flush=True)
    value = {
        "schema_version": 1,
        "kind": "longmemeval-s-temporal-relation-confirmation-answer-inputs",
        "registration_sha256": _sha256_file(registration_path),
        "resolver_result_sha256": _sha256_file(resolver_result_path),
        "jev_result_sha256": _sha256_file(jev_result_path),
        "question_ids": inputs["question_ids"],
        "generation_system_prompt": GENERATION_SYSTEM_PROMPT,
        "arms": list(ARMS),
        "jev_threshold": JEV_THRESHOLD,
        "rows": [item[0] for item in values],
        "audits": [item[1] for item in values],
    }
    if any("answer" in row for row in value["rows"]):
        raise ValueError("confirmation reader inputs contain answers")
    value["result_sha256"] = _sha256(paired.canonical(value))
    _write(output_path, value)
    return value


def run_reader(
    *,
    registration_path: Path,
    resolver_result_path: Path,
    jev_result_path: Path,
    paired_inputs_path: Path,
    state_path: Path,
    output_path: Path,
    base_url: str,
    validation: dict[str, Any],
) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("fresh reader output required")
    registration = validation["registration"]
    prepared = json.loads(paired_inputs_path.read_text())
    if (
        prepared.get("kind")
        != "longmemeval-s-temporal-relation-confirmation-answer-inputs"
        or prepared.get("registration_sha256") != _sha256_file(registration_path)
        or prepared.get("resolver_result_sha256") != _sha256_file(resolver_result_path)
        or prepared.get("jev_result_sha256") != _sha256_file(jev_result_path)
        or prepared.get("question_ids") != registration["dataset"]["question_ids"]
        or prepared.get("arms") != list(ARMS)
        or prepared.get("jev_threshold") != JEV_THRESHOLD
        or any("answer" in row for row in prepared["rows"])
        or not _validate_self_hash(prepared)
    ):
        raise ValueError("confirmation paired inputs differ")
    reader = registration["models"]["reader"]
    predictions = paired.run_reader(
        prepared,
        ARMS,
        state_path,
        registration_sha256=_sha256_file(registration_path),
        prepared_sha256=_sha256_file(paired_inputs_path),
        model=reader["model"],
        model_digest=reader["model_digest"],
        options=dict(READER_OPTIONS),
        system_prompt=GENERATION_SYSTEM_PROMPT,
        base_url=base_url,
    )
    if predictions["failed_attempts"]:
        raise ValueError("confirmation reader execution contains failed attempts")
    result = {
        "schema_version": 1,
        "kind": "longmemeval-s-temporal-relation-confirmation-reader-result",
        "registration_sha256": _sha256_file(registration_path),
        "paired_inputs_sha256": _sha256_file(paired_inputs_path),
        "reader": predictions,
    }
    result["result_sha256"] = _sha256(paired.canonical(result))
    _write(output_path, result)
    return result


def run_judge(
    *,
    registration_path: Path,
    paired_inputs_path: Path,
    reader_result_path: Path,
    references_path: Path,
    output_dir: Path,
    summary_path: Path,
    base_url: str,
    validation: dict[str, Any],
) -> dict[str, Any]:
    if summary_path.exists():
        raise ValueError("fresh confirmation summary required")
    registration = validation["registration"]
    prepared = json.loads(paired_inputs_path.read_text())
    reader_result = json.loads(reader_result_path.read_text())
    if (
        not _validate_self_hash(prepared)
        or not _validate_self_hash(reader_result)
        or reader_result.get("kind")
        != "longmemeval-s-temporal-relation-confirmation-reader-result"
        or reader_result.get("registration_sha256") != _sha256_file(registration_path)
        or reader_result.get("paired_inputs_sha256") != _sha256_file(paired_inputs_path)
        or not reader_result.get("reader", {}).get("complete")
        or reader_result.get("reader", {}).get("failed_attempts")
    ):
        raise ValueError("confirmation reader result differs")

    # Reference answers are intentionally loaded only after the complete reader
    # artifact has been validated above.
    references = json.loads(references_path.read_text())
    ids = registration["dataset"]["question_ids"]
    if (
        _sha256_file(references_path) != registration["source"]["references_sha256"]
        or [row.get("question_id") for row in references] != ids
        or any(row.get("question_type") != "temporal-reasoning" for row in references)
    ):
        raise ValueError("confirmation references differ")
    cases = paired.judge_cases(reader_result["reader"], references, ARMS)
    output_dir.mkdir(parents=True, exist_ok=True)
    judgments = judge_runtime.run_cases(
        cases,
        registration["models"]["judge"],
        output_dir / "judge-state.json",
        base_url,
    )
    _write(output_dir / "judge.json", judgments)
    metrics = paired.paired_metrics(
        cases,
        judgments,
        control_arm=ARMS[0],
        candidate_arm=ARMS[1],
    )
    metrics["accepted_hints"] = answer_trial._hint_metrics(prepared, cases, judgments)
    accepted = metrics["accepted_hints"]["questions"]
    gate = {
        "complete_reader_execution": reader_result["reader"]["complete"] is True,
        "complete_judge_execution": judgments["complete"] is True,
        "reader_failed_attempts": len(reader_result["reader"]["failed_attempts"]),
        "judge_failed_attempts": len(judgments["prior_failed_attempts"]),
        "accepted_hints": accepted,
        "candidate_improved": (
            metrics["overall"]["candidate_correct"]
            > metrics["overall"]["control_correct"]
        ),
        "wins_exceed_losses": (
            metrics["overall"]["paired_wins"] > metrics["overall"]["paired_losses"]
        ),
        "accepted_hint_losses": metrics["accepted_hints"]["paired_losses"],
        "accepted_hint_wins": metrics["accepted_hints"]["paired_wins"],
    }
    gate["passed"] = (
        gate["complete_reader_execution"]
        and gate["complete_judge_execution"]
        and gate["reader_failed_attempts"] == 0
        and gate["judge_failed_attempts"] == 0
        and accepted >= 10
        and gate["candidate_improved"]
        and gate["wins_exceed_losses"]
        and gate["accepted_hint_losses"] == 0
        and gate["accepted_hint_wins"] >= 3
    )
    metrics["gate"] = gate
    execution = {
        "registration_sha256": _sha256_file(registration_path),
        "paired_inputs_sha256": _sha256_file(paired_inputs_path),
        "reader_result_sha256": _sha256_file(reader_result_path),
        "reader": reader_result["reader"],
        "judge": judgments,
        "metrics": metrics,
    }
    _write(output_dir / "execution.json", execution)
    result = {
        "schema_version": 1,
        "kind": "longmemeval-s-temporal-relation-confirmation-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": _sha256_file(registration_path),
        "questions": len(prepared["rows"]),
        "metrics": metrics,
        "packing": {
            "hint_questions": accepted,
            "total_dropped_records": sum(
                audit["dropped_records"] for audit in prepared["audits"]
            ),
            "maximum_dropped_records": max(
                audit["dropped_records"] for audit in prepared["audits"]
            ),
            "control_tokens": sum(
                row["contexts"]["auditable"]["tokens"] for row in prepared["rows"]
            ),
            "candidate_tokens": sum(
                row["contexts"]["temporal_relation"]["tokens"]
                for row in prepared["rows"]
            ),
        },
        "execution_sha256": _sha256_file(output_dir / "execution.json"),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "claim_boundary": registration["claim_boundary"],
    }
    result["result_sha256"] = _sha256(paired.canonical(result))
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def _add_validation_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--development-registration", type=Path, required=True)
    parser.add_argument("--baseline-identity", type=Path, required=True)
    parser.add_argument("--source-identity", type=Path, required=True)
    parser.add_argument("--source-cases-root", type=Path, required=True)
    parser.add_argument("--resolver-inputs", type=Path, required=True)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--controls", type=Path, required=True)
    parser.add_argument("--judge-declaration", type=Path, required=True)
    parser.add_argument("--judge-calibration", type=Path, required=True)
    parser.add_argument("--resolver-seed-registration", type=Path, required=True)
    parser.add_argument("--resolver-seed-state", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register")
    register.add_argument("--dataset", type=Path, required=True)
    register.add_argument("--development-registration", type=Path, required=True)
    register.add_argument("--baseline-identity", type=Path, required=True)
    register.add_argument("--source-identity", type=Path, required=True)
    register.add_argument("--source-cases-root", type=Path, required=True)
    register.add_argument("--resolver-inputs", type=Path, required=True)
    register.add_argument("--references", type=Path, required=True)
    register.add_argument("--controls", type=Path, required=True)
    register.add_argument("--judge-declaration", type=Path, required=True)
    register.add_argument("--judge-calibration", type=Path, required=True)
    register.add_argument("--resolver-seed-registration", type=Path, required=True)
    register.add_argument("--resolver-seed-state", type=Path, required=True)
    register.add_argument("--resolver-model", required=True)
    register.add_argument("--reader-model", required=True)
    register.add_argument("--base-url", default="http://127.0.0.1:11434")
    register.add_argument("--project-root", type=Path, default=Path.cwd())
    register.add_argument("--output", type=Path, required=True)

    resolve = commands.add_parser("resolve")
    _add_validation_args(resolve)
    resolve.add_argument("--state", type=Path, required=True)
    resolve.add_argument("--output", type=Path, required=True)

    gate = commands.add_parser("gate")
    _add_validation_args(gate)
    gate.add_argument("--resolver-result", type=Path, required=True)
    gate.add_argument("--state", type=Path, required=True)
    gate.add_argument("--output", type=Path, required=True)
    gate.add_argument("--env-file", type=Path)

    prepare = commands.add_parser("prepare")
    _add_validation_args(prepare)
    prepare.add_argument("--resolver-result", type=Path, required=True)
    prepare.add_argument("--jev-result", type=Path, required=True)
    prepare.add_argument("--baseline-root", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)

    reader = commands.add_parser("reader")
    _add_validation_args(reader)
    reader.add_argument("--resolver-result", type=Path, required=True)
    reader.add_argument("--jev-result", type=Path, required=True)
    reader.add_argument("--paired-inputs", type=Path, required=True)
    reader.add_argument("--state", type=Path, required=True)
    reader.add_argument("--output", type=Path, required=True)

    judge = commands.add_parser("judge")
    _add_validation_args(judge)
    judge.add_argument("--paired-inputs", type=Path, required=True)
    judge.add_argument("--reader-result", type=Path, required=True)
    judge.add_argument("--output-dir", type=Path, required=True)
    judge.add_argument("--summary", type=Path, required=True)
    return parser


def _validation(args: argparse.Namespace) -> dict[str, Any]:
    registration, resolver_inputs, resolver_seed = _validate_registration(
        args.registration.resolve(),
        development_registration_path=args.development_registration.resolve(),
        baseline_identity_path=args.baseline_identity.resolve(),
        source_identity_path=args.source_identity.resolve(),
        resolver_inputs_path=args.resolver_inputs.resolve(),
        references_path=args.references.resolve(),
        controls_path=args.controls.resolve(),
        declaration_path=args.judge_declaration.resolve(),
        calibration_path=args.judge_calibration.resolve(),
        resolver_seed_registration_path=args.resolver_seed_registration.resolve(),
        resolver_seed_state_path=args.resolver_seed_state.resolve(),
        source_cases_root=args.source_cases_root.resolve(),
        base_url=args.base_url,
        project_root=args.project_root.resolve(),
    )
    return {
        "registration": registration,
        "resolver_inputs": resolver_inputs,
        "resolver_seed": resolver_seed,
    }


def main() -> None:
    args = _parser().parse_args()
    if args.command == "register":
        value = asyncio.run(
            create_registration(
                dataset_path=args.dataset.resolve(),
                development_registration_path=args.development_registration.resolve(),
                baseline_identity_path=args.baseline_identity.resolve(),
                source_identity_path=args.source_identity.resolve(),
                source_cases_root=args.source_cases_root.resolve(),
                resolver_inputs_path=args.resolver_inputs.resolve(),
                references_path=args.references.resolve(),
                controls_path=args.controls.resolve(),
                declaration_path=args.judge_declaration.resolve(),
                calibration_path=args.judge_calibration.resolve(),
                resolver_seed_registration_path=(
                    args.resolver_seed_registration.resolve()
                ),
                resolver_seed_state_path=args.resolver_seed_state.resolve(),
                resolver_model=args.resolver_model,
                reader_model=args.reader_model,
                base_url=args.base_url,
                project_root=args.project_root.resolve(),
                output_path=args.output.resolve(),
            )
        )
        print(
            json.dumps(
                {"dataset": value["dataset"], "protocol": value["protocol"]}, indent=2
            )
        )
        return

    validation = _validation(args)
    shared = {
        "registration_path": args.registration.resolve(),
        "validation": validation,
    }
    if args.command == "resolve":
        value = run_resolver(
            resolver_inputs_path=args.resolver_inputs.resolve(),
            state_path=args.state.resolve(),
            output_path=args.output.resolve(),
            base_url=args.base_url,
            **shared,
        )
    elif args.command == "gate":
        value = asyncio.run(
            run_jev_gate(
                resolver_inputs_path=args.resolver_inputs.resolve(),
                resolver_result_path=args.resolver_result.resolve(),
                state_path=args.state.resolve(),
                output_path=args.output.resolve(),
                env_file=args.env_file.resolve() if args.env_file else None,
                **shared,
            )
        )
    elif args.command == "prepare":
        value = asyncio.run(
            prepare_paired_inputs(
                resolver_result_path=args.resolver_result.resolve(),
                jev_result_path=args.jev_result.resolve(),
                baseline_root=args.baseline_root.resolve(),
                source_cases_root=args.source_cases_root.resolve(),
                output_path=args.output.resolve(),
                registration_path=args.registration.resolve(),
                resolver_inputs_path=args.resolver_inputs.resolve(),
                validation=validation,
            )
        )
    elif args.command == "reader":
        value = run_reader(
            resolver_result_path=args.resolver_result.resolve(),
            jev_result_path=args.jev_result.resolve(),
            paired_inputs_path=args.paired_inputs.resolve(),
            state_path=args.state.resolve(),
            output_path=args.output.resolve(),
            base_url=args.base_url,
            **shared,
        )
    else:
        value = run_judge(
            paired_inputs_path=args.paired_inputs.resolve(),
            reader_result_path=args.reader_result.resolve(),
            references_path=args.references.resolve(),
            output_dir=args.output_dir.resolve(),
            summary_path=args.summary.resolve(),
            registration_path=args.registration.resolve(),
            base_url=args.base_url,
            validation=validation,
        )
    print(
        json.dumps(
            {
                key: value.get(key)
                for key in (
                    "questions",
                    "validated_relations",
                    "accepted_relations",
                    "metrics",
                    "result_sha256",
                )
                if key in value
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
