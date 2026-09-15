"""Compare verified, matched PRME and BM25 MemoryAgentBench task arms."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from benchmarks.compare_evidence import paired_statistics


_READER_FIELDS = (
    "model",
    "temperature",
    "reader_reasoning_effort",
    "reader_seed",
    "reader_output_contract",
)
_TASK_FIELDS = (
    "dataset",
    "sub_dataset",
    "context_count",
    "query_count",
    "query_limit",
)
_QUERY_FIELDS = (
    "query_id",
    "query_sha256",
    "retrieval_query_sha256",
    "answer_sha256",
    "qa_pair_id_sha256",
)
_PRIMARY_METRICS = {
    "Accurate_Retrieval": "substring_exact_match",
    "Conflict_Resolution": "substring_exact_match",
    "Long_Range_Understanding": "exact_match",
    "Test_Time_Learning": "exact_match",
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read JSON object: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _resolve(base: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"task pair is missing {label}")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"task pair {label} is not a file: {path}")
    return path


def _source_identity(registration: dict[str, Any]) -> tuple[str, str, str]:
    source = registration.get("source")
    if not isinstance(source, dict):
        raise ValueError("registration source identity is missing")
    revision = source.get("prme_revision")
    upstream = source.get("upstream_revision")
    dataset = source.get("dataset_revision")
    if not all(
        isinstance(value, str) and value for value in (revision, upstream, dataset)
    ):
        raise ValueError("registration source identity is incomplete")
    assert isinstance(revision, str)
    assert isinstance(upstream, str)
    assert isinstance(dataset, str)
    return revision, upstream, dataset


def _validate_registrations(
    prme: dict[str, Any], bm25: dict[str, Any]
) -> dict[str, Any]:
    if (
        prme.get("schema_version") != 1
        or prme.get("kind") != "memoryagentbench-prme-registration"
        or bm25.get("schema_version") != 1
        or bm25.get("kind") != "memoryagentbench-bm25-registration"
    ):
        raise ValueError("unsupported MemoryAgentBench registration")
    identity = _source_identity(prme)
    if _source_identity(bm25) != identity:
        raise ValueError("paired registrations use different source revisions")

    prme_config = prme.get("configuration")
    bm25_config = bm25.get("configuration")
    if not isinstance(prme_config, dict) or not isinstance(bm25_config, dict):
        raise ValueError("paired registration configuration is missing")
    if prme_config.get("dataset") != bm25_config.get("dataset"):
        raise ValueError("paired registrations use different dataset configuration")
    prme_agent = prme_config.get("agent")
    bm25_agent = bm25_config.get("agent")
    if not isinstance(prme_agent, dict) or not isinstance(bm25_agent, dict):
        raise ValueError("paired registration agent configuration is missing")
    if any(prme_agent.get(name) != bm25_agent.get(name) for name in _READER_FIELDS):
        raise ValueError("paired registrations use different reader settings")

    prme_source = prme["source"]
    bm25_source = bm25["source"]
    preprocessing = prme_source.get("preprocessing")
    dependencies = bm25_source.get("dependencies")
    if not isinstance(preprocessing, dict) or not isinstance(dependencies, dict):
        raise ValueError("paired preprocessing identity is missing")
    if any(dependencies.get(name) != value for name, value in preprocessing.items()):
        raise ValueError("paired registrations use different preprocessing")

    prme_task = prme.get("task")
    bm25_task = bm25.get("task")
    if not isinstance(prme_task, dict) or not isinstance(bm25_task, dict):
        raise ValueError("paired registration task identity is missing")
    if any(prme_task.get(name) != bm25_task.get(name) for name in _TASK_FIELDS):
        raise ValueError("paired registrations identify different tasks")
    dataset = prme_task.get("dataset")
    if dataset not in _PRIMARY_METRICS:
        raise ValueError(f"no official primary metric is registered for {dataset!r}")

    prme_contexts = prme_task.get("contexts")
    bm25_contexts = bm25_task.get("contexts")
    if (
        not isinstance(prme_contexts, list)
        or not isinstance(bm25_contexts, list)
        or len(prme_contexts) != len(bm25_contexts)
    ):
        raise ValueError("paired registration contexts are missing or misaligned")
    query_ids: list[int] = []
    for prme_context, bm25_context in zip(prme_contexts, bm25_contexts):
        if not isinstance(prme_context, dict) or not isinstance(bm25_context, dict):
            raise ValueError("paired registration context is invalid")
        if prme_context.get("context_id") != bm25_context.get("context_id"):
            raise ValueError("paired registrations use different context assignments")
        prme_chunks = prme_context.get("source_chunks")
        bm25_chunks = bm25_context.get("source_chunks")
        if not isinstance(prme_chunks, list) or not isinstance(bm25_chunks, list):
            raise ValueError("paired source identities are missing")
        compact_prme_chunks = [
            (chunk.get("index"), chunk.get("sha256"))
            for chunk in prme_chunks
            if isinstance(chunk, dict)
        ]
        compact_bm25_chunks = [
            (chunk.get("index"), chunk.get("sha256"))
            for chunk in bm25_chunks
            if isinstance(chunk, dict)
        ]
        if (
            len(compact_prme_chunks) != len(prme_chunks)
            or compact_prme_chunks != compact_bm25_chunks
        ):
            raise ValueError("paired registrations use different source chunks")
        prme_queries = prme_context.get("queries")
        bm25_queries = bm25_context.get("queries")
        if not isinstance(prme_queries, list) or not isinstance(bm25_queries, list):
            raise ValueError("paired registered queries are missing")
        compact_prme_queries = [
            tuple(query.get(name) for name in _QUERY_FIELDS)
            for query in prme_queries
            if isinstance(query, dict)
        ]
        compact_bm25_queries = [
            tuple(query.get(name) for name in _QUERY_FIELDS)
            for query in bm25_queries
            if isinstance(query, dict)
        ]
        if (
            len(compact_prme_queries) != len(prme_queries)
            or compact_prme_queries != compact_bm25_queries
        ):
            raise ValueError("paired registrations use different questions or answers")
        for query in compact_prme_queries:
            query_id = query[0]
            if isinstance(query_id, bool) or not isinstance(query_id, int):
                raise ValueError("paired registration query ID is invalid")
            query_ids.append(query_id)
    query_count = prme_task.get("query_count")
    if (
        isinstance(query_count, bool)
        or not isinstance(query_count, int)
        or query_count <= 0
        or query_ids != list(range(query_count))
    ):
        raise ValueError("paired registration query IDs are incomplete or out of order")
    return {
        "source_identity": identity,
        "dataset": dataset,
        "sub_dataset": prme_task.get("sub_dataset"),
        "queries": query_count,
        "contexts": len(prme_contexts),
        "source_chunks": sum(len(context["source_chunks"]) for context in prme_contexts),
        "primary_metric": _PRIMARY_METRICS[dataset],
        "reader": {name: prme_agent.get(name) for name in _READER_FIELDS},
    }


def _validate_verification(
    report: dict[str, Any],
    *,
    kind: str,
    registration_path: Path,
    result_path: Path,
    expected: dict[str, Any],
) -> None:
    if (
        report.get("schema_version") != 1
        or report.get("kind") != kind
        or report.get("status") != "verified_complete"
    ):
        raise ValueError(f"{kind} is not a complete supported verification")
    source = report.get("source")
    task = report.get("task")
    if not isinstance(source, dict) or not isinstance(task, dict):
        raise ValueError(f"{kind} is missing source or task identity")
    if source.get("registration_sha256") != _digest(registration_path):
        raise ValueError(f"{kind} does not bind the supplied registration")
    if source.get("result_sha256") != _digest(result_path):
        raise ValueError(f"{kind} does not bind the supplied result")
    revision, upstream, dataset_revision = expected["source_identity"]
    if (
        source.get("prme_revision") != revision
        or source.get("upstream_revision") != upstream
        or source.get("dataset_revision") != dataset_revision
    ):
        raise ValueError(f"{kind} source identity differs from the registration")
    for name in ("dataset", "sub_dataset", "queries", "contexts", "source_chunks"):
        if task.get(name) != expected[name]:
            raise ValueError(f"{kind} task identity differs at {name}")


def _metric_values(result: dict[str, Any], name: str, count: int) -> list[int]:
    metrics = result.get("metrics")
    values = metrics.get(name) if isinstance(metrics, dict) else None
    if not isinstance(values, list) or len(values) != count:
        raise ValueError(f"result metric {name!r} does not cover every query")
    normalized: list[int] = []
    for value in values:
        if isinstance(value, bool):
            normalized.append(int(value))
        elif isinstance(value, (int, float)) and math.isfinite(value) and value in (0, 1):
            normalized.append(int(value))
        else:
            raise ValueError(f"result metric {name!r} is not binary")
    return normalized


def _exact_mcnemar(wins: int, losses: int) -> float:
    discordant = wins + losses
    if discordant == 0:
        return 1.0
    tail = sum(math.comb(discordant, index) for index in range(min(wins, losses) + 1))
    return min(1.0, 2.0 * tail / (2**discordant))


def _paired_summary(prme: list[int], bm25: list[int], *, samples: int) -> dict[str, Any]:
    statistics = paired_statistics(list(zip(bm25, prme)), samples=samples, seed=42)
    wins = int(statistics["wins"])
    losses = int(statistics["losses"])
    statistics["exact_mcnemar_p_two_sided"] = _exact_mcnemar(wins, losses)
    return {
        "questions": len(prme),
        "prme_correct": sum(prme),
        "bm25_correct": sum(bm25),
        "prme_accuracy": sum(prme) / len(prme),
        "bm25_accuracy": sum(bm25) / len(bm25),
        "paired_prme_minus_bm25": statistics,
    }


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} is not finite numeric evidence")
    return float(value)


def compare(manifest_path: Path, *, samples: int = 10_000) -> dict[str, Any]:
    """Build a paired report from exact registrations and complete verifications."""
    if isinstance(samples, bool) or not isinstance(samples, int) or samples <= 0:
        raise ValueError("bootstrap samples must be positive")
    manifest_path = manifest_path.expanduser().resolve()
    manifest = _load_object(manifest_path)
    tasks = manifest.get("tasks")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "memoryagentbench-paired-manifest"
        or not isinstance(tasks, list)
        or not tasks
    ):
        raise ValueError("unsupported or empty MemoryAgentBench paired manifest")

    base = manifest_path.parent
    labels: set[str] = set()
    task_identities: set[tuple[object, object]] = set()
    common_source: tuple[str, str, str] | None = None
    common_reader: dict[str, Any] | None = None
    all_prme: list[int] = []
    all_bm25: list[int] = []
    task_reports: dict[str, Any] = {}
    for entry in tasks:
        if not isinstance(entry, dict):
            raise ValueError("paired manifest task entry is not an object")
        label = entry.get("label")
        if not isinstance(label, str) or not label or label in labels:
            raise ValueError("paired manifest task labels must be non-empty and unique")
        labels.add(label)
        paths = {
            name: _resolve(base, entry.get(name), name)
            for name in (
                "prme_registration",
                "bm25_registration",
                "prme_verification",
                "bm25_verification",
                "prme_result",
                "bm25_result",
            )
        }
        registrations = {
            "prme": _load_object(paths["prme_registration"]),
            "bm25": _load_object(paths["bm25_registration"]),
        }
        expected = _validate_registrations(registrations["prme"], registrations["bm25"])
        task_identity = (expected["dataset"], expected["sub_dataset"])
        if task_identity in task_identities:
            raise ValueError("paired manifest repeats a benchmark task")
        task_identities.add(task_identity)
        if common_source is None:
            common_source = expected["source_identity"]
            common_reader = expected["reader"]
        elif common_source != expected["source_identity"] or common_reader != expected["reader"]:
            raise ValueError("paired manifest mixes source revisions or reader settings")

        verifications = {
            "prme": _load_object(paths["prme_verification"]),
            "bm25": _load_object(paths["bm25_verification"]),
        }
        _validate_verification(
            verifications["prme"],
            kind="memoryagentbench-prme-verification",
            registration_path=paths["prme_registration"],
            result_path=paths["prme_result"],
            expected=expected,
        )
        _validate_verification(
            verifications["bm25"],
            kind="memoryagentbench-bm25-verification",
            registration_path=paths["bm25_registration"],
            result_path=paths["bm25_result"],
            expected=expected,
        )
        results = {
            "prme": _load_object(paths["prme_result"]),
            "bm25": _load_object(paths["bm25_result"]),
        }
        metric = expected["primary_metric"]
        prme_values = _metric_values(results["prme"], metric, expected["queries"])
        bm25_values = _metric_values(results["bm25"], metric, expected["queries"])
        summary = _paired_summary(prme_values, bm25_values, samples=samples)
        summary.update(
            {
                "dataset": expected["dataset"],
                "sub_dataset": expected["sub_dataset"],
                "official_primary_metric": metric,
                "contexts": expected["contexts"],
                "source_chunks": expected["source_chunks"],
                "efficiency": {
                    "prme_retrieved_context_tokens_mean": _number(
                        verifications["prme"].get("retrieval", {}).get(
                            "context_tokens_mean"
                        ),
                        "PRME retrieved context tokens",
                    ),
                    "bm25_retrieved_context_tokens_mean": _number(
                        verifications["bm25"].get("retrieval", {}).get(
                            "context_tokens_mean"
                        ),
                        "BM25 retrieved context tokens",
                    ),
                    "prme_reader_input_tokens_mean": _number(
                        verifications["prme"].get("averaged_metrics", {}).get(
                            "input_len"
                        ),
                        "PRME reader input tokens",
                    ),
                    "bm25_reader_input_tokens_mean": _number(
                        verifications["bm25"].get("averaged_metrics", {}).get(
                            "input_len"
                        ),
                        "BM25 reader input tokens",
                    ),
                },
                "artifacts": {name: _digest(path) for name, path in paths.items()},
            }
        )
        task_reports[label] = summary
        all_prme.extend(prme_values)
        all_bm25.extend(bm25_values)

    aggregate = _paired_summary(all_prme, all_bm25, samples=samples)
    aggregate["task_macro_accuracy"] = {
        "prme": sum(value["prme_accuracy"] for value in task_reports.values())
        / len(task_reports),
        "bm25": sum(value["bm25_accuracy"] for value in task_reports.values())
        / len(task_reports),
    }
    assert common_source is not None and common_reader is not None
    return {
        "schema_version": 1,
        "kind": "memoryagentbench-paired-comparison",
        "status": "verified_complete",
        "source": {
            "prme_revision": common_source[0],
            "upstream_revision": common_source[1],
            "dataset_revision": common_source[2],
            "manifest_sha256": _digest(manifest_path),
            "comparator_sha256": _digest(Path(__file__).resolve()),
        },
        "reader": common_reader,
        "bootstrap": {"samples": samples, "seed": 42, "unit": "question"},
        "tasks": task_reports,
        "overall": aggregate,
        "limitations": [
            "The comparison covers only the exact registered development tasks and reader.",
            "Question bootstrap intervals condition on these selected questions and do not establish population-level product leadership.",
            "BM25 is a matched lexical control, not a feature-equivalent memory product.",
            "Arms ran sequentially, so timing fields are descriptive rather than a controlled latency comparison.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    args = parser.parse_args()
    report = compare(args.manifest, samples=args.bootstrap_samples)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(_canonical(report) + b"\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
