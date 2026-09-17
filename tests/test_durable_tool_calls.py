from __future__ import annotations

import asyncio
import json
from pathlib import Path
import stat

import pytest

from benchmarks.diagnostics import durable_tool_calls as subject


TOOL = {
    "type": "function",
    "function": {
        "name": "submit",
        "description": "Submit a value",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    },
}


def _job() -> dict:
    return {"id": "case-1", "messages": [{"role": "user", "content": "value"}]}


def _response(value: int) -> dict:
    return {
        "model": "remote",
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 10,
        "eval_count": 2,
        "message": {
            "content": "",
            "tool_calls": [
                {"function": {"name": "submit", "arguments": {"value": value}}}
            ],
        },
    }


async def _run(path: Path, caller, *, validator=None, max_transport_attempts=2):
    return await subject.run(
        jobs=[_job()],
        state_path=path,
        run_identity={"trial": "test"},
        model="alias",
        accepted_models=frozenset({"alias", "remote"}),
        tool=TOOL,
        options={"temperature": 0},
        timeout_seconds=1,
        max_transport_attempts=max_transport_attempts,
        max_semantic_attempts=2,
        concurrency=1,
        caller=caller,
        validator=validator or (lambda _job_id, args: (args["value"] == 7, ["wrong"])),
        repair_message=lambda errors: "repair " + ",".join(errors),
    )


@pytest.mark.asyncio
async def test_transport_retry_is_separate_from_semantic_attempt(
    tmp_path: Path,
) -> None:
    calls = 0

    async def caller(_body):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError
        return _response(7)

    state_path = tmp_path / "state.json"
    state = await _run(state_path, caller)

    saved = state["jobs"]["case-1"]
    assert state["complete"] is True
    assert saved["status"] == "complete"
    assert len(saved["semantic_attempts"]) == 1
    assert [
        value["status"] for value in saved["semantic_attempts"][0]["transport_attempts"]
    ] == ["failed", "succeeded"]
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_semantic_retry_includes_machine_feedback(tmp_path: Path) -> None:
    bodies = []

    async def caller(body):
        bodies.append(body)
        return _response(6 if len(bodies) == 1 else 7)

    state = await _run(tmp_path / "state.json", caller)

    attempts = state["jobs"]["case-1"]["semantic_attempts"]
    assert len(attempts) == 2
    assert attempts[0]["validation_errors"] == ["wrong"]
    assert bodies[1]["messages"][-1] == {"role": "user", "content": "repair wrong"}
    assert bodies[1]["messages"][-2]["role"] == "assistant"


@pytest.mark.asyncio
async def test_cancelled_attempt_resumes_with_frozen_identity(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    entered = asyncio.Event()

    async def blocked(_body):
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(_run(state_path, blocked))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async def succeeds(_body):
        return _response(7)

    state = await _run(state_path, succeeds)
    transports = state["jobs"]["case-1"]["semantic_attempts"][0]["transport_attempts"]
    assert [value["status"] for value in transports] == ["cancelled", "succeeded"]
    assert state["complete"] is True


@pytest.mark.asyncio
async def test_resume_rejects_changed_identity_and_tampering(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"

    async def succeeds(_body):
        return _response(7)

    await _run(state_path, succeeds)
    value = json.loads(state_path.read_text())
    value["jobs"]["case-1"]["semantic_attempts"][0]["response"]["done"] = False
    state_path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="checksum"):
        await _run(state_path, succeeds)

    state_path.unlink()
    await _run(state_path, succeeds)
    changed = dict(_job())
    changed["messages"] = [{"role": "user", "content": "changed"}]
    with pytest.raises(ValueError, match="identity differs"):
        await subject.run(
            jobs=[changed],
            state_path=state_path,
            run_identity={"trial": "test"},
            model="alias",
            accepted_models=frozenset({"alias", "remote"}),
            tool=TOOL,
            options={"temperature": 0},
            timeout_seconds=1,
            max_transport_attempts=2,
            max_semantic_attempts=2,
            concurrency=1,
            caller=succeeds,
            validator=lambda _job_id, args: (args["value"] == 7, ["wrong"]),
            repair_message=lambda errors: "repair " + ",".join(errors),
        )


@pytest.mark.asyncio
async def test_resume_validates_a_saved_response_without_calling_provider(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.json"

    async def succeeds(_body):
        return _response(7)

    await _run(state_path, succeeds)
    value = json.loads(state_path.read_text())
    saved = value["jobs"]["case-1"]
    semantic = saved["semantic_attempts"][0]
    semantic.pop("arguments")
    semantic.pop("validation_errors")
    saved["status"] = "running"
    value["complete"] = False
    state_path.write_text(json.dumps(value))

    async def must_not_run(_body):
        raise AssertionError("saved response should be validated after restart")

    resumed = await _run(state_path, must_not_run)

    assert resumed["complete"] is True
    assert resumed["jobs"]["case-1"]["status"] == "complete"


@pytest.mark.asyncio
async def test_transport_exhaustion_is_terminal_for_frozen_limits(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.json"
    calls = 0

    async def times_out(_body):
        nonlocal calls
        calls += 1
        raise TimeoutError

    state = await _run(state_path, times_out, max_transport_attempts=1)
    assert state["jobs"]["case-1"]["status"] == "transport_exhausted"

    await _run(state_path, times_out, max_transport_attempts=1)
    assert calls == 1


@pytest.mark.asyncio
async def test_each_job_can_bind_a_distinct_schema_for_the_same_tool(
    tmp_path: Path,
) -> None:
    observed = []
    second_tool = json.loads(json.dumps(TOOL))
    second_tool["function"]["parameters"]["properties"] = {"other": {"type": "integer"}}
    second_tool["function"]["parameters"]["required"] = ["other"]
    jobs = [
        _job(),
        {
            "id": "case-2",
            "messages": [{"role": "user", "content": "other"}],
            "tool": second_tool,
        },
    ]

    async def caller(body):
        observed.append(body["tools"][0])
        key = next(iter(body["tools"][0]["function"]["parameters"]["properties"]))
        response = _response(7)
        response["message"]["tool_calls"][0]["function"]["arguments"] = {key: 7}
        return response

    state = await subject.run(
        jobs=jobs,
        state_path=tmp_path / "state.json",
        run_identity={"trial": "per-job-schema"},
        model="alias",
        accepted_models=frozenset({"alias", "remote"}),
        tool=TOOL,
        options={"temperature": 0},
        timeout_seconds=1,
        max_transport_attempts=1,
        max_semantic_attempts=1,
        concurrency=1,
        caller=caller,
        validator=lambda _job_id, _args: (True, []),
        repair_message=lambda errors: ",".join(errors),
    )

    assert state["complete"] is True
    assert observed == [TOOL, second_tool]
