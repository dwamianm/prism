"""The assay must preserve failures, input identity and independent labels."""

import copy

import pytest

from benchmarks.diagnostics import jev_memory_admission as assay


def oracle_cache(reg):
    cache = {"registration_sha256": assay.cache_identity(reg), "results": {}}
    for row in reg["cases"]:
        selected = row["fact"]["fact_type"]
        cache["results"][row["id"]] = {
            "request_sha256": assay.digest(assay.payload(row)),
            "elapsed_seconds": 0.2,
            "response": {
                "model": assay.MODEL,
                "answers": {
                    "supported": {
                        "type": "noul",
                        "noul": 0.99 if row["expected_admit"] else 0.01,
                    },
                    "owner_attributed": {"type": "noul", "noul": 0.99},
                    "kind": {
                        "type": "choice",
                        "choice": selected,
                        "confidence": 1,
                        "probabilities": {
                            k: int(k == selected)
                            for k in assay.QUESTIONS["kind"]["criteria"]
                        },
                    },
                },
                "usage": {"input_tokens": 100, "output_tokens": 0},
            },
        }
    return cache


def test_fixture_is_balanced_and_provider_never_gets_labels():
    rows = assay.cases()
    assert len(rows) == len({row["id"] for row in rows}) == 40
    assert sum(row["expected_admit"] for row in rows) == 20
    for row in rows:
        assert set(assay.payload(row)["state"]) == {"source", "claim", "evidence_quote"}


def test_known_decisions_measure_safety_and_retention_separately():
    reg = assay.registration()
    report = assay.score(reg, oracle_cache(reg))
    assert report["screening_passed"]
    assert report["jev_gate"]["tp"] == report["jev_gate"]["tn"] == 20
    assert report["local_support_validator"]["false_admissions"] > 0
    assert report["supported_kind_correct"] == report["supported_kind_total"] == 20


def test_missing_provider_results_count_as_lost_claims_and_errors():
    reg = assay.registration()
    cache = oracle_cache(reg)
    valid = next(row for row in reg["cases"] if row["expected_admit"])
    del cache["results"][valid["id"]]
    report = assay.score(reg, cache)
    assert report["provider_errors_or_missing"] == 1
    assert report["jev_gate"]["valid_claims_lost"] == 1
    assert not report["screening_passed"]


def test_cached_threshold_sweep_is_marked_exploratory():
    reg = assay.registration()
    assert assay.score(reg, oracle_cache(reg), 0.8)["exploratory_threshold"]


def test_cache_cannot_reuse_a_decision_for_changed_source(tmp_path):
    reg = assay.registration()
    cache = oracle_cache(reg)
    cache["results"][reg["cases"][0]["id"]]["request_sha256"] = "stale"
    path = tmp_path / "cache.json"
    assay.write(path, cache)
    with pytest.raises(ValueError, match="cached request differs"):
        assay.read_cache(path, reg)


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), -0.1, 1.1])
def test_probability_validation_fails_closed(bad):
    with pytest.raises(ValueError, match="invalid probability"):
        assay.probability(bad)


def test_model_identity_and_distribution_are_validated():
    reg = assay.registration()
    response = next(iter(oracle_cache(reg)["results"].values()))["response"]
    mutated = copy.deepcopy(response)
    mutated["model"] = "jev-latest"
    with pytest.raises(ValueError, match="model mismatch"):
        assay.validate_response(mutated)
    mutated = copy.deepcopy(response)
    mutated["answers"]["kind"]["probabilities"]["other"] = 0.8
    with pytest.raises(ValueError, match="invalid Choice sum"):
        assay.validate_response(mutated)
