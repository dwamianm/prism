"""Protocol controls for the paired MemoryArena travel runner."""

import json
from pathlib import Path

import pytest

from benchmarks.integrations import run_memoryarena_travel as runner


def _rows():
    rows = []
    identity = 1
    for count in (5, 6, 7, 8):
        for _ in range(5):
            rows.append({
                "id": identity,
                "questions": [f"q-{index}" for index in range(count)],
            })
            identity += 1
    return rows


def test_cohort_selection_is_seeded_stratified_and_excludes_preflight():
    first = runner.select_cohort(
        _rows(), seed=17, groups_per_stratum=2, excluded_ids={1}
    )
    second = runner.select_cohort(
        _rows(), seed=17, groups_per_stratum=2, excluded_ids={1}
    )

    assert first == second
    assert len(first) == 8
    assert 1 not in {item["id"] for item in first}
    assert sorted(item["person_count"] for item in first) == [5, 5, 6, 6, 7, 7, 8, 8]
    assert len({item["id"] for item in first}) == len(first)


@pytest.mark.parametrize("groups", [0, -1, 6])
def test_cohort_selection_rejects_invalid_or_unavailable_strata(groups):
    with pytest.raises(ValueError):
        runner.select_cohort(
            _rows(), seed=17, groups_per_stratum=groups, excluded_ids=set()
        )


def test_group_checkpoints_resume_and_bind_registration(tmp_path: Path):
    path = tmp_path / "checkpoints.jsonl"
    record = {
        "schema_version": 1,
        "registration_sha256": "a" * 64,
        "arm": "prme",
        "group_id": 7,
        "persons": [],
    }
    runner._append_checkpoint(path, record)
    assert runner._load_checkpoints(path, "a" * 64) == {("prme", 7): record}

    with pytest.raises(RuntimeError, match="registration differs"):
        runner._load_checkpoints(path, "b" * 64)


def test_group_checkpoint_repairs_only_incomplete_tail(tmp_path: Path):
    path = tmp_path / "checkpoints.jsonl"
    record = {
        "registration_sha256": "a" * 64,
        "arm": "prme",
        "group_id": 7,
    }
    path.write_bytes(runner._canonical(record) + b"\n{\"interrupted\"")

    assert runner._load_checkpoints(path, "a" * 64) == {("prme", 7): record}
    assert path.read_bytes().endswith(b"\n")
    assert len(path.read_text().splitlines()) == 1


def test_person_checkpoints_resume_and_bind_registration(tmp_path: Path):
    path = tmp_path / "persons.jsonl"
    record = {
        "schema_version": 1,
        "registration_sha256": "a" * 64,
        "arm": "native_full_history",
        "group_id": 7,
        "person_idx": 2,
        "person": {"result": "saved"},
    }
    runner._append_checkpoint(path, record)

    assert runner._load_person_checkpoints(path, "a" * 64) == {
        ("native_full_history", 7, 2): record
    }
    with pytest.raises(RuntimeError, match="person checkpoint registration differs"):
        runner._load_person_checkpoints(path, "b" * 64)


def test_submission_preserves_explicit_failed_plans(tmp_path: Path):
    path = tmp_path / "submission.jsonl"
    rows = runner._write_submission(path, [{
        "group_id": 3,
        "persons": [{
            "person_idx": 1,
            "name": "Ada",
            "query": "plan",
            "plan": None,
        }],
    }])

    assert rows[0]["persons"][0]["plan"] is None
    assert json.loads(path.read_text())["persons"][0]["plan"] is None


def test_final_plan_projection_discards_reasoning_and_requires_named_boundary():
    response = (
        "analysis with Day 1: fragments\n"
        "=== Ada's Plan ===\nDay 1:\nBreakfast: Tea\n"
    )
    assert runner._final_plan_block(response, "Ada") == (
        "=== Ada's Plan ===\nDay 1:\nBreakfast: Tea"
    )
    assert runner._final_plan_block(response, "Bob") == ""


def test_final_plan_projection_accepts_exact_marker_without_line_boundary():
    response = "analysis\nFinal plan:=== Ada's Plan ===\nDay 1:\nBreakfast: Tea"
    assert runner._final_plan_block(response, "Ada") == (
        "=== Ada's Plan ===\nDay 1:\nBreakfast: Tea"
    )


def test_server_attempt_paths_do_not_reuse_a_prior_pack(tmp_path: Path, monkeypatch):
    (tmp_path / "prme-pack-attempt-1").mkdir()
    captured = {}

    class Process:
        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

    def popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    monkeypatch.setattr(runner, "_wait_for_server", lambda *_args: None)

    _process, log = runner._start_server(Path("/project"), tmp_path, 8018, 4096)
    log.close()

    command = captured["command"]
    assert str(tmp_path / "prme-pack-attempt-2") in command
    assert (tmp_path / "prme-adapter-attempt-2.log").is_file()


def test_actor_transport_policy_is_explicitly_bound(monkeypatch):
    class Agent:
        client = object()

    configured = object()
    monkeypatch.setattr(runner, "OllamaNativeTravelClient", lambda actor: configured)
    agent = Agent()
    runner._configure_actor_client(agent, {"transport": "ollama_native_chat"})
    assert agent.client is configured


def test_actor_transport_rejects_unregistered_compatibility_path():
    with pytest.raises(RuntimeError, match="transport is unsupported"):
        runner._configure_actor_client(type("Agent", (), {})(), {
            "transport": "openai_compatibility",
        })


def test_tool_executor_resolves_at_boundary_and_retains_audit():
    class Delegate:
        calls = []

        def execute(self, name, arguments):
            self.calls.append((name, arguments))
            return "found"

    class Memory:
        user_id = "alice"
        memory_system_name = "prme"
        calls = []

        def _post(self, path, payload):
            self.calls.append((path, payload))
            return {
                "response": {
                    "arguments": {"city": "Salt Lake City"},
                    "replacements": [{
                        "json_pointer": "/city",
                        "presentation": "Salt Lake City(Utah)",
                        "lookup": "Salt Lake City",
                    }],
                    "binding_uses": [{
                        "json_pointer": "/city",
                        "operation": "replaced",
                        "kind": "city",
                        "presentation": "Salt Lake City(Utah)",
                        "lookup": "Salt Lake City",
                    }],
                }
            }

    delegate = Delegate()
    memory = Memory()
    executor = runner._ResolvingToolExecutor(delegate, memory)
    arguments = {"city": "Salt Lake City(Utah)"}

    executor.start_turn()
    result = executor.execute("RestaurantSearch", arguments)
    assert result.startswith("found\n\n<prme_value_presentations>")
    assert '"presentation":"Salt Lake City(Utah)"' in result
    assert '"lookup"' not in result
    assert arguments == {"city": "Salt Lake City(Utah)"}
    assert delegate.calls == [("RestaurantSearch", {"city": "Salt Lake City"})]
    assert memory.calls[0][0] == "/memory/resolve_tool_arguments"
    records = executor.drain()
    assert records[0]["original_arguments"] == arguments
    assert records[0]["resolved_arguments"] == {"city": "Salt Lake City"}
    assert records[0]["replacements"][0]["json_pointer"] == "/city"
    assert records[0]["binding_uses"][0]["operation"] == "replaced"
    assert records[0]["result_presentation_guidance_enabled"] is True
    assert records[0]["result_presentation_guidance"]["values"] == [{
        "kind": "city",
        "presentation": "Salt Lake City(Utah)",
    }]
    assert executor.drain() == []

    control = runner._ResolvingToolExecutor(
        delegate, memory, annotate_results=False
    )
    control.start_turn()
    assert control.execute("RestaurantSearch", arguments) == "found"
    control_record = control.drain()[0]
    assert control_record["binding_uses"][0]["operation"] == "replaced"
    assert control_record["result_presentation_guidance_enabled"] is False
    assert control_record["result_presentation_guidance"] is None


def test_tool_executor_does_not_annotate_unmatched_result():
    class Delegate:
        def execute(self, _name, _arguments):
            return "found"

    class Memory:
        user_id = "alice"
        memory_system_name = "prme"

        def _post(self, _path, payload):
            return {
                "response": {
                    "arguments": payload["arguments"],
                    "replacements": [],
                    "binding_uses": [],
                }
            }

    executor = runner._ResolvingToolExecutor(Delegate(), Memory())
    executor.start_turn()
    assert executor.execute("RestaurantSearch", {"city": "Boise"}) == "found"
    assert executor.drain()[0]["result_presentation_guidance"] is None


def test_native_response_parsing_preserves_parallel_tool_calls():
    client = runner.OllamaNativeTravelClient.__new__(runner.OllamaNativeTravelClient)
    client.accepted_models = {"deepseek"}
    client.total_input_tokens = 0
    client.total_output_tokens = 0
    client.turn = 4
    response = client._parse({
        "model": "deepseek",
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 100,
        "eval_count": 20,
        "message": {
            "content": "searching",
            "tool_calls": [
                {"function": {"name": "FlightSearch", "arguments": {"date": "2022-01-01"}}},
                {"function": {"name": "CitySearch", "arguments": {"state": "Texas"}}},
            ],
        },
    })

    assert response.content == "searching"
    assert [(call.id, call.name) for call in response.tool_calls] == [
        ("native_4_0", "FlightSearch"), ("native_4_1", "CitySearch")
    ]
    assert client.get_usage_stats() == {
        "total_input_tokens": 100,
        "total_output_tokens": 20,
        "total_cost": 0.0,
    }


def test_native_client_recovers_bounded_truncation_with_complete_answer():
    class Response:
        def __init__(self, value):
            self.value = value

        def raise_for_status(self):
            return None

        def json(self):
            return self.value

    class Client:
        def __init__(self, values):
            self.values = iter(values)
            self.bodies = []

        def post(self, _path, *, json):
            self.bodies.append(json)
            return Response(next(self.values))

    truncated = {
        "model": "deepseek",
        "done": True,
        "done_reason": "length",
        "prompt_eval_count": 100,
        "eval_count": 8192,
        "message": {"content": "long analysis"},
    }
    completed = {
        "model": "deepseek",
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 150,
        "eval_count": 25,
        "message": {"content": "=== Ada's Plan ===\nDay 1: complete"},
    }
    client = runner.OllamaNativeTravelClient.__new__(runner.OllamaNativeTravelClient)
    client.model_name = "deepseek"
    client.accepted_models = {"deepseek"}
    client.options = {"temperature": 0, "seed": 17}
    client.think = False
    client.attempts = 1
    client.max_output_tokens = 8192
    client.truncation_recovery_attempts = 1
    client.truncation_recovery_instruction = "Return only the complete final answer."
    client.client = Client([truncated, completed])
    client.total_input_tokens = 0
    client.total_output_tokens = 0
    client.turn = 0

    response = client.chat_with_tools(
        [{"role": "user", "content": "Plan"}], [], max_tokens=32768
    )

    assert response.content == "=== Ada's Plan ===\nDay 1: complete"
    assert response.raw_response["recovered_from_truncation"] is True
    assert client.total_input_tokens == 250
    assert client.total_output_tokens == 8217
    assert client.client.bodies[0]["options"]["num_predict"] == 8192
    assert client.client.bodies[1]["tools"] is None
    assert client.client.bodies[1]["messages"][-2:] == [
        {"role": "assistant", "content": "long analysis"},
        {"role": "user", "content": "Return only the complete final answer."},
    ]


def test_resume_restores_native_history_and_replays_prme_trace():
    class Agent:
        all_queries = ["Base: query"]
        accumulated_plans = "base plan"

    class Memory:
        entries = []

        def add(self, value):
            self.entries.append(value)

    question = {"name": "Ada", "query": "new constraints"}
    prior = {
        "person": {
            "name": "Ada",
            "query": "new constraints",
            "result": "analysis\n=== Ada's Plan ===\nsaved plan",
        },
        "memory_entry": "saved raw trace",
    }
    agent = Agent()
    memory = Memory()

    runner._restore_actor_state(agent, memory, question, prior)

    assert agent.all_queries == ["Base: query", "Ada: new constraints"]
    assert agent.accumulated_plans == (
        "base plan\n\n=== Ada's Plan ===\nsaved plan"
    )
    assert memory.entries == ["saved raw trace"]


def test_resume_rejects_changed_traveler_or_missing_prme_trace():
    class Agent:
        all_queries = []
        accumulated_plans = ""

    question = {"name": "Ada", "query": "registered"}
    with pytest.raises(RuntimeError, match="saved traveler differs"):
        runner._restore_actor_state(
            Agent(), None, question,
            {"person": {"name": "Ada", "query": "changed", "result": "plan"}},
        )
    with pytest.raises(RuntimeError, match="lacks its raw trace"):
        runner._restore_actor_state(
            Agent(), object(), question,
            {"person": {"name": "Ada", "query": "registered", "result": "plan"}},
        )
