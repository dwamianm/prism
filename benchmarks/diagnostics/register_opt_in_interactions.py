"""Freeze the opt-in study design, without running or scoring any dataset.

Later task manifests and untouched confirmation identities must be verified
before their corresponding stages can run. Existing output is never replaced.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
from unittest.mock import patch

from prme import PRMEConfig
from benchmarks.integrations import run_longmemeval_s_baseline as baseline


BASE_REVISION = "a66ee854325890c6bc28f6b515efeb6ed7df4deb"
INGESTION_FLAGS = (
    "enable_store_supersedence", "enable_qa_pairing", "enable_surprise_gating",
)
SINGLES = {
    "store_supersedence": {"enable_store_supersedence": True},
    "qa_pairing": {"enable_qa_pairing": True},
    "surprise_gating": {"enable_surprise_gating": True},
    "reranker": {"enable_reranker": True},
    "query_reformulation": {"enable_query_reformulation": True},
    "temporal_relations": {"temporal_relation.enabled": True},
    "episode_routing": {"packing.episode_context_top_k": 2},
    "evidence_augmentation": {"packing.evidence_augmentation_top_k": 10},
}
INTERACTIONS = {
    "episode_augmentation": ("episode_routing", "evidence_augmentation"),
    "episode_projection": ("episode_routing",),
    "reranker_reformulation": ("reranker", "query_reformulation"),
    "temporal_episode": ("temporal_relations", "episode_routing"),
    "supersedence_balanced": ("store_supersedence",),
    "qa_balanced": ("qa_pairing",),
    "surprise_balanced": ("surprise_gating",),
    "full_feature_exploratory": tuple(SINGLES),
}
ALIASES = dict(zip(
    ("supersedence_balanced", "qa_balanced", "surprise_balanced"),
    ("store_supersedence", "qa_pairing", "surprise_gating"),
))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha(path):
    return baseline._sha256_file(path)


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation is intentional: amendments must have a new identity.
    with path.open("x") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")


def clean_config():
    # _env_file=None alone does NOT disable PRME_* process environment settings.
    with patch.dict(os.environ, {}, clear=True):
        return PRMEConfig(_env_file=None).model_dump(mode="json")


def configure(defaults, overrides):
    data = deepcopy(defaults)
    for key, value in overrides.items():
        parts = key.split(".")
        target = data
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value
    with patch.dict(os.environ, {}, clear=True):
        # model_copy(update=...) would bypass the mutually-exclusive policy check.
        return PRMEConfig(_env_file=None, **data).model_dump(mode="json")


def arms(defaults):
    all_overrides = {"baseline": {}, **SINGLES}
    for name, components in INTERACTIONS.items():
        changes = {key: value for arm in components for key, value in SINGLES[arm].items()}
        if name == "episode_projection":
            changes["packing.evidence_projection_top_k"] = 50
        if name in ALIASES:
            changes["packing.multipath_ordering"] = "balanced"
        all_overrides[name] = changes
    result = []
    for name, changes in all_overrides.items():
        config = configure(defaults, changes)
        ingestion = {key: config[key] for key in INGESTION_FLAGS}
        result.append({
            "id": name, "overrides": changes, "config": config,
            "config_sha256": sha(config),
            "ingestion_flags": ingestion,
            "ingestion_group": sha(ingestion),
            "fresh_ingestion_required": name == "baseline" or any(ingestion.values()),
            "alias_of": ALIASES.get(name),
            "exploratory": name == "full_feature_exploratory",
            "memory_artifact_sha256": None,
            "status": "registered_not_run",
        })
    result.append({
        "id": "best_individuals_combined", "status": "pending_registered_selection",
        "config": None, "config_sha256": None, "memory_artifact_sha256": None,
        "selection": "See the frozen selection rule in OPT-IN-INTERACTIONS-PROTOCOL.md; no outcome-selected arm exists yet.",
    })
    return result


def register(root, dataset, output):
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if revision != BASE_REVISION:
        raise ValueError("Register from the specified production revision before research commits")
    defaults = clean_config()
    # All paths are expanded to a private pack by a future executor.
    defaults.update(db_path="{pack}/memory.duckdb", vector_path="{pack}/vectors.usearch",
                    lexical_path="{pack}/lexical_index")
    defaults["extraction"].update(provider="ollama", model="deepseek-v4.1-flash:cloud")
    cases = baseline._load_dataset(dataset)
    dataset_identity = baseline._dataset_identity(dataset, cases)
    references = [
        "AGENTS.md", "memory_bank/GOALS.md", "docs/RESEARCH-AGENDA.md",
        "docs/PACKING.md", "docs/INTEGRATION.md", "docs/TEMPORAL-RELATIONS.md",
        "docs/JEV-PRODUCT-ADVISOR.md", "pyproject.toml",
        "benchmarks/integrations/run_longmemeval_s_baseline.py",
        "benchmarks/results/research/2026-09-17/longmemeval-s-prme-baseline-v1-registration.json",
        "benchmarks/results/research/2026-09-18/longmemeval-s-temporal-relation-live-preflight-v1-registration.json",
        "benchmarks/results/research/2026-09-22/OPT-IN-INTERACTIONS-PROTOCOL.md",
        "benchmarks/diagnostics/register_opt_in_interactions.py",
        "benchmarks/diagnostics/run_opt_in_interactions.py",
        "benchmarks/results/research/2026-09-22/opt-in-model-assets.json",
        "benchmarks/results/research/2026-09-22/opt-in-artifact-integrity-result.json",
        "benchmarks/results/research/2026-09-22/opt-in-interactions-deepseek-preflight-result.json",
        "benchmarks/results/research/2026-09-22/opt-in-interactions-reformulation-preflight-result.json",
        "benchmarks/results/research/2026-09-22/opt-in-explicit-workflow-preflight-result.json",
    ]
    packages = sorted({
        (dist.metadata["Name"], dist.version)
        for dist in importlib.metadata.distributions() if dist.metadata["Name"].lower() != "prme"
    })
    previous = json.loads((root / references[9]).read_text())
    result = {
        "schema_version": 1, "kind": "prme-opt-in-interactions-design-registration",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "status": "registered_before_dataset_execution",
        "production_revision": revision,
        "branch": "research/opt-in-interactions-2026-09-22",
        "source_sha256": {name: file_sha(root / name) for name in references},
        "production_python_sources_sha256": {
            str(path.relative_to(root)): file_sha(path) for path in sorted((root / "src/prme").rglob("*.py"))
        },
        "local_validation_environment": {
            "python": platform.python_version(), "platform": platform.platform(),
            "packages": dict(packages), "packages_sha256": sha(packages),
            "historical_dependency_parity_verified": False,
            "boundary": "Observed local validation environment; the old LongMemEval-S registration has no complete dependency lock. Do not claim recovered historical pins.",
        },
        "longmemeval_s": {
            "dataset": dataset_identity,
            "ordered_question_ids": [case["question_id"] for case in cases],
            "original_reader": previous["protocol"]["reader"], "original_judge": previous["protocol"]["judge"],
            "provider_amendment": {
                "user_instruction": "Use the ollama deepseek4.1 flash instead",
                "reader_and_judge": "deepseek-v4.1-flash:cloud",
                "model_digest": "e04da138d31e0c9468e982e1ae9503d06cb7e170caa16a90c17d931c4aa140f8",
                "base_url": "http://127.0.0.1:11434",
                "options": {"temperature": 0, "seed": 42, "num_ctx": 65536},
                "think": False, "stream": False,
                "reader_num_predict": 1024, "judge_num_predict": 64,
                "prompts_and_scoring": "Unchanged original reader prompt and official evaluator; new provider, not a matched GPT-5.4 comparison.",
            },
            "ingestion": previous["protocol"]["ingestion"],
            "classification": "Previously examined development cohort, all 500; never an untouched confirmation.",
            "current_baseline_required": True,
            "historical_results_are_new_control": False,
        },
        "arms": arms(defaults),
        "readiness_gates": [
            "User-amended DeepSeek reader/judge live authored preflight passes; retain original GPT failures.",
            "Documented historical pins match; current full environment is frozen. Unknown historical transitive dependencies remain an explicit limitation.",
            "Pinned upstream evaluator/generation file hashes and installed product source hashes verified.",
            "Reranker assets and query-reformulation provider/model/options are pinned and healthy.",
            "Temporal Ollama digest/options and TypeSafe Jev protocol match the confirmation.",
            "Executor records swallowed provider/expansion failures, complete pack and receipt chains, immutable arm inputs, and every registered question.",
            "No dataset execution on failed readiness; no partial answer estimates.",
        ],
        "stage_order": ["LongMemEval-S", "MAB Banking", "MAB EventQA", "MAB Conflict", "MAB Detective", "BEAM", "MemoryArena"],
        "later_stages": "Design only until upstream configuration/manifests and exclusion-based untouched cohort identities are bound before inference. Do not label them fully preregistered cohorts yet.",
        "jev_product_workflow": "Separate authored explicit pair-selection/publication/review assay; never a retrieval flag or bulk pipeline.",
        "promotion_authorized": False,
    }
    result["registration_sha256"] = sha(result)
    write_new(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = register(args.root, args.dataset, args.output)
    print(json.dumps({"registration_sha256": result["registration_sha256"], "arms": len(result["arms"])}))


if __name__ == "__main__":
    main()
