"""Operational evidence must be complete and fail closed."""

import json
from types import SimpleNamespace

import pytest

from benchmarks.operational_eval import _percentile, run


def test_nearest_rank_percentile_uses_observed_samples():
    values = [4.0, 1.0, 3.0, 2.0]
    assert _percentile(values, .5) == 2
    assert _percentile(values, .95) == 4
    with pytest.raises(ValueError):
        _percentile([], .5)


async def test_operational_smoke_verifies_exact_repeats_and_owner_isolation(tmp_path):
    output = tmp_path / "operational.json"
    report = await run(SimpleNamespace(
        output=output,
        sizes=[3],
        warmups=1,
        latency_samples=3,
        determinism_samples=4,
        isolation_samples=20,
        result_limit=3,
        owners=2,
        duckdb_threads=1,
        embedding_provider="deterministic",
        embedding_model="unused",
    ))
    assert report["complete"] is True
    assert report["cases"][0]["repeatability"]["exact_matches"] == 4
    assert report["cases"][0]["isolation"]["leak_count"] == 0
    assert report["conformance"]["rfc_0005_operational_requirements_met"] is False
    saved = json.loads(output.read_bytes())
    assert saved == report
    assert saved["provenance"]["engine"]["vector_exact_search"] is True


async def test_operational_invalid_plan_does_not_create_an_artifact(tmp_path):
    output = tmp_path / "invalid.json"
    with pytest.raises(ValueError):
        await run(SimpleNamespace(
            output=output, sizes=[0], warmups=0, latency_samples=1,
            determinism_samples=1, isolation_samples=1, result_limit=1,
            owners=2,
            duckdb_threads=1, embedding_provider="deterministic",
            embedding_model="unused",
        ))
    assert not output.exists()
