"""Preregistered real-model assay for attempt and intention preservation.

The authored contrast set measures one narrow extraction boundary. It is a
development assay, not held-out accuracy or a competitive comparison.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from typing import Any
from urllib.request import ProxyHandler, build_opener

from benchmarks.diagnostics._process import checked_report
from benchmarks.diagnostics.entity_references import run as run_extraction
from prme.ingestion.extraction import EXTRACTION_SYSTEM_PROMPT, _CitedExtractionResult


REGISTRATION_KIND = "speech-act-extraction-registration"
REGISTRATION_SCHEMA = 1


CASES: tuple[dict[str, Any], ...] = (
    {
        "name": "trying_setup",
        "source": "I'm trying to set up ESLint v8.39 with the Airbnb style guide.",
        "targets": (("nonactual", ("Airbnb style guide",), ("try", "attempt")),),
    },
    {
        "name": "unicode_trying",
        "source": "I’m trying to configure Vault for the production cluster.",
        "targets": (("nonactual", ("Vault",), ("try", "attempt")),),
    },
    {
        "name": "plan_migration",
        "source": "We plan to migrate the Atlas service to PostgreSQL next quarter.",
        "targets": (("nonactual", ("PostgreSQL",), ("plan", "intend")),),
    },
    {
        "name": "want_adoption",
        "source": "I want to adopt Ruff, but I have not chosen a linter yet.",
        "targets": (("nonactual", ("Ruff",), ("want", "consider")),),
    },
    {
        "name": "need_evaluation",
        "source": "We need to evaluate Amazon S3 before choosing it for backups.",
        "targets": (("nonactual", ("Amazon S3", "S3"), ("need", "evaluat")),),
    },
    {
        "name": "past_attempt",
        "source": "I've tried to install CUDA 12.4 twice, but the installer fails.",
        "targets": (("nonactual", ("CUDA 12.4", "CUDA"), ("try", "attempt")),),
    },
    {
        "name": "conditional_desire",
        "source": "I would like to use Redis if the latency test succeeds.",
        "targets": (("nonactual", ("Redis",), ("like", "want", "hope")),),
    },
    {
        "name": "work_toward_replacement",
        "source": "I'm working to replace Celery with Temporal after the migration rehearsal.",
        "targets": (("nonactual", ("Temporal",), ("work", "try", "attempt")),),
    },
    {
        "name": "hope_deployment",
        "source": "We hope to deploy Phoenix after the security review.",
        "targets": (("nonactual", ("Phoenix",), ("hope", "plan", "intend")),),
    },
    {
        "name": "hypothetical_example",
        "source": "For example, I want to use TLS 1.3; this is hypothetical, not my configuration.",
        "targets": (("nonactual", ("TLS 1.3",), ("want", "example", "hypothetical")),),
    },
    {
        "name": "configured_control",
        "source": "I configured ESLint v8.39 with the Airbnb style guide yesterday.",
        "targets": (("actual", ("Airbnb style guide",), ("configur", "set_up")),),
    },
    {
        "name": "usage_control",
        "source": "We use Redis for session storage in production.",
        "targets": (("actual", ("Redis",), ("use",)),),
    },
    {
        "name": "decision_control",
        "source": "I decided to adopt Ruff for the Orion repository.",
        "targets": (("actual", ("Ruff",), ("adopt", "decid", "chos")),),
    },
    {
        "name": "mixed_plan_and_actual",
        "source": "We plan to migrate to PostgreSQL, but today we run MySQL in production.",
        "targets": (
            ("nonactual", ("PostgreSQL",), ("plan", "intend")),
            ("actual", ("MySQL",), ("run", "use")),
        ),
    },
)


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _matches_object(value: str, aliases: tuple[str, ...]) -> bool:
    normalized = _normalized(value)
    return normalized in {_normalized(alias) for alias in aliases}


def _matches_predicate(value: str, stems: tuple[str, ...]) -> bool:
    normalized = _normalized(value)
    return any(_normalized(stem) in normalized for stem in stems)


def _raw_claims(extraction: dict[str, Any]) -> list[dict[str, str]]:
    claims = [
        {
            "kind": "fact",
            "predicate": str(item.get("predicate", "")),
            "object": str(item.get("object", "")),
        }
        for item in extraction.get("facts", ())
        if isinstance(item, dict)
    ]
    claims.extend(
        {
            "kind": "relationship",
            "predicate": str(item.get("relationship_type", "")),
            "object": str(item.get("target_entity", "")),
        }
        for item in extraction.get("relationships", ())
        if isinstance(item, dict)
    )
    return claims


def _materialized_claims(row: dict[str, Any]) -> list[dict[str, str]]:
    claims = []
    for node in row.get("evaluated_nodes", ()):
        if not isinstance(node, dict) or node.get("node_type") not in {
            "fact",
            "preference",
            "decision",
        }:
            continue
        metadata = node.get("metadata")
        if isinstance(metadata, dict):
            claims.append(
                {
                    "kind": str(node["node_type"]),
                    "predicate": str(metadata.get("predicate", "")),
                    "object": str(metadata.get("object", "")),
                }
            )
    return claims


def score_report(
    report: dict[str, Any], cases: tuple[dict[str, Any], ...] = CASES
) -> dict[str, Any]:
    """Score safety and useful qualifier retention from complete saved output."""
    rows = {
        row.get("case"): row
        for row in report.get("cases", ())
        if isinstance(row, dict) and isinstance(row.get("case"), str)
    }
    results: list[dict[str, Any]] = []
    unsafe_count = missing_count = 0
    target_count = 0
    policy_errors: list[str] = []
    for case in cases:
        name = case["name"]
        row = rows.get(name, {})
        if row.get("extraction_grounding_policy") != "speech_act_v4":
            policy_errors.append(f"{name}:grounding")
        if row.get("materialization_policy") != "speech_act_v10":
            policy_errors.append(f"{name}:materialization")
        raw = _raw_claims(row.get("extraction", {}))
        materialized = _materialized_claims(row)
        target_results = []
        for mode, aliases, stems in case["targets"]:
            target_count += 1
            raw_target = [
                claim for claim in raw if _matches_object(claim["object"], aliases)
            ]
            materialized_target = [
                claim
                for claim in materialized
                if _matches_object(claim["object"], aliases)
            ]
            preserved = any(
                _matches_predicate(claim["predicate"], stems)
                for claim in materialized_target
            )
            unsafe = []
            if mode == "nonactual":
                unsafe = [
                    claim
                    for claim in raw_target
                    if not _matches_predicate(claim["predicate"], stems)
                ]
            unsafe_count += len(unsafe)
            missing_count += int(not preserved)
            target_results.append(
                {
                    "mode": mode,
                    "objects": list(aliases),
                    "predicate_stems": list(stems),
                    "preserved": preserved,
                    "unsafe_claims": unsafe,
                    "raw_target_claims": raw_target,
                    "materialized_target_claims": materialized_target,
                }
            )
        results.append(
            {
                "case": name,
                "source": case["source"],
                "passed": bool(target_results)
                and all(target["preserved"] for target in target_results)
                and all(not target["unsafe_claims"] for target in target_results)
                and not any(error.startswith(name + ":") for error in policy_errors),
                "targets": target_results,
                **(
                    {"execution_error": row.get("error_type", "missing result")}
                    if "extraction" not in row
                    else {}
                ),
            }
        )
    gates = {
        "unsafe_nonactual_claims": {"maximum": 0, "actual": unsafe_count},
        "missing_expected_targets": {"maximum": 0, "actual": missing_count},
        "policy_errors": {"maximum": 0, "actual": len(policy_errors)},
    }
    return {
        "passed": all(result["passed"] for result in results)
        and all(gate["actual"] <= gate["maximum"] for gate in gates.values()),
        "case_count": len(cases),
        "cases_passed": sum(result["passed"] for result in results),
        "target_count": target_count,
        "targets_preserved": target_count - missing_count,
        "unsafe_nonactual_claims": unsafe_count,
        "policy_errors": policy_errors,
        "gates": gates,
        "cases": results,
    }


def _model_inventory(base_url: str) -> dict[str, str]:
    if not base_url.endswith("/v1"):
        raise ValueError("model base URL must end with /v1")
    opener = build_opener(ProxyHandler({}))
    with opener.open(base_url[:-3] + "/api/tags", timeout=10) as response:
        payload = json.load(response)
    return {
        item["name"]: item["digest"]
        for item in payload.get("models", ())
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and isinstance(item.get("digest"), str)
    }


def _implementation_files(root: Path) -> dict[str, str]:
    relative = (
        "benchmarks/diagnostics/entity_references.py",
        "benchmarks/diagnostics/speech_act_extraction.py",
        "src/prme/ingestion/extraction.py",
        "src/prme/ingestion/pipeline.py",
        "src/prme/ingestion/planning.py",
        "src/prme/models/derivation.py",
        "src/prme/models/extraction.py",
    )
    return {path: _file_digest(root / path) for path in relative}


def register(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    if args.registration.exists():
        raise ValueError("fresh registration path required")
    inventory = _model_inventory(args.base_url)
    digest = inventory.get(args.model)
    if digest is None:
        raise ValueError("registered model is unavailable")
    registration = {
        "schema_version": REGISTRATION_SCHEMA,
        "kind": REGISTRATION_KIND,
        "status": "preregistered",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "implementation_sha256": _implementation_files(root),
            "cases_sha256": _canonical_digest(CASES),
            "prompt_sha256": hashlib.sha256(
                EXTRACTION_SYSTEM_PROMPT.encode()
            ).hexdigest(),
            "response_schema_sha256": _canonical_digest(
                _CitedExtractionResult.model_json_schema()
            ),
        },
        "model": {
            "provider": args.provider,
            "model": args.model,
            "model_digest": digest,
            "base_url": args.base_url,
            "temperature": 0.0,
            "max_retries": 3,
            "timeout_seconds": args.timeout,
        },
        "protocol": {
            "cases": len(CASES),
            "targets": sum(len(case["targets"]) for case in CASES),
            "gates": {
                "unsafe_nonactual_claims_max": 0,
                "missing_expected_targets_max": 0,
                "policy_errors_max": 0,
            },
            "limits": "Authored development contrasts; no held-out or competitive claim.",
        },
    }
    args.registration.parent.mkdir(parents=True, exist_ok=True)
    args.registration.write_text(json.dumps(registration, indent=2) + "\n")
    return registration


def verify_registration(
    registration: dict[str, Any], root: Path
) -> SimpleNamespace:
    if registration.get("schema_version") != REGISTRATION_SCHEMA:
        raise ValueError("unsupported registration schema")
    if registration.get("kind") != REGISTRATION_KIND:
        raise ValueError("unexpected registration kind")
    if registration.get("status") != "preregistered":
        raise ValueError("speech-act assay must be preregistered")
    source = registration.get("source")
    if not isinstance(source, dict):
        raise ValueError("registration source is missing")
    expected_source = {
        "implementation_sha256": _implementation_files(root),
        "cases_sha256": _canonical_digest(CASES),
        "prompt_sha256": hashlib.sha256(EXTRACTION_SYSTEM_PROMPT.encode()).hexdigest(),
        "response_schema_sha256": _canonical_digest(
            _CitedExtractionResult.model_json_schema()
        ),
    }
    if source != expected_source:
        raise ValueError("registered implementation or assay inputs differ")
    model = registration.get("model")
    if not isinstance(model, dict):
        raise ValueError("registration model is missing")
    base_url = model.get("base_url")
    model_name = model.get("model")
    model_digest = model.get("model_digest")
    if (
        not isinstance(base_url, str)
        or not isinstance(model_name, str)
        or not isinstance(model_digest, str)
    ):
        raise ValueError("registered model identity is invalid")
    if _model_inventory(base_url).get(model_name) != model_digest:
        raise ValueError("model digest differs from registration")
    protocol = registration.get("protocol")
    expected_gates = {
        "unsafe_nonactual_claims_max": 0,
        "missing_expected_targets_max": 0,
        "policy_errors_max": 0,
    }
    if (
        not isinstance(protocol, dict)
        or protocol.get("gates") != expected_gates
        or protocol.get("cases") != len(CASES)
        or protocol.get("targets") != sum(len(case["targets"]) for case in CASES)
    ):
        raise ValueError("registered gates differ")
    if (
        model.get("provider") != "ollama"
        or model.get("temperature") != 0.0
        or model.get("max_retries") != 3
    ):
        raise ValueError("registered extraction controls differ")
    timeout = model.get("timeout_seconds")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError("invalid registered timeout")
    return SimpleNamespace(
        provider=model["provider"],
        model=model["model"],
        base_url=model["base_url"],
        timeout=float(timeout),
    )


async def execute(registration: dict[str, Any], root: Path) -> dict[str, Any]:
    args = verify_registration(registration, root)
    raw = await run_extraction(
        args,
        cases=[(case["name"], case["source"], {}) for case in CASES],
    )
    score = score_report(raw)
    binding_errors = []
    for key, expected in {
        "provider": registration["model"]["provider"],
        "model": registration["model"]["model"],
        "provider_temperature": registration["model"]["temperature"],
        "provider_max_retries": registration["model"]["max_retries"],
    }.items():
        if raw.get(key) != expected:
            binding_errors.append(key)
    return {
        **score,
        "quality_gates_passed": score["passed"] and not binding_errors,
        "passed": True,
        "kind": "speech-act-extraction-results",
        "schema_version": 1,
        "registration_sha256": _canonical_digest(registration),
        "model": registration["model"],
        "elapsed_seconds": raw.get("elapsed_seconds"),
        "execution_binding_errors": binding_errors,
        "raw_cases": raw.get("cases", []),
        "limits": registration["protocol"]["limits"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--provider", choices=("ollama",), default="ollama")
    parser.add_argument("--model", default="deepseek-v4.1-flash:cloud")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    if args.register:
        result = register(args, root)
    elif args.worker:
        registration = json.loads(args.registration.read_text())
        try:
            result = asyncio.run(execute(registration, root))
        except Exception as exc:
            result = {
                "passed": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "limits": "Incomplete execution; no success claim.",
            }
    else:
        registration = json.loads(args.registration.read_text())
        model = registration.get("model", {})
        timeout = float(model.get("timeout_seconds", args.timeout))
        result = checked_report(
            [
                sys.executable,
                "-m",
                "benchmarks.diagnostics.speech_act_extraction",
                "--worker",
                "--registration",
                str(args.registration.resolve()),
            ],
            timeout=timeout * len(CASES) + 120,
        )
        workflow_passed = result["passed"]
        quality_passed = result.get("quality_gates_passed") is True
        result["workflow_passed"] = workflow_passed
        result["passed"] = workflow_passed and quality_passed
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key not in {"cases", "raw_cases"}}))
    if not result.get("passed", args.register):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
