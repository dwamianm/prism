"""Run a fresh frozen reader chain on a task-owned, single-model Ollama server."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import time

from benchmarks.diagnostics import ollama_load_probe as service

CHILD_NETWORK = {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}


def identity(args):
    return {
        "module_sha256": service.digest(Path(__file__).read_bytes()),
        "service_helper_sha256": service.digest(Path(service.__file__).read_bytes()),
        "binary_sha256": service.digest(Path(args.binary).read_bytes()),
        "python_sha256": service.digest(Path(args.python).read_bytes()),
        "reader_registration_sha256": service.digest(args.registration.read_bytes()),
        "checkout_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=args.checkout, text=True).strip(),
        "checkout_dirty": bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=args.checkout, text=True).strip()),
        "server_options": service.SERVER_OPTIONS,
        "child_network": CHILD_NETWORK,
    }


def stop(process):
    if process.poll() is None:
        process.terminate()
    try:
        return process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.wait(timeout=5)


def run(args):
    plan = json.loads(args.plan.read_bytes())
    if plan["identity"] != identity(args) or plan["identity"]["checkout_dirty"]:
        raise ValueError("Registered execution identity changed")
    if plan["registered_at"] >= datetime.now(timezone.utc).isoformat():
        raise ValueError("Registration must predate execution")
    for path in (args.output, args.server_log, args.child_log):
        if path.exists():
            raise ValueError("Fresh execution artifacts required")
    result = {"complete": False, "plan_sha256": service.digest(args.plan.read_bytes()),
              "started_at": datetime.now(timezone.utc).isoformat(), "identity": identity(args)}
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    env = {**os.environ, **service.SERVER_OPTIONS, "OLLAMA_HOST": f"127.0.0.1:{port}"}
    child = None
    with args.server_log.open("xb") as log:
        server = subprocess.Popen([args.binary, "serve"], env=env, stdout=log, stderr=subprocess.STDOUT)
        result.update(owned_server_pid=server.pid, owned_loopback_port=port)
        service.write(args.output, result)
        try:
            url = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 20
            while True:
                if server.poll() is not None:
                    raise RuntimeError("Task-owned server exited before readiness")
                try:
                    service.request(url, "/api/version", timeout=1)
                    break
                except (OSError, ValueError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Task-owned server readiness")
                    time.sleep(.1)
            if service.inventory(url) != plan["inventory"]:
                raise ValueError("Task server inventory changed")
            command = [args.python, "-m", "benchmarks.diagnostics.packing_head_reader", "run",
                       "--root", str(args.root), "--directory", str(args.directory),
                       "--registration", str(args.registration), "--base-url", url]
            with args.child_log.open("xb") as child_log:
                child = subprocess.Popen(command, cwd=args.checkout,
                                         env={**os.environ, **CHILD_NETWORK, "PYTHONPATH":
                                              str(args.checkout / "src") + os.pathsep + str(args.checkout)},
                                         stdout=child_log, stderr=subprocess.STDOUT)
                result["native_child_pid"] = child.pid
                service.write(args.output, result)
                result["native_child_exit_code"] = child.wait()
            if result["native_child_exit_code"] != 0:
                raise RuntimeError("Frozen reader chain failed; no retry")
            if identity(args) != plan["identity"]:
                raise ValueError("Execution identity changed during trial")
            result["complete"] = True
        except BaseException as exc:
            result["error_type"] = type(exc).__name__
            raise
        finally:
            if child is not None and child.poll() is None:
                result["native_child_exit_code"] = stop(child)
            result["owned_server_exit_code"] = stop(server)
            service.write(args.output, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("register", "run"))
    for name in ("root", "checkout", "directory", "registration", "plan", "output",
                 "server-log", "child-log"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--binary", default="/usr/local/bin/ollama")
    parser.add_argument("--python", required=True)
    args = parser.parse_args()
    if args.mode == "register":
        if args.plan.exists() or identity(args)["checkout_dirty"]:
            raise ValueError("Fresh plan and clean frozen reader checkout required")
        service.write(args.plan, {"registered_at": datetime.now(timezone.utc).isoformat(),
                                  "identity": identity(args),
                                  "inventory": service.inventory("http://127.0.0.1:11434")})
    else:
        run(args)


if __name__ == "__main__":
    main()
