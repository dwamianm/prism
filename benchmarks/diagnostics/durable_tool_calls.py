"""Durable, restart-safe execution for benchmark tool calls.

This module is benchmark infrastructure, not a production provider abstraction.
It checkpoints every transport attempt before and after I/O, keeps transport
failures separate from semantic validation failures, and refuses to resume when
the frozen job or provider identity changes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
from typing import Any


JsonObject = dict[str, Any]
ResponseCaller = Callable[[JsonObject], Awaitable[JsonObject]]
ArgumentValidator = Callable[[JsonObject], tuple[bool, Sequence[str]]]
RepairMessage = Callable[[Sequence[str]], str]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _write_private(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(_canonical(value) + b"\n")
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


@contextmanager
def _exclusive_state(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a") as lock:
        lock_path.chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def _request_body(
    *,
    model: str,
    messages: list[JsonObject],
    tool: JsonObject,
    options: JsonObject,
) -> JsonObject:
    return {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": options,
        "tools": [tool],
    }


def _job_tool(job: JsonObject, default: JsonObject, tool_name: str) -> JsonObject:
    value = job.get("tool", default)
    function = value.get("function") if isinstance(value, dict) else None
    if (
        not isinstance(function, dict)
        or function.get("name") != tool_name
        or not isinstance(function.get("parameters"), dict)
    ):
        raise ValueError("job tool must preserve the declared function identity")
    return value


def _tool_arguments(
    response: JsonObject,
    *,
    tool_name: str,
    accepted_models: frozenset[str],
) -> JsonObject:
    if (
        response.get("model") not in accepted_models
        or response.get("done") is not True
        or response.get("done_reason") != "stop"
    ):
        raise ValueError("provider response is incomplete or from another model")
    message = response.get("message")
    if not isinstance(message, dict):
        raise ValueError("provider response has no message")
    for field in ("prompt_eval_count", "eval_count"):
        if type(response.get(field)) is not int or response[field] < 0:
            raise ValueError("provider response has invalid token observations")
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        raise ValueError("provider must make exactly one tool call")
    function = calls[0].get("function") if isinstance(calls[0], dict) else None
    if not isinstance(function, dict) or function.get("name") != tool_name:
        raise ValueError("provider called an unexpected tool")
    arguments = function.get("arguments")
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be a JSON object")
    return arguments


def _validate_saved_state(state: JsonObject, expected_identity: JsonObject) -> None:
    if state.get("schema_version") != 1 or state.get("identity") != expected_identity:
        raise ValueError("durable tool-call state identity differs")
    jobs = state.get("jobs")
    if not isinstance(jobs, dict):
        raise ValueError("durable tool-call state has no jobs")
    expected_jobs = expected_identity["jobs"]
    if set(jobs) != set(expected_jobs):
        raise ValueError("durable tool-call state job set differs")
    for job_id, saved in jobs.items():
        if (
            not isinstance(saved, dict)
            or saved.get("request_sha256") != expected_jobs[job_id]
            or not isinstance(saved.get("semantic_attempts"), list)
        ):
            raise ValueError("durable tool-call job identity differs")
        for semantic in saved["semantic_attempts"]:
            if not isinstance(semantic, dict):
                raise ValueError("durable semantic attempt is invalid")
            response = semantic.get("response")
            if response is not None and semantic.get("response_sha256") != _sha256(
                response
            ):
                raise ValueError("saved provider response checksum differs")
            transports = semantic.get("transport_attempts")
            if not isinstance(transports, list):
                raise ValueError("durable transport attempt list is invalid")
            for transport in transports:
                if not isinstance(transport, dict) or transport.get("status") not in {
                    "cancelled",
                    "failed",
                    "inflight",
                    "interrupted",
                    "succeeded",
                }:
                    raise ValueError("durable transport attempt is invalid")


def _assistant_message(response: JsonObject) -> JsonObject:
    message = response.get("message")
    if not isinstance(message, dict):
        raise ValueError("saved response has no assistant message")
    retained = {
        key: message[key]
        for key in ("content", "thinking", "tool_calls")
        if key in message
    }
    return {"role": "assistant", **retained}


async def run(
    *,
    jobs: Sequence[JsonObject],
    state_path: Path,
    run_identity: JsonObject,
    model: str,
    accepted_models: frozenset[str],
    tool: JsonObject,
    options: JsonObject,
    timeout_seconds: float,
    max_transport_attempts: int,
    max_semantic_attempts: int,
    concurrency: int,
    caller: ResponseCaller,
    validator: Callable[[str, JsonObject], tuple[bool, Sequence[str]]],
    repair_message: RepairMessage,
) -> JsonObject:
    """Run frozen tool-call jobs with durable per-attempt checkpoints."""
    if (
        not jobs
        or timeout_seconds <= 0
        or max_transport_attempts < 1
        or max_semantic_attempts < 1
        or concurrency < 1
    ):
        raise ValueError("durable tool-call limits and jobs must be positive")
    tool_function = tool.get("function")
    tool_name = tool_function.get("name") if isinstance(tool_function, dict) else None
    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError("durable tool call requires one named function")
    job_ids = [job.get("id") for job in jobs]
    if any(not isinstance(value, str) or not value for value in job_ids) or len(
        set(job_ids)
    ) != len(job_ids):
        raise ValueError("durable tool-call job identities are invalid")
    job_tools = {job["id"]: _job_tool(job, tool, tool_name) for job in jobs}
    initial_bodies = {
        job["id"]: _request_body(
            model=model,
            messages=list(job["messages"]),
            tool=job_tools[job["id"]],
            options=options,
        )
        for job in jobs
    }
    identity = {
        "run": run_identity,
        "model": model,
        "accepted_models": sorted(accepted_models),
        "tool_sha256": _sha256(tool),
        "options": options,
        "timeout_seconds": timeout_seconds,
        "max_transport_attempts": max_transport_attempts,
        "max_semantic_attempts": max_semantic_attempts,
        "job_tool_sha256": {
            job_id: _sha256(job_tools[job_id]) for job_id in sorted(job_ids)
        },
        "jobs": {job_id: _sha256(initial_bodies[job_id]) for job_id in sorted(job_ids)},
    }
    jobs_by_id = {job["id"]: job for job in jobs}

    with _exclusive_state(state_path):
        if state_path.exists():
            state = json.loads(state_path.read_text())
            _validate_saved_state(state, identity)
        else:
            state = {
                "schema_version": 1,
                "identity": identity,
                "started_at": _now(),
                "jobs": {
                    job_id: {
                        "request_sha256": identity["jobs"][job_id],
                        "status": "pending",
                        "semantic_attempts": [],
                    }
                    for job_id in sorted(job_ids)
                },
                "complete": False,
            }
        for saved in state["jobs"].values():
            for semantic in saved["semantic_attempts"]:
                for transport in semantic["transport_attempts"]:
                    if transport["status"] == "inflight":
                        transport["status"] = "interrupted"
                        transport["finished_at"] = _now()
                        transport["error_type"] = "ProcessInterrupted"
            if saved["status"] == "running":
                saved["status"] = "pending"
            if saved["semantic_attempts"]:
                last = saved["semantic_attempts"][-1]
                if last.get("validation_errors") == [] and isinstance(
                    last.get("arguments"), dict
                ):
                    saved["status"] = "complete"
        _write_private(state_path, state)

        state_lock = asyncio.Lock()
        semaphore = asyncio.Semaphore(concurrency)

        async def persist() -> None:
            async with state_lock:
                _write_private(state_path, state)

        async def execute(job_id: str) -> None:
            saved = state["jobs"][job_id]
            if saved["status"] in {
                "complete",
                "semantic_exhausted",
                "transport_exhausted",
            }:
                return
            async with semaphore:
                saved["status"] = "running"
                await persist()
                messages = list(jobs_by_id[job_id]["messages"])
                for prior in saved["semantic_attempts"]:
                    response = prior.get("response")
                    errors = prior.get("validation_errors")
                    if response is not None and errors:
                        messages.extend(
                            [
                                _assistant_message(response),
                                {
                                    "role": "user",
                                    "content": repair_message(errors),
                                },
                            ]
                        )
                semantic: JsonObject | None = None
                response: JsonObject | None = None
                if saved["semantic_attempts"]:
                    last = saved["semantic_attempts"][-1]
                    if "validation_errors" not in last:
                        semantic = last
                        response = last.get("response")
                while True:
                    if semantic is None:
                        if len(saved["semantic_attempts"]) >= max_semantic_attempts:
                            break
                        body = _request_body(
                            model=model,
                            messages=messages,
                            tool=job_tools[job_id],
                            options=options,
                        )
                        semantic = {
                            "index": len(saved["semantic_attempts"]) + 1,
                            "request_sha256": _sha256(body),
                            "transport_attempts": [],
                        }
                        saved["semantic_attempts"].append(semantic)
                        response = None
                    else:
                        body = _request_body(
                            model=model,
                            messages=messages,
                            tool=job_tools[job_id],
                            options=options,
                        )
                        if semantic["request_sha256"] != _sha256(body):
                            raise ValueError(
                                "resumed semantic request identity differs"
                            )
                    while (
                        response is None
                        and len(semantic["transport_attempts"]) < max_transport_attempts
                    ):
                        transport = {
                            "index": len(semantic["transport_attempts"]) + 1,
                            "status": "inflight",
                            "started_at": _now(),
                        }
                        semantic["transport_attempts"].append(transport)
                        await persist()
                        try:
                            response = await asyncio.wait_for(
                                caller(body), timeout=timeout_seconds
                            )
                        except asyncio.CancelledError:
                            transport.update(
                                {
                                    "status": "cancelled",
                                    "finished_at": _now(),
                                    "error_type": "CancelledError",
                                }
                            )
                            saved["status"] = "pending"
                            await persist()
                            raise
                        except Exception as error:
                            transport.update(
                                {
                                    "status": "failed",
                                    "finished_at": _now(),
                                    "error_type": type(error).__name__,
                                }
                            )
                            await persist()
                            continue
                        transport.update({"status": "succeeded", "finished_at": _now()})
                        semantic["response"] = response
                        semantic["response_sha256"] = _sha256(response)
                        await persist()
                        break
                    if response is None:
                        semantic["validation_errors"] = ["transport_exhausted"]
                        saved["status"] = "transport_exhausted"
                        await persist()
                        return
                    try:
                        arguments = _tool_arguments(
                            response,
                            tool_name=tool_name,
                            accepted_models=accepted_models,
                        )
                        valid, observed_errors = validator(job_id, arguments)
                        errors = sorted(set(str(value) for value in observed_errors))
                        if not valid and not errors:
                            errors = ["semantic_validation_failed"]
                    except Exception as error:
                        arguments = None
                        valid = False
                        errors = [f"tool_response_invalid:{type(error).__name__}"]
                    semantic["arguments"] = arguments
                    semantic["validation_errors"] = errors
                    await persist()
                    if valid:
                        saved["status"] = "complete"
                        await persist()
                        return
                    messages.extend(
                        [
                            _assistant_message(response),
                            {"role": "user", "content": repair_message(errors)},
                        ]
                    )
                    semantic = None
                    response = None
                saved["status"] = "semantic_exhausted"
                await persist()

        await asyncio.gather(*(execute(job_id) for job_id in job_ids))
        state["complete"] = all(
            value["status"] == "complete" for value in state["jobs"].values()
        )
        state["finished_at"] = _now()
        _write_private(state_path, state)
        return state
