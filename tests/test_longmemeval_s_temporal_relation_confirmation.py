import json

from benchmarks.diagnostics import longmemeval_s_temporal_relation_confirmation as trial


def test_confirmation_protocol_freezes_disjoint_stages_and_gate() -> None:
    protocol = trial._protocol({"valid_generations": 99, "invalid_schema_attempts": 1})

    assert protocol["stages"] == ["resolve", "gate", "prepare", "reader", "judge"]
    assert "judge is the first stage" in protocol["answer_isolation"]
    assert protocol["jev"]["minimum_operand_probability"] == 0.85
    assert protocol["jev"]["threshold_fixed_from_development"] is True
    assert protocol["gate"]["accepted_hints"] == ">= 10"
    assert protocol["gate"]["accepted_hint_losses"] == 0
    assert protocol["gate"]["accepted_hint_wins"] == ">= 3"
    assert protocol["resolver"]["seeded_valid_generations"] == 99
    assert protocol["resolver"]["maximum_schema_repairs_per_question"] == 1


def test_confirmation_source_type_rule_preserves_abstention_controls() -> None:
    assert trial._source_question_type("ordinary", "temporal-reasoning") == (
        "temporal-reasoning"
    )
    assert trial._source_question_type("example_abs", "temporal-reasoning") == (
        "abstention"
    )


def test_self_hash_rejects_mutation() -> None:
    value = {"kind": "example", "count": 2}
    value["result_sha256"] = trial._sha256(trial.paired.canonical(value))

    assert trial._validate_self_hash(value) is True
    value["count"] = 3
    assert trial._validate_self_hash(value) is False


def _response(content: str) -> dict:
    return {
        "model": trial.RESOLVER_MODEL,
        "done": True,
        "done_reason": "stop",
        "message": {"role": "assistant", "content": content},
        "prompt_eval_count": 100,
        "eval_count": 20,
    }


def test_resolver_repairs_seeded_schema_failure_without_new_original_call(
    tmp_path, monkeypatch
) -> None:
    evidence_id = "00000000-0000-0000-0000-000000000001"
    row = {
        "question_id": "example_abs",
        "question": "Which happened first?",
        "question_date": "2024/03/20 10:00",
        "question_type": "temporal-reasoning",
        "control": {
            "context": json.dumps(
                {
                    "id": evidence_id,
                    "event_time": "2024-03-04T10:00:00Z",
                    "text": "I fixed the fence today.",
                }
            )
        },
    }
    jobs = trial._resolver_jobs([row], trial.RESOLVER_MODEL)
    prompt_sha256 = jobs[0][3]
    invalid = _response(
        json.dumps(
            {
                "operation": "order",
                "operands": [
                    {
                        "name": "missing event",
                        "evidence_id": "unsupported",
                        "quote": "",
                        "time_expression": "",
                        "time_basis": "none",
                    }
                ],
            }
        )
    )
    valid = _response(json.dumps({"operation": "unsupported", "operands": []}))
    registration_path = tmp_path / "registration.json"
    inputs_path = tmp_path / "inputs.json"
    registration_path.write_text("{}")
    inputs_path.write_text("{}")
    validation = {
        "registration": {
            "models": {
                "resolver": {
                    "model": trial.RESOLVER_MODEL,
                    "model_digest": "digest",
                }
            }
        },
        "resolver_inputs": {"rows": [row]},
        "resolver_seed": {
            "registration_sha256": "seed-registration",
            "state_sha256": "seed-state",
            "summary": {"valid_generations": 0, "invalid_schema_attempts": 1},
            "generations": {},
            "invalid": {
                prompt_sha256: {
                    "prompt_sha256": prompt_sha256,
                    "response": invalid,
                }
            },
        },
    }
    requests = []

    def request(_base_url, _endpoint, body):
        requests.append(body)
        return valid

    monkeypatch.setattr(trial.reader_runtime, "model_digest", lambda *_: "digest")
    monkeypatch.setattr(trial.reader_runtime, "request", request)

    result = trial.run_resolver(
        registration_path=registration_path,
        resolver_inputs_path=inputs_path,
        state_path=tmp_path / "state.json",
        output_path=tmp_path / "result.json",
        base_url="http://example.invalid",
        validation=validation,
    )

    assert result["complete"] is True
    assert result["failed_attempts"] == []
    assert result["execution"]["fresh_original_calls"] == 0
    assert result["execution"]["schema_repair_calls"] == 1
    assert len(requests) == 1
    assert requests[0]["format"] == trial.resolver.RawResolution.model_json_schema()
    assert requests[0]["messages"][-1]["content"] == trial.SCHEMA_REPAIR_PROMPT
