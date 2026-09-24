import asyncio
import dataclasses
import fcntl
import hashlib
import json
from pathlib import Path
import shutil

import httpx
import pytest

from benchmarks.diagnostics import product_packing as gate
from benchmarks.integrations import gpt54_baselines as baselines
from benchmarks.integrations import gpt54_budget
from benchmarks.integrations import run_gpt54_comparison as study
from benchmarks.integrations.gpt54_budget import JUDGE_LIMIT, MODEL, READER_LIMIT, Ledger, digest, usage_cost
from prme.retrieval.tokenization import count_tokens
from tests.test_product_packing_diagnostic import (  # noqa: F401 - mock_embeddings is a fixture
    gate_case, make_gate_pack, mock_embeddings,
)

CONVERSATION = {
    "speaker_a": "Caroline", "speaker_b": "Melanie",
    "session_2_date_time": "7:00 pm on 20 May, 2023",
    "session_2": [{"speaker": "Melanie", "dia_id": "D2:1", "text": "I adopted a puppy named Oscar."}],
    "session_1_date_time": "1:56 pm on 8 May, 2023",
    "session_1": [
        {"speaker": "Caroline", "dia_id": "D1:1", "text": "I painted a sunset."},
        {"speaker": "Melanie", "dia_id": "D1:2", "text": ""},
        {"speaker": "Melanie", "dia_id": "D1:3", "text": "Look.", "blip_caption": "a blue bird"},
    ],
}
LOCOMO = [{
    "sample_id": "conv-1", "conversation": CONVERSATION,
    "qa": [{"question": "What did Caroline paint?", "answer": "a sunset", "evidence": ["D1:1"], "category": 4},
           {"question": "What is the puppy called?", "answer": "Oscar", "evidence": ["D2:1"], "category": 1},
           {"question": "Adversarial?", "answer": "n/a", "evidence": [], "category": 5}],
    "observation": "FORBIDDEN", "session_summary": "FORBIDDEN",
}]
LONGMEM = [{
    "question_id": "q1", "question_type": "single-session-user", "question": "Where did I travel?",
    "question_date": "2023/05/30 (Tue) 23:40", "answer": "Lisbon",
    "haystack_session_ids": ["s1"], "haystack_dates": ["2023/05/20 (Sat) 02:21"],
    "haystack_sessions": [[{"role": "user", "content": "I just got back from Lisbon."}]],
}]
LME_CONTEXT = "(2023/05/20 (Sat) 02:21) user: I just got back from Lisbon."
OFFICIAL_JUDGE = '''
def get_anscheck_prompt(task, question, answer, response, abstention=False):
    return f"OFFICIAL JUDGE {task} | {question} | {answer} | {response} | {abstention}"
'''
LOADER = "benchmarks/integrations/gpt54_official_prompt_loader.py"


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """A registered comparison over tiny datasets; every file it reads or writes is under tmp_path."""
    locomo, longmem = tmp_path / "locomo10.json", tmp_path / "longmemeval_s.json"
    locomo.write_text(json.dumps(LOCOMO))
    longmem.write_text(json.dumps(LONGMEM))
    official = tmp_path / "official"
    (official / "src/evaluation").mkdir(parents=True)
    (official / "src/evaluation/evaluate_qa.py").write_text(OFFICIAL_JUDGE)
    public = tmp_path / "public"
    public.mkdir()
    for name, value in (("LOCOMO", locomo), ("LONGMEM", longmem), ("OFFICIAL", official), ("PUBLIC", public)):
        monkeypatch.setattr(study, name, value)
    registration = {
        "sources": {path: digest(study.ROOT / path) for path in baselines.FROZEN_SOURCES},
        "prompts": {"locomo_reader": study.LOCO_READER, "locomo_judge": study.LOCO_JUDGE,
                    "longmemeval_reader": study.lme._reader_prompt("{context}", "{date}", "{question}"),
                    "longmemeval_judge_sha256": digest(official / "src/evaluation/evaluate_qa.py")},
        "model": MODEL, "reasoning": "medium", "service_tier": "flex", "reader_limit": READER_LIMIT,
        "judge_limit": JUDGE_LIMIT, "provider_concurrency": 4,
        "datasets": {"locomo": {"sha256": digest(locomo)}, "longmemeval": {"sha256": digest(longmem)}},
        "cohort_ids": {name: [row["question_id"] for row in study.question_rows(name)]
                       for name in ("locomo", "longmemeval")},
    }
    reg = tmp_path / "registration.json"
    reg.write_text(json.dumps(registration))
    monkeypatch.setattr(study, "REG", reg)
    (public / "gpt54-official-loader-amendment.json").write_text(json.dumps({
        "registration_sha256": digest(reg), "loader_sha256": digest(study.ROOT / LOADER),
        "official_source_sha256": digest(official / "src/evaluation/evaluate_qa.py")}))
    archive = tmp_path / "archive"
    (archive / "authored-calibration").mkdir(parents=True)
    (archive / "authored-calibration/result.json").write_text(
        json.dumps({"complete": True, "registration_sha256": digest(reg)}))
    return {"root": tmp_path, "registration": registration, "reg": reg, "data": tmp_path / "data",
            "results": tmp_path / "results", "archive": archive}


def run(harness, arm="full-context", benchmark="locomo", *, max_usd=5):
    return baselines.run(arm, benchmark, max_usd=max_usd, data=harness["data"], results=harness["results"],
                         archive=harness["archive"], api_key="test")


USAGE = {"input_tokens": 100, "output_tokens": 10, "input_tokens_details": {"cached_tokens": 20},
         "output_tokens_details": {"reasoning_tokens": 4}}
PER_CALL = usage_cost({"service_tier": "flex", "usage": USAGE})


def provider(monkeypatch, *, fail: str | None = None, verdict: str = "yes"):
    """Replace the OpenAI endpoint; the real call(), ledger and call verifier still run."""
    prompts = []

    def handler(request):
        prompt = json.loads(request.content)["input"]
        prompts.append(prompt)
        if fail is not None and fail in prompt:
            return httpx.Response(400, json={"error": {"code": "invalid_request"}})
        judge = prompt.startswith(("Evaluate the response", "OFFICIAL JUDGE"))
        return httpx.Response(200, json={
            "model": MODEL, "status": "completed", "service_tier": "flex", "usage": USAGE,
            "output": [{"content": [{"type": "output_text", "text": verdict if judge else "The answer."}]}]})

    monkeypatch.setattr(baselines, "client_for", lambda key: httpx.AsyncClient(
        base_url="https://api.openai.com/v1", transport=httpx.MockTransport(handler)))
    return prompts


def arm_folder(harness, arm="full-context", benchmark="locomo"):
    return harness["data"] / arm / benchmark


def published(harness, arm="full-context", benchmark="locomo"):
    return list(harness["results"].glob(f"*/gpt54-baseline-{arm}-{benchmark}-result.json"))


def fabricate_plain(data: Path, benchmark: str, contexts: dict[str, str]) -> None:
    """Prepared plain-rrf contexts in the layout prepare writes, without replaying packs."""
    folder = data / "plain-rrf" / benchmark
    entries = []
    for qid, context in contexts.items():
        path = folder / "contexts" / benchmark / f"{qid}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"question_id": qid, "context": context}))
        entries.append({"question_id": qid, "path": str(path.relative_to(folder)), "sha256": digest(path),
                        "text_sha256": hashlib.sha256(context.encode()).hexdigest(),
                        "context_tokens": count_tokens(context), "retrieval_seconds": .1})
    (folder / "prepared.json").write_text(json.dumps({
        "kind": "gpt54-baseline-contexts", "complete": True, "arm": "plain-rrf", "benchmark": benchmark,
        "questions": len(entries), "registration_sha256": digest(study.REG), "tokenizer": "cl100k_base",
        "context_budget": 3996, "context_rule": "Stored turns ranked by RRF.", "contexts": entries}))


def test_full_context_has_every_session_in_order_under_its_date_and_only_source_text():
    context = baselines.full_context(CONVERSATION)
    assert context == ("Session 1 (1:56 pm on 8 May, 2023)\n"
                       "Caroline: I painted a sunset.\n"
                       "Melanie: Look. [Image: a blue bird]\n\n"
                       "Session 2 (7:00 pm on 20 May, 2023)\n"
                       "Melanie: I adopted a puppy named Oscar.")
    assert "FORBIDDEN" not in baselines.full_context({**CONVERSATION, "qa": [{"answer": "FORBIDDEN"}]})


def test_registered_protocol_rejects_changed_prompts_settings_sources_datasets_or_questions(harness):
    registration, questions = baselines.registered_protocol("locomo")
    assert registration == harness["registration"] and len(questions) == 2
    assert baselines.registered_protocol("longmemeval")[1] == LONGMEM
    changes = [
        ("locomo", lambda reg: reg["prompts"].update(locomo_judge="Be generous.")),
        ("longmemeval", lambda reg: reg["prompts"].update(longmemeval_reader="Answer.")),
        ("locomo", lambda reg: reg.update(model="another-model")),
        ("locomo", lambda reg: reg.update(judge_limit=1)),
        ("locomo", lambda reg: reg["sources"].update({baselines.FROZEN_SOURCES[0]: "0" * 64})),
        ("longmemeval", lambda reg: reg["datasets"]["longmemeval"].update(sha256="0" * 64)),
        ("locomo", lambda reg: reg["cohort_ids"]["locomo"].reverse()),
    ]
    for benchmark, change in changes:
        registration = json.loads(json.dumps(harness["registration"]))
        change(registration)
        harness["reg"].write_text(json.dumps(registration))
        with pytest.raises(ValueError, match="differ"):
            baselines.registered_protocol(benchmark)
    harness["reg"].write_text(json.dumps(harness["registration"]))
    amendment = study.PUBLIC / "gpt54-official-loader-amendment.json"
    amendment.write_text(json.dumps({**json.loads(amendment.read_text()), "loader_sha256": "0" * 64}))
    with pytest.raises(ValueError, match="registered amendment"):
        baselines.registered_protocol("longmemeval")
    baselines.registered_protocol("locomo")  # LoCoMo arms never read the LongMemEval judge.


def test_prepare_full_context_stores_each_conversation_once_and_never_overwrites(harness):
    data = harness["data"]
    prepared = baselines.prepare("full-context", "locomo", data=data)
    ids = ["conv-1-q0000", "conv-1-q0001"]
    assert [row["question_id"] for row in prepared["contexts"]] == ids
    assert {row["path"] for row in prepared["contexts"]} == {"contexts/locomo/conv-1.json"}
    assert prepared["context_budget"] is None and prepared["registration_sha256"] == digest(harness["reg"])
    questions = study.question_rows("locomo")
    folder, loaded, entries = baselines.load_prepared("full-context", "locomo", questions, data=data)
    assert loaded == prepared
    assert baselines._context(folder, entries[ids[0]]) == baselines.full_context(CONVERSATION)
    assert entries[ids[1]]["context_tokens"] == count_tokens(baselines.full_context(CONVERSATION))
    with pytest.raises(ValueError, match="already exists"):
        baselines.prepare("full-context", "locomo", data=data)
    with pytest.raises(ValueError, match="covers locomo only"):
        baselines.prepare("full-context", "longmemeval", data=data)
    with pytest.raises(ValueError, match="no budget"):
        baselines.prepare("full-context", "locomo", data=data / "other",
                          overrides=gate.parse_overrides(["packing.token_budget=8192"]))
    path = folder / "contexts" / "locomo" / "conv-1.json"
    path.write_text(path.read_text().replace("sunset", "sunrise"))
    with pytest.raises(ValueError, match="changed"):
        baselines.load_prepared("full-context", "locomo", questions, data=data)


def test_prepared_context_paths_cannot_leave_the_arm_folder(harness):
    baselines.prepare("full-context", "locomo", data=harness["data"])
    manifest = arm_folder(harness) / "prepared.json"
    prepared = json.loads(manifest.read_text())
    prepared["contexts"][0]["path"] = "../../../registration.json"
    manifest.write_text(json.dumps(prepared))
    with pytest.raises(ValueError, match="outside its arm"):
        baselines.load_prepared("full-context", "locomo", study.question_rows("locomo"), data=harness["data"])


@pytest.mark.usefixtures("mock_embeddings")
async def test_plain_arm_prepares_through_the_gate_and_answers_its_contexts(harness, monkeypatch):
    pack = harness["root"] / "pack"
    identity = await make_gate_pack(pack)
    cases = [dataclasses.replace(gate_case(pack, identity), question_id=qid)
             for qid in ("conv-1-q0000", "conv-1-q0001")]
    monkeypatch.setattr(gate, "_locomo_cases", lambda archive: cases)
    (harness["archive"] / "locomo").mkdir()
    (harness["archive"] / "locomo" / "prepared.json").write_text("{}")
    # prepare runs the gate in its own event loop, as the CLI does.
    prepared = await asyncio.to_thread(baselines.prepare, "plain-rrf", "locomo", data=harness["data"],
                                       archive=harness["archive"])
    folder = arm_folder(harness, "plain-rrf")
    report = json.loads((folder / "gate.json").read_text())
    assert report["provenance"]["plain"] == "rrf" and digest(folder / "gate.json") == prepared["gate_report_sha256"]
    assert prepared["context_budget"] == 3996 and "k=60" in prepared["context_rule"]
    assert [row["context_tokens"] for row in prepared["contexts"]] == [row["context_tokens"]
                                                                       for row in report["rows"]]
    prompts = provider(monkeypatch)
    result = await run(harness, "plain-rrf")
    assert result["complete"] and result["context_budget"] == 3996
    context = baselines._context(folder, prepared["contexts"][0])
    assert prompts[0] == study.reader_prompt("locomo", study.question_rows("locomo")[0], context)
    assert result["retrieval_seconds"]["p50"] is not None


async def test_run_retries_questions_that_got_no_answer_and_reports_cost_tokens_and_budget(harness, monkeypatch):
    baselines.prepare("full-context", "locomo", data=harness["data"])
    provider(monkeypatch, fail="What is the puppy called?")
    with pytest.raises(RuntimeError, match="1/2 answered, 0 final failures"):
        await run(harness)
    folder = arm_folder(harness)
    partial = json.loads((folder / "result.json").read_text())
    assert partial["complete"] is False and "accuracy" not in partial and not published(harness)
    [failure] = partial["failures"]
    assert failure["question_id"] == "conv-1-q0001" and failure["retryable"] and not failure["replaced"]

    prompts = provider(monkeypatch)
    result = await run(harness)
    # Only the failed question is asked again, in a new attempt, with the whole conversation.
    assert len(prompts) == 2 and baselines.full_context(CONVERSATION) in prompts[0]
    assert (folder / "execution" / "conv-1-q0001" / "attempt-2" / "result.json").exists()
    assert result["complete"] is True and result["unreplaced_failures"] == 0 == result["final_failures"]
    assert result["failures"][0]["replaced"] is True
    assert (result["correct"], result["accuracy"]) == (2, 1.0)
    assert result["categories"] == {"multi-hop": {"correct": 1, "total": 1},
                                    "single-hop": {"correct": 1, "total": 1}}
    assert result["context_budget"] is None and result["context_tokens"]["max"] == count_tokens(
        baselines.full_context(CONVERSATION))
    assert result["retrieval_seconds"]["p50"] is None
    assert result["cost"]["usd"] == pytest.approx(4 * PER_CALL / 1e9)
    assert result["cost"]["unsettled_reservations"] == 0
    tokens = result["provider_tokens"]
    assert (tokens["input_tokens"], tokens["cached_input_tokens"], tokens["output_tokens"],
            tokens["reasoning_tokens"], tokens["successful_calls"]) == (400, 80, 40, 16, 4)
    assert tokens["http_status_counts"] == {"200": 4} and tokens["observed_nanodollars"] == 4 * PER_CALL
    assert result["retry_policy"] == baselines.RETRY_POLICY and result["max_usd"] == 5
    [path] = published(harness)
    public = json.loads(path.read_text())
    assert "message" not in public["failures"][0] and "message" in result["failures"][0]
    assert {key: value for key, value in public.items() if key != "failures"} == {
        key: value for key, value in result.items() if key != "failures"}
    with pytest.raises(ValueError, match="never rerun"):
        await run(harness)


async def test_run_never_asks_again_after_an_answer_or_verdict_was_received(harness, monkeypatch):
    baselines.prepare("full-context", "locomo", data=harness["data"])
    provider(monkeypatch, verdict="maybe")
    with pytest.raises(RuntimeError, match="final failures"):
        await run(harness)
    result = json.loads((arm_folder(harness) / "result.json").read_text())
    assert result["final_failures"] >= 1 and all(not failure["retryable"] for failure in result["failures"])
    final = {failure["question_id"] for failure in result["failures"]}
    texts = [row["question"] for row in study.question_rows("locomo") if row["question_id"] in final]
    prompts = provider(monkeypatch)
    with pytest.raises(RuntimeError, match="incomplete"):
        await run(harness)
    assert not any(text in prompt for prompt in prompts for text in texts)


async def test_an_interrupted_attempt_is_reported_and_asked_again(harness, monkeypatch):
    baselines.prepare("full-context", "locomo", data=harness["data"])
    interrupted = arm_folder(harness) / "execution" / "conv-1-q0000" / "attempt-1"
    interrupted.mkdir(parents=True)
    (interrupted / "reader.request.json").write_text("{}")
    provider(monkeypatch)
    result = await run(harness)
    assert result["complete"] and result["failures"] == [{
        "attempt": "attempt-1", "exception_type": "Interrupted", "retryable": True, "budget_stop": False,
        "replaced": True}]


async def test_run_needs_an_approved_cap_that_it_enforces_and_only_raises(harness, monkeypatch):
    baselines.prepare("full-context", "locomo", data=harness["data"])
    prompts = provider(monkeypatch)
    for cap in (0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="--max-usd"):
            await run(harness, max_usd=cap)
    with pytest.raises(RuntimeError, match="incomplete"):
        await run(harness, max_usd=1e-6)
    result = json.loads((arm_folder(harness) / "result.json").read_text())
    assert prompts == [] and result["cost"]["usd"] == 0
    assert result["failures"] and all(failure["budget_stop"] and failure["retryable"]
                                      for failure in result["failures"])
    with pytest.raises(ValueError, match="cannot lower it"):
        await run(harness, max_usd=1e-7)
    result = await run(harness, max_usd=2)
    ledger = json.loads((harness["data"] / "ledgers" / "full-context-locomo.json").read_text())
    assert result["complete"] and ledger["cap_nanodollars"] == 2 * 10**9
    assert ledger["cap_history"][0]["from_nanodollars"] == 1000
    assert all(failure["replaced"] for failure in result["failures"])


async def test_spending_survives_removing_a_preparation_and_blocks_preparing_again(harness, monkeypatch):
    baselines.prepare("full-context", "locomo", data=harness["data"])
    provider(monkeypatch, fail="What is the puppy called?")
    with pytest.raises(RuntimeError):
        await run(harness)
    shutil.rmtree(arm_folder(harness))
    with pytest.raises(ValueError, match="already made paid calls"):
        baselines.prepare("full-context", "locomo", data=harness["data"])


async def test_report_refuses_a_ledger_that_misses_verified_spend(harness, monkeypatch):
    baselines.prepare("full-context", "locomo", data=harness["data"])
    provider(monkeypatch)
    await run(harness)
    questions = study.question_rows("locomo")
    folder, prepared, entries = baselines.load_prepared("full-context", "locomo", questions, data=harness["data"])
    with pytest.raises(ValueError, match="records less than the verified calls cost"):
        baselines.report("full-context", "locomo", folder, questions, prepared, entries,
                         Ledger(harness["root"] / "fresh.json", cap=10**9))


async def test_run_uses_the_registered_longmemeval_reader_and_amended_official_judge(harness, monkeypatch):
    fabricate_plain(harness["data"], "longmemeval", {"q1": LME_CONTEXT})
    prompts = provider(monkeypatch)
    result = await run(harness, "plain-rrf", "longmemeval", max_usd=1)
    reader, judge = prompts
    assert reader == study.lme._reader_prompt(LME_CONTEXT, "2023/05/30 (Tue) 23:40", "Where did I travel?")
    assert judge == "OFFICIAL JUDGE single-session-user | Where did I travel? | Lisbon | The answer. | False"
    assert result["complete"] is True and result["context_budget"] == 3996 and result["accuracy"] == 1.0


async def test_run_refuses_a_lock_held_by_another_run_or_a_missing_calibration(harness, monkeypatch):
    fabricate_plain(harness["data"], "longmemeval", {"q1": LME_CONTEXT})
    prompts = provider(monkeypatch)
    with (arm_folder(harness, "plain-rrf", "longmemeval") / "run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="in progress"):
            await run(harness, "plain-rrf", "longmemeval", max_usd=1)
    (harness["archive"] / "authored-calibration/result.json").write_text(json.dumps({"complete": False}))
    with pytest.raises(ValueError, match="calibration"):
        await run(harness, "plain-rrf", "longmemeval", max_usd=1)
    assert prompts == []


async def test_run_rejects_a_context_changed_after_preparation(harness, monkeypatch):
    fabricate_plain(harness["data"], "longmemeval", {"q1": LME_CONTEXT})
    path = arm_folder(harness, "plain-rrf", "longmemeval") / "contexts" / "longmemeval" / "q1.json"
    path.write_text(path.read_text().replace("Lisbon", "Porto"))
    prompts = provider(monkeypatch)
    with pytest.raises(ValueError, match="changed"):
        await run(harness, "plain-rrf", "longmemeval", max_usd=1)
    assert prompts == []


def test_estimate_scales_recorded_usage_and_adds_the_ledger_reservation_headroom(harness):
    data, archive = harness["data"], harness["archive"]
    prepared = baselines.prepare("full-context", "locomo", data=data)
    reader_usage = {"input_tokens": 4100, "output_tokens": 100}
    judge = {"service_tier": "flex", "usage": {"input_tokens": 200, "output_tokens": 20}}
    (archive / "locomo" / "contexts").mkdir(parents=True)
    for row in prepared["contexts"]:
        folder = archive / "locomo" / "execution" / row["question_id"]
        folder.mkdir(parents=True)
        (folder / "reader.json").write_text(json.dumps({"response": {"usage": reader_usage}}))
        (folder / "judge.json").write_text(json.dumps({"response": judge}))
        (archive / "locomo" / "contexts" / f"{row['question_id']}.json").write_text(
            json.dumps({"context_tokens": 4000}))
    result = baselines.estimate("full-context", "locomo", data=data, archive=archive)
    tokens = count_tokens(baselines.full_context(CONVERSATION))

    def cost(cached):
        return usage_cost(judge) + usage_cost({"service_tier": "flex", "usage": {
            "input_tokens": 100 + tokens, "output_tokens": 100, "input_tokens_details": {"cached_tokens": cached}}})

    assert result["estimated_usd"]["uncached"] == pytest.approx(2 * cost(0) / 1e9)
    assert result["estimated_usd"]["prefix_cached"] == pytest.approx((cost(0) + cost(tokens)) / 1e9)
    assert result["context_tokens"] == {"mean": tokens, "p50": tokens, "max": tokens, "total": 2 * tokens}
    prompt = max((study.reader_prompt("locomo", question, baselines.full_context(CONVERSATION))
                  for question in study.question_rows("locomo")), key=lambda text: len(text.encode()))
    headroom = 4 * (baselines._reservation(len(prompt.encode()), READER_LIMIT)
                    + baselines._reservation(baselines._JUDGE_BYTES, JUDGE_LIMIT))
    assert result["reservation_headroom_usd"] == pytest.approx(headroom / 1e9)
    assert result["minimum_cap_usd"] == pytest.approx(result["estimated_usd"]["uncached"] + headroom / 1e9)


@pytest.mark.parametrize("argv", [
    ["run", "full-context"],
    ["prepare", "plain-rrf"],
    ["estimate", "full-context", "--set", "packing.token_budget=8192"],
    ["estimate", "full-context", "--max-usd", "5"],
    ["prepare", "full-context", "--benchmark", "longmemeval"],
    ["prepare", "plain-rrf", "--benchmark", "locomo", "--set", "no-separator"],
    ["prepare", "plain-rrf", "--benchmark", "locomo", "--set", "scoring.relevance_floor=0.3"],
    ["run", "full-context", "--max-usd", "inf"],
    ["run", "full-context", "--max-usd", "5"],  # stdin is not a terminal under pytest
])
def test_cli_refuses_unapproved_runs_and_invalid_arguments(argv, monkeypatch):
    monkeypatch.setattr(baselines.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(baselines, "run", lambda *args, **kwargs: pytest.fail("started a paid run"))
    with pytest.raises(SystemExit):
        baselines.main(argv)


def test_cli_run_needs_the_owner_to_type_the_arm_name(monkeypatch):
    monkeypatch.setattr(baselines.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(baselines, "run", lambda *args, **kwargs: pytest.fail("started a paid run"))
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    with pytest.raises(SystemExit):
        baselines.main(["run", "full-context", "--max-usd", "5"])


def test_cli_prepare_refuses_uncommitted_code(monkeypatch):
    monkeypatch.setattr(baselines, "_provenance", lambda: {"dirty": True})
    monkeypatch.setattr(baselines, "prepare", lambda *args, **kwargs: pytest.fail("prepared a dirty tree"))
    with pytest.raises(SystemExit):
        baselines.main(["prepare", "full-context"])


# DeepSeek through Ollama -----------------------------------------------------

OLLAMA = baselines.ollama_answers
OLLAMA_MODEL = OLLAMA.AnswerModel()
IDENTITY = {"provider": "ollama_cloud", "model": OLLAMA_MODEL.model, "resolved_model": OLLAMA_MODEL.model,
            "manifest_digest_sha256": "e" * 64, "remote_host": "https://ollama.com",
            "remote_model": "deepseek-v4.1-flash", "remote_weights_pinned": False, "server_version": "0.34.3"}
OLLAMA_USAGE = {"prompt_tokens": 100, "completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": 30}}
TWO_LME = [LONGMEM[0], {**LONGMEM[0], "question_id": "q2", "question": "Where do I live?", "answer": "Porto"}]


def ollama_provider(monkeypatch, *, verdict: str = "yes", fail: str | None = None, identities=None):
    """Replace the Ollama server; any attempt to build or use the paid OpenAI path fails the test.

    ``identities`` is the sequence of identities the server reports, one per lookup; the last one repeats.
    """
    requests = []
    reported = list(identities or [IDENTITY])

    def handler(request):
        body = json.loads(request.content)
        prompt = body["messages"][0]["content"]
        requests.append((str(request.url), body, request.headers.get("authorization")))
        if fail is not None and fail in prompt:
            return httpx.Response(404, json={"error": "model not found"})
        judge = prompt.startswith(("Evaluate the response", "OFFICIAL JUDGE"))
        text = ("no" if baselines.AUTHORED_WRONG_ANSWER in prompt else verdict) if judge else "The answer."
        return httpx.Response(200, json={
            "object": "chat.completion", "model": "deepseek-v4.1-flash", "usage": OLLAMA_USAGE,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]})

    def paid(*args, **kwargs):
        pytest.fail("built or used the paid OpenAI path")

    for name in ("client_for", "call", "_api_key", "_check_calibration", "_ledger"):
        monkeypatch.setattr(baselines, name, paid)
    for name in ("client_for", "call"):
        monkeypatch.setattr(gpt54_budget, name, paid)
    monkeypatch.setattr("dotenv.dotenv_values", paid)
    monkeypatch.setattr(OLLAMA, "identity", lambda model: dict(reported.pop(0) if len(reported) > 1 else reported[0]))
    monkeypatch.setattr(OLLAMA, "client_for", lambda model: httpx.AsyncClient(
        base_url=model.endpoint, transport=httpx.MockTransport(handler)))
    return requests


def run_ollama(harness, arm="full-context", benchmark="locomo", **kwargs):
    return baselines.run(arm, benchmark, model=OLLAMA_MODEL, data=harness["data"], results=harness["results"],
                         **kwargs)


def ollama_published(harness, arm="full-context", benchmark="locomo", suffix=""):
    return list(harness["results"].glob(f"*/{OLLAMA_MODEL.track}-{arm}-{benchmark}{suffix}-result.json"))


def use_longmemeval(harness, rows):
    """Register a different LongMemEval-S question set in the harness."""
    study.LONGMEM.write_text(json.dumps(rows))
    registration = json.loads(harness["reg"].read_text())
    registration["datasets"]["longmemeval"]["sha256"] = digest(study.LONGMEM)
    registration["cohort_ids"]["longmemeval"] = [row["question_id"] for row in rows]
    harness["reg"].write_text(json.dumps(registration))
    amendment = study.PUBLIC / "gpt54-official-loader-amendment.json"
    amendment.write_text(json.dumps({**json.loads(amendment.read_text()), "registration_sha256": digest(harness["reg"])}))
    (harness["archive"] / "authored-calibration/result.json").write_text(
        json.dumps({"complete": True, "registration_sha256": digest(harness["reg"])}))


def test_authored_cases_are_the_registered_calibration_cases():
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(study.calibrate)))
    [cases] = [ast.literal_eval(node.value) for node in ast.walk(tree)
               if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "cases"]
    assert tuple(cases) == baselines.AUTHORED_CASES
    assert f"'negative','{baselines.AUTHORED_WRONG_ANSWER}'" in inspect.getsource(study.calibrate)


def test_the_recorded_gpt54_settings_are_what_the_frozen_client_sends():
    client = gpt54_budget.client_for("key")
    assert str(client.base_url).rstrip("/") == baselines.OPENAI_ANSWER_MODEL["endpoint"]
    assert baselines.OPENAI_ANSWER_MODEL["model"] == MODEL


async def test_ollama_run_never_builds_a_paid_client_and_records_the_reader_and_judge(harness, monkeypatch):
    baselines.prepare("full-context", "locomo", data=harness["data"])
    requests = ollama_provider(monkeypatch)
    calibration = await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    assert calibration["complete"] and [case["labels"] for case in calibration["cases"]] == [[True, True, False]] * 4
    assert [case["benchmark"] for case in calibration["cases"]] == ["longmemeval"] * 2 + ["locomo"] * 2
    result = await run_ollama(harness)
    # Four calibration cases of four calls each, then a reader and a judge call for each of two questions.
    assert len(requests) == 16 + 4
    assert {url for url, _, _ in requests} == {"http://127.0.0.1:11434/v1/chat/completions"}
    assert {auth for _, _, auth in requests} == {None}
    assert all(body == OLLAMA_MODEL.body(body["messages"][0]["content"], body["max_tokens"])
               for _, body, _ in requests)
    assert result["complete"] and (result["correct"], result["accuracy"]) == (2, 1.0)
    assert result["kind"] == "ollama-answer-result" and result["model"] == OLLAMA_MODEL.model
    assert result["answer_model"] == {**OLLAMA_MODEL.settings(), "identity": IDENTITY}
    assert result["answer_model"]["provider"] == "ollama" and result["answer_model"]["seed"] == 20260923
    assert result["calibration"] == {"attempt": "attempt-1", "attempts": 1, "attempts_before_pass": 0,
                                     "sha256": digest(harness["data"] / "authored-calibration/attempt-1/result.json")}
    assert result["cost"]["usd"] == 0 and "max_usd" not in result
    provenance = json.loads((arm_folder(harness) / "prepared.json").read_text())["provenance"]
    assert result["prepared"] == {"commit": provenance["commit"], "dirty": provenance["dirty"],
                                  "worktree_sha256": provenance["worktree_sha256"], "overrides": {},
                                  "context_rule": "The whole conversation; no budget.",
                                  "contexts_matching_saved_run": None}
    tokens = result["provider_tokens"]
    assert (tokens["input_tokens"], tokens["cached_input_tokens"], tokens["output_tokens"],
            tokens["successful_calls"], tokens["http_status_counts"]) == (400, 120, 40, 4, {"200": 4})
    assert "reasoning_tokens" not in tokens and "observed_nanodollars" not in tokens
    assert not (harness["data"] / "ledgers").exists() and not published(harness)
    [path] = ollama_published(harness)
    assert json.loads(path.read_text())["answer_model"]["endpoint"] == "http://127.0.0.1:11434/v1"
    log = [json.loads(line) for line in (harness["data"] / "runs" / "full-context-locomo.jsonl").read_text().splitlines()]
    assert [event["event"] for event in log] == ["started", "finished"] and log[1]["complete"] is True
    # The receipts replay: every call is verified again against its prompt and settings.
    questions = study.question_rows("locomo")
    folder, prepared, entries = baselines.load_prepared("full-context", "locomo", questions, data=harness["data"])
    replayed = baselines.report("full-context", "locomo", folder, questions, prepared, entries, None,
                                model=OLLAMA_MODEL)
    assert replayed["rows"] == result["rows"]
    with pytest.raises(ValueError, match="differs"):
        baselines.report("full-context", "locomo", folder, questions, prepared, entries, None,
                         model=OLLAMA.AnswerModel(seed=7))


async def test_calibration_keeps_every_attempt_and_run_needs_one_that_passed_with_this_model(harness, monkeypatch):
    baselines.prepare("full-context", "locomo", data=harness["data"])
    requests = ollama_provider(monkeypatch)
    with pytest.raises(ValueError, match="calibrate first"):
        await run_ollama(harness)
    assert requests == []
    ollama_provider(monkeypatch, verdict="no")
    with pytest.raises(RuntimeError, match="failed the authored calibration"):
        await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    ollama_provider(monkeypatch, verdict="Yes, it matches.")  # A malformed verdict fails the attempt, on record.
    with pytest.raises(RuntimeError, match="failed the authored calibration"):
        await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    failed = json.loads((harness["data"] / "authored-calibration/attempt-2/result.json").read_text())
    assert not failed["complete"] and failed["error"].startswith("ValueError: Malformed judge verdict")
    with pytest.raises(ValueError, match="calibrate first"):
        await run_ollama(harness)
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    with pytest.raises(ValueError, match="already calibrated"):
        await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    result = await run_ollama(harness)
    assert result["calibration"]["attempts"] == 3 and result["calibration"]["attempts_before_pass"] == 2
    # A pulled model with a new manifest is not the calibrated one, and can be calibrated in its turn.
    changed = {**IDENTITY, "manifest_digest_sha256": "f" * 64}
    ollama_provider(monkeypatch, identities=[changed])
    with pytest.raises(ValueError, match="already complete"):
        await run_ollama(harness)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    assert len(list((harness["data"] / "authored-calibration").glob("attempt-*"))) == 4


async def test_a_model_change_during_calibration_fails_it(harness, monkeypatch):
    ollama_provider(monkeypatch, identities=[IDENTITY, {**IDENTITY, "manifest_digest_sha256": "f" * 64}])
    with pytest.raises(RuntimeError, match="failed the authored calibration"):
        await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    record = json.loads((harness["data"] / "authored-calibration/attempt-1/result.json").read_text())
    assert record["error"] == "The model identity changed during calibration"


async def test_only_ollama_cloud_models_are_accepted(harness, monkeypatch):
    requests = ollama_provider(monkeypatch, identities=[{**IDENTITY, "provider": "ollama"}])
    with pytest.raises(ValueError, match="not an Ollama cloud model"):
        await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    assert requests == []


async def test_an_arm_is_never_answered_by_two_models(harness, monkeypatch):
    baselines.prepare("full-context", "locomo", data=harness["data"])
    changed = {**IDENTITY, "manifest_digest_sha256": "f" * 64}
    ollama_provider(monkeypatch, fail="What is the puppy called?")
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    with pytest.raises(RuntimeError, match="1/2 answered"):
        await run_ollama(harness)
    # The model is pulled again and calibrated; the arm keeps the answers of the first one.
    requests = ollama_provider(monkeypatch, identities=[changed])
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    requests.clear()
    with pytest.raises(ValueError, match="another reader and judge"):
        await run_ollama(harness)
    assert requests == []
    # The GPT-5.4 track never answers an arm the Ollama track started.
    with pytest.raises(ValueError, match="never mixed in"):
        await baselines.run("full-context", "locomo", max_usd=5, data=harness["data"], results=harness["results"],
                            archive=harness["archive"], api_key="test")


async def test_a_binding_without_answers_follows_the_calibrated_model(harness, monkeypatch):
    baselines.prepare("full-context", "locomo", data=harness["data"])
    ollama_provider(monkeypatch, fail="What did Caroline paint?")
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    with pytest.raises(RuntimeError, match="0/2 answered"):
        await run_ollama(harness)
    changed = {**IDENTITY, "manifest_digest_sha256": "f" * 64}
    ollama_provider(monkeypatch, identities=[changed])
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    result = await run_ollama(harness)
    assert result["complete"] and result["answer_model"]["identity"] == changed


async def test_a_model_change_during_a_run_publishes_nothing(harness, monkeypatch):
    baselines.prepare("full-context", "locomo", data=harness["data"])
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    ollama_provider(monkeypatch, identities=[IDENTITY, {**IDENTITY, "manifest_digest_sha256": "f" * 64}])
    with pytest.raises(RuntimeError, match="identity changed"):
        await run_ollama(harness)
    assert not ollama_published(harness) and not (arm_folder(harness) / "result.json").exists()
    log = (harness["data"] / "runs" / "full-context-locomo.jsonl").read_text()
    assert '"event": "model-changed"' in log


async def test_ollama_retries_questions_with_no_answer_on_a_later_run(harness, monkeypatch):
    for message in ("Provider HTTP 429; attempt retained", "Ambiguous provider failure retained without retry"):
        assert baselines._retryable(RuntimeError(message))
    assert not baselines._retryable(ValueError("Malformed provider response"))
    baselines.prepare("full-context", "locomo", data=harness["data"])
    ollama_provider(monkeypatch, fail="What is the puppy called?")
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    with pytest.raises(RuntimeError, match="1/2 answered, 0 final failures"):
        await run_ollama(harness)
    partial = json.loads((arm_folder(harness) / "result.json").read_text())
    [failure] = partial["failures"]
    assert failure["retryable"] and failure["message"].startswith("Provider HTTP 404")
    requests = ollama_provider(monkeypatch)
    result = await run_ollama(harness)
    assert result["complete"] and len(requests) == 2 and "puppy" in requests[0][1]["messages"][0]["content"]


async def test_preparing_again_stays_on_record_and_never_follows_a_complete_run(harness, monkeypatch):
    baselines.prepare("full-context", "locomo", data=harness["data"])
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    ollama_provider(monkeypatch, verdict="maybe")  # A malformed verdict is final: the arm can never finish.
    with pytest.raises(RuntimeError, match="final failures"):
        await run_ollama(harness)
    with pytest.raises(RuntimeError, match="final failures"):
        await run_ollama(harness)
    shutil.rmtree(arm_folder(harness))
    baselines.prepare("full-context", "locomo", data=harness["data"])
    ollama_provider(monkeypatch)
    result = await run_ollama(harness)
    assert result["complete"] and result["run_log"]["runs_started"] == 3
    assert result["run_log"]["prepared_again"] == 1
    assert result["run_log"]["sha256"] == digest(harness["data"] / "runs" / "full-context-locomo.jsonl")
    shutil.rmtree(arm_folder(harness))
    with pytest.raises(ValueError, match="complete answer run"):
        baselines.prepare("full-context", "locomo", data=harness["data"])


async def test_ollama_run_refuses_a_spending_cap_or_api_key_and_gpt54_refuses_its_arms(harness):
    with pytest.raises(ValueError, match="no paid calls"):
        await run_ollama(harness, max_usd=5)
    with pytest.raises(ValueError, match="no paid calls"):
        await run_ollama(harness, api_key="sk-test")
    for arm, kwargs in (("prme", {}), ("prme-rrf", {}), ("full-context", {"sample": 1})):
        with pytest.raises(ValueError, match="Ollama track only"):
            await baselines.run(arm, "locomo", max_usd=5, data=harness["data"], api_key="test", **kwargs)


def test_tracks_keep_their_contexts_and_answers_apart():
    assert baselines.data_root(None) == baselines.DATA
    assert baselines.data_root(OLLAMA_MODEL) == baselines.OLLAMA_DATA / "ollama-deepseek-v4.1-flash-cloud"


def test_sample_takes_the_first_questions_of_each_category_in_registered_order():
    rows = [{"question_id": str(n), "question_type": kind} for n, kind in enumerate("aababc")]
    assert [row["question_id"] for row in baselines.sample_questions(rows, 1)] == ["0", "2", "5"]
    assert [row["question_id"] for row in baselines.sample_questions(rows, 2)] == ["0", "1", "2", "4", "5"]
    with pytest.raises(ValueError):
        baselines.sample_questions(rows, 0)


async def test_a_sample_run_is_a_labelled_smoke_check_that_the_full_run_reuses(harness, monkeypatch):
    use_longmemeval(harness, TWO_LME)
    fabricate_plain(harness["data"], "longmemeval", {"q1": LME_CONTEXT, "q2": "(2023/05/21) user: I live in Porto."})
    requests = ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    requests.clear()
    sample = await run_ollama(harness, "plain-rrf", "longmemeval", sample=1)
    assert sample["complete"] and sample["sample"]["question_ids"] == ["q1"] and len(requests) == 2
    assert sample["kind"] == "ollama-answer-result-sample" and sample["correct"] == 1
    assert not {"accuracy", "ci95_questions", "ci95_source_clusters", "categories"} & set(sample)
    reader, judge = (body["messages"][0]["content"] for _, body, _ in requests)
    assert reader == study.lme._reader_prompt(LME_CONTEXT, "2023/05/30 (Tue) 23:40", "Where did I travel?")
    assert judge.startswith("OFFICIAL JUDGE single-session-user")
    assert ollama_published(harness, "plain-rrf", "longmemeval", "-sample-1")
    with pytest.raises(ValueError, match="never rerun"):
        await run_ollama(harness, "plain-rrf", "longmemeval", sample=1)
    full = await run_ollama(harness, "plain-rrf", "longmemeval")
    # Only the question the sample did not ask is asked now.
    assert full["complete"] and "sample" not in full and len(requests) == 4
    assert "Where do I live?" in requests[2][1]["messages"][0]["content"]
    assert full["rows"][:1] == sample["rows"] and full["accuracy"] == 1.0
    with pytest.raises(ValueError, match="not a complete answer run"):
        baselines.compare(sample, full)


@pytest.mark.usefixtures("mock_embeddings")
async def test_prme_arms_prepare_the_defaults_or_a_named_variant_through_the_gate(harness, monkeypatch):
    pack = harness["root"] / "pack"
    identity = await make_gate_pack(pack)
    cases = [dataclasses.replace(gate_case(pack, identity), question_id=qid)
             for qid in ("conv-1-q0000", "conv-1-q0001")]
    monkeypatch.setattr(gate, "_locomo_cases", lambda archive: cases)
    (harness["archive"] / "locomo").mkdir()
    (harness["archive"] / "locomo" / "prepared.json").write_text("{}")
    budget = gate.parse_overrides(["packing.token_budget=2048"])
    with pytest.raises(ValueError, match="name a variant"):
        baselines.prepare("prme", "locomo", data=harness["data"], archive=harness["archive"], overrides=budget)
    with pytest.raises(ValueError, match="needs the settings it changes"):
        baselines.prepare("prme-small", "locomo", data=harness["data"], archive=harness["archive"])
    prepared = await asyncio.to_thread(baselines.prepare, "prme", "locomo", data=harness["data"],
                                       archive=harness["archive"])
    variant = await asyncio.to_thread(baselines.prepare, "prme-small", "locomo", data=harness["data"],
                                      archive=harness["archive"], overrides=budget)
    folder = arm_folder(harness, "prme")
    report = json.loads((folder / "gate.json").read_text())
    assert "plain" not in report["provenance"] and report["provenance"]["overrides"] == {}
    assert prepared["context_budget"] == 3996 and prepared["context_rule"].startswith("PRME retrieve() with the "
                                                                                      "current defaults, as")
    assert prepared["contexts_matching_saved_run"] == 0  # The test cases name no saved context.
    assert variant["context_budget"] == 1948 and '{"packing": {"token_budget": 2048}}' in variant["context_rule"]
    assert variant["provenance"]["overrides"] == budget
    context = baselines._context(folder, prepared["contexts"][0])
    assert count_tokens(context) == prepared["contexts"][0]["context_tokens"] <= 3996
    requests = ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    requests.clear()
    before = await run_ollama(harness, "prme")
    assert before["complete"] and requests[0][1]["messages"][0]["content"] == study.reader_prompt(
        "locomo", study.question_rows("locomo")[0], context)
    ollama_provider(monkeypatch, verdict="no")
    after = await run_ollama(harness, "prme-small")
    assert ollama_published(harness, "prme-small")
    # Whether this test's own tree is committed does not matter here.
    before, after = ({**result, "prepared": {**result["prepared"], "dirty": False}} for result in (before, after))
    comparison = baselines.compare(before, after)
    assert comparison["arms"] == {"before": "prme", "after": "prme-small"} and comparison["warnings"] == []
    moved = {**after, "prepared": {**after["prepared"], "commit": "0" * 40, "dirty": True}}
    assert len(baselines.compare(before, moved)["warnings"]) == 2
    assert comparison["accuracy"]["delta"] == -1.0 and comparison["lost"] == ["conv-1-q0000", "conv-1-q0001"]
    assert comparison["prepared"]["after"]["overrides"] == budget
    with pytest.raises(ValueError, match="different readers and judges"):
        baselines.compare(before, {**after, "answer_model": {**after["answer_model"], "seed": 1}})
    with pytest.raises(ValueError, match="different questions"):
        baselines.compare(before, {**after, "rows": after["rows"][::-1]})


def test_variant_names_are_checked():
    assert baselines.arm_name("prme", "rrf-k30") == "prme-rrf-k30" and baselines.is_prme("prme-rrf-k30")
    for arm, variant in (("prme", "RRF"), ("prme", "../x"), ("plain-rrf", "x"), ("prme", "")):
        with pytest.raises(ValueError, match="--variant"):
            baselines.arm_name(arm, variant)
    assert not baselines.is_prme("prme-../x")


@pytest.mark.parametrize("argv", [
    ["calibrate"],
    ["calibrate", "prme", "--provider", "ollama"],
    ["calibrate", "--provider", "ollama", "--benchmark", "locomo"],
    ["calibrate", "--provider", "ollama", "--archive", "somewhere"],
    ["run", "prme", "--benchmark", "locomo", "--max-usd", "5"],
    ["run", "full-context", "--sample", "1", "--max-usd", "5"],
    ["run", "--provider", "ollama"],
    ["run", "full-context", "--provider", "ollama", "--max-usd", "5"],
    ["run", "full-context", "--provider", "ollama", "--sample", "0"],
    ["run", "full-context", "--provider", "ollama", "--archive", "somewhere"],
    ["estimate", "full-context", "--provider", "ollama"],
    ["prepare", "full-context", "--provider", "ollama", "--sample", "2"],
    ["prepare", "prme", "--benchmark", "locomo", "--provider", "ollama", "--set", "packing.token_budget=8192"],
    ["prepare", "prme", "--benchmark", "locomo", "--provider", "ollama", "--variant", "big"],
    ["prepare", "plain-rrf", "--benchmark", "locomo", "--provider", "ollama", "--variant", "big"],
    ["compare", "--before", "a.json"],
    ["compare", "prme", "--before", "a.json", "--after", "b.json"],
    ["run", "full-context", "--provider", "ollama", "--before", "a.json"],
])
def test_cli_refuses_invalid_ollama_prme_and_compare_arguments(argv, monkeypatch):
    for name in ("run", "calibrate", "prepare", "compare"):
        monkeypatch.setattr(baselines, name, lambda *args, **kwargs: pytest.fail("started work"))
    monkeypatch.setattr(baselines, "_provenance", lambda: {"dirty": False})
    monkeypatch.setattr(baselines, "_on_main", lambda: True)
    with pytest.raises(SystemExit):
        baselines.main(argv)


def test_cli_prepares_the_defaults_only_from_main_and_variants_anywhere(monkeypatch):
    seen = []
    monkeypatch.setattr(baselines, "_provenance", lambda: {"dirty": False})
    monkeypatch.setattr(baselines.gate, "_quiet_offline_cli", lambda: None)
    monkeypatch.setattr(baselines, "prepare", lambda arm, benchmark, **kwargs: seen.append((arm, kwargs)) or {
        "arm": arm, "benchmark": benchmark, "questions": 0, "context_budget": 3996})
    monkeypatch.setattr(baselines, "_on_main", lambda: False)
    with pytest.raises(SystemExit):
        baselines.main(["prepare", "prme", "--benchmark", "locomo", "--provider", "ollama"])
    baselines.main(["prepare", "prme", "--benchmark", "locomo", "--provider", "ollama", "--variant", "rrf",
                    "--set", 'scoring.fusion="rrf"'])
    monkeypatch.setattr(baselines, "_on_main", lambda: True)
    baselines.main(["prepare", "prme", "--benchmark", "longmemeval", "--provider", "ollama"])
    assert [(arm, kwargs["data"], kwargs["overrides"]) for arm, kwargs in seen] == [
        ("prme-rrf", baselines.data_root(OLLAMA_MODEL), {"scoring": {"fusion": "rrf"}}),
        ("prme", baselines.data_root(OLLAMA_MODEL), None)]


def test_cli_ollama_run_needs_no_terminal_confirmation_or_cap(monkeypatch, capsys):
    monkeypatch.setattr(baselines.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("asked for a spending confirmation"))
    seen = {}

    async def fake_run(arm, benchmark, **kwargs):
        seen.update(arm=arm, benchmark=benchmark, **kwargs)
        return {"complete": True, "rows": [], "failures": []}

    monkeypatch.setattr(baselines, "run", fake_run)
    baselines.main(["run", "prme", "--benchmark", "longmemeval", "--provider", "ollama", "--variant", "rrf",
                    "--sample", "2"])
    assert seen == {"arm": "prme-rrf", "benchmark": "longmemeval", "model": OLLAMA_MODEL, "sample": 2}
    assert "Ollama's hosted service" in capsys.readouterr().err


def test_cli_calibrate_and_compare(monkeypatch, tmp_path, capsys):
    async def fake_calibrate(model):
        return {"complete": True, "model": model.model}

    monkeypatch.setattr(baselines, "calibrate", fake_calibrate)
    baselines.main(["calibrate", "--provider", "ollama"])
    assert json.loads(capsys.readouterr().out) == {"complete": True, "model": OLLAMA_MODEL.model}
    with pytest.raises(SystemExit):
        baselines.main(["compare", "--before", str(tmp_path / "missing.json"), "--after", str(tmp_path / "x.json")])
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    before.write_text(json.dumps({"complete": False}))
    after.write_text(json.dumps({"complete": True}))
    with pytest.raises(SystemExit):
        baselines.main(["compare", "--before", str(before), "--after", str(after)])
    assert "not a complete answer run" in capsys.readouterr().err
