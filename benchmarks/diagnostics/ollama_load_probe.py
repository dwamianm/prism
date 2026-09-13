"""Bounded loading probe on a task-owned Ollama server using existing assets.

This is an execution diagnostic, not a benchmark or a repair of failed answers.
It never downloads models, changes the existing service, or retries responses.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import urllib.request


MODELS = (("qwen3.5:4b", 65536), ("gemma4:26b", 65536), ("gemma4:31b", 32768))
SERVER_OPTIONS = {"OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "1",
                  "OLLAMA_NO_CLOUD": "1"}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def request(url, route, body=None, *, timeout=5):
    # Direct loopback connection: do not inherit a configured HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url + route,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with opener.open(req, timeout=timeout) as response:
        return json.load(response)


def inventory(url):
    tags = request(url, "/api/tags")["models"]
    result = {}
    for model, _ in MODELS:
        matches = [entry["digest"] for entry in tags if entry["name"] == model]
        if len(matches) != 1:
            raise ValueError("Each registered local model must be installed exactly once")
        result[model] = matches[0]
    return {"version": request(url, "/api/version")["version"], "models": result}


def probe(url, plan, report, output):
    if inventory(url) != plan["inventory"]:
        raise ValueError("Task server inventory differs from registration")
    for model, context in MODELS:
        for ordinal in (1, 2):
            body = {"model": model, "stream": False, "think": False,
                    "messages": [{"role": "user", "content":
                                  f"Reply with PRME_LOAD_PROBE_{ordinal}."}],
                    "options": {"num_ctx": context, "num_predict": 32,
                                "temperature": 0, "seed": 42}}
            attempt = {"model": model, "ordinal": ordinal,
                       "requested_context": context,
                       "request_sha256": digest(json.dumps(body, sort_keys=True).encode()),
                       "started_at": datetime.now(timezone.utc).isoformat(),
                       "complete": False}
            report["attempts"].append(attempt)
            write(output, report)  # Preserve intent before any generation.
            start = time.monotonic()
            try:
                response = request(url, "/api/chat", body, timeout=90)
                attempt["wall_seconds"] = time.monotonic() - start
                attempt["response"] = response  # Authored public canary only.
                if (response.get("model") != model or response.get("done") is not True
                        or response.get("done_reason") != "stop"
                        or not response.get("message", {}).get("content", "").strip()):
                    raise ValueError("Incomplete or unexpected canary response")
                matches = [m for m in request(url, "/api/ps")["models"]
                           if m["name"] == model]
                if len(matches) != 1:
                    raise ValueError("Requested model is not uniquely loaded")
                loaded = {k: matches[0].get(k) for k in
                          ("name", "digest", "context_length", "size", "size_vram")}
                attempt["loaded"] = loaded
                if (loaded["digest"] != plan["inventory"]["models"][model]
                        or loaded["context_length"] != context):
                    raise ValueError("Loaded model identity or context differs")
                attempt["complete"] = True
            except Exception as exc:
                attempt["wall_seconds"] = time.monotonic() - start
                attempt["error_type"] = type(exc).__name__
                if isinstance(exc, urllib.error.HTTPError):
                    attempt["http_status"] = exc.code
                raise
            finally:
                write(output, report)
            print(f"Completed loading probe {len(report['attempts'])}/6: {model}", flush=True)
    if inventory(url) != plan["inventory"]:
        raise ValueError("Model inventory changed during loading probe")
    report["complete"] = True


def run(args):
    if args.output.exists() or args.log.exists():
        raise ValueError("Fresh output and server log required; no resume")
    raw = args.plan.read_bytes()
    plan = json.loads(raw)
    if (plan["module_sha256"] != digest(Path(__file__).read_bytes())
            or plan["binary_sha256"] != digest(Path(args.binary).read_bytes())
            or plan["server_options"] != SERVER_OPTIONS
            or plan["models"] != [list(pair) for pair in MODELS]
            or plan["request_timeout_seconds"] != 90):
        raise ValueError("Probe implementation or policy changed after registration")
    report = {"complete": False, "attempts": [], "plan_sha256": digest(raw),
              "started_at": datetime.now(timezone.utc).isoformat(),
              "limits": ["Six short canaries do not prove long benchmark reliability.",
                         "Task server shares host hardware with other processes.",
                         "No benchmark answers, quality judgments or response retries."]}
    if plan["registered_at"] >= report["started_at"]:
        raise ValueError("Registration must predate execution")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    env = {**os.environ, **SERVER_OPTIONS, "OLLAMA_HOST": f"127.0.0.1:{port}"}
    with args.log.open("xb") as log:
        server = subprocess.Popen([args.binary, "serve"], env=env,
                                  stdout=log, stderr=subprocess.STDOUT)
        report["owned_server_pid"] = server.pid
        report["owned_loopback_port"] = port
        write(args.output, report)
        try:
            url = f"http://127.0.0.1:{port}"
            ready = time.monotonic() + 20
            while True:
                if server.poll() is not None:
                    raise RuntimeError("Task-owned server exited before readiness")
                try:
                    request(url, "/api/version", timeout=1)
                    break
                except (OSError, ValueError):
                    if time.monotonic() >= ready:
                        raise TimeoutError("Task-owned server readiness")
                    time.sleep(.1)
            probe(url, plan, report, args.output)
        except Exception as exc:
            report["error_type"] = type(exc).__name__
            raise
        finally:
            server.terminate()
            try:
                report["owned_server_exit_code"] = server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                report["owned_server_exit_code"] = server.wait(timeout=5)
            write(args.output, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("register", "run"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--binary", default="/usr/local/bin/ollama")
    args = parser.parse_args()
    if args.mode == "register":
        if args.plan.exists():
            raise ValueError("Fresh registration required")
        write(args.plan, {"registered_at": datetime.now(timezone.utc).isoformat(),
                          "module_sha256": digest(Path(__file__).read_bytes()),
                          "binary_sha256": digest(Path(args.binary).read_bytes()),
                          "server_options": SERVER_OPTIONS, "models": MODELS,
                          "request_timeout_seconds": 90,
                          "inventory": inventory("http://127.0.0.1:11434")})
    else:
        if args.output is None or args.log is None:
            raise ValueError("Run requires fresh output and server log")
        run(args)


if __name__ == "__main__":
    main()
