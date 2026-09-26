"""Repository-local coding memory over PRME's existing authenticated MCP API.

Run ``python -m prme.integrations.coding --help``. Git worktrees share a service
through their common Git directory; independent clones have separate volumes.
No model extraction or arbitrary command execution is performed by this client.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from typing import Any
from uuid import uuid4

import httpx
from filelock import FileLock


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True, stderr=subprocess.PIPE
    ).strip()


def repository(repo: Path) -> tuple[Path, Path]:
    root = Path(git(repo, "rev-parse", "--show-toplevel")).resolve()
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    return root, common / "prme-coding"


def configure(repo: Path, port: int = 18765) -> Path:
    """Write private local configuration without changing global agent settings."""
    if not 1024 <= port <= 65535:
        raise ValueError("port must be between 1024 and 65535")
    root, state = repository(repo)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    config_path = state / "config.json"
    identity = hashlib.sha256(str(state).encode()).hexdigest()[:12]
    token = secrets.token_urlsafe(36)
    configuration = {"url": f"http://127.0.0.1:{port}/mcp", "token": token}
    # JSON is a YAML subset. No credentials enter tracked files or the build context.
    compose = {
        "name": f"prme-coding-{identity}",
        "services": {
            "memory": {
                "build": {
                    "context": str(root),
                    "dockerfile": "docker/coding-memory.Dockerfile",
                    "target": "runtime",
                },
                "ports": [f"127.0.0.1:{port}:8000"],
                "environment": {"PRME_CODING_TOKEN": token},
                "volumes": ["memory:/memory", "models:/cache"],
                "cpus": 2,
                "mem_limit": "2g",
                "restart": "unless-stopped",
            }
        },
        "volumes": {"memory": {}, "models": {}},
    }
    with FileLock(state / "setup.lock", timeout=30):
        if config_path.exists():
            return config_path
        for path, value in (
            (state / "compose.json", compose),
            (config_path, configuration),
        ):
            temporary = path.with_suffix(".tmp")
            with open(
                temporary, "w", opener=lambda p, flags: os.open(p, flags, 0o600)
            ) as handle:
                json.dump(value, handle, indent=2)
                handle.write("\n")
            temporary.replace(path)
    return config_path


class MemoryTools:
    def __init__(self, url: str, token: str, *, client: httpx.Client | None = None):
        self.url = url
        parsed = httpx.URL(self.url)
        if parsed.scheme != "http" or parsed.host != "127.0.0.1":
            raise ValueError("Coding memory must use the configured loopback service")
        self.token = token
        self._client = client

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        body = {
            "jsonrpc": "2.0",
            "id": str(uuid4()),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-11-25",
        }
        if self._client is None:
            with httpx.Client(timeout=120, trust_env=False) as client:
                response = client.post(self.url, json=body, headers=headers)
        else:
            response = self._client.post(self.url, json=body, headers=headers)
        response.raise_for_status()
        envelope = response.json()
        if "error" in envelope:
            raise RuntimeError(f"MCP request failed: {envelope['error']}")
        result = envelope["result"]
        if result.get("isError"):
            raise RuntimeError("Memory tool rejected the request")
        data = json.loads(result["content"][0]["text"])
        if "error" in data:
            raise RuntimeError(data["error"])
        return data

    def recall(self, query: str, *, budget: int = 2048) -> dict[str, Any]:
        if not query.strip() or not 256 <= budget <= 8192:
            raise ValueError("Provide a query and a token budget between 256 and 8192")
        return self.call(
            "memory_retrieve",
            {
                "query": query,
                "scope": "project",
                "include_cross_scope": False,
                "include_context": True,
                "token_budget": budget,
                "limit": 8,
                "max_per_source": 1,
                "min_fidelity": "prose",
            },
        )


class CodingMemory(MemoryTools):
    def __init__(self, repo: Path, *, client: httpx.Client | None = None):
        self.root, self.state = repository(repo)
        config = json.loads((self.state / "config.json").read_text())
        super().__init__(config["url"], config["token"], client=client)

    def remember(
        self,
        text: str,
        *,
        kind: str = "lesson",
        status: str = "observed",
        sources: list[str] | None = None,
        session: str | None = None,
    ) -> dict[str, Any]:
        if not text.strip():
            raise ValueError("Memory text must not be empty")
        if kind not in {"decision", "lesson", "handoff"}:
            raise ValueError("Unknown coding memory kind")
        if status not in {"observed", "hypothesis", "completed", "incomplete"}:
            raise ValueError("Unknown coding memory status")
        revision = git(self.root, "rev-parse", "HEAD")
        evidence = [source_excerpt(self.root, source) for source in sources or []]
        provenance = {
            "schema_version": 1,
            "kind": kind,
            "status": status,
            "commit": revision,
            "branch": git(self.root, "rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(
                git(self.root, "status", "--porcelain", "--untracked-files=no")
            ),
            "sources": [
                {k: v for k, v in item.items() if k != "text"} for item in evidence
            ],
        }
        refs = (
            ", ".join(item["reference"] for item in evidence)
            or "no file evidence supplied"
        )
        projection = (
            f"Coding {kind}; reported status: {status}; commit: {revision[:12]}.\n"
            f"Sources: {refs}\n{text.strip()}"
        )
        content = projection
        for item in evidence:
            content += f"\n\nSource {item['reference']} (sha256 {item['sha256']}):\n{item['text']}"
        arguments: dict[str, Any] = {
            "content": content,
            "node_type": {"decision": "decision", "lesson": "note", "handoff": "task"}[
                kind
            ],
            "scope": "project",
            "role": "assistant",
            "session_id": session,
            "metadata": {"coding_memory_v1": provenance},
        }
        if content != projection:
            arguments["retrieval_content"] = projection
        return self.call("memory_store", arguments)


def source_excerpt(root: Path, reference: str) -> dict[str, Any]:
    """Read an explicit tracked text excerpt; never follow paths outside the repo."""
    parts = reference.rsplit(":", 1)
    relative = parts[0]
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path.is_symlink():
        raise ValueError("Source must be inside the repository")
    relative = path.relative_to(root).as_posix()
    git(root, "ls-files", "--error-unmatch", "--", relative)
    data = path.read_bytes()
    if len(data) > 1_000_000:
        raise ValueError("Source exceeds one megabyte; select a smaller file")
    lines = data.decode("utf-8").splitlines(keepends=True)
    start, end = 1, len(lines)
    if len(parts) == 2:
        bounds = parts[1].split("-", 1)
        start = int(bounds[0])
        end = int(bounds[-1])
    if not 1 <= start <= end <= len(lines):
        raise ValueError("Invalid source line range")
    text = "".join(lines[start - 1 : end])
    if len(text) > 32_000:
        raise ValueError(
            "Source excerpt exceeds 32,000 characters; select a line range"
        )
    return {
        "reference": f"{relative}:{start}-{end}",
        "sha256": hashlib.sha256(data).hexdigest(),
        "excerpt_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "text": text,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("init", help="Create private Docker configuration")
    setup.add_argument("--port", type=int, default=18765)
    commands.add_parser("start", help="Build and start the repository memory service")
    commands.add_parser("stop", help="Stop the service, retaining memory")
    recall = commands.add_parser("recall", help="Retrieve task-relevant memory")
    recall.add_argument("query")
    recall.add_argument("--budget", type=int, default=2048)
    recall.add_argument("--json", action="store_true")
    remember = commands.add_parser(
        "remember", help="Read a concise lesson or handoff from stdin"
    )
    remember.add_argument(
        "--kind", choices=["decision", "lesson", "handoff"], default="lesson"
    )
    remember.add_argument(
        "--status",
        choices=["observed", "hypothesis", "completed", "incomplete"],
        default="observed",
    )
    remember.add_argument("--source", action="append", default=[])
    remember.add_argument("--session")
    archive = commands.add_parser(
        "archive", help="Retire a stale memory from retrieval"
    )
    archive.add_argument("node_id")
    args = parser.parse_args()
    try:
        if args.command == "init":
            print(configure(args.repo, args.port))
        elif args.command in {"start", "stop"}:
            _, state = repository(args.repo)
            action = ["up", "--build", "-d"] if args.command == "start" else ["stop"]
            subprocess.run(
                ["docker", "compose", "-f", str(state / "compose.json"), *action],
                check=True,
            )
        else:
            memory = CodingMemory(args.repo)
            if args.command == "recall":
                result = memory.recall(args.query, budget=args.budget)
                print(
                    json.dumps(result, indent=2)
                    if args.json
                    else result.get("context", "")
                )
            elif args.command == "remember":
                print(
                    json.dumps(
                        memory.remember(
                            sys.stdin.read(),
                            kind=args.kind,
                            status=args.status,
                            sources=args.source,
                            session=args.session,
                        ),
                        indent=2,
                    )
                )
            else:
                print(
                    json.dumps(
                        memory.call("memory_archive_node", {"node_id": args.node_id}),
                        indent=2,
                    )
                )
    except (
        OSError,
        ValueError,
        RuntimeError,
        subprocess.CalledProcessError,
        httpx.HTTPError,
    ) as exc:
        # Do not print HTTP requests/headers or local config contents.
        parser.exit(1, f"Coding memory unavailable ({type(exc).__name__}): {exc}\n")


if __name__ == "__main__":
    main()
