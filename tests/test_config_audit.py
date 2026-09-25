"""Provisional configuration must be discoverable without reading source."""

from __future__ import annotations

import json
from argparse import Namespace

import pytest
from pydantic import BaseModel, Field, SecretStr

from prme import PRMEConfig, audit_hypotheses
from prme.cli import build_parser, cmd_config_audit
from prme.retrieval.config import PackingConfig, ScoringWeights


EXPECTED_HYPOTHESES = {
    "scoring.current_update_multiplier",
    "scoring.rrf_k",
    "scoring.rrf_recency_boost",
    "packing.session_context_rank_fusion_score_decay",
    "packing.cross_scope_top_n",
    "packing.episode_context_top_k",
    "packing.episode_context_local_k",
    "packing.episode_context_score_decay",
    "packing.evidence_projection_top_k",
    "packing.evidence_projection_max_sources",
    "packing.evidence_projection_score_decay",
    "packing.evidence_augmentation_top_k",
    "packing.evidence_augmentation_max_sources",
    "packing.evidence_augmentation_score_decay",
    "packing.evidence_augmentation_anchor_policy",
    "organizer.promotion_age_days",
    "organizer.promotion_evidence_count",
    "organizer.dedup_similarity_threshold",
    "organizer.alias_similarity_threshold",
    "organizer.consolidation_min_cluster_size",
    "organizer.consolidation_similarity_threshold",
    "organizer.consolidation_preserve_recent_days",
    "organizer.consolidation_min_confidence_preserve",
    "enable_qa_pairing",
    "novelty_high_threshold",
    "novelty_low_threshold",
    "novelty_salience_boost",
    "novelty_salience_penalty",
    "query_reformulation_count",
    "epistemic_weights",
    "unverified_confidence_threshold",
}


def _by_path(config: PRMEConfig):
    report = audit_hypotheses(config)
    return report, {item.path: item for item in report.settings}


def test_default_hypothesis_audit_is_complete_and_reports_dormant_features():
    report, settings = _by_path(PRMEConfig())

    assert set(settings) == EXPECTED_HYPOTHESES
    assert report.hypothesis_count == len(EXPECTED_HYPOTHESES)
    assert report.effective_count == 15
    assert report.customized_count == 0
    assert settings["scoring.current_update_multiplier"].effective is True
    # Rank fusion, its recency boost and its session decay are the defaults, so their provisional values apply
    # and are the defaults the audit reports, not customizations.
    rank_constant = settings["scoring.rrf_k"]
    assert (rank_constant.effective, rank_constant.value, rank_constant.default) == (True, 60, 60)
    recency_boost = settings["scoring.rrf_recency_boost"]
    assert (recency_boost.effective, recency_boost.value, recency_boost.default) == (True, .25, .25)
    session_decay = settings["packing.session_context_rank_fusion_score_decay"]
    assert (session_decay.effective, session_decay.value, session_decay.default) == (True, .6, .6)
    assert not rank_constant.customized and not recency_boost.customized and not session_decay.customized
    assert settings["packing.episode_context_top_k"].effective is False
    assert settings["packing.episode_context_local_k"].effective is False
    assert settings["enable_qa_pairing"].effective is False
    assert settings["novelty_high_threshold"].effective is False
    assert settings["query_reformulation_count"].effective is False
    assert settings["organizer.promotion_evidence_count"].environment_variable == (
        "PRME_ORGANIZER__PROMOTION_EVIDENCE_COUNT"
    )
    # Under the weighted formula the rank fusion settings are dormant, and dropping them is a customization.
    _, weighted = _by_path(PRMEConfig(scoring=ScoringWeights()))
    for name in ("scoring.rrf_k", "scoring.rrf_recency_boost"):
        assert weighted[name].effective is False
        assert weighted[name].customized is True


def test_hypothesis_audit_resolves_feature_gates_and_custom_values():
    config = PRMEConfig(
        packing=PackingConfig(
            episode_context_top_k=2,
            evidence_projection_top_k=1,
        ),
        enable_qa_pairing=True,
        enable_surprise_gating=True,
        enable_query_reformulation=True,
        query_reformulation_count=3,
    )
    report, settings = _by_path(config)

    # A PackingConfig built in code has no rank fusion session decay, which the audit reports.
    assert report.effective_count == 26
    assert report.customized_count == 5
    assert settings["packing.session_context_rank_fusion_score_decay"].value is None
    assert settings["packing.episode_context_local_k"].effective is True
    assert settings["packing.evidence_projection_score_decay"].effective is True
    assert settings["packing.evidence_augmentation_anchor_policy"].effective is False
    assert settings["novelty_salience_boost"].effective is True
    assert settings["query_reformulation_count"].value == 3

    _, augmentation = _by_path(
        PRMEConfig(packing=PackingConfig(evidence_augmentation_top_k=1))
    )
    assert augmentation["packing.evidence_augmentation_anchor_policy"].effective is True

    _, rank_fused = _by_path(PRMEConfig(scoring=ScoringWeights(fusion="rrf")))
    assert rank_fused["scoring.rrf_k"].effective is True
    assert rank_fused["scoring.rrf_k"].value == 60
    assert rank_fused["scoring.rrf_k"].activation_condition == "scoring.fusion == 'rrf'"

    _, session = _by_path(PRMEConfig(
        scoring=ScoringWeights(fusion="rrf"),
        packing=PackingConfig(session_context_rank_fusion_score_decay=.5),
    ))
    session_decay = session["packing.session_context_rank_fusion_score_decay"]
    assert session_decay.effective is True
    assert session_decay.value == .5
    assert session_decay.customized is True
    assert session_decay.activation_condition == (
        "packing.session_context_rank_fusion_score_decay is set"
    )
    assert session_decay.environment_variable == (
        "PRME_PACKING__SESSION_CONTEXT_RANK_FUSION_SCORE_DECAY"
    )

    _, recency = _by_path(PRMEConfig(scoring=ScoringWeights(fusion="rrf", rrf_recency_boost=.5)))
    boost = recency["scoring.rrf_recency_boost"]
    assert boost.effective is True
    assert boost.value == .5
    assert boost.customized is True
    assert boost.activation_condition == (
        "scoring.rrf_recency_boost is set (current-state questions only)"
    )
    assert boost.environment_variable == "PRME_SCORING__RRF_RECENCY_BOOST"


def test_hypothesis_audit_redacts_future_secret_fields():
    class FutureConfig(BaseModel):
        credential: SecretStr = Field(
            default=SecretStr("default-secret"),
            description="Provisional credential [HYPOTHESIS]",
        )

    report = audit_hypotheses(FutureConfig(credential="runtime-secret"))
    serialized = report.model_dump_json()

    assert report.settings[0].value == "<redacted>"
    assert report.settings[0].default == "<redacted>"
    assert report.settings[0].customized is True
    assert "runtime-secret" not in serialized
    assert "default-secret" not in serialized


def test_config_audit_parser_exposes_machine_readable_format():
    args = build_parser().parse_args(["config-audit", "--format", "json"])

    assert args.command == "config-audit"
    assert args.format == "json"
    assert args.func is cmd_config_audit


@pytest.mark.asyncio
async def test_config_audit_command_emits_versioned_json(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)

    await cmd_config_audit(Namespace(format="json"))

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "hypothesis-audit-v1"
    assert payload["hypothesis_count"] == len(EXPECTED_HYPOTHESES)
    assert {item["path"] for item in payload["settings"]} == EXPECTED_HYPOTHESES
