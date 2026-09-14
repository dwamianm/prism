"""Invalid numerical configuration fails before retrieval or persistence."""

import pytest
from pydantic import ValidationError

from prme import PRMEConfig
from prme.retrieval.config import PackingConfig, ScoringWeights


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("field", ["w_semantic", "recency_lambda", "node_type_boost"])
def test_nonfinite_scoring_fields_have_specific_validation_errors(field, bad):
    value = {"fact": bad} if field == "node_type_boost" else bad
    with pytest.raises(ValidationError) as exc:
        ScoringWeights(**{field: value})
    assert any(
        error["loc"][0] == field and error["type"] == "finite_number"
        for error in exc.value.errors()
    )


@pytest.mark.parametrize(
    "field",
    ["chars_per_token", "session_context_score_decay", "aggregation_k_multiplier"],
)
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_packing_parameters_fail_at_load(field, bad):
    with pytest.raises(ValidationError) as exc:
        PackingConfig(**{field: bad})
    assert exc.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize(
    "setting,payload,location",
    [
        ("PRME_SCORING", '{"w_semantic":"NaN"}', ("scoring", "w_semantic")),
        (
            "PRME_PACKING",
            '{"aggregation_k_multiplier":"Infinity"}',
            ("packing", "aggregation_k_multiplier"),
        ),
        (
            "PRME_NAMESPACE_WEIGHTS",
            '{"personal":{"node_type_boost":{"fact":"NaN"}}}',
            ("namespace_weights", "personal", "node_type_boost", "fact"),
        ),
    ],
)
def test_nested_environment_configuration_cannot_bypass_validation(
    monkeypatch, setting, payload, location
):
    monkeypatch.setenv(setting, payload)
    with pytest.raises(ValidationError) as exc:
        PRMEConfig(_env_file=None)
    assert any(
        error["loc"] == location and error["type"] == "finite_number"
        for error in exc.value.errors()
    )


def test_valid_configurations_keep_roundtrip_versions_and_values():
    weights = ScoringWeights(
        w_semantic=0.3, w_lexical=0.15, node_type_boost={"fact": 1.2}
    )
    restored = ScoringWeights.model_validate_json(weights.model_dump_json())
    assert restored == weights and restored.version_id == weights.version_id
    packing = PackingConfig(
        aggregation_k_multiplier=2, session_context_score_decay=0.75
    )
    assert PackingConfig.model_validate_json(packing.model_dump_json()) == packing


@pytest.mark.parametrize(
    ("field", "value"),
    [("chars_per_token", 3.5), ("cross_scope_token_budget", 128)],
)
def test_ignored_legacy_packing_fields_warn_on_nondefault_use(field, value):
    with pytest.warns(FutureWarning, match=field):
        packing = PackingConfig(**{field: value})
    assert getattr(packing, field) == value


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("graph_max_candidates", -1),
        ("vector_k", -1),
        ("lexical_k", -1),
        ("graph_max_hops", 0),
        ("graph_max_hops", 4),
        ("cross_scope_top_n", -1),
    ],
)
def test_candidate_generation_limits_reject_impossible_values(field, value):
    with pytest.raises(ValidationError) as exc:
        PackingConfig(**{field: value})
    assert exc.value.errors()[0]["loc"] == (field,)
