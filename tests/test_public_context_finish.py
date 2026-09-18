"""Process completion, not an optimistic result field, gates scoring."""

import hashlib
import json
import sys

import pytest

from benchmarks.diagnostics.public_context_finish import run_stage


@pytest.mark.parametrize("exit_code", [0, 1])
def test_stage_records_native_exit_even_if_report_claims_success(tmp_path, exit_code):
    report = tmp_path / "report.json"
    completion = tmp_path / "completion.json"
    log = tmp_path / "log"
    code = "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('{\"passed\":true}'); sys.exit(int(sys.argv[2]))"
    command = [sys.executable, "-c", code, str(report), str(exit_code)]
    if exit_code:
        with pytest.raises(RuntimeError, match="downstream work stopped"):
            run_stage(command, report=report, completion=completion, log=log)
    else:
        run_stage(command, report=report, completion=completion, log=log)
    saved = json.loads(completion.read_bytes())
    assert saved["native_exit_code"] == exit_code
    assert saved["report_sha256"] == hashlib.sha256(report.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="overwrite"):
        run_stage(command, report=report, completion=completion, log=log)


def test_native_zero_without_report_is_not_success(tmp_path):
    with pytest.raises(RuntimeError, match="did not produce a report"):
        run_stage([sys.executable, "-c", "pass"], report=tmp_path / "report.json",
                  completion=tmp_path / "completion.json", log=tmp_path / "log")
    assert json.loads((tmp_path / "completion.json").read_bytes())["report_sha256"] is None
