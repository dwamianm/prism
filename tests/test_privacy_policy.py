"""AP02/AP03: policy validation/composition and fake-clock deadline semantics."""

from datetime import timedelta

from pydantic import ValidationError
import pytest

from prme.privacy import CategoryAction, MemoryRetention, PrivacyDenied, PrivacyPolicy
from prme.privacy.policy import compose, snapshot
from tests.privacy_fakes import make_runtime, policy_for


@pytest.mark.parametrize("changes", [
    {"capture": "none", "evidence_use": "runtime"},
    {"capture": "minimal"},
    {"capture": "full", "evidence_lifetime_seconds": -1},
    {"language": "fr"}, {"registry_version": "future"},
    {"preset": "strict-v1"}, {"processing_ceiling_seconds": 0},
    {"category_actions": [{"category": "credential", "action": "allow"}]},
])
def test_AP02_invalid_closed_config(changes):
    runtime = make_runtime()
    with pytest.raises((ValidationError, PrivacyDenied)):
        policy_for(runtime, **changes)
    assert runtime.sanitizer.calls == [] and runtime.embedder.sends == []


def test_AP03_composition_and_deadlines_are_restrictive():
    r = make_runtime()
    base = policy_for(r, capture="full", evidence_use="runtime", evidence_lifetime_seconds=500,
                      memory_retention=MemoryRetention(mode="erase_after", seconds=1000))
    caller = PrivacyPolicy.model_validate({**base.model_dump(), "revision": 2, "capture": "minimal",
        "evidence_use": "diagnostics_only", "evidence_lifetime_seconds": 100,
        "memory_retention": {"mode": "until_deleted"}, "durable_processing_input": False})
    saved = snapshot(base, caller, r.clock())
    assert saved.policy.capture == "minimal" and saved.policy.evidence_use == "diagnostics_only"
    assert saved.policy.memory_retention.seconds == 1000
    assert saved.evidence_deadline == saved.processing_deadline == r.clock() + timedelta(seconds=100)
    assert saved.memory_deadline == r.clock() + timedelta(seconds=1000)
    assert not saved.policy.durable_processing_input
    with pytest.raises(ValidationError):
        saved.policy.capture = "full"
    assert len(saved.references) == 2


def test_AP03_conflicting_transforms_reject_and_scopes_do_not_bleed():
    r = make_runtime()
    base = policy_for(r, preset="custom-v1", category_actions=(CategoryAction(category="email", action="redact"),))
    caller = PrivacyPolicy.model_validate({**base.model_dump(), "category_actions": [{"category": "email", "action": "pseudonymize"}]})
    with pytest.raises(PrivacyDenied, match="conflict"):
        compose(base, caller)
    foreign = policy_for(r)
    with pytest.raises(PrivacyDenied, match="boundary"):
        compose(base, foreign)
    assert base.category_actions[0].action == "redact"


def test_unvalidated_model_copy_cannot_bypass_policy_validation():
    r = make_runtime()
    p = policy_for(r)
    with pytest.raises(PrivacyDenied, match="configuration_invalid"):
        snapshot(None, p.model_copy(update={"capture": "unrecognized"}), r.clock())
    with pytest.raises(PrivacyDenied, match="conflict"):
        compose(p, p.model_copy(update={"durable_processing_input": False}))
    with pytest.raises(PrivacyDenied, match="configuration_invalid"):
        snapshot(None, policy_for(r, memory_retention=MemoryRetention(mode="erase_after", seconds=10**30)), r.clock())
