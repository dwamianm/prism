"""A passing workflow cannot hide a failed native/interpreter shutdown."""

import sys

import pytest

from benchmarks.diagnostics._process import checked_report


@pytest.mark.parametrize("exit_code", [0, 134])
def test_diagnostic_success_requires_clean_process_exit(exit_code):
    code = "import pathlib, sys; pathlib.Path(sys.argv[-1]).write_text('{\"passed\": true}'); sys.exit(int(sys.argv[1]))"
    report = checked_report([sys.executable, "-c", code, str(exit_code)], timeout=5)
    assert report["workflow_assertions_passed"]
    assert report["process_exit_code"] == exit_code
    assert report["passed"] == (exit_code == 0)


def test_missing_report_and_timeout_cannot_pass():
    assert not checked_report([sys.executable, "-c", "pass"], timeout=5)["passed"]
    report = checked_report([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.1)
    assert report["passed"] is False and report["error_type"] == "TimeoutExpired"
