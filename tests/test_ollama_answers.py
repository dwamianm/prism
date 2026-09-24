import asyncio
import json

import httpx
import pytest

from benchmarks.integrations import ollama_answers as ollama
from benchmarks.integrations.gpt54_budget import JUDGE_LIMIT, READER_LIMIT, sha

MODEL = ollama.AnswerModel()


def completion(text="The answer.", *, model="deepseek-v4.1-flash", finish="stop", usage=None):
    return {"object": "chat.completion", "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}],
            "usage": usage or {"prompt_tokens": 120, "completion_tokens": 8,
                               "prompt_tokens_details": {"cached_tokens": 20}}}


def mock_client(*responses):
    """A client for MODEL's endpoint that answers each request with the next response and records it."""
    requests = []
    queue = list(responses)

    def handler(request):
        requests.append(request)
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    return httpx.AsyncClient(base_url=MODEL.endpoint, transport=httpx.MockTransport(handler)), requests


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    async def instant(_seconds):
        return None
    monkeypatch.setattr(ollama.asyncio, "sleep", instant)


@pytest.mark.parametrize("endpoint", [
    "https://api.openai.com/v1", "http://api.openai.com/v1", "http://10.0.0.5:11434/v1",
    "http://localhost:11434/v1", "http://127.0.0.2:11434/v1", "http://0.0.0.0:11434/v1",
    "http://127.0.0.1:11434", "http://127.0.0.1:11434/api", "http://user:secret@127.0.0.1:11434/v1",
    "http://127.0.0.1:11434/v1?key=secret", "https://127.0.0.1:11434/v1",
])
def test_only_a_loopback_ollama_endpoint_can_receive_prompts(endpoint):
    with pytest.raises(ValueError, match="loopback"):
        ollama.AnswerModel(endpoint=endpoint)


@pytest.mark.parametrize("endpoint", ["http://127.0.0.1:11434/v1", "http://127.0.0.1:11434/v1/",
                                      "http://[::1]:8080/v1"])
def test_loopback_endpoints_are_accepted(endpoint):
    assert ollama.AnswerModel(endpoint=endpoint).endpoint == endpoint


def test_client_targets_the_loopback_endpoint_without_credentials_or_proxies():
    client = ollama.client_for(MODEL)
    assert str(client.base_url) == "http://127.0.0.1:11434/v1/"
    assert "authorization" not in client.headers and client.trust_env is False


def test_requests_and_settings_record_model_endpoint_and_sampling():
    assert MODEL.model == "deepseek-v4.1-flash:cloud" and MODEL.track == "ollama-deepseek-v4.1-flash-cloud"
    assert MODEL.body("Question?", READER_LIMIT) == {
        "model": "deepseek-v4.1-flash:cloud", "messages": [{"role": "user", "content": "Question?"}],
        "temperature": 0, "seed": 20260923, "max_tokens": READER_LIMIT, "reasoning_effort": "none", "stream": False}
    assert MODEL.settings() == {
        "provider": "ollama", "api": "chat.completions", "endpoint": "http://127.0.0.1:11434/v1",
        "model": "deepseek-v4.1-flash:cloud", "temperature": 0, "seed": 20260923, "reasoning_effort": "none",
        "stream": False, "reader_limit": READER_LIMIT, "judge_limit": JUDGE_LIMIT, "attempts": 4,
        "timeout_seconds": 900}
    with pytest.raises(ValueError, match="nonempty"):
        ollama.AnswerModel(model="deepseek v4")


def test_response_text_takes_only_a_complete_answer_from_the_requested_model():
    assert ollama.response_text(completion("  yes \n"), MODEL) == "yes"
    assert ollama.response_text(completion(model="deepseek-v4.1-flash:cloud"), MODEL) == "The answer."
    bad = [completion(finish="length"), completion(model="qwen3.5:9b"), completion(""),
           completion(usage={"prompt_tokens": 1}), {**completion(), "object": "chat.completion.chunk"},
           {**completion(), "choices": completion()["choices"] * 2}, {**completion(), "choices": ["text"]},
           completion(usage={"prompt_tokens": 5, "completion_tokens": 1, "prompt_tokens_details": {"cached_tokens": None}}),
           completion(usage={"prompt_tokens": 5, "completion_tokens": 1, "prompt_tokens_details": {"cached_tokens": 6}})]
    for body in bad:
        with pytest.raises(ValueError):
            ollama.response_text(body, MODEL)


def test_usage_uses_the_baseline_token_names():
    assert ollama.usage(completion()) == {"input_tokens": 120, "cached_input_tokens": 20, "output_tokens": 8}
    assert ollama.usage(completion(usage={"prompt_tokens": 5, "completion_tokens": 2}))["cached_input_tokens"] == 0


async def test_call_retries_transient_errors_keeps_every_attempt_and_verifies(tmp_path):
    client, requests = mock_client(httpx.Response(503, json={"error": "busy"}), httpx.Response(200, json=completion()))
    path = tmp_path / "reader.json"
    async with client:
        result = await ollama.call(client, asyncio.Semaphore(1), MODEL, "Question?", READER_LIMIT, path)
    assert [request.url.path for request in requests] == ["/v1/chat/completions"] * 2
    assert all("authorization" not in request.headers for request in requests)
    assert result["text"] == "The answer." and result["attempts"] == 2 and result["endpoint"] == MODEL.endpoint
    body = MODEL.body("Question?", READER_LIMIT)
    assert json.loads(path.with_suffix(".request.json").read_text()) == body
    first = json.loads(path.with_suffix(".attempt-1.json").read_text())
    assert first["http_status"] == 503 and first["request_sha256"] == sha(body)
    verified = ollama.verify_call(path, "Question?", READER_LIMIT, model=MODEL)
    assert verified["verified_http_statuses"] == ["503", "200"]
    with pytest.raises(ValueError, match="differs"):
        ollama.verify_call(path, "Another question?", READER_LIMIT, model=MODEL)
    with pytest.raises(ValueError, match="differs"):
        ollama.verify_call(path, "Question?", READER_LIMIT, model=ollama.AnswerModel(seed=1))
    with pytest.raises(FileExistsError):  # Records are written once and never replaced.
        await ollama.call(client, asyncio.Semaphore(1), MODEL, "Question?", READER_LIMIT, path)


@pytest.mark.parametrize("reply,error,message", [
    (httpx.ConnectError("refused"), RuntimeError, "Ambiguous provider failure"),
    (httpx.Response(404, json={"error": "model not found"}), RuntimeError, "Provider HTTP 404"),
    (httpx.Response(200, text="not json"), ValueError, "Malformed provider response"),
    (httpx.Response(200, json=["not", "an", "object"]), ValueError, "Malformed provider response"),
])
async def test_call_failures_are_retained_and_named_like_the_gpt54_path(tmp_path, reply, error, message):
    client, requests = mock_client(reply)
    path = tmp_path / "judge.json"
    async with client:
        with pytest.raises(error, match=message):
            await ollama.call(client, asyncio.Semaphore(1), MODEL, "Correct?", JUDGE_LIMIT, path)
    assert len(requests) == 1 and path.with_suffix(".attempt-1.json").exists() and not path.exists()


async def test_a_transient_error_without_a_json_body_is_retried_and_kept(tmp_path):
    client, requests = mock_client(httpx.Response(502, text="<html>Bad gateway</html>"),
                                   httpx.Response(200, json=completion()))
    path = tmp_path / "reader.json"
    async with client:
        result = await ollama.call(client, asyncio.Semaphore(1), MODEL, "Question?", READER_LIMIT, path)
    first = json.loads(path.with_suffix(".attempt-1.json").read_text())
    assert result["attempts"] == 2 and first["response"] is None and "Bad gateway" in first["text"]
    assert ollama.verify_call(path, "Question?", READER_LIMIT, model=MODEL)["verified_http_statuses"] == ["502", "200"]


async def test_call_gives_up_after_four_transient_attempts(tmp_path):
    client, requests = mock_client(*[httpx.Response(429, json={"error": "usage limit"})] * 4)
    async with client:
        with pytest.raises(RuntimeError, match="Provider HTTP 429"):
            await ollama.call(client, asyncio.Semaphore(1), MODEL, "Correct?", JUDGE_LIMIT, tmp_path / "judge.json")
    assert len(requests) == ollama.ATTEMPTS


async def test_a_truncated_answer_is_final_and_not_retried(tmp_path):
    client, requests = mock_client(httpx.Response(200, json=completion(finish="length")))
    async with client:
        with pytest.raises(ValueError, match="Incomplete"):
            await ollama.call(client, asyncio.Semaphore(1), MODEL, "Question?", READER_LIMIT,
                              tmp_path / "reader.json")
    assert len(requests) == 1


async def test_a_truncated_answer_is_recorded_as_truncated_only_for_a_caller_that_takes_it(tmp_path):
    client, requests = mock_client(httpx.Response(503, json={"error": "busy"}),
                                   httpx.Response(200, json=completion("Step 1. Step 1.", finish="length")))
    path = tmp_path / "reader.json"
    async with client:
        result = await ollama.call(client, asyncio.Semaphore(1), MODEL, "Question?", READER_LIMIT, path,
                                   truncated_ok=True)
    # A truncated response is still one request: HTTP attempts are retried only for transient statuses.
    assert len(requests) == 2 and result["attempts"] == 2
    assert result["truncated"] is True and result["text"] == "Step 1. Step 1."
    assert json.loads(path.read_text()) == result
    verified = ollama.verify_call(path, "Question?", READER_LIMIT, model=MODEL, truncated_ok=True)
    assert verified["verified_http_statuses"] == ["503", "200"]
    # A verifier that does not take truncated answers refuses the record.
    with pytest.raises(ValueError, match="Incomplete"):
        ollama.verify_call(path, "Question?", READER_LIMIT, model=MODEL)
    # The truncated mark must be what the response gives: a whole answer is never recorded as truncated, and a
    # truncated one never passes as whole.
    value = json.loads(path.read_text())
    for tampered in ({key: item for key, item in value.items() if key != "truncated"}, {**value, "truncated": 1}):
        path.write_text(json.dumps(tampered))
        with pytest.raises(ValueError, match="binding differs"):
            ollama.verify_call(path, "Question?", READER_LIMIT, model=MODEL, truncated_ok=True)
    client, _ = mock_client(httpx.Response(200, json=completion("yes")))
    whole = tmp_path / "judge.json"
    async with client:
        answer = await ollama.call(client, asyncio.Semaphore(1), MODEL, "Correct?", JUDGE_LIMIT, whole,
                                   truncated_ok=True)
    assert "truncated" not in answer and answer["text"] == "yes"
    for mark in (True, 1, False):
        whole.write_text(json.dumps({**answer, "truncated": mark}))
        with pytest.raises(ValueError, match="binding differs"):
            ollama.verify_call(whole, "Correct?", JUDGE_LIMIT, model=MODEL, truncated_ok=True)


async def test_an_empty_answer_is_recorded_only_for_a_caller_that_takes_it(tmp_path):
    client, _ = mock_client(httpx.Response(200, json=completion("  ")), httpx.Response(200, json=completion(None)))
    async with client:
        with pytest.raises(ValueError, match="Missing answer text"):
            await ollama.call(client, asyncio.Semaphore(1), MODEL, "Correct?", JUDGE_LIMIT, tmp_path / "judge.json")
        empty = await ollama.call(client, asyncio.Semaphore(1), MODEL, "Correct?", JUDGE_LIMIT,
                                  tmp_path / "judge-retry.json", empty_ok=True)
    assert empty["text"] == "" and "truncated" not in empty
    assert ollama.verify_call(tmp_path / "judge-retry.json", "Correct?", JUDGE_LIMIT, model=MODEL,
                              empty_ok=True)["text"] == ""
    with pytest.raises(ValueError, match="Missing answer text"):
        ollama.verify_call(tmp_path / "judge-retry.json", "Correct?", JUDGE_LIMIT, model=MODEL)


def test_answer_record_is_what_a_response_answered():
    assert ollama.answer_record(completion("  Step 1.\n", finish="length"), MODEL, truncated_ok=True) == {
        "text": "Step 1.", "truncated": True}
    # A response cut off before any text still ended early, with nothing said.
    assert ollama.answer_record(completion(None, finish="length"), MODEL, truncated_ok=True) == {
        "text": "", "truncated": True}
    assert ollama.answer_record(completion(" yes "), MODEL, truncated_ok=True, empty_ok=True) == {"text": "yes"}
    for body in (completion(model="qwen3.5:9b", finish="length"), completion(finish="length", usage={"prompt_tokens": 1}),
                 completion(finish=None), completion(["a", "list"], finish="length")):
        with pytest.raises(ValueError):
            ollama.answer_record(body, MODEL, truncated_ok=True, empty_ok=True)
    # A response without a finish reason is malformed, not truncated.
    with pytest.raises(ValueError, match="Incomplete"):
        ollama.response_text(completion(finish=None), MODEL)


def _edit(path, change):
    value = json.loads(path.read_text())
    change(value)
    path.write_text(json.dumps(value))


@pytest.mark.parametrize("tamper", [
    lambda path: _edit(path, lambda value: value.update(text="Another answer.")),
    lambda path: _edit(path, lambda value: value.update(endpoint="http://127.0.0.1:9999/v1")),
    lambda path: _edit(path, lambda value: value.update(attempts=5)),
    lambda path: _edit(path.with_suffix(".attempt-1.json"), lambda value: value.update(request_sha256="0" * 64)),
    lambda path: _edit(path.with_suffix(".attempt-1.json"), lambda value: value.update(endpoint="http://x/v1")),
    lambda path: _edit(path.with_suffix(".attempt-1.json"), lambda value: value.update(http_status=400)),
    lambda path: _edit(path.with_suffix(".attempt-2.json"), lambda value: value.update(http_status=503)),
    lambda path: _edit(path.with_suffix(".attempt-2.json"), lambda value: value["response"].update(model="other")),
    lambda path: _edit(path.with_suffix(".request.json"), lambda value: value.update(seed=1)),
])
async def test_verify_call_rejects_every_changed_record(tmp_path, tamper):
    client, _ = mock_client(httpx.Response(503, json={"error": "busy"}), httpx.Response(200, json=completion()))
    path = tmp_path / "reader.json"
    async with client:
        await ollama.call(client, asyncio.Semaphore(1), MODEL, "Question?", READER_LIMIT, path)
    ollama.verify_call(path, "Question?", READER_LIMIT, model=MODEL)
    tamper(path)
    with pytest.raises(ValueError):
        ollama.verify_call(path, "Question?", READER_LIMIT, model=MODEL)


def test_describe_accepts_only_cloud_models(monkeypatch):
    monkeypatch.setattr(ollama, "identity", lambda model: {"provider": "ollama_cloud", "model": model.model})
    assert ollama.describe(MODEL) == {**MODEL.settings(), "identity": {"provider": "ollama_cloud",
                                                                        "model": MODEL.model}}
    monkeypatch.setattr(ollama, "identity", lambda model: {"provider": "ollama", "model": model.model})
    with pytest.raises(ValueError, match="not an Ollama cloud model"):
        ollama.describe(MODEL)


def test_same_model_ignores_the_server_version_but_not_the_model_or_settings():
    identity = {"provider": "ollama_cloud", "model": MODEL.model, "manifest_digest_sha256": "a" * 64,
                "remote_model": "deepseek-v4.1-flash", "server_version": "0.34.3"}
    recorded = {**MODEL.settings(), "identity": identity}
    assert ollama.same_model(recorded, {**recorded, "identity": {**identity, "server_version": "0.35.0"}})
    assert not ollama.same_model(recorded, {**recorded, "identity": {**identity, "manifest_digest_sha256": "b" * 64}})
    assert not ollama.same_model(recorded, {**recorded, "seed": 1})


def test_identity_asks_the_server_at_the_endpoint_origin(monkeypatch):
    seen = []
    monkeypatch.setattr(ollama, "_ollama_reader_identity", lambda origin, model: seen.append((origin, model)) or {})
    ollama.identity(MODEL)
    assert seen == [("http://127.0.0.1:11434", "deepseek-v4.1-flash:cloud")]
