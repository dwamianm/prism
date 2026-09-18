"""Include interpreter/native shutdown in a diagnostic's success boundary."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile


def checked_report(command: list[str], *, timeout: float) -> dict:
    """Run a worker that accepts --output; publish success only after exit zero."""
    with tempfile.TemporaryDirectory(prefix="prme-diagnostic-worker-") as directory:
        output = Path(directory) / "report.json"
        try:
            child = subprocess.run([*command, "--output", str(output)], capture_output=True, timeout=timeout)
            exit_code, error = child.returncode, None
        except subprocess.TimeoutExpired:
            exit_code, error = None, "TimeoutExpired"
        try:
            report = json.loads(output.read_text())
        except (OSError, ValueError):
            report = {"passed": False, "error_type": "MissingWorkerReport"}
        report["workflow_assertions_passed"] = report.get("passed") is True
        report["process_exit_code"] = exit_code
        report["passed"] = report["workflow_assertions_passed"] and exit_code == 0
        if not report["passed"]:
            report["error_type"] = error or report.get("error_type", "WorkerExitError")
            report["limits"] = "Incomplete end-to-end process; no success or accuracy claim."
        return report


def run_diagnostic(module: str, args) -> dict:
    command = [
        sys.executable,
        "-m",
        module,
        "--worker",
        "--model",
        args.model,
        "--base-url",
        args.base_url,
        "--timeout",
        str(args.timeout),
    ]
    if provider := getattr(args, "provider", None):
        command[4:4] = ["--provider", provider]
    return checked_report(
        command,
        timeout=max(60, args.timeout * 6 + 60),
    )
