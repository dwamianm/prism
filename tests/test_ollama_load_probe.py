import json

import pytest

from benchmarks.diagnostics import ollama_load_probe as probe


@pytest.mark.parametrize("failure", ["context", "timeout"])
def test_failed_probe_retains_attempt_and_stops_without_retry(tmp_path, monkeypatch, failure):
    model, context = probe.MODELS[0]
    identity = {"version": "authored", "models": {model: "digest"}}
    monkeypatch.setattr(probe, "inventory", lambda url: identity)
    calls = []

    def request(url, route, body=None, *, timeout=5):
        calls.append(route)
        if route == "/api/chat":
            if failure == "timeout":
                raise TimeoutError("Do not retain this provider error text")
            return {"model": model, "done": True, "done_reason": "stop",
                    "message": {"content": "authored response"}}
        return {"models": [{"name": model, "digest": "digest", "context_length": context // 2}]}

    monkeypatch.setattr(probe, "request", request)
    output = tmp_path / "probe.json"
    record = {"complete": False, "attempts": []}
    with pytest.raises((ValueError, TimeoutError)):
        probe.probe("authored", {"inventory": identity}, record, output)
    saved = json.loads(output.read_bytes())
    assert not saved["complete"] and len(saved["attempts"]) == 1
    assert not saved["attempts"][0]["complete"]
    assert saved["attempts"][0]["error_type"] in {"ValueError", "TimeoutError"}
    assert calls.count("/api/chat") == 1
    assert "Do not retain" not in output.read_text()
