"""Run frozen judging and scoring once, recording actual child-process exits.

This outer driver does not change prompts, model settings, inputs or scoring.
Run with the registered interpreter and PYTHONPATH pointing at the frozen
reader/judge/scorer checkout. Nonzero stages stop the chain without retrying.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def run_stage(command, *, report, completion, log):
    for path in (report, completion, log):
        if path.exists():
            raise ValueError("Refusing to overwrite stage artifact: " + str(path))
    with log.open("xb") as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT)
    record = {
        "native_exit_code": result.returncode,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest()
        if report.exists() else None,
        "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
        "observed_by": "public_context_finish subprocess.run after native child exit",
    }
    with completion.open("x") as stream:
        json.dump(record, stream, sort_keys=True)
        stream.write("\n")
    if result.returncode != 0 or record["report_sha256"] is None:
        raise RuntimeError("Stage failed or did not produce a report; downstream work stopped")
    return record


def finish(root):
    evidence = root / "benchmarks/results/research/2026-09-12"
    data = root / "data/benchmarks"
    common = {
        "inputs": data / "public-context-judge-dev-cff3b21-inputs.json",
        "plan": evidence / "public-context-judge-dev-plan.json",
        "controls": root / "benchmarks/fixtures/reader_judge_controls.json",
        "calibration": data / "public-context-judge-calibration-f511aaa.json",
        "state": data / "public-context-judge-dev-cff3b21-state.json",
    }
    report = data / "public-context-judge-dev-cff3b21.json"
    completion = evidence / "public-context-judge-dev-completion.json"
    scores = data / "public-context-scores-dev-cff3b21.json"
    scores_completion = evidence / "public-context-scores-dev-completion.json"
    judge_log = data / "public-context-judge-dev-cff3b21.log"
    score_log = data / "public-context-scores-dev-cff3b21.log"
    # Do not launch a model if any downstream destination or prior state exists.
    for path in (common["state"], report, completion, scores, scores_completion,
                 judge_log, score_log):
        if path.exists():
            raise ValueError("Fresh study destinations required: " + str(path))

    def command(module, paths):
        return [sys.executable, "-m", module, *[
            part for name, path in paths.items() for part in ("--" + name, str(path))
        ]]

    run_stage(
        command("benchmarks.diagnostics.public_context_judge", {**common, "output": report}),
        report=report, completion=completion, log=judge_log,
    )
    print("Judge exited zero; starting complete independent artifact verification and scoring", flush=True)
    run_stage(
        command("benchmarks.diagnostics.public_context_scores", {
            **common,
            "readers": evidence / "public-reader-dev-artifacts.json",
            "references": data / "hindsight-prme-dev-references.json",
            "registration": evidence / "hindsight-prme-reader-registration.json",
            "neutral-inputs": data / "hindsight-prme-dev-normalized-inputs.json",
            "mapping": data / "public-context-judge-dev-cff3b21-mapping.json",
            "report": report, "completion": completion,
            "capture-analysis": data / "hindsight-prme-native-dev-2f3ac2e-analysis.json",
            "capture-completion": evidence / "hindsight-prme-native-dev-analysis-completion.json",
            "output": scores,
        }),
        report=scores, completion=scores_completion, log=score_log,
    )
    print("Frozen judging and scoring completed with native exit zero", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    finish(args.root.resolve())


if __name__ == "__main__":
    main()
