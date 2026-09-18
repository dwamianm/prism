"""Results-scoped launcher for the frozen confirmation v2 reader function."""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.diagnostics import longmemeval_s_temporal_relation_confirmation as trial


REPO = Path("/private/tmp/prme-longmemeval-s-packing")
DATA = Path(
    "/Users/dwamianm/Sites/prism/data/benchmarks/"
    "longmemeval-s-temporal-relation-confirmation-v2"
)

registration, resolver_inputs, resolver_seed = trial._validate_registration(
    REPO / "benchmarks/results/research/2026-09-18/"
    "longmemeval-s-temporal-relation-confirmation-v2-registration.json",
    development_registration_path=REPO / "benchmarks/results/research/2026-09-18/"
    "longmemeval-s-monotonic-answer-dev-v2-registration.json",
    baseline_identity_path=Path(
        "/Users/dwamianm/Sites/prism/data/benchmarks/"
        "longmemeval-s-prme-baseline-v1/identity.json"
    ),
    source_identity_path=Path(
        "/Users/dwamianm/Sites/prism/data/benchmarks/"
        "longmemeval-s-monotonic-compact-v1/identity.json"
    ),
    source_cases_root=Path(
        "/Users/dwamianm/Sites/prism/data/benchmarks/"
        "longmemeval-s-monotonic-compact-v1/cases"
    ),
    resolver_inputs_path=DATA / "resolver-inputs.json",
    references_path=DATA / "references.json",
    controls_path=REPO / "benchmarks/fixtures/reader_judge_controls.json",
    declaration_path=Path(
        "/Users/dwamianm/Sites/prism/data/benchmarks/"
        "longmemeval-s-monotonic-answer-dev-v1/judge-declaration-v3.json"
    ),
    calibration_path=Path(
        "/Users/dwamianm/Sites/prism/data/benchmarks/"
        "longmemeval-s-monotonic-answer-dev-v1/judge-calibration-v3.json"
    ),
    resolver_seed_registration_path=REPO / "benchmarks/results/research/2026-09-18/"
    "longmemeval-s-temporal-relation-confirmation-v1-registration.json",
    resolver_seed_state_path=Path(
        "/Users/dwamianm/Sites/prism/data/benchmarks/"
        "longmemeval-s-temporal-relation-confirmation-v1/"
        "resolver-state-aborted.json"
    ),
    base_url="http://127.0.0.1:11434",
    project_root=REPO,
)
result = trial.run_reader(
    registration_path=REPO / "benchmarks/results/research/2026-09-18/"
    "longmemeval-s-temporal-relation-confirmation-v2-registration.json",
    resolver_result_path=DATA / "resolver-result.json",
    jev_result_path=DATA / "jev-result.json",
    paired_inputs_path=DATA / "paired-inputs.json",
    state_path=DATA / "reader-state.json",
    output_path=DATA / "reader-result.json",
    base_url="http://127.0.0.1:11434",
    validation={
        "registration": registration,
        "resolver_inputs": resolver_inputs,
        "resolver_seed": resolver_seed,
    },
)
print(
    json.dumps(
        {
            "questions": result["reader"]["questions"],
            "unique_generations": result["reader"]["unique_generations"],
            "result_sha256": result["result_sha256"],
        },
        indent=2,
    )
)
