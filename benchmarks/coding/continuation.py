"""One final, fixed three-arm study; see FINAL-PROTOCOL.md. No auto-resume."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
from uuid import uuid4

import httpx
import tiktoken

from benchmarks.coding import ticket108 as agent
from benchmarks.coding.continuation_tasks import TASKS
from benchmarks.coding.run import digest, docker_check, model_identity, save
from prme.integrations.coding import MemoryTools, git

REVISION = "df2d58ea9679a801fc9c176db493008405d7adbe"
MODEL = "qwen3.5:35b-a3b"
ARMS = ("control", "notes", "prme")
BUDGET = 2048


def chunks(history):
    result = []
    for event_id, event in sorted(history["events"].items()):
        lines = event["content"].splitlines(keepends=True)
        pending, start = [], 1
        for end, line in enumerate(lines, 1):
            pending.append(line)
            if (not line.strip() and sum(map(len, pending)) >= 1000) or end == len(
                lines
            ):
                body = "".join(pending)
                item = dict(event_id=event_id, start=start, end=end, text=body)
                item["id"] = digest(item)
                item["content"] = (
                    f"Historical event {event_id}; lines {start}-{end}.\n\n{body}"
                )
                result.append(item)
                pending, start = [], end + 1
    return result


def notes_context(records, query):
    """Plain document search, without PRME dependencies or semantic expansion."""

    def terms(text):
        return re.findall(r"[a-z0-9]+", text.casefold())

    counts = [Counter(terms(item["content"])) for item in records]
    lengths = [sum(row.values()) for row in counts]
    average = sum(lengths) / len(lengths)
    q = sorted(set(terms(query)))
    df = {term: sum(term in row for row in counts) for term in q}
    scores = []
    for item, row, length in zip(records, counts, lengths):
        score = sum(
            math.log(1 + (len(records) - df[t] + 0.5) / (df[t] + 0.5))
            * row[t]
            * 2.2
            / (row[t] + 1.2 * (0.25 + 0.75 * length / average))
            for t in q
            if row[t]
        )
        scores.append((score, item))
    encoding = tiktoken.get_encoding("cl100k_base")
    context, selected = "", []
    for score, item in sorted(scores, key=lambda pair: (-pair[0], pair[1]["id"])):
        if score <= 0 or len(selected) == 8:
            break
        candidate = context + ("\n\n" if context else "") + item["content"]
        if len(encoding.encode(candidate)) <= BUDGET:
            context = candidate
            selected.append({"id": item["id"], "score": score})
    return dict(
        context=context, selected=selected, tokens=len(encoding.encode(context))
    )


def decision(rows, *, complete):
    totals = {
        arm: {
            "passes": sum(r["passed"] for r in rows if r["arm"] == arm),
            "input_tokens": sum(r["input_tokens"] for r in rows if r["arm"] == arm),
            "output_tokens": sum(r["output_tokens"] for r in rows if r["arm"] == arm),
            "seconds": sum(r["seconds"] for r in rows if r["arm"] == arm),
        }
        for arm in ARMS
    }
    passed = {(r["task"], r["repeat"], r["arm"]): r["passed"] for r in rows}
    reliable = {
        arm: sum(
            all(passed.get((t["id"], rep, arm), False) for rep in range(2))
            for t in TASKS
        )
        for arm in ARMS
    }
    losses = [
        dict(task=t["id"], repeat=rep, baseline=arm)
        for t in TASKS
        for rep in range(2)
        for arm in ARMS[:2]
        if passed.get((t["id"], rep, arm), False)
        and not passed.get((t["id"], rep, "prme"), False)
    ]
    failures = sum(r["status"] != "complete" for r in rows)
    complete = complete and len(passed) == 24 and len(rows) == 24
    base = complete and not failures and not losses and reliable["prme"] >= 3
    notes_tokens = totals["notes"]["input_tokens"]
    quality = (
        base
        and all(reliable["prme"] >= reliable[a] + 2 for a in ARMS[:2])
        and totals["prme"]["input_tokens"] <= 1.1 * notes_tokens
    )
    efficiency = (
        base
        and notes_tokens > 0
        and totals["prme"]["input_tokens"] <= 0.8 * notes_tokens
    )
    return dict(
        complete=complete,
        totals=totals,
        reliable_tasks=reliable,
        repeat_losses=losses,
        provider_failures=failures,
        correctness_gate=bool(quality),
        efficiency_gate=bool(efficiency),
        decision="limited_followup_eligible"
        if quality or efficiency
        else "shelve_automatic_integration",
    )


def input_hashes(root):
    names = [
        "benchmarks/coding/continuation.py",
        "benchmarks/coding/continuation_tasks.py",
        "benchmarks/coding/ticket108.py",
        "benchmarks/coding/run.py",
        "benchmarks/coding/FINAL-PROTOCOL.md",
        "docker/coding-memory.Dockerfile",
    ]
    return {name: digest((root / name).read_text()) for name in names}


def prepare(output, image_tag):
    if (output / "prepared.json").exists():
        raise ValueError("Inputs already prepared; no overwrite")
    root = Path.cwd()
    history = json.loads((output / "history.json").read_text())
    records = chunks(history)
    files = agent.snapshot(root, REVISION)
    image = subprocess.check_output(
        ["docker", "image", "inspect", image_tag, "--format", "{{.Id}}"], text=True
    ).strip()
    agent.verify_image(image, files)
    save(output / "corpus.json", records)
    save(output / "source-hashes.json", {p: digest(s) for p, s in files.items()})
    preflight = {}
    for task in TASKS:
        preflight[task["id"]] = {
            name: docker_check(image, task, task[name], task["checks"])
            for name in ("stub", "witness")
        }
        save(output / "preflight.json", preflight)
        assert (
            not preflight[task["id"]]["stub"]["passed"]
            and preflight[task["id"]]["witness"]["passed"]
        ), task["id"]
    name = "prme-coding-final-" + uuid4().hex[:10]
    token = secrets.token_urlsafe(36)
    (output / "pack").mkdir()
    subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--name",
            name,
            "--publish",
            "127.0.0.1::8000",
            "--memory",
            "2g",
            "--cpus",
            "2",
            "--env",
            "PRME_CODING_TOKEN",
            "--mount",
            f"type=bind,src={output / 'pack'},dst=/memory",
            "--mount",
            "type=volume,src=prme-coding-experiment-models,dst=/cache",
            image,
            "python",
            "-m",
            "prme.integrations.coding_server",
        ],
        env={**os.environ, "PRME_CODING_TOKEN": token},
        check=True,
        capture_output=True,
    )
    try:
        address = subprocess.check_output(
            ["docker", "port", name, "8000/tcp"], text=True
        ).strip()
        memory = MemoryTools(f"http://{address}/mcp", token)
        for _ in range(60):
            try:
                memory.call("memory_scan_nodes", {"scope": "project", "limit": 1})
                break
            except httpx.HTTPError:
                time.sleep(1)
        else:
            raise RuntimeError("Experiment service did not start")
        imports = []
        for item in records:
            receipt = memory.call(
                "memory_store",
                {
                    "content": item["content"],
                    "node_type": "note",
                    "scope": "project",
                    "role": "tool",
                    "source_type": "external_document",
                    "metadata": {
                        "continuation_source_v1": {
                            k: item[k] for k in ("id", "event_id", "start", "end")
                        }
                    },
                },
            )
            imports.append(dict(id=item["id"], receipt=receipt))
        save(output / "imports.json", imports)
        contexts = {}
        encoding = tiktoken.get_encoding("cl100k_base")
        for task in TASKS:
            started = time.monotonic()
            notes = notes_context(records, task["prompt"])
            notes["seconds"] = time.monotonic() - started
            started = time.monotonic()
            response = memory.recall(task["prompt"], budget=BUDGET)
            context = response.get("context", "")
            assert context and response["metrics"]["receipt_persisted"]
            prme = dict(
                context=context,
                response=response,
                seconds=time.monotonic() - started,
                tokens=len(encoding.encode(context)),
            )
            assert prme["tokens"] <= BUDGET, "PRME exceeded context ceiling"
            contexts[task["id"]] = dict(notes=notes, prme=prme)
        save(output / "contexts.json", contexts)
    finally:
        log = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
        (output / "service.log").write_text(log.stdout + log.stderr)
        subprocess.run(["docker", "stop", name], capture_output=True)
        subprocess.run(["docker", "rm", name], capture_output=True)
    with httpx.Client(
        base_url="http://127.0.0.1:11434", timeout=240, trust_env=False
    ) as client:
        identity = model_identity(client, MODEL)
    prepared = dict(
        revision=REVISION,
        image_id=image,
        model=identity,
        prepared_at=datetime.now(timezone.utc).isoformat(),
        history_events=len(history["events"]),
        chunks=len(records),
        frozen_files=len(files),
        code_hashes=input_hashes(root),
        artifacts={p.name: digest(p.read_text()) for p in output.glob("*.json")},
    )
    save(output / "prepared.json", prepared)
    print(json.dumps(prepared, indent=2), flush=True)


def run(output, registration):
    root = Path.cwd()
    registered = json.loads(registration.read_text())
    prepared = json.loads((output / "prepared.json").read_text())
    if prepared != registered or prepared["code_hashes"] != input_hashes(root):
        raise ValueError("Prepared inputs do not match committed registration")
    for filename, expected in prepared["artifacts"].items():
        assert digest((output / filename).read_text()) == expected, filename
    # Prove registration is committed before the first scored call.
    assert (
        git(root, "show", f"HEAD:{registration.relative_to(root)}")
        == registration.read_text().strip()
    )
    manifest = output / "manifest.json"
    if manifest.exists():
        raise ValueError(
            "This study has already started; no retries or automatic resume"
        )
    files = agent.snapshot(root, REVISION)
    agent.verify_image(prepared["image_id"], files)
    records = json.loads((output / "corpus.json").read_text())
    contexts = json.loads((output / "contexts.json").read_text())
    state = dict(
        registration_commit=git(root, "rev-parse", "HEAD"),
        started=datetime.now(timezone.utc).isoformat(),
        complete=False,
    )
    save(manifest, state)
    rows = []
    try:
        with httpx.Client(
            base_url="http://127.0.0.1:11434", timeout=240, trust_env=False
        ) as client:
            assert model_identity(client, MODEL) == prepared["model"]
            client.post(
                "/api/generate",
                json={"model": MODEL, "stream": False, "keep_alive": "20m"},
            ).raise_for_status()
            for repeat in range(2):
                for i, task in enumerate(TASKS):
                    offset = (i + repeat) % 3
                    order = ARMS[offset:] + ARMS[:offset]
                    if repeat:
                        order = tuple(reversed(order))
                    for arm in order:
                        assert model_identity(client, MODEL) == prepared["model"]
                        view = dict(files)
                        view[task["path"]] = task["stub"]
                        if arm != "control":
                            view.update(
                                {
                                    f"history/{item['id']}.txt": item["content"]
                                    for item in records
                                }
                            )
                        context = (
                            contexts[task["id"]][arm]["context"]
                            if arm != "control"
                            else ""
                        )
                        print(
                            f"Running {task['id']} repeat {repeat + 1} {arm}",
                            flush=True,
                        )
                        result = agent.run_agent(
                            client,
                            MODEL,
                            view,
                            context,
                            prepared["image_id"],
                            output / f"{task['id']}-{repeat}-{arm}.json",
                            task["checks"],
                            seed=20260926 + repeat,
                            task=task,
                        )
                        row = {
                            key: result[key]
                            for key in (
                                "status",
                                "failure",
                                "task",
                                "passed",
                                "seconds",
                                "input_tokens",
                                "output_tokens",
                                "actions",
                                "candidate_sha256",
                            )
                        }
                        row.update(arm=arm, repeat=repeat)
                        rows.append(row)
                        save(output / "results.json", rows)
                        save(output / "summary.json", decision(rows, complete=False))
                        print(json.dumps(row), flush=True)
                        assert model_identity(client, MODEL) == prepared["model"]
            state["complete"] = True
    except BaseException as exc:
        state["failure"] = type(exc).__name__
        raise
    finally:
        state["finished"] = datetime.now(timezone.utc).isoformat()
        save(manifest, state)
        save(output / "summary.json", decision(rows, complete=state["complete"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "run"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", default="prme-coding-final:base")
    parser.add_argument("--registration", type=Path)
    args = parser.parse_args()
    if args.phase == "prepare":
        prepare(args.output.resolve(), args.image)
    else:
        if args.registration is None:
            parser.error("run requires --registration")
        run(args.output.resolve(), args.registration.resolve())


if __name__ == "__main__":
    main()
