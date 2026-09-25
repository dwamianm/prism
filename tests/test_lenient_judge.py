"""The lenient second judge over saved LoCoMo answers (#96). No test reaches a real provider."""
import asyncio
import json
from pathlib import Path
import shutil

import httpx
import pytest

from benchmarks.integrations import gpt54_baselines as baselines
from benchmarks.integrations import gpt54_budget
from benchmarks.integrations import lenient_judge as lenient
from benchmarks.integrations import ollama_answers
from benchmarks.integrations import run_gpt54_comparison as study
from benchmarks.integrations.gpt54_budget import JUDGE_LIMIT, MODEL, digest, usage_cost
from prme.retrieval.tokenization import count_tokens
from tests import test_gpt54_baselines as answer_runs
from tests.test_gpt54_baselines import (
    IDENTITY, OLLAMA_MODEL, OLLAMA_USAGE, PER_CALL, USAGE, ollama_provider, ollama_published, run_ollama, text_sha256,
)

# The answer runs' fixtures: a registered comparison over tiny datasets, and private data kept under tmp_path.
harness = answer_runs.harness
private_data = answer_runs.private_data

CARO, PUPPY = "conv-1-q0000", "conv-1-q0001"
CORRECT = '{"label": "CORRECT"}'


def test_the_lenient_prompt_is_mem0s_locomo_judge_verbatim():
    # The hash of ACCURACY_PROMPT in Mem0's evaluation/metrics/llm_judge.py at the pinned commit.
    assert text_sha256(lenient.LENIENT_JUDGE) == lenient.LENIENT_JUDGE_SHA256
    assert lenient.LENIENT_JUDGE.startswith(
        "\nYour task is to label an answer to a question as ’CORRECT’ or ’WRONG’. You will be given the following "
        "data:\n    (1) a question (posed by one user to another user), \n    (2) a ’gold’ (ground truth) answer, \n")
    assert "you should be generous with your grading" in lenient.LENIENT_JUDGE
    assert "aae5989e78a6188b3b047c104d960c9ad0927e75" in lenient.LENIENT_JUDGE_SOURCE
    prompt = lenient.lenient_prompt({"question": "What did Caroline paint?", "answer": "a sunset"}, "A sunset {and}.")
    assert prompt.endswith("Question: What did Caroline paint?\nGold answer: a sunset\nGenerated answer: A sunset "
                           "{and}.\n\nFirst, provide a short (one sentence) explanation of your reasoning, then finish "
                           "with CORRECT or WRONG. \nDo NOT include both CORRECT and WRONG in your response, or it "
                           "will break the evaluation script.\n\nJust return the label CORRECT or WRONG in a json "
                           "format with the key as \"label\".\n")
    # Integer references, which LoCoMo has, are filled in as the strict judge fills them.
    assert "Gold answer: 2022\n" in lenient.lenient_prompt({"question": "When?", "answer": 2022}, "2022")


@pytest.mark.parametrize(("text", "verdict"), [
    (CORRECT, True),
    ('{\n  "label": "WRONG"\n}', False),
    ('It names pottery, one of the listed activities.\n\n{"label": "CORRECT"}', True),
    ('```json\n{"label": "correct"}\n```', True),
    ('{"reason": "same date", "label": "Wrong"}', False),
    ('{"reason": "it says {a sunset}", "label": "CORRECT"}', True),
    ('Uses {x} loosely. {"label": "WRONG"}', False),
    ('{"result": {"label": "CORRECT"}}', True),
    ('{"label": "CORRECT"} and again {"label": "CORRECT"}', True),
    ('{"label": "CORRECT"} {"label": "WRONG"}', None),
    ("The answer names the same date. CORRECT", None),
    ('{"label": "PARTIAL"}', None),
    ('{"label": true}', None),
    ('{"verdict": "CORRECT"}', None),
    ('{"label": "CORRECT"', None),
    ("", None),
])
def test_the_lenient_verdict_is_the_json_label_the_prompt_asks_for(text, verdict):
    assert lenient.lenient_verdict(text) is verdict


# DeepSeek through Ollama ----------------------------------------------------

def lenient_ollama(monkeypatch, *, replies: dict[str, list] | None = None, identities=None):
    """An Ollama server that answers only lenient judge prompts: CORRECT, or each question's queued replies.

    A queued reply given as ``(text, finish_reason)`` ends for that reason. A reader or strict judge prompt gets
    HTTP 400, and any use of the paid path fails the test.
    """
    requests = []
    queued = {key: list(value) for key, value in (replies or {}).items()}
    reported = list(identities or [IDENTITY])

    def handler(request):
        body = json.loads(request.content)
        prompt = body["messages"][0]["content"]
        requests.append(body)
        if not prompt.startswith(lenient.LENIENT_JUDGE[:60]):
            return httpx.Response(400, json={"error": "only lenient judge prompts are expected"})
        key = next((key for key in queued if key in prompt), None)
        text, finish = CORRECT, "stop"
        if key is not None and queued[key]:
            reply = queued[key].pop(0)
            text, finish = reply if isinstance(reply, tuple) else (reply, "stop")
        return httpx.Response(200, json={
            "object": "chat.completion", "model": "deepseek-v4.1-flash", "usage": OLLAMA_USAGE,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}]})

    def paid(*args, **kwargs):
        pytest.fail("built or used the paid OpenAI path")

    for name in ("client_for", "call", "_api_key", "_ledger"):
        monkeypatch.setattr(baselines, name, paid)
    for name in ("client_for", "call"):
        monkeypatch.setattr(gpt54_budget, name, paid)
    monkeypatch.setattr("dotenv.dotenv_values", paid)
    monkeypatch.setattr(ollama_answers, "identity",
                        lambda model: dict(reported.pop(0) if len(reported) > 1 else reported[0]))
    monkeypatch.setattr(ollama_answers, "client_for", lambda model: httpx.AsyncClient(
        base_url=model.endpoint, transport=httpx.MockTransport(handler)))
    return requests


async def deepseek_source(harness, monkeypatch) -> Path:
    """A complete DeepSeek full-context run: Caroline's question answered right, the puppy question wrong."""
    baselines.prepare("full-context", "locomo", data=harness["data"])
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    ollama_provider(monkeypatch, wrong_when="What is the puppy called?")
    await run_ollama(harness)
    [path] = ollama_published(harness)
    return path


def registered_policy_copy(path: Path) -> Path:
    """The same run as published before the track's amended policy (#132): no policy, no outcomes or retries.

    That is how the DeepSeek baseline this pass grades was published.
    """
    result = json.loads(path.read_text())
    result["answer_model"].pop("failure_policy")
    result.pop("failure_policy")
    for row in result["rows"]:
        for key in ("outcome", "reader_retry_sha256", "judge_retry_sha256", "verdict_normalized"):
            row.pop(key)
    copy = path.with_name(path.name.replace("full-context", "full-context-registered"))
    copy.write_text(json.dumps(result))
    return copy


def lenient_run(harness, source, **kwargs):
    return lenient.run(source, data=harness["data"], results=harness["results"], **kwargs)


def lenient_published(harness, name):
    return list(harness["results"].glob(f"*/{name.removesuffix('-result.json')}-lenient-judge-result.json"))


def run_log(harness, name="full-context") -> list[dict]:
    return baselines._run_events(baselines._run_log_path(harness["data"], f"lenient-judge-{name}", "locomo"))


def answer_files(harness) -> dict[str, str]:
    """Every file of the full-context answer run with its hash, to show the pass writes nothing there."""
    folder = harness["data"] / "full-context" / "locomo"
    return {str(path.relative_to(folder)): digest(path) for path in folder.rglob("*") if path.is_file()
            and path.name != "run.lock"}


async def test_a_deepseek_run_is_graded_again_by_its_own_judge_beside_the_strict_score(harness, monkeypatch):
    path = await deepseek_source(harness, monkeypatch)
    before = answer_files(harness)
    requests = lenient_ollama(monkeypatch)
    source = lenient.load_source(path, data=harness["data"])
    assert source.name == "full-context" and source.model == OLLAMA_MODEL and not source.paid
    assert source.answers[PUPPY]["text"] == baselines.AUTHORED_WRONG_ANSWER and not source.answers[PUPPY]["strict"]
    with pytest.raises(ValueError, match="no spending cap"):
        await lenient_run(harness, source, max_usd=5)
    result = await lenient_run(harness, source)
    # One judge call per saved answer, with the reader's text and the reference, and no reader call.
    assert len(requests) == 2 and all(body == OLLAMA_MODEL.body(body["messages"][0]["content"], JUDGE_LIMIT)
                                      for body in requests)
    questions = {question["question_id"]: question for question in study.question_rows("locomo")}
    assert sorted(body["messages"][0]["content"] for body in requests) == sorted([
        lenient.lenient_prompt(questions[CARO], "The answer."),
        lenient.lenient_prompt(questions[PUPPY], baselines.AUTHORED_WRONG_ANSWER)])
    assert result["complete"] and result["kind"] == "lenient-judge-result" and result["model"] == OLLAMA_MODEL.model
    assert result["registration_sha256"] == digest(study.REG)
    scores = result["scores"]
    assert {key: scores[key] for key in ("total", "strict_correct", "lenient_correct", "strict_accuracy",
                                         "lenient_accuracy", "accepted_only_by_lenient", "accepted_only_by_strict")} == {
        "total": 2, "strict_correct": 1, "lenient_correct": 2, "strict_accuracy": .5, "lenient_accuracy": 1.0,
        "accepted_only_by_lenient": 1, "accepted_only_by_strict": 0}
    assert scores["strict_ci95_questions"] == study.confidence([{"correct": True}, {"correct": False}])
    assert scores["lenient_ci95_questions"] == [1.0, 1.0]
    # Each category under its upstream LoCoMo number, in that order.
    assert [(row["category"], row["name"], row["strict_correct"], row["lenient_correct"])
            for row in result["categories"]] == [(1, "multi-hop", 0, 1), (4, "single-hop", 1, 1)]
    assert [(row["question_id"], row["category"], row["strict_correct"], row["strict_outcome"], row["correct"],
             row["outcome"]) for row in result["rows"]] == [
        (CARO, 4, True, "judged", True, "judged"), (PUPPY, 1, False, "judged", True, "judged")]
    assert result["source"] == {"name": "full-context", "path": str(path.relative_to(harness["results"])),
                                "sha256": digest(path), "kind": "ollama-answer-result",
                                "failure_policy": baselines.FAILURE_POLICY, "server_versions": ["0.34.3"]}
    assert result["judges"]["lenient"]["prompt_sha256"] == lenient.LENIENT_JUDGE_SHA256
    assert result["judges"]["strict"]["prompt_sha256"] == text_sha256(study.LOCO_JUDGE)
    assert result["answer_model"] == {**OLLAMA_MODEL.settings(), "identity": IDENTITY,
                                      "failure_policy": lenient.FAILURE_POLICY}
    assert result["calibration"]["strict_judge"]["attempt"] == "attempt-1"
    assert result["calibration"]["lenient_prompt"] is None
    assert result["server_versions"] == ["0.34.3"] and result["cost"]["usd"] == 0
    assert result["failure_policy"] == {"id": lenient.FAILURE_POLICY, "judge_retries": 0, "truncated": 0,
                                        "verdict_unresolved": 0, "unscored": 0, "unscored_limit_percent": 1,
                                        "within_limit": True}
    assert result["retry_policy"] == lenient.RETRY_POLICY
    tokens = result["provider_tokens"]
    assert (tokens["input_tokens"], tokens["successful_calls"], tokens["http_status_counts"]) == (200, 2, {"200": 2})
    [published] = lenient_published(harness, path.name)
    public = json.loads(published.read_text())
    assert public["rows"] == result["rows"] and public["run_log"]["runs_started"] == 1
    assert [event["event"] for event in run_log(harness)] == ["started", "finished"]
    # The receipts replay, and nothing is written next to the answer run's own records.
    folder = lenient.judge_folder(source, harness["data"])
    assert lenient.report(source, folder, None)["rows"] == result["rows"]
    assert answer_files(harness) == before
    with pytest.raises(ValueError, match="never rerun"):
        await lenient_run(harness, source)
    # Nor after its folder is moved aside: the pass's run log lives outside it.
    shutil.move(folder, folder.with_name("moved-aside"))
    with pytest.raises(ValueError, match="even after its folder is moved"):
        await lenient_run(harness, source)
    assert len(requests) == 2


async def test_a_source_published_under_the_registered_policy_is_graded(harness, monkeypatch):
    # prme@46647825, the DeepSeek baseline this pass grades, was answered before the amended policy existed.
    path = registered_policy_copy(await deepseek_source(harness, monkeypatch))
    lenient_ollama(monkeypatch)
    source = lenient.load_source(path, data=harness["data"])
    assert source.answers[CARO]["strict_outcome"] == "judged" and source.answers[CARO]["reader_retry_sha256"] is None
    result = await lenient_run(harness, source)
    assert result["complete"] and result["scores"]["lenient_correct"] == 2
    assert result["source"]["failure_policy"] is None and result["source"]["server_versions"] == ["0.34.3"]
    assert lenient_published(harness, path.name)


async def test_an_unreadable_label_is_asked_once_more_then_scored_unresolved(harness, monkeypatch, capsys):
    path = await deepseek_source(harness, monkeypatch)
    requests = lenient_ollama(monkeypatch, replies={
        "Caroline": ["It is right. CORRECT", ("{", "length")],
        "puppy": [("{\"lab", "length"), 'Close enough.\n{"label": "WRONG"}']})
    source = lenient.load_source(path, data=harness["data"])
    result = await lenient_run(harness, source)
    assert len(requests) == 4
    assert [(row["question_id"], row["correct"], row["outcome"]) for row in result["rows"]] == [
        (CARO, False, "verdict_unresolved"), (PUPPY, False, "judged")]
    assert all(row["judge_retry_sha256"] for row in result["rows"])
    assert result["failure_policy"] == {"id": lenient.FAILURE_POLICY, "judge_retries": 2, "truncated": 0,
                                        "verdict_unresolved": 1, "unscored": 1, "unscored_limit_percent": 1,
                                        "within_limit": False}
    assert result["scores"]["accepted_only_by_strict"] == 1
    # A result outside the limit is still published, with a warning and a mark in the run log.
    assert "complete but outside the limit" in capsys.readouterr().err
    assert lenient_published(harness, path.name) and "invalid" in run_log(harness)[-1]
    folder = lenient.judge_folder(source, harness["data"])
    assert (folder / "execution" / CARO / "attempt-1" / "judge-retry.json").is_file()
    assert lenient.report(source, folder, None)["rows"] == result["rows"]


async def test_the_replay_refuses_records_the_policy_never_makes(harness, monkeypatch):
    path = await deepseek_source(harness, monkeypatch)
    lenient_ollama(monkeypatch)
    source = lenient.load_source(path, data=harness["data"])
    result = await lenient_run(harness, source)
    folder = lenient.judge_folder(source, harness["data"])
    attempt = folder / "execution" / CARO / "attempt-1"
    recorded = (attempt / "result.json").read_text()
    # An answer that was not truncated, recorded as truncated without its judge call.
    judge = attempt / "judge.json"
    judge.rename(attempt / "judge.moved")
    (attempt / "result.json").write_text(json.dumps({**result["rows"][0], "correct": False, "outcome": "truncated",
                                                     "judge_sha256": None, "judge_retry_sha256": None}))
    with pytest.raises(ValueError, match="no judge call for an answer that was not truncated"):
        lenient.report(source, folder, None)
    # A retry recorded without the call it repeats.
    (attempt / "judge.moved").rename(attempt / "judge-retry.json")
    with pytest.raises(ValueError, match="a retry recorded without the call it repeats"):
        lenient.report(source, folder, None)
    (attempt / "judge-retry.json").rename(judge)
    (attempt / "result.json").write_text(recorded.replace('"correct": true', '"correct": false'))
    with pytest.raises(ValueError, match="does not match its saved answer or calls"):
        lenient.report(source, folder, None)


async def test_an_ollama_source_is_graded_only_by_the_model_that_judged_it(harness, monkeypatch):
    path = await deepseek_source(harness, monkeypatch)
    changed = {**IDENTITY, "manifest_digest_sha256": "f" * 64}
    requests = lenient_ollama(monkeypatch, identities=[changed])
    source = lenient.load_source(path, data=harness["data"])
    with pytest.raises(ValueError, match="differ from the ones that gave"):
        await lenient_run(harness, source)
    assert requests == []
    # A change during the pass publishes nothing, and its run log keeps the change.
    requests = lenient_ollama(monkeypatch, identities=[IDENTITY, changed])
    with pytest.raises(RuntimeError, match="identity changed"):
        await lenient_run(harness, source)
    assert len(requests) == 2 and not lenient_published(harness, path.name)
    assert not (lenient.judge_folder(source, harness["data"]) / "result.json").exists()
    assert [event["event"] for event in run_log(harness)] == ["started", "model-changed"]
    requests = lenient_ollama(monkeypatch)
    result = await lenient_run(harness, source)
    assert result["complete"] and requests == []
    assert result["run_log"]["runs_started"] == 2 and result["run_log"]["model_changes"] == 1


async def test_a_pass_resumes_only_the_questions_whose_verdict_never_arrived(harness, monkeypatch):
    path = await deepseek_source(harness, monkeypatch)
    source = lenient.load_source(path, data=harness["data"])
    requests = lenient_ollama(monkeypatch)
    real = ollama_answers.call

    async def flaky(client, semaphore, model, prompt, limit, path, **options):
        if "puppy" in prompt:
            raise RuntimeError("Provider HTTP 503; attempt retained")
        return await real(client, semaphore, model, prompt, limit, path, **options)

    monkeypatch.setattr(ollama_answers, "call", flaky)
    with pytest.raises(RuntimeError, match="1/2 graded, 0 final failures. Run it again"):
        await lenient_run(harness, source)
    folder = lenient.judge_folder(source, harness["data"])
    partial = json.loads((folder / "result.json").read_text())
    assert not partial["complete"] and "scores" not in partial and partial["failures"][0]["retryable"]
    monkeypatch.setattr(ollama_answers, "call", real)
    requests.clear()
    result = await lenient_run(harness, source)
    assert len(requests) == 1 and "puppy" in requests[0]["messages"][0]["content"]
    assert result["complete"] and result["failures"][0]["replaced"] and result["unreplaced_failures"] == 0
    assert (folder / "execution" / PUPPY / "attempt-2" / "result.json").is_file()


async def test_a_pass_never_mixes_verdicts_under_another_source_prompt_or_judge(harness, monkeypatch):
    path = await deepseek_source(harness, monkeypatch)
    lenient_ollama(monkeypatch)
    source = lenient.load_source(path, data=harness["data"])
    folder = lenient.judge_folder(source, harness["data"])
    folder.mkdir(parents=True)
    binding = {"source_sha256": source.sha256, "prompt_sha256": "0" * 64,
               "verdict_rule_sha256": text_sha256(lenient.VERDICT_RULE), "failure_policy": lenient.FAILURE_POLICY,
               "judge": {**OLLAMA_MODEL.settings(), "identity": IDENTITY}}
    (folder / "source.json").write_text(json.dumps(binding))
    with pytest.raises(ValueError, match="under another prompt_sha256; they are never mixed"):
        await lenient_run(harness, source)
    (folder / "source.json").write_text(json.dumps({**binding, "prompt_sha256": lenient.LENIENT_JUDGE_SHA256,
                                                    "judge": {**binding["judge"], "seed": 7}}))
    with pytest.raises(ValueError, match="under another judge"):
        await lenient_run(harness, source)


async def test_sources_other_than_a_complete_published_locomo_answer_run_are_refused(harness, monkeypatch):
    path = await deepseek_source(harness, monkeypatch)
    result = json.loads(path.read_text())
    cases = {
        "sample": ({**result, "kind": "ollama-answer-result-sample"}, "is a ollama-answer-result-sample"),
        "pair": ({**result, "pair": {"id": "x"}}, "one side of a pair"),
        "longmemeval": ({**result, "benchmark": "longmemeval"}, "not a LoCoMo result"),
        "incomplete": ({**result, "complete": False}, "not a complete result"),
        "reordered": ({**result, "rows": result["rows"][::-1]}, "not a complete result"),
        "gpt54-arm": ({**result, "kind": "gpt54-baseline-result"}, "grades the registered GPT-5.4 run or"),
        "traversal": ({**result, "arm": "../full-context"}, "Choose an arm"),
        "other-model": ({**result, "answer_model": {**result["answer_model"], "model": "other:cloud"}},
                        "was answered by other:cloud"),
    }
    for name, (value, message) in cases.items():
        other = path.with_name(f"{name}-result.json")
        other.write_text(json.dumps(value))
        with pytest.raises(ValueError, match=message):
            lenient.load_source(other, data=harness["data"])
    # A result must be published in this checkout, and a DeepSeek result takes no archive.
    outside = harness["root"] / "outside-result.json"
    shutil.copy(path, outside)
    monkeypatch.setattr(study, "PUBLIC", harness["results"] / "registered")
    with pytest.raises(ValueError, match="is not published under"):
        lenient.load_source(outside, data=harness["data"])
    with pytest.raises(ValueError, match="--archive names the registered run"):
        lenient.load_source(path, data=harness["data"], archive=harness["archive"])
    # A saved answer or strict judge call that no longer matches the published row is refused.
    attempt = harness["data"] / "full-context" / "locomo" / "execution" / PUPPY / "attempt-1"
    judge = attempt / "judge.json"
    judge.write_text(judge.read_text().replace('"no"', '"yes"'))
    with pytest.raises(ValueError, match="strict judge call for conv-1-q0001"):
        lenient.load_source(path, data=harness["data"])
    reader = attempt / "reader.json"
    reader.write_text(reader.read_text().replace("balloon", "kite"))
    with pytest.raises(ValueError, match="saved answer to conv-1-q0001"):
        lenient.load_source(path, data=harness["data"])


# The registered GPT-5.4 run -------------------------------------------------------

def registered_run(harness, *, answers=("The answer.", "a red balloon"), verdicts=("yes", "no")) -> Path:
    """The registered 2026-09-23 LoCoMo result, its verification and its saved answers, in the harness."""
    execution = harness["archive"] / "locomo" / "execution"
    rows = []
    for question, answer, verdict in zip(study.question_rows("locomo"), answers, verdicts):
        record = execution / question["question_id"]
        record.mkdir(parents=True)
        (record / "reader.json").write_text(json.dumps({"text": answer}))
        (record / "judge.json").write_text(json.dumps({"text": verdict, "response": {
            "service_tier": "flex", "usage": USAGE}}))
        rows.append({"question_id": question["question_id"], "question_type": question["question_type"],
                     "cluster": question["conversation_id"], "correct": verdict == "yes",
                     "reader_sha256": digest(record / "reader.json"), "judge_sha256": digest(record / "judge.json")})
    path = study.PUBLIC / lenient.REGISTERED_RESULT
    path.write_text(json.dumps({"benchmark": "locomo", "complete": True, "rows": rows}))
    (study.PUBLIC / lenient.REGISTERED_VERIFICATION).write_text(json.dumps({
        "complete": True, "registration_sha256": digest(study.REG),
        "benchmarks": {"locomo": {"result_sha256": digest(path)}}}))
    return path


def gpt54(monkeypatch, *, text=CORRECT, fail: str | None = None, status: str = "completed"):
    """Replace the OpenAI endpoint; the real call(), ledger and call verifier still run."""
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if fail is not None and fail in body["input"]:
            return httpx.Response(400, json={"error": {"code": "invalid_request"}})
        return httpx.Response(200, json={
            "model": MODEL, "status": status, "service_tier": "flex", "usage": USAGE,
            "output": [{"content": [{"type": "output_text", "text": text}]}]})

    monkeypatch.setattr(baselines, "client_for", lambda key: httpx.AsyncClient(
        base_url="https://api.openai.com/v1", transport=httpx.MockTransport(handler)))
    return bodies


def ledger_path(harness) -> Path:
    return harness["data"] / "ledgers" / "lenient-judge-gpt54-comparison-v1-locomo.json"


def test_the_registered_run_is_found_verified_and_estimated(harness):
    path = registered_run(harness)
    assert lenient.registered_result() == path
    source = lenient.load_source(path, archive=harness["archive"])
    assert source.name == lenient.REGISTERED_RUN and source.paid and source.published == "public/" + path.name
    assert [answer["strict"] for answer in source.answers.values()] == [True, False]
    found = lenient.estimate(source)
    expected = 0
    for question in study.question_rows("locomo"):
        answer = source.answers[question["question_id"]]["text"]
        extra = count_tokens(lenient.lenient_prompt(question, answer)) - count_tokens(
            study.judge_prompt("locomo", question, answer))
        expected += usage_cost({"service_tier": "flex", "usage": {
            "input_tokens": USAGE["input_tokens"] + extra, "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": USAGE["output_tokens"] + lenient.EXPLANATION_TOKENS}})
    assert found["recorded_strict_judge_usd"] == pytest.approx(2 * PER_CALL / 1e9)
    assert found["estimated_usd"] == pytest.approx(expected / 1e9)
    assert found["minimum_cap_usd"] == pytest.approx(found["estimated_usd"] + found["reservation_headroom_usd"])
    # Only the verified result is the registered run.
    path.write_text(path.read_text().replace('"complete": true', '"complete": true ', 1))
    with pytest.raises(ValueError, match="not the verified result"):
        lenient.load_source(path, archive=harness["archive"])


async def test_the_registered_run_is_graded_by_gpt54_within_an_approved_cap(harness, monkeypatch):
    source = lenient.load_source(registered_run(harness), archive=harness["archive"])
    archive = {str(path): digest(path) for path in (harness["archive"] / "locomo").rglob("*") if path.is_file()}
    bodies = gpt54(monkeypatch)
    with pytest.raises(ValueError, match="--max-usd"):
        await lenient_run(harness, source, api_key="test")
    result = await lenient_run(harness, source, max_usd=5, api_key="test")
    questions = study.question_rows("locomo")
    assert sorted(body["input"] for body in bodies) == sorted(
        lenient.lenient_prompt(question, answer) for question, answer in zip(questions, ("The answer.",
                                                                                         "a red balloon")))
    assert all(body == {"model": MODEL, "input": body["input"], "reasoning": {"effort": "medium"},
                        "service_tier": "flex", "max_output_tokens": JUDGE_LIMIT, "store": False} for body in bodies)
    assert result["scores"]["strict_correct"] == 1 and result["scores"]["lenient_correct"] == 2
    assert result["source"]["kind"] == "gpt54-registered-result" and result["model"] == MODEL
    assert result["source"]["failure_policy"] is None
    assert result["answer_model"] == {**baselines.OPENAI_ANSWER_MODEL, "failure_policy": lenient.FAILURE_POLICY}
    assert result["cost"]["usd"] == pytest.approx(2 * PER_CALL / 1e9) and result["max_usd"] == 5
    assert "server_versions" not in result and result["retry_policy"] == lenient.RETRY_POLICY
    assert ledger_path(harness).is_file()
    assert lenient_published(harness, lenient.REGISTERED_RESULT)
    folder = lenient.judge_folder(source, harness["data"])
    assert folder == harness["data"] / "lenient-judge" / "gpt54-comparison-v1" / "locomo"
    ledger = gpt54_budget.Ledger(ledger_path(harness), cap=5_000_000_000)
    assert lenient.report(source, folder, ledger)["rows"] == result["rows"]
    # The frozen run's archive is only read.
    assert {str(path): digest(path) for path in (harness["archive"] / "locomo").rglob("*")
            if path.is_file()} == archive


async def test_a_gpt54_label_that_cannot_be_read_is_asked_once_more_then_unresolved(harness, monkeypatch):
    source = lenient.load_source(registered_run(harness), archive=harness["archive"])
    bodies = gpt54(monkeypatch, text="The answer is on the same topic. CORRECT")
    result = await lenient_run(harness, source, max_usd=5, api_key="test")
    # The paid pass still completes: each question is asked twice and scored incorrect, as Mem0 scores any reply
    # whose label is not CORRECT.
    assert len(bodies) == 4 and result["complete"]
    assert [row["outcome"] for row in result["rows"]] == ["verdict_unresolved"] * 2
    assert result["failure_policy"]["judge_retries"] == 2
    assert result["cost"]["usd"] == pytest.approx(4 * PER_CALL / 1e9)
    folder = lenient.judge_folder(source, harness["data"])
    ledger = gpt54_budget.Ledger(ledger_path(harness), cap=5_000_000_000)
    assert lenient.report(source, folder, ledger)["rows"] == result["rows"]


async def test_a_final_gpt54_failure_is_never_asked_again_and_says_the_pass_cannot_complete(harness, monkeypatch):
    source = lenient.load_source(registered_run(harness), archive=harness["archive"])
    gpt54(monkeypatch, status="incomplete")
    with pytest.raises(RuntimeError, match="0/2 graded, 1 final failures. It can never complete"):
        await lenient_run(harness, source, max_usd=5, api_key="test")
    # The first failure stopped new questions, so a later run grades the question it never reached, and never
    # asks again the one whose reply was final. Nothing is published.
    bodies = gpt54(monkeypatch)
    with pytest.raises(RuntimeError, match="1/2 graded, 1 final failures. It can never complete"):
        await lenient_run(harness, source, max_usd=5, api_key="test")
    assert len(bodies) == 1 and "puppy" in bodies[0]["input"]
    assert not lenient_published(harness, lenient.REGISTERED_RESULT)


async def test_a_gpt54_pass_asks_again_after_no_verdict_arrived(harness, monkeypatch):
    source = lenient.load_source(registered_run(harness), archive=harness["archive"])
    gpt54(monkeypatch, fail="puppy")
    with pytest.raises(RuntimeError, match="1/2 graded, 0 final failures"):
        await lenient_run(harness, source, max_usd=5, api_key="test")
    bodies = gpt54(monkeypatch)
    result = await lenient_run(harness, source, max_usd=5, api_key="test")
    assert len(bodies) == 1 and "puppy" in bodies[0]["input"] and result["complete"]


def test_a_paid_pass_needs_its_api_key_before_it_starts(harness, monkeypatch):
    source = lenient.load_source(registered_run(harness), archive=harness["archive"])

    def missing():
        raise ValueError("OPENAI_API_KEY is not set")

    monkeypatch.setattr(baselines, "_api_key", missing)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        asyncio.run(lenient_run(harness, source, max_usd=5))
    assert not lenient.judge_folder(source, harness["data"]).exists() and not ledger_path(harness).exists()


# CLI ------------------------------------------------------------------------------

@pytest.fixture
def cli(harness, monkeypatch):
    """The CLI over the harness's registered run; a pass it starts is recorded rather than run."""
    registered_run(harness)
    monkeypatch.setattr(baselines.gate, "default_archive", lambda: harness["archive"])
    monkeypatch.setattr(baselines, "_api_key", lambda: "test")
    started = []

    async def fake_run(source, **kwargs):
        started.append((source.name, kwargs))
        return {"complete": True, "rows": [], "failures": []}

    monkeypatch.setattr(lenient, "run", fake_run)
    return started


@pytest.mark.parametrize(("argv", "message"), [
    (["run"], "pass the owner-approved cap"),
    (["run", "--max-usd", "0"], "pass the owner-approved cap"),
    (["run", "--max-usd", "nan"], "pass the owner-approved cap"),
    (["run", "--max-usd", "5"], "start it from an interactive terminal"),  # stdin is not a terminal under pytest
    (["estimate", "--max-usd", "5"], "estimate takes no --max-usd"),
    (["run", "--result", "missing-result.json", "--max-usd", "5"], "cannot grade missing-result.json"),
])
def test_cli_refuses_unapproved_paid_passes(argv, message, cli, monkeypatch, capsys):
    monkeypatch.setattr(baselines.sys.stdin, "isatty", lambda: False, raising=False)
    with pytest.raises(SystemExit):
        lenient.main(argv)
    assert message in capsys.readouterr().err and cli == []


def test_cli_starts_a_paid_pass_only_when_the_owner_types_its_name(cli, monkeypatch, capsys):
    monkeypatch.setattr(baselines.sys.stdin, "isatty", lambda: True, raising=False)
    prompts = []
    typed = iter(["lenient-judge", "lenient-judge-gpt54-comparison-v1"])
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or next(typed))
    with pytest.raises(SystemExit):
        lenient.main(["run", "--max-usd", "5"])
    assert cli == [] and "not confirmed" in capsys.readouterr().err
    lenient.main(["run", "--max-usd", "5"])
    assert cli == [("gpt54-comparison-v1", {"max_usd": 5.0})]
    assert "Type the pass name, lenient-judge-gpt54-comparison-v1, to confirm" in prompts[0]
    assert '"complete": true' in capsys.readouterr().out


def test_cli_estimates_the_registered_run(cli, capsys):
    lenient.main(["estimate"])
    assert json.loads(capsys.readouterr().out)["source"] == lenient.REGISTERED_RUN


async def test_cli_grades_an_ollama_source_without_a_cap_or_confirmation(harness, monkeypatch, capsys):
    path = await deepseek_source(harness, monkeypatch)
    monkeypatch.setattr(baselines, "data_root", lambda model: harness["data"])
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("asked for a spending confirmation"))
    started = []

    async def fake_run(source, **kwargs):
        started.append((source.name, kwargs))
        return {"complete": True, "rows": [], "failures": []}

    monkeypatch.setattr(lenient, "run", fake_run)
    for argv, message in ((["run", "--result", str(path), "--max-usd", "5"], "graded without paid calls"),
                          (["estimate", "--result", str(path)], "applies to the GPT-5.4 source"),
                          (["run", "--result", str(path), "--archive", str(harness["archive"])],
                           "--archive names the registered run")):
        with pytest.raises(SystemExit):
            lenient.main(argv)
        assert message in capsys.readouterr().err
    # asyncio.run cannot start inside this test's event loop, so run it on its own thread.
    await asyncio.to_thread(lenient.main, ["run", "--result", str(path)])
    assert started == [("full-context", {"max_usd": None})]
    assert "Judge: deepseek-v4.1-flash:cloud" in capsys.readouterr().err


# The published DeepSeek pass ------------------------------------------------------

RESEARCH = Path(__file__).resolve().parents[1] / "benchmarks" / "results" / "research"
SOURCE_NAME = "ollama-deepseek-v4.1-flash-cloud-prme@46647825-locomo-result.json"


def test_the_published_deepseek_pass_grades_the_baseline_answers_beside_their_strict_verdicts():
    [path] = RESEARCH.glob(f"*/{SOURCE_NAME.removesuffix('-result.json')}-lenient-judge-result.json")
    result = json.loads(path.read_text())
    source = RESEARCH / "2026-09-24" / SOURCE_NAME
    strict = json.loads(source.read_text())
    assert result["complete"] and result["total"] == 1540 == len(result["rows"])
    assert result["source"]["sha256"] == digest(source) and result["source"]["path"] == f"2026-09-24/{SOURCE_NAME}"
    assert result["judges"]["lenient"]["prompt_sha256"] == lenient.LENIENT_JUDGE_SHA256
    assert result["answer_model"]["identity"]["manifest_digest_sha256"].startswith("e04da138")
    assert result["answer_model"]["failure_policy"] == lenient.FAILURE_POLICY and result["cost"]["usd"] == 0
    # The strict side is the baseline's own verdicts, question by question.
    assert [(row["question_id"], row["strict_correct"], row["reader_sha256"]) for row in result["rows"]] == [
        (row["question_id"], row["correct"], row["reader_sha256"]) for row in strict["rows"]]
    scores = result["scores"]
    assert scores["strict_correct"] == strict["correct"] == 1007
    assert scores["lenient_correct"] == sum(row["correct"] for row in result["rows"])
    assert scores["lenient_correct"] - scores["strict_correct"] == (
        scores["accepted_only_by_lenient"] - scores["accepted_only_by_strict"])
    assert [(row["category"], row["name"], row["total"]) for row in result["categories"]] == [
        (1, "multi-hop", 282), (2, "temporal", 321), (3, "open-domain", 96), (4, "single-hop", 841)]
    assert {row["name"]: row["strict_correct"] for row in result["categories"]} == {
        name: value["correct"] for name, value in strict["categories"].items()}
    assert result["failure_policy"]["within_limit"]
