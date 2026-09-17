"""Preregister and run a paired MemoryArena travel development trial.

The runner keeps the pinned upstream agent, prompts, tools, databases and native
evaluator. It adds exact cohort coverage, strict full-string scoring, durable
group checkpoints and a counterbalanced PRME versus native full-history trial.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from benchmarks.diagnostics.memoryarena_travel_audit import (
    score_strict,
    validate_coverage,
)
from benchmarks.integrations.run_longmemeval_v2 import _ollama_reader_identity


UPSTREAM_REVISION = "6cd9de14b71915e39ac742a20dc33785e14b6aab"
DATASET_NAME = "ZexueHe/memoryarena"
DATASET_CONFIG = "group_travel_planner"
DATASET_SPLIT = "test"
REGISTRATION_KIND = "memoryarena-travel-paired-registration"
RESULT_KIND = "memoryarena-travel-paired-result"
SCHEMA_VERSION = 1
ARMS = ("native_full_history", "prme")
FLIGHT_RELATIVE_PATH = (
    "env/env_systems/travel_planner_env/database/flights/clean_Flights_2022.csv"
)
UPSTREAM_SOURCE_FILES = (
    "run_travel.py",
    "agent/travel_planner.py",
    "env/env_systems/travel_env.py",
    "env/env_systems/travel_planner_env/clients/openai_client.py",
    "env/env_systems/travel_planner_env/combination.py",
    "env/env_systems/travel_planner_env/eval.py",
    "env/env_systems/travel_planner_env/prompts.py",
    "env/env_systems/travel_planner_env/tool_executor.py",
    "env/env_systems/travel_planner_env/tool_schemas.py",
)
DATABASE_FILES = (
    FLIGHT_RELATIVE_PATH,
    "env/env_systems/travel_planner_env/database/restaurants/clean_restaurant_2022.csv",
    "env/env_systems/travel_planner_env/database/accommodations/clean_accommodations_2022.csv",
    "env/env_systems/travel_planner_env/database/attractions/attractions.csv",
    "env/env_systems/travel_planner_env/database/googleDistanceMatrix/distance.csv",
    "env/env_systems/travel_planner_env/database/background/citySet.txt",
    "env/env_systems/travel_planner_env/database/background/citySet_with_states.txt",
    "env/env_systems/travel_planner_env/database/background/stateSet.txt",
)


@dataclass
class _NativeToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class _NativeResponse:
    content: str | None
    tool_calls: list[_NativeToolCall] | None
    raw_response: dict[str, Any]


class OllamaNativeTravelClient:
    """MemoryArena model-client contract over Ollama's native chat endpoint."""

    def __init__(self, actor: dict[str, Any]):
        import httpx

        self.model_name = actor["model"]
        runtime = actor["runtime_identity"]
        self.accepted_models = {
            value
            for value in (self.model_name, runtime.get("remote_model"))
            if isinstance(value, str) and value
        }
        self.options = dict(actor["native_options"])
        self.think = actor["think"]
        self.attempts = actor["transport_attempts"]
        self.client = httpx.Client(
            base_url=runtime["api_base_url"],
            timeout=actor["request_timeout_seconds"],
        )
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.turn = 0

    def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: int = 32768,
    ) -> _NativeResponse:
        if temperature != self.options["temperature"]:
            raise RuntimeError("actor temperature differs from registration")
        options = {**self.options, "num_predict": max_tokens}
        body = {
            "model": self.model_name,
            "messages": messages,
            "stream": False,
            "think": self.think,
            "options": options,
            "tools": tools or None,
        }
        last_error = None
        for _attempt in range(self.attempts):
            try:
                response = self.client.post("/api/chat", json=body)
                response.raise_for_status()
                value = response.json()
                return self._parse(value)
            except Exception as error:  # transport/schema failures are bounded alike
                last_error = error
        raise RuntimeError("Ollama native actor attempts exhausted") from last_error

    def _parse(self, value: Any) -> _NativeResponse:
        if (
            not isinstance(value, dict)
            or value.get("model") not in self.accepted_models
            or value.get("done") is not True
            or value.get("done_reason") != "stop"
        ):
            raise ValueError("Ollama returned an incomplete or foreign response")
        message = value.get("message")
        if not isinstance(message, dict):
            raise ValueError("Ollama response has no assistant message")
        prompt_tokens = value.get("prompt_eval_count")
        output_tokens = value.get("eval_count")
        if (
            type(prompt_tokens) is not int
            or prompt_tokens < 0
            or type(output_tokens) is not int
            or output_tokens < 0
        ):
            raise ValueError("Ollama response has invalid token observations")
        calls = message.get("tool_calls")
        parsed_calls = None
        if calls:
            if not isinstance(calls, list):
                raise ValueError("Ollama tool calls must be a list")
            parsed_calls = []
            for index, call in enumerate(calls):
                function = call.get("function") if isinstance(call, dict) else None
                if not isinstance(function, dict):
                    raise ValueError("Ollama tool call lacks a function")
                name = function.get("name")
                arguments = function.get("arguments")
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    raise ValueError("Ollama tool call has invalid arguments")
                parsed_calls.append(_NativeToolCall(
                    id=f"native_{self.turn}_{index}",
                    name=name,
                    arguments=arguments,
                ))
        self.turn += 1
        self.total_input_tokens += prompt_tokens
        self.total_output_tokens += output_tokens
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise ValueError("Ollama assistant content must be text or null")
        return _NativeResponse(content, parsed_calls, value)

    def format_assistant_tool_calls(
        self, tool_calls: list[_NativeToolCall]
    ) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": call.name, "arguments": call.arguments}}
                for call in tool_calls
            ],
        }

    def format_tool_result(
        self, tool_call_id: str, result: str, name: str | None = None
    ) -> dict[str, Any]:
        del tool_call_id
        if not name:
            raise ValueError("Ollama native tool results require a tool name")
        return {"role": "tool", "tool_name": name, "content": str(result)}

    def get_usage_stats(self) -> dict[str, Any]:
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost": 0.0,
        }


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode()


def _sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _identity(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"required file is unavailable: {path}")
    return {"bytes": path.stat().st_size, "sha256": _sha256(path)}


def _git(root: Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(f"could not inspect Git checkout at {root}") from error


def _check_upstream(upstream: Path) -> None:
    if _git(upstream, "rev-parse", "HEAD") != UPSTREAM_REVISION:
        raise RuntimeError("MemoryArena checkout is not at the pinned revision")
    tracked = _git(upstream, "diff", "HEAD", "--name-only", "--").splitlines()
    untracked = _git(
        upstream, "ls-files", "--others", "--exclude-standard"
    ).splitlines()
    if tracked or sorted(untracked) not in ([], [FLIGHT_RELATIVE_PATH]):
        raise RuntimeError("MemoryArena checkout has unexpected changes")


def _check_prme_clean(root: Path) -> str:
    revision = _git(root, "rev-parse", "HEAD")
    if _git(root, "status", "--porcelain"):
        raise RuntimeError("registration and execution require a clean PRME worktree")
    return revision


def _source_files(root: Path, upstream: Path) -> dict[str, dict[str, Any]]:
    local = {
        "runner": Path(__file__).resolve(),
        "adapter": root / "benchmarks/diagnostics/memoryarena_server.py",
        "strict_scorer": root / "benchmarks/diagnostics/memoryarena_travel_audit.py",
    }
    values = {name: _identity(path) for name, path in local.items()}
    values.update(
        {
            f"upstream:{relative}": _identity(upstream / relative)
            for relative in UPSTREAM_SOURCE_FILES
        }
    )
    return values


def _database_identity(upstream: Path) -> dict[str, dict[str, Any]]:
    return {relative: _identity(upstream / relative) for relative in DATABASE_FILES}


def _load_dataset() -> tuple[Any, dict[str, Any]]:
    try:
        import datasets
        from datasets import load_dataset
    except ImportError as error:
        raise RuntimeError("install the MemoryArena dataset dependencies") from error
    dataset = load_dataset(DATASET_NAME, DATASET_CONFIG, split=DATASET_SPLIT)
    cache_files = []
    for item in dataset.cache_files:
        path = Path(item["filename"])
        cache_files.append({"name": path.name, **_identity(path)})
    identity = {
        "name": DATASET_NAME,
        "config": DATASET_CONFIG,
        "split": DATASET_SPLIT,
        "datasets_version": datasets.__version__,
        "fingerprint": dataset._fingerprint,
        "rows": len(dataset),
        "cache_files": cache_files,
        "content_sha256": hashlib.sha256(
            _canonical([dict(row) for row in dataset])
        ).hexdigest(),
    }
    return dataset, identity


def select_cohort(
    rows: list[dict[str, Any]],
    *,
    seed: int,
    groups_per_stratum: int,
    excluded_ids: set[int],
) -> list[dict[str, int]]:
    if groups_per_stratum <= 0:
        raise ValueError("groups_per_stratum must be positive")
    strata: dict[int, list[int]] = {}
    for row in rows:
        group_id = row["id"]
        if group_id not in excluded_ids:
            strata.setdefault(len(row["questions"]), []).append(group_id)
    selected = []
    generator = random.Random(seed)
    for count in sorted(strata):
        identities = sorted(strata[count])
        if len(identities) < groups_per_stratum:
            raise ValueError(f"stratum {count} has insufficient groups")
        for group_id in generator.sample(identities, groups_per_stratum):
            selected.append({"id": group_id, "person_count": count})
    generator.shuffle(selected)
    return selected


def _converted_rows(dataset: Any, upstream: Path) -> list[dict[str, Any]]:
    sys.path.insert(0, str(upstream))
    try:
        from env.env_systems.travel_planner_env.data_loader import _convert_row

        return [_convert_row(row) for row in dataset]
    finally:
        sys.path.pop(0)


def register(
    *,
    root: Path,
    upstream: Path,
    output: Path,
    ollama_origin: str,
    model: str,
    seed: int,
    groups_per_stratum: int,
    excluded_group_ids: set[int],
) -> dict[str, Any]:
    if output.exists():
        raise RuntimeError("registration output already exists")
    _check_upstream(upstream)
    revision = _check_prme_clean(root)
    dataset, dataset_identity = _load_dataset()
    raw_rows = [dict(row) for row in dataset]
    selected = select_cohort(
        raw_rows,
        seed=seed,
        groups_per_stratum=groups_per_stratum,
        excluded_ids=excluded_group_ids,
    )
    selected_ids = {item["id"] for item in selected}
    cohort_rows = [row for row in raw_rows if row["id"] in selected_ids]
    runtime = _ollama_reader_identity(ollama_origin, model)
    registration = {
        "schema_version": SCHEMA_VERSION,
        "kind": REGISTRATION_KIND,
        "status": "preregistered",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "prme_revision": revision,
            "upstream_revision": UPSTREAM_REVISION,
            "files": _source_files(root, upstream),
        },
        "dataset": dataset_identity,
        "databases": _database_identity(upstream),
        "cohort": {
            "selection": "seeded stratified sample by number of travelers",
            "seed": seed,
            "groups_per_stratum": groups_per_stratum,
            "excluded_group_ids": sorted(excluded_group_ids),
            "groups": selected,
            "group_count": len(selected),
            "person_count": sum(item["person_count"] for item in selected),
            "content_sha256": hashlib.sha256(_canonical(cohort_rows)).hexdigest(),
        },
        "actor": {
            "runtime_identity": runtime,
            "constructor_openai_base_url": ollama_origin.rstrip("/") + "/v1",
            "transport": "ollama_native_chat",
            "model": model,
            "temperature": 0,
            "max_steps": 30,
            "tool_choice": "auto",
            "think": False,
            "native_options": {"temperature": 0, "seed": 17},
            "request_timeout_seconds": 180,
            "transport_attempts": 2,
            "remote_weights_pinned": False,
        },
        "protocol": {
            "arms": list(ARMS),
            "arm_order": "counterbalanced within each registered group",
            "judgement_mode": "none",
            "use_step_memory": False,
            "prme_memory_tokens": 4096,
            "native_full_history": (
                "Pinned upstream memory_system=none behavior: base plan and all "
                "prior generated plans remain in the agent history."
            ),
            "prme": (
                "Pinned upstream three-endpoint client with the registered PRME "
                "source-preserving trace-projection adapter; exact raw traces remain "
                "events while traveler final plans are retrieved per query."
            ),
            "prme_retrieval_projection": "traveler_final_plan_v2",
            "prme_query_projection": (
                "base traveler plus named plan dependencies outside the roster preamble"
            ),
            "checkpoint_unit": "one completed traveler within each group arm",
            "resume_policy": (
                "Rebuild the pinned actor history from saved actions and replay "
                "saved raw trace entries into a fresh PRME pack before continuing."
            ),
        },
        "evaluation": {
            "primary": "strict full-normalized-string PS, SPS and SR",
            "secondary": "unchanged upstream native PS, SPS and SR",
            "coverage": "exact registered group/person coverage in every arm",
            "decision_rules": {
                "complete_execution": True,
                "minimum_prme_minus_native_strict_ps_points": -5.0,
                "minimum_prme_minus_native_strict_sps_points": -5.0,
            },
            "limits": [
                "Development cohort; it does not establish universal superiority.",
                "The cloud route pins a local manifest and remote alias, not remote weights.",
                "Strict string scoring is not a semantic itinerary-validity judge.",
                "A single generation per arm does not estimate model variance.",
            ],
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(_canonical(registration) + b"\n")
    return registration


def _verify_registration(
    registration_path: Path,
    *,
    root: Path,
    upstream: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    registration = json.loads(registration_path.read_bytes())
    if (
        registration.get("schema_version") != SCHEMA_VERSION
        or registration.get("kind") != REGISTRATION_KIND
        or registration.get("status") != "preregistered"
    ):
        raise RuntimeError("invalid MemoryArena travel registration")
    _check_upstream(upstream)
    current_revision = _check_prme_clean(root)
    registered_revision = registration["source"]["prme_revision"]
    if subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", registered_revision, current_revision]
    ).returncode:
        raise RuntimeError("registered PRME revision is not an ancestor of HEAD")
    if _source_files(root, upstream) != registration["source"]["files"]:
        raise RuntimeError("registered source files changed")
    if _database_identity(upstream) != registration["databases"]:
        raise RuntimeError("registered databases changed")
    dataset, identity = _load_dataset()
    if identity != registration["dataset"]:
        raise RuntimeError("registered dataset changed")
    raw_rows = [dict(row) for row in dataset]
    groups = registration["cohort"]["groups"]
    selected_ids = [item["id"] for item in groups]
    selected = [row for identity in selected_ids for row in raw_rows if row["id"] == identity]
    if len(selected) != len(selected_ids):
        raise RuntimeError("registered cohort is unavailable")
    if hashlib.sha256(_canonical(selected)).hexdigest() != registration["cohort"]["content_sha256"]:
        # Registration hashes source-order rows; keep that order stable here.
        source_order = [row for row in raw_rows if row["id"] in set(selected_ids)]
        if hashlib.sha256(_canonical(source_order)).hexdigest() != registration["cohort"]["content_sha256"]:
            raise RuntimeError("registered cohort changed")
    converted = _converted_rows(dataset, upstream)
    by_id = {row["id"]: row for row in converted}
    return registration, [by_id[identity] for identity in selected_ids]


def _repair_tail(path: Path) -> None:
    data = path.read_bytes()
    if not data or data.endswith(b"\n"):
        return
    start = data.rfind(b"\n") + 1
    try:
        json.loads(data[start:])
    except (UnicodeDecodeError, json.JSONDecodeError):
        with path.open("r+b") as handle:
            handle.truncate(start)
            handle.flush()
            os.fsync(handle.fileno())
    else:
        with path.open("ab") as handle:
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())


def _load_checkpoints(path: Path, registration_sha256: str) -> dict[tuple[str, int], dict[str, Any]]:
    if not path.exists():
        return {}
    _repair_tail(path)
    records = {}
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        value = json.loads(line)
        if value.get("registration_sha256") != registration_sha256:
            raise RuntimeError(f"checkpoint registration differs at line {number}")
        key = (value.get("arm"), value.get("group_id"))
        if key in records and records[key] != value:
            raise RuntimeError(f"conflicting checkpoint at line {number}")
        records[key] = value
    return records


def _load_person_checkpoints(
    path: Path, registration_sha256: str
) -> dict[tuple[str, int, int], dict[str, Any]]:
    if not path.exists():
        return {}
    _repair_tail(path)
    records = {}
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        value = json.loads(line)
        if value.get("registration_sha256") != registration_sha256:
            raise RuntimeError(f"person checkpoint registration differs at line {number}")
        key = (
            value.get("arm"),
            value.get("group_id"),
            value.get("person_idx"),
        )
        if key in records and records[key] != value:
            raise RuntimeError(f"conflicting person checkpoint at line {number}")
        records[key] = value
    return records


def _append_checkpoint(path: Path, value: dict[str, Any]) -> None:
    payload = _canonical(value) + b"\n"
    with path.open("ab") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _wait_for_server(port: int, process: subprocess.Popen, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/openapi.json"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("PRME benchmark adapter exited during startup")
        try:
            with urlopen(url, timeout=1) as response:  # noqa: S310
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.2)
    raise RuntimeError("timed out waiting for the PRME benchmark adapter")


def _start_server(root: Path, output: Path, port: int, memory_tokens: int):
    attempt = 1
    while (output / f"prme-pack-attempt-{attempt}").exists():
        attempt += 1
    pack = output / f"prme-pack-attempt-{attempt}"
    log = (output / f"prme-adapter-attempt-{attempt}.log").open("x")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "benchmarks.diagnostics.memoryarena_server",
            "--directory",
            str(pack),
            "--port",
            str(port),
            "--memory-tokens",
            str(memory_tokens),
        ],
        cwd=root,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_server(port, process)
    except Exception:
        process.terminate()
        process.wait(timeout=10)
        log.close()
        raise
    return process, log


def _upstream_runtime(upstream: Path):
    sys.path.insert(0, str(upstream))
    from agent.travel_planner import TravelPlannerAgent
    from env.env_systems.travel_env import TravelPlannerEnvironment
    from env.env_systems.travel_planner_env.combination import parse_plan_text
    from env.env_systems.travel_planner_env.eval import evaluate as native_evaluate
    from memory.client import MemoryClient
    from run_travel import format_person_plan

    return {
        "agent": TravelPlannerAgent,
        "environment": TravelPlannerEnvironment,
        "parse_plan": parse_plan_text,
        "native_evaluate": native_evaluate,
        "memory_client": MemoryClient,
        "format_plan": format_person_plan,
    }


def _usage_delta(after: dict[str, Any], before: dict[str, Any]) -> dict[str, Any]:
    return {
        key: after.get(key, 0) - before.get(key, 0)
        for key in ("total_input_tokens", "total_output_tokens", "total_cost")
    }


def _configure_actor_client(agent: Any, actor: dict[str, Any]) -> None:
    if actor.get("transport") != "ollama_native_chat":
        raise RuntimeError("registered actor transport is unsupported")
    agent.client = OllamaNativeTravelClient(actor)


def _restore_actor_state(
    agent: Any,
    memory: Any,
    question: dict[str, Any],
    prior: dict[str, Any],
) -> None:
    person = prior["person"]
    if person["name"] != question["name"] or person["query"] != question["query"]:
        raise RuntimeError("saved traveler differs from the registered cohort")
    agent.all_queries.append(f"{question['name']}: {question['query']}")
    agent.accumulated_plans += (
        f"\n\n{person['result']}" if agent.accumulated_plans else person["result"]
    )
    if memory is not None:
        memory_entry = prior.get("memory_entry")
        if not isinstance(memory_entry, str):
            raise RuntimeError("PRME traveler checkpoint lacks its raw trace")
        memory.add(memory_entry)


def _run_group(
    row: dict[str, Any],
    *,
    arm: str,
    runtime: dict[str, Any],
    agent: Any,
    memory_url: str,
    model: str,
    registration_sha256: str,
    person_checkpoint_path: Path,
    person_checkpoints: dict[tuple[str, int, int], dict[str, Any]],
) -> dict[str, Any]:
    started = time.perf_counter()
    environment = runtime["environment"]({"judgement_mode": "none"})
    observation = environment.reset(seed=row["id"])
    if observation.get("group_id") != row["id"]:
        raise RuntimeError("upstream environment reset selected the wrong group")
    agent.reset()
    memory = None
    memory_contexts = []
    base = observation["base_person"]
    if arm == "prme":
        memory = runtime["memory_client"](
            user_id=f"registered_{row['id']}_{model}_prme",
            memory_system_name="prme",
            base_url=memory_url,
        )
        base_plan = runtime["format_plan"](
            base["name"], base["daily_plans"]
        )
        memory.add(json.dumps({
            "name": base["name"],
            "query": base["query"],
            "is_base_person": True,
            "final_plan": base_plan,
        }, ensure_ascii=False))
    else:
        agent.set_base_person(
            base["name"],
            base["query"],
            runtime["format_plan"](base["name"], base["daily_plans"]),
        )

    truth = {item["round_idx"]: item for item in observation["answers"]}
    persons = []
    scratchpads = []
    for question in observation["questions"]:
        checkpoint_key = (arm, row["id"], question["round_idx"])
        prior = person_checkpoints.get(checkpoint_key)
        if prior is not None:
            person = prior["person"]
            answer = truth[question["round_idx"]]
            _, _, info = environment.step(
                person["result"],
                ground_truth={
                    "name": question["name"],
                    "daily_plans": answer["daily_plans"],
                    "judgement_mode": "none",
                },
                need_judge=True,
            )
            if info.get("judgement") is not None:
                raise RuntimeError("no-feedback protocol exposed a replay judgement")
            _restore_actor_state(agent, memory, question, prior)
            persons.append(person)
            memory_contexts.append(prior["memory_context"])
            scratchpads.append(prior["scratchpad"])
            continue

        person_started = time.perf_counter()
        before = agent.get_usage_stats()
        memory_context = None
        if memory is not None:
            wrapped = memory.wrap_user_prompt(question["query"])
            marker = "</memory_context>"
            if wrapped.count(marker) != 1:
                raise RuntimeError("memory adapter returned an invalid context wrapper")
            memory_context = wrapped.split(marker)[0] + marker
        memory_contexts.append({
            "round_idx": question["round_idx"],
            "context": memory_context,
        })
        agent.prepare_for_person(
            name=question["name"],
            round_idx=question["round_idx"],
            include_previous_plans=arm == "native_full_history",
            memory_context=memory_context,
            memory_system=None,
        )
        action = agent.act(question["query"])
        result = agent.last_result
        answer = truth[question["round_idx"]]
        _, reward, info = environment.step(
            action,
            ground_truth={
                "name": question["name"],
                "daily_plans": answer["daily_plans"],
                "judgement_mode": "none",
            },
            need_judge=True,
        )
        if info.get("judgement") is not None:
            raise RuntimeError("no-feedback protocol exposed a judgement")
        memory_entry = None
        if memory is not None:
            memory_entry = agent.build_memory_entry(
                task=question["query"],
                action=action,
                observation={"judgement": None},
                reward=reward,
            )
            memory.add(memory_entry)
        person = {
            "person_idx": question["round_idx"],
            "name": question["name"],
            "query": question["query"],
            "plan": runtime["parse_plan"](action),
            "result": action,
            "agent_success": result.success,
            "agent_error": result.error_message,
            "steps": result.total_steps,
            "reward_hidden_from_agent": reward,
            "usage": _usage_delta(agent.get_usage_stats(), before),
            "duration_seconds": round(time.perf_counter() - person_started, 6),
        }
        scratchpad = {
            "person_idx": question["round_idx"],
            "scratchpad": agent.get_scratchpad_dict(),
        }
        checkpoint = {
            "schema_version": 1,
            "registration_sha256": registration_sha256,
            "arm": arm,
            "group_id": row["id"],
            "person_idx": question["round_idx"],
            "person": person,
            "memory_context": memory_contexts[-1],
            "memory_entry": memory_entry,
            "scratchpad": scratchpad,
        }
        _append_checkpoint(person_checkpoint_path, checkpoint)
        person_checkpoints[checkpoint_key] = checkpoint
        persons.append(person)
        scratchpads.append(scratchpad)
    environment.close()
    return {
        "schema_version": 1,
        "arm": arm,
        "group_id": row["id"],
        "duration_seconds": round(
            sum(person["duration_seconds"] for person in persons), 6
        ),
        "wall_seconds_this_attempt": round(time.perf_counter() - started, 6),
        "persons": persons,
        "memory_contexts": memory_contexts,
        "scratchpads": scratchpads,
    }


def _write_submission(path: Path, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        {
            "id": record["group_id"],
            "persons": [
                {
                    "person_idx": person["person_idx"],
                    "name": person["name"],
                    "query": person["query"],
                    "plan": person["plan"],
                }
                for person in record["persons"]
            ],
        }
        for record in records
    ]
    path.write_bytes(b"".join(_canonical(row) + b"\n" for row in rows))
    return rows


def _native_score(evaluator, submission: Path) -> dict[str, float]:
    with redirect_stdout(io.StringIO()):
        return evaluator(str(submission))


def run(
    *,
    root: Path,
    upstream: Path,
    registration_path: Path,
    output: Path,
    memory_port: int,
) -> dict[str, Any]:
    registration, cohort = _verify_registration(
        registration_path, root=root, upstream=upstream
    )
    expected_runtime = registration["actor"]["runtime_identity"]
    observed_runtime = _ollama_reader_identity(
        expected_runtime["api_base_url"], registration["actor"]["model"]
    )
    if observed_runtime != expected_runtime:
        raise RuntimeError("Ollama actor identity changed after registration")
    registration_sha256 = _sha256(registration_path)
    manifest_path = output / "execution_manifest.json"
    if output.exists():
        if not output.is_dir() or not manifest_path.is_file():
            raise RuntimeError("existing output is not a resumable execution")
        manifest = json.loads(manifest_path.read_bytes())
        expected_manifest = {
            "schema_version": 1,
            "registration_sha256": registration_sha256,
            "source_revision": _git(root, "rev-parse", "HEAD"),
            "actor_runtime": observed_runtime,
        }
        if any(manifest.get(key) != value for key, value in expected_manifest.items()):
            raise RuntimeError("existing execution manifest differs from this run")
    else:
        output.mkdir(parents=True)
        manifest = {
            "schema_version": 1,
            "registration_sha256": registration_sha256,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "source_revision": _git(root, "rev-parse", "HEAD"),
            "actor_runtime": observed_runtime,
        }
        manifest_path.write_bytes(_canonical(manifest) + b"\n")
    checkpoint_path = output / "group_checkpoints.jsonl"
    checkpoints = _load_checkpoints(checkpoint_path, registration_sha256)
    person_checkpoint_path = output / "person_checkpoints.jsonl"
    person_checkpoints = _load_person_checkpoints(
        person_checkpoint_path, registration_sha256
    )
    runtime = _upstream_runtime(upstream)
    previous_base = os.environ.get("OPENAI_API_BASE")
    previous_key = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_BASE"] = registration["actor"]["constructor_openai_base_url"]
    os.environ["OPENAI_API_KEY"] = "ollama"
    process = None
    log = None
    try:
        pending_prme = any(("prme", row["id"]) not in checkpoints for row in cohort)
        if pending_prme:
            process, log = _start_server(
                root,
                output,
                memory_port,
                registration["protocol"]["prme_memory_tokens"],
            )
        shared_agent = runtime["agent"](
            model_name=registration["actor"]["model"],
            temperature=registration["actor"]["temperature"],
            max_steps=registration["actor"]["max_steps"],
        )
        _configure_actor_client(shared_agent, registration["actor"])
        # The upstream reset removes all behavioral state. Reusing its immutable
        # local tool databases avoids loading the 305 MB flight table twice.
        agents = {arm: shared_agent for arm in ARMS}
        memory_url = f"http://127.0.0.1:{memory_port}"
        for index, row in enumerate(cohort):
            order = ARMS if index % 2 == 0 else tuple(reversed(ARMS))
            for arm in order:
                key = (arm, row["id"])
                if key in checkpoints:
                    continue
                print(
                    f"[memoryarena] group={row['id']} arm={arm} "
                    f"({index + 1}/{len(cohort)})",
                    flush=True,
                )
                record = _run_group(
                    row,
                    arm=arm,
                    runtime=runtime,
                    agent=agents[arm],
                    memory_url=memory_url,
                    model=registration["actor"]["model"],
                    registration_sha256=registration_sha256,
                    person_checkpoint_path=person_checkpoint_path,
                    person_checkpoints=person_checkpoints,
                )
                record["registration_sha256"] = registration_sha256
                _append_checkpoint(checkpoint_path, record)
                checkpoints[key] = record
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        if log is not None:
            log.close()
        if previous_base is None:
            os.environ.pop("OPENAI_API_BASE", None)
        else:
            os.environ["OPENAI_API_BASE"] = previous_base
        if previous_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = previous_key

    expected = {
        row["id"]: [answer["round_idx"] for answer in row["answers"]]
        for row in cohort
    }
    scores = {}
    ordered_records = {}
    for arm in ARMS:
        records = [checkpoints[(arm, row["id"])] for row in cohort]
        ordered_records[arm] = records
        submission = output / f"submission-{arm}.jsonl"
        rows = _write_submission(submission, records)
        validate_coverage(expected, rows)
        scores[arm] = {
            "strict": score_strict(cohort, rows),
            "native": _native_score(runtime["native_evaluate"], submission),
            "agent_failures": sum(
                not person["agent_success"]
                for record in records
                for person in record["persons"]
            ),
            "duration_seconds": round(
                sum(record["duration_seconds"] for record in records), 6
            ),
            "input_tokens": sum(
                person["usage"]["total_input_tokens"]
                for record in records
                for person in record["persons"]
            ),
            "output_tokens": sum(
                person["usage"]["total_output_tokens"]
                for record in records
                for person in record["persons"]
            ),
        }
    deltas = {
        metric: scores["prme"]["strict"][metric]
        - scores["native_full_history"]["strict"][metric]
        for metric in ("ps", "sps", "sr")
    }
    rules = registration["evaluation"]["decision_rules"]
    gates = {
        "complete_execution": len(checkpoints) == len(cohort) * len(ARMS),
        "strict_ps_noninferiority": deltas["ps"]
        >= rules["minimum_prme_minus_native_strict_ps_points"],
        "strict_sps_noninferiority": deltas["sps"]
        >= rules["minimum_prme_minus_native_strict_sps_points"],
    }
    result = {
        "schema_version": SCHEMA_VERSION,
        "kind": RESULT_KIND,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": registration_sha256,
        "source": registration["source"],
        "dataset": registration["dataset"],
        "cohort": registration["cohort"],
        "actor": registration["actor"],
        "protocol": registration["protocol"],
        "scores": scores,
        "prme_minus_native_strict_points": deltas,
        "gates": {**gates, "passed": all(gates.values())},
        "limits": registration["evaluation"]["limits"],
    }
    (output / "result.json").write_bytes(_canonical(result) + b"\n")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    register_parser = subparsers.add_parser("register")
    register_parser.add_argument("--upstream", required=True, type=Path)
    register_parser.add_argument("--output", required=True, type=Path)
    register_parser.add_argument(
        "--ollama-origin", default="http://127.0.0.1:11434"
    )
    register_parser.add_argument("--model", default="deepseek-v4.1-flash:cloud")
    register_parser.add_argument("--seed", type=int, default=20260917)
    register_parser.add_argument("--groups-per-stratum", type=int, default=3)
    register_parser.add_argument(
        "--exclude-group-id", type=int, action="append", default=[1]
    )
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--upstream", required=True, type=Path)
    run_parser.add_argument("--registration", required=True, type=Path)
    run_parser.add_argument("--output", required=True, type=Path)
    run_parser.add_argument("--memory-port", type=int, default=8018)
    return parser


def main() -> None:
    args = _parser().parse_args()
    root = Path(__file__).resolve().parents[2]
    if args.mode == "register":
        value = register(
            root=root,
            upstream=args.upstream.resolve(),
            output=args.output.resolve(),
            ollama_origin=args.ollama_origin,
            model=args.model,
            seed=args.seed,
            groups_per_stratum=args.groups_per_stratum,
            excluded_group_ids=set(args.exclude_group_id),
        )
    else:
        value = run(
            root=root,
            upstream=args.upstream.resolve(),
            registration_path=args.registration.resolve(),
            output=args.output.resolve(),
            memory_port=args.memory_port,
        )
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
