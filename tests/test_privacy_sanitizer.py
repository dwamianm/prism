"""Bounded mechanism checks; these do not substitute for the 24 SN executions."""

from dataclasses import replace
import json
from uuid import uuid4

import pytest

from prme.privacy import CategoryAction, PrivacyDenied
from prme.privacy.capabilities import OperationBudget, SourceOnlyRegion
from prme.privacy.local_analyzer import Detection
from prme.privacy.recognizers import CustomRule, MAX_INPUT_BYTES, Span
from prme.privacy.sanitizer import canonical, restore_budget
from prme.privacy.strict_schema import ClosedSchema, ValueValidator
from tests.privacy_sanitizer_support import Rules, Schemas, ScopedTestKeys, admit, setup
from tests.privacy_sanitizer_support import analyzer as analyzer


async def test_nested_admission_and_sanitized_checksum(analyzer):
    runtime, policy, saved = setup(analyzer)
    source = await admit(runtime.sanitizer, saved, "Email sample.person@example.com.",
                         metadata={"sample.person@example.com": {"name": "sample.person@example.com"}})
    assert "sample.person@example.com" not in source.fields_json
    assert "[EMAIL_REMOVED]" in source.fields_json
    assert source.source_snapshot == saved
    assert dict(source.action_counts)["email:redact"] == 3
    assert source.continuation.input_bytes > 0
    with pytest.raises(PrivacyDenied):
        await admit(runtime.sanitizer, saved, "fine", metadata={"password": "synthetic"})
    with pytest.raises(PrivacyDenied):
        await admit(runtime.sanitizer, saved, "fine", metadata={
            "first@example.com": "one", "second@example.com": "two"})
    with pytest.raises(PrivacyDenied):
        await admit(runtime.sanitizer, saved, "", mode="generated_output",
                    source=replace(source, fields_json=canonical({"content": "changed"})),
                    raw_fields=canonical({"summary": "fine"}))


async def test_placeholder_provenance_and_key_loss(analyzer):
    keys, epoch = ScopedTestKeys(), uuid4()
    runtime, policy, saved = setup(analyzer, sanitizer_options={"key_provider": keys, "pseudonym_epoch": epoch},
                                  policy_options={"category_actions": (CategoryAction(category="email", action="pseudonymize"),)})
    keys.provision(policy, epoch)
    first = await admit(runtime.sanitizer, saved, "sample.person@example.com")
    second = await admit(runtime.sanitizer, saved, "sample.person@example.com")
    assert first.issued_tokens == second.issued_tokens and len(first.issued_tokens) == 1
    token = first.issued_tokens[0]
    output = await admit(runtime.sanitizer, saved, "", mode="generated_output", source=first,
                         raw_fields=canonical({"summary": token}), budget=restore_budget(first.continuation))
    assert token in output.fields_json
    spoof = await admit(runtime.sanitizer, saved, token)
    assert token not in spoof.fields_json
    assert "[LITERAL_PRME_PSEUDONYM_V1:" in spoof.fields_json
    with pytest.raises(PrivacyDenied):
        await admit(runtime.sanitizer, saved, "", mode="generated_output", source=second,
                    raw_fields=canonical({"summary": token.replace(str(epoch), str(uuid4()))}))
    keys.keys.clear()
    with pytest.raises(PrivacyDenied):
        await admit(runtime.sanitizer, saved, "", mode="generated_output", source=first,
                    raw_fields=canonical({"summary": token}))


async def test_closed_values_and_private_rule_resolution(analyzer):
    ref = uuid4()
    schema = ClosedSchema(ref, (("enabled", ValueValidator("boolean")),
                               ("count", ValueValidator("bounded_integer", minimum=0, maximum=10))))
    runtime, _, saved = setup(analyzer, sanitizer_options={"schema_provider": Schemas(schema)},
                              policy_options={"strict_schema_ref": ref, "strict_schema_version": "prme-strict-schema-v1"})
    accepted = await admit(runtime.sanitizer, saved, canonical({"enabled": True, "count": 4}))
    assert json.loads(json.loads(accepted.fields_json)["content"]) == {"enabled": True, "count": 4}
    for invalid in ({"enabled": "true", "count": 4}, {"enabled": True, "count": True},
                    {"enabled": True, "count": 4, "notes": "arbitrary escape"}):
        with pytest.raises(PrivacyDenied):
            await admit(runtime.sanitizer, saved, canonical(invalid))
    runtime, _, saved = setup(analyzer, sanitizer_options={"rule_provider": Rules((CustomRule(
        "literal_topic", "synthetic violet orchard"),))}, policy_options={"exclusion_refs": (uuid4(),)})
    with pytest.raises(PrivacyDenied):
        await admit(runtime.sanitizer, saved, "synthetic violet orchard")
    assert (await admit(runtime.sanitizer, saved, "The public agenda is ready.")).complete


async def test_source_only_classification_is_engine_supplied(analyzer):
    class ClassifiedSource:
        async def classify(self, fields_json, snapshot):
            # Fixture declaration: this entire sanitized source is explicitly
            # source-only. No topic/marker detector makes this determination.
            return (SourceOnlyRegion(("content",), 0, len(json.loads(fields_json)["content"])),)

    runtime, _, saved = setup(analyzer, sanitizer_options={"source_classifier": ClassifiedSource()})
    source = await admit(runtime.sanitizer, saved, "The meeting is nearby.")
    with pytest.raises(PrivacyDenied, match="privacy_evidence_ambiguous"):
        await admit(runtime.sanitizer, saved, "", mode="generated_output", source=source,
                    raw_fields=canonical({"summary": "The meeting is nearby."}))
    assert (await admit(runtime.sanitizer, saved, "", mode="generated_output", source=source,
                        raw_fields=canonical({"summary": "The cup is on the table."}))).complete


class NoInference:
    """Budget/offset fault injection only; never detector quality evidence."""
    def close(self):
        pass

    async def ready(self):
        pass

    async def analyze(self, texts, *, seconds):
        return Detection(tuple(() for text in texts))


async def test_aggregate_exact_bound_and_failure_poison():
    runtime, _, saved = setup(NoInference())
    budget = OperationBudget(uuid4())
    result = await admit(runtime.sanitizer, saved, "x" * MAX_INPUT_BYTES, budget=budget)
    assert result.continuation.input_bytes == MAX_INPUT_BYTES
    with pytest.raises(PrivacyDenied):
        await admit(runtime.sanitizer, saved, "x", budget=budget)
    with pytest.raises(PrivacyDenied):
        await admit(runtime.sanitizer, saved, "", budget=budget)
    resumed = restore_budget(result.continuation)
    with pytest.raises(PrivacyDenied):
        await admit(runtime.sanitizer, saved, "", mode="generated_output", source=result,
                    raw_fields=canonical({"summary": "x"}), budget=resumed)


async def test_invalid_or_partial_detector_results_fail_closed():
    class Invalid(NoInference):
        async def analyze(self, texts, *, seconds):
            return Detection(tuple((Span("email", 0, len(text) + 1, "injected"),) for text in texts))

    runtime, _, saved = setup(Invalid())
    with pytest.raises(PrivacyDenied, match="privacy_invalid_result"):
        await admit(runtime.sanitizer, saved, "The cup is on the table.")

    class Partial(NoInference):
        async def analyze(self, texts, *, seconds):
            return Detection(tuple(() for text in texts), complete=False)

    runtime, _, saved = setup(Partial())
    with pytest.raises(PrivacyDenied, match="privacy_capability_unavailable"):
        await admit(runtime.sanitizer, saved, "The cup is on the table.")
