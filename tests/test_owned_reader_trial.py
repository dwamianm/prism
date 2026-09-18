import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from benchmarks.diagnostics import owned_reader_trial as trial


def test_failed_native_child_is_retained_and_owned_server_reaped(tmp_path, monkeypatch):
    args = SimpleNamespace(
        output=tmp_path / "output.json", plan=tmp_path / "plan.json",
        server_log=tmp_path / "server.log", child_log=tmp_path / "child.log",
        binary="authored-server", python=sys.executable, checkout=tmp_path,
        root=tmp_path, directory=tmp_path / "data", registration=tmp_path / "registration.json",
    )
    identity = {"checkout_dirty": False}
    args.plan.write_text(json.dumps({"identity": identity, "inventory": {},
                                     "registered_at": "2000-01-01T00:00:00+00:00"}))
    monkeypatch.setattr(trial, "identity", lambda args: identity)
    monkeypatch.setattr(trial.service, "request", lambda *a, **kw: {})
    monkeypatch.setattr(trial.service, "inventory", lambda *a: {})
    native_popen = subprocess.Popen
    processes = []

    def launch(command, **kwargs):
        code = "import time; time.sleep(60)" if not processes else "raise SystemExit(3)"
        child = native_popen([sys.executable, "-c", code], **kwargs)
        processes.append(child)
        return child

    monkeypatch.setattr(trial.subprocess, "Popen", launch)
    try:
        with pytest.raises(RuntimeError, match="chain failed"):
            trial.run(args)
        assert len(processes) == 2
        assert processes[0].poll() is not None
        assert processes[1].poll() == 3
        saved = json.loads(Path(args.output).read_bytes())
        assert not saved["complete"] and saved["native_child_exit_code"] == 3
        assert saved["owned_server_exit_code"] == processes[0].returncode
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
