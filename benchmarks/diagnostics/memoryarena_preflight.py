"""Exercise the pinned, unmodified MemoryArena client against a local PRME server."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import inspect
import json
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time

import requests

from prme import MemoryEngine
from prme.retrieval.tokenization import count_tokens
from benchmarks.diagnostics.hybrid_lexical import raw_config, embedding_identity
from benchmarks.diagnostics.hindsight_capture import digest, write
from benchmarks.diagnostics import memoryarena_server

UPSTREAM_COMMIT = "6cd9de14b71915e39ac742a20dc33785e14b6aab"
SOURCE = "Nia prefers green tea; her colleague prefers black coffee."
QUESTION = "What does Nia prefer?"


async def verify_saved(store, event_id):
    config = raw_config().model_copy(update={
        "db_path": str(store / "memory.duckdb"),
        "vector_path": str(store / "vectors.usearch"),
        "lexical_path": str(store / "lexical"), "duckdb_threads": 1,
    })
    async with MemoryEngine.open(config) as engine:
        event = await engine.get_event(event_id)
        if event.content != SOURCE or event.event_time is not None:
            raise ValueError("Original source changed after shutdown")
        nodes = await engine.get_event_nodes(event_id, user_id=event.user_id)
        if (len(nodes) != 1 or nodes[0].source_type.value != "external_document"
                or nodes[0].metadata["record_kind"] != "agent_environment_trace"):
            raise ValueError("External-trace provenance changed")
    return await embedding_identity()


def run(upstream, report, log_path):
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=upstream, text=True).strip()
    if commit != UPSTREAM_COMMIT or subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=upstream
    ).strip():
        raise ValueError("Use the pinned, clean upstream checkout")
    client_file = upstream / "memory/client.py"
    spec = importlib.util.spec_from_file_location("native_memoryarena_client", client_file)
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    report.update(upstream_commit=commit, client_sha256=digest(client_file.read_bytes()))
    with tempfile.TemporaryDirectory(prefix="prme-arena-native-") as directory:
        store = Path(directory) / "store"
        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            port = port_socket.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        with log_path.open("xb") as log:
            server = subprocess.Popen([
                sys.executable, "-m", "benchmarks.diagnostics.memoryarena_server",
                "--directory", str(store), "--port", str(port),
            ], stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 45
                while True:
                    if server.poll() is not None:
                        raise RuntimeError("Server exited before readiness")
                    try:
                        schema = requests.get(url + "/openapi.json", timeout=1).json()
                        if schema["info"]["title"] == "PRME MemoryArena raw adapter":
                            break
                    except (requests.RequestException, ValueError, KeyError):
                        pass
                    if time.monotonic() > deadline:
                        raise TimeoutError("Server readiness")
                    time.sleep(.1)
                report["checks"].append("native loopback server ready")
                alice = native.MemoryClient("authored-alice", "prme", base_url=url, timeout=45)
                bob = native.MemoryClient("authored-bob", "prme", base_url=url, timeout=45)
                if alice.wrap_user_prompt(QUESTION) != "<memory_context>\nNone\n</memory_context>\nUser: " + QUESTION:
                    raise ValueError("Initial memory is not empty")
                event_id = alice.add(SOURCE)["response"]["event_id"]
                prompt = alice.wrap_user_prompt(QUESTION)
                block = prompt[:-len("\nUser: " + QUESTION)]
                if SOURCE not in prompt or not prompt.endswith("\nUser: " + QUESTION):
                    raise ValueError("Source or query changed")
                if count_tokens(block, "cl100k_base") > 4096:
                    raise ValueError("Memory block exceeds budget")
                if SOURCE in bob.wrap_user_prompt(QUESTION):
                    raise ValueError("Source leaked to another task")
                reset = native.MemoryClient("authored-alice", "prme", base_url=url, timeout=45)
                if SOURCE in reset.wrap_user_prompt(QUESTION):
                    raise ValueError("Reinitialization did not reset logical memory")
                report["checks"] += [
                    "unmodified upstream client initializes/adds/wraps",
                    "real BGE retrieval returns exact source",
                    "complete memory block fits 4096 tokens",
                    "other task cannot retrieve source",
                    "reinitialization starts fresh memory",
                ]
                report.update(context_sha256=digest(prompt.encode()),
                              memory_block_tokens=count_tokens(block, "cl100k_base"))
            finally:
                report["shutdown_signal_sent"] = server.poll() is None
                if report["shutdown_signal_sent"]:
                    server.terminate()
                try:
                    report["server_native_exit_code"] = server.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    server.kill()
                    report["server_native_exit_code"] = server.wait()
                    raise
        server_log = log_path.read_bytes()
        report["server_log_sha256"] = digest(server_log)
        if (not report["shutdown_signal_sent"]
                or report["server_native_exit_code"] != -signal.SIGTERM
                or b"Application shutdown complete." not in server_log):
            raise ValueError("Server did not finish the expected graceful SIGTERM shutdown")
        report["embedding_assets"] = asyncio.run(verify_saved(store, event_id))
        report["checks"].append("source and external-trace provenance survive shutdown and reset")
    report.update(passed=True,
                  package_path=str(Path(inspect.getfile(MemoryEngine)).resolve()),
                  engine_sha256=digest(Path(inspect.getfile(MemoryEngine)).read_bytes()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    log_path = args.output.with_suffix(".server.log")
    if args.output.exists() or log_path.exists():
        raise ValueError("Use fresh output and server-log paths")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "passed": False, "checks": [],
        "runner_sha256": digest(Path(__file__).read_bytes()),
        "adapter_sha256": digest(Path(memoryarena_server.__file__).read_bytes()),
        "expected_server_native_exit_code": -signal.SIGTERM,
        "limits": "Authored compatibility and durability only. No MemoryArena dataset, "
                  "task agent, judge or task-success score was run. Server exit -15 is "
                  "the explicitly requested daemon shutdown; preflight success requires exit zero.",
    }
    try:
        run(args.upstream, report, log_path)
    except BaseException as exc:
        report["error_type"] = type(exc).__name__
        raise
    finally:
        write(args.output, report)
        print(json.dumps({key: report.get(key) for key in ("passed", "checks", "server_native_exit_code")}))


if __name__ == "__main__":
    main()
