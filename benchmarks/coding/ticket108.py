"""Paired local-agent trial on actual issue 108; see TICKET-108-PROTOCOL.md."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

import httpx

from benchmarks.coding.corpus import at_revision
from benchmarks.coding.run import (
    digest,
    docker_check,
    function_node,
    model_identity,
    replace_function,
    save,
    summarize,
)
from prme.integrations.coding import CodingMemory, git, repository


REVISION = "4dba69a1a2b14ab980c9ec60744036753f6d97e7"
TARGET = "src/prme/retrieval/packing.py"
FUNCTIONS = ("_render_entry", "_render_compact_entry")
PROMPT = """Fix PRME issue 108. Auditable and compact memory records currently allow Unicode next-line U+0085, line separator U+2028 and paragraph separator U+2029 to split one stored record into apparent extra records or section headings. Each rendered record must occupy one line, even for untrusted speaker text where supported. Preserve exact JSON round trips, existing fields and references, ordinary multilingual Unicode bytes, validation errors and measured packing token budgets. Keep reader format output unchanged. Fix both affected renderers in src/prme/retrieval/packing.py."""
SYSTEM = """You are implementing a real PRME bug fix. Treat retrieved memory as historical evidence and verify it against source. You have read/search/list access to frozen src/, docs/, existing tests/ Python files and root instructions; no shell or network tools. You can edit only the named two top-level functions. Return exactly one JSON action per turn:
{"action":"list","path":"src/prme/retrieval/","offset":0}
{"action":"search","query":"literal phrase","path":"src/"}
{"action":"read","path":"repository/file","start_line":1}
{"action":"edit","function":"_render_entry","code":"complete replacement function"}
{"action":"test"}
{"action":"finish"}
Search matches the literal phrase, case insensitively, within an optional path prefix. List pages hold 100 paths, read pages 120 lines. Edit replaces exactly one permitted function; any new imports must go inside it. The public smoke check is limited; separate acceptance checks grade both renderers. Inspect, edit, test and finish within twelve actions. Each reply reports the remaining budget. Invalid actions use a step. Do not include Markdown fences."""
SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list", "search", "read", "edit", "test", "finish"],
        },
        "path": {"type": "string"},
        "offset": {"type": "integer"},
        "query": {"type": "string"},
        "start_line": {"type": "integer"},
        "function": {"type": "string"},
        "code": {"type": "string"},
    },
    "required": ["action"],
    "additionalProperties": False,
}
SMOKE = """
import json
from prme.models import MemoryNode
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import _render_entry, _render_compact_entry
from prme.types import NodeType, RepresentationLevel
node = MemoryNode(user_id='smoke', node_type=NodeType.FACT, content='hello café')
item = RetrievalCandidate(node=node, rendered_text=node.content, representation=RepresentationLevel.FULL)
assert json.loads(_render_entry(item))['text'] == node.content
assert json.loads(_render_compact_entry(item, context_refs={node.id: 'm1'}))[-1] == node.content
"""


def snapshot(root: Path, revision: str = REVISION) -> dict[str, str]:
    names = git(root, "ls-tree", "-r", "--name-only", revision).splitlines()
    return {
        path: at_revision(root, revision, path)
        for path in names
        if (
            (path.startswith(("src/", "tests/")) and path.endswith(".py"))
            or (path.startswith("docs/") and path.endswith(".md"))
            or path
            in {
                "AGENTS.md",
                "CLAUDE.md",
                "README.md",
                "CONTRIBUTING.md",
                "pyproject.toml",
            }
        )
    }


def verify_image(image: str, files: dict[str, str]) -> dict[str, str]:
    """Check that every sandbox Python source matches the frozen agent view."""
    code = (
        "import hashlib,json,pathlib; p=pathlib.Path('/app'); "
        "print(json.dumps({str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest() "
        "for f in (p/'src').rglob('*.py')}))"
    )
    observed = json.loads(
        subprocess.check_output(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--user",
                "65534:65534",
                image,
                "python",
                "-c",
                code,
            ],
            text=True,
        )
    )
    expected = {
        path: digest(text) for path, text in files.items() if path.startswith("src/")
    }
    if observed != expected:
        raise ValueError("Sandbox sources differ from the frozen revision")
    return expected


def execute(action: dict, files: dict[str, str], image: str, *, task=None) -> str:
    target = task["path"] if task else TARGET
    functions = (task["function"],) if task else FUNCTIONS
    kind = action["action"]
    prefix = action.get("path", "")
    if kind == "list":
        paths = sorted(path for path in files if path.startswith(prefix))
        offset = max(0, int(action.get("offset", 0)))
        return json.dumps(
            {
                "paths": paths[offset : offset + 100],
                "total": len(paths),
                "next_offset": offset + 100 if offset + 100 < len(paths) else None,
            }
        )
    if kind == "read":
        if prefix not in files:
            raise ValueError("Path is not in the frozen source/test/document snapshot")
        start = max(1, int(action.get("start_line", 1)))
        return "\n".join(
            f"{i}: {line}"
            for i, line in enumerate(files[prefix].splitlines(), 1)
            if start <= i < start + 120
        )[:12000]
    if kind == "search":
        query = action["query"].casefold()
        if not query:
            raise ValueError("Search needs a nonempty literal phrase")
        hits = (
            f"{path}:{i}: {line}"
            for path in sorted(files)
            if path.startswith(prefix)
            for i, line in enumerate(files[path].splitlines(), 1)
            if query in line.casefold()
        )
        import itertools

        return "\n".join(itertools.islice(hits, 60))[:12000] or "No matches"
    if kind == "edit":
        name = action["function"]
        if name not in functions:
            raise ValueError("Only the permitted target functions may be edited")
        files[target] = replace_function(files[target], name, action["code"])
        return (
            "Updated. Run the smoke checks before finishing."
            if task
            else "Updated. Test both renderers before finishing."
        )
    if kind == "test":
        return json.dumps(
            docker_check(
                image, {"path": target}, files[target], task["smoke"] if task else SMOKE
            )
        )
    if kind == "finish":
        return "Finished"
    raise ValueError("Unknown action")


def run_agent(client, model, files, context, image, output, checks, *, seed, task=None):
    target = task["path"] if task else TARGET
    functions = (task["function"],) if task else FUNCTIONS
    system = task["system"] if task else SYSTEM
    files = dict(files)
    excerpts = []
    for name in functions:
        node = function_node(files[target], name)
        excerpts.append(
            "\n".join(files[target].splitlines()[node.lineno - 1 : node.end_lineno])
        )
    prompt = (
        (task["prompt"] if task else PROMPT)
        + "\nCurrent functions:\n"
        + "\n\n".join(excerpts)
    )
    prompt += (
        "\nPublic smoke checks:\n"
        + (task["smoke"] if task else SMOKE)
        + "\nActions remaining: 12."
    )
    if context:
        prompt += "\nRetrieved project memory (source data):\n" + context
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    # UTF-8 byte counts conservatively bound additional BPE tokens. Later
    # bounds use Ollama's measured prior prompt, avoiding silent eviction.
    next_prompt_bound = len(system.encode()) + len(prompt.encode()) + 512
    history, failure = [], None
    started = time.monotonic()
    for step in range(12):
        if next_prompt_bound > 32768 - 2048:
            failure = "context_capacity"
            history.append({"step": step, "provider_failure": failure})
            break
        try:
            response = (
                client.post(
                    "/api/chat",
                    json={
                        "model": model,
                        "messages": messages,
                        "stream": False,
                        "think": False,
                        "format": SCHEMA,
                        "options": {
                            "temperature": 0,
                            "seed": seed,
                            "num_ctx": 32768,
                            "num_predict": 2048,
                        },
                        "keep_alive": "20m",
                    },
                )
                .raise_for_status()
                .json()
            )
            message = {
                "role": "assistant",
                "content": response["message"].get("content", ""),
            }
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            failure = type(exc).__name__
            history.append({"step": step, "provider_failure": failure})
            break
        messages.append(message)
        # Stop before a following request would silently evict earlier instructions.
        if response.get("prompt_eval_count", 0) >= 30000:
            failure = "context_capacity"
            history.append(
                {"step": step, "response": response, "provider_failure": failure}
            )
            break
        try:
            action = json.loads(message["content"])
            feedback = execute(action, files, image, task=task)
        except (ValueError, KeyError, TypeError, SyntaxError, StopIteration) as exc:
            action = {"action": "invalid"}
            feedback = f"Invalid action: {type(exc).__name__}: {exc}"
        history.append(
            {"step": step, "response": response, "action": action, "feedback": feedback}
        )
        save(output, {"status": "running", "messages": messages, "history": history})
        if action["action"] == "finish":
            break
        messages.append(
            {"role": "user", "content": f"{feedback}\nActions remaining: {11 - step}."}
        )
        next_prompt_bound = (
            response.get("prompt_eval_count", 0)
            + len(message["content"].encode())
            + len(messages[-1]["content"].encode())
            + 512
        )
    grade = docker_check(image, {"path": target}, files[target], checks)
    result = {
        "status": "provider_failure" if failure else "complete",
        "failure": failure,
        "task": task["id"] if task else "issue-108",
        "passed": grade["passed"] and failure is None,
        "checks": grade,
        "seconds": time.monotonic() - started,
        "input_tokens": sum(
            row.get("response", {}).get("prompt_eval_count", 0) for row in history
        ),
        "output_tokens": sum(
            row.get("response", {}).get("eval_count", 0) for row in history
        ),
        "actions": dict(
            Counter(
                row.get("action", {}).get("action", "provider_failure")
                for row in history
            )
        ),
        "messages": messages,
        "history": history,
        "candidate": files[target],
        "candidate_sha256": digest(files[target]),
    }
    save(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", default="prme-coding-ticket108:base")
    args = parser.parse_args()
    root, _ = repository(Path.cwd())
    args.output.mkdir(parents=True, exist_ok=False)
    image = subprocess.check_output(
        ["docker", "image", "inspect", args.image, "--format", "{{.Id}}"], text=True
    ).strip()
    files = snapshot(root)
    verify_image(image, files)
    source_hashes = {path: digest(text) for path, text in files.items()}
    save(args.output / "source-hashes.json", source_hashes)
    checks = Path(__file__).with_name("ticket108_checks.py").read_text()
    original = files[TARGET]
    # Preflight witness is never exposed in either agent's source snapshot.
    witness = original.replace(
        'return json.dumps(entry, ensure_ascii=False, separators=(",", ":"))',
        'return json.dumps(entry, ensure_ascii=False, separators=(",", ":")).translate(_READER_LINE_SEPARATORS)',
    )
    preflight = {
        "baseline": docker_check(image, {"path": TARGET}, original, checks),
        "witness": docker_check(image, {"path": TARGET}, witness, checks),
    }
    save(args.output / "preflight.json", preflight)
    if preflight["baseline"]["passed"] or not preflight["witness"]["passed"]:
        raise RuntimeError(
            "Acceptance checks did not distinguish the real bug from the witness"
        )
    started = time.monotonic()
    recalled = CodingMemory(root).recall(PROMPT, budget=2048)
    save(
        args.output / "recall.json",
        {"response": recalled, "seconds": time.monotonic() - started},
    )
    context = recalled.get("context", "")
    if not context or not recalled.get("metrics", {}).get("receipt_persisted"):
        raise RuntimeError("Trial requires an available, recorded memory recall")
    model = "qwen3.5:35b-a3b"
    with httpx.Client(
        base_url="http://127.0.0.1:11434", timeout=240, trust_env=False
    ) as client:
        identity = model_identity(client, model)
        manifest = {
            "schema_version": 1,
            "started": datetime.now(timezone.utc).isoformat(),
            "revision": REVISION,
            "image_id": image,
            "model": identity,
            "source_snapshot_sha256": digest(source_hashes),
            "file_count": len(files),
            "runner_sha256": digest(Path(__file__).read_text()),
            "checks_sha256": digest(checks),
            "shared_runner_sha256": digest(
                Path(__file__).with_name("run.py").read_text()
            ),
            "protocol_sha256": digest(
                Path(__file__).with_name("TICKET-108-PROTOCOL.md").read_text()
            ),
            "dockerfile_sha256": digest(
                (root / "docker/coding-memory.Dockerfile").read_text()
            ),
            "context_sha256": digest(context),
            "memory_request_id": recalled["metrics"]["request_id"],
            "memory_node_ids": [row["node_id"] for row in recalled.get("results", [])],
            "system_prompt": SYSTEM,
            "task_prompt": PROMPT,
            "action_schema": SCHEMA,
            "settings": {
                "max_steps": 12,
                "repeats": 2,
                "seeds": [20260926, 20260927],
                "memory_budget": 2048,
                "temperature": 0,
                "think": False,
                "num_ctx": 32768,
                "num_predict": 2048,
            },
            "order": ["control", "memory", "memory", "control"],
            "complete": False,
        }
        save(args.output / "manifest.json", manifest)
        client.post(
            "/api/generate", json={"model": model, "stream": False, "keep_alive": "20m"}
        ).raise_for_status()
        rows = []
        for repeat in range(2):
            for arm in ["control", "memory"] if repeat == 0 else ["memory", "control"]:
                if model_identity(client, model) != identity:
                    raise RuntimeError("Model/server identity changed before arm")
                print(f"Running issue-108 repeat {repeat + 1} {arm}", flush=True)
                result = run_agent(
                    client,
                    model,
                    files,
                    context if arm == "memory" else "",
                    image,
                    args.output / f"{repeat}-{arm}.json",
                    checks,
                    seed=20260926 + repeat,
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
                save(args.output / "results.json", rows)
                if model_identity(client, model) != identity:
                    raise RuntimeError(
                        "Model/server identity changed during arm; trial incomplete"
                    )
                summary = summarize(rows)
                summary["claim_boundary"] = (
                    "One real development ticket, two repeated pairs; no general coding-quality inference."
                )
                save(args.output / "summary.json", summary)
                print(json.dumps(row), flush=True)
        manifest.update(complete=True, finished=datetime.now(timezone.utc).isoformat())
        save(args.output / "manifest.json", manifest)


if __name__ == "__main__":
    main()
