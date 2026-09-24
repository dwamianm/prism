import asyncio
import dataclasses
import fcntl
from datetime import datetime
from functools import partial
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


@pytest.fixture(autouse=True)
def private_data(tmp_path, monkeypatch):
    """No test reads or writes the main checkout's private contexts, answers or run logs."""
    monkeypatch.setattr(baselines, "DATA", tmp_path / "gpt54-baselines")
    monkeypatch.setattr(baselines, "OLLAMA_DATA", tmp_path / "ollama-answers")
    # compare and record_aa_check read and write the A/A record here, where the harness publishes (#137).
    monkeypatch.setattr(baselines, "RESULTS", tmp_path / "results")


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
    monkeypatch.setattr(baselines, "FAILURE_AMENDMENT", public / "ollama-failure-policy-amendment.json")
    amend_registration(reg)
    return {"root": tmp_path, "registration": registration, "reg": reg, "data": tmp_path / "data",
            "results": tmp_path / "results", "archive": archive}


RECORDED_AMENDMENT = baselines.FAILURE_AMENDMENT


def amend_registration(reg: Path) -> None:
    """The recorded failure policy amendment (#132), made against the harness's registration."""
    baselines.FAILURE_AMENDMENT.write_text(json.dumps({**json.loads(RECORDED_AMENDMENT.read_text()),
                                                       "registration_sha256": digest(reg)}))


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


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def fabricate_plain(data: Path, benchmark: str, contexts: dict[str, str], arm: str = "plain-rrf") -> None:
    """Prepared contexts of an arm (plain-rrf by default) in the layout prepare writes, without replaying packs."""
    folder = data / arm / benchmark
    entries = []
    for qid, context in contexts.items():
        path = folder / "contexts" / benchmark / f"{qid}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"question_id": qid, "context": context}))
        entries.append({"question_id": qid, "path": str(path.relative_to(folder)), "sha256": digest(path),
                        "text_sha256": text_sha256(context), "context_tokens": count_tokens(context),
                        "retrieval_seconds": .1})
    (folder / "prepared.json").write_text(json.dumps({
        "kind": "gpt54-baseline-contexts", "complete": True, "arm": arm, "benchmark": benchmark,
        "questions": len(entries), "registration_sha256": digest(study.REG), "tokenizer": "cl100k_base",
        "context_budget": 3996, "context_rule": "Stored turns ranked by RRF.", "contexts": entries}))


async def gate_cases(harness, monkeypatch) -> None:
    """Two LoCoMo questions over one saved pack, which prepare replays through the evidence gate."""
    pack = harness["root"] / "pack"
    identity = await make_gate_pack(pack)
    cases = [dataclasses.replace(gate_case(pack, identity), question_id=qid)
             for qid in ("conv-1-q0000", "conv-1-q0001")]
    monkeypatch.setattr(gate, "_locomo_cases", lambda archive: cases)
    (harness["archive"] / "locomo").mkdir()
    (harness["archive"] / "locomo" / "prepared.json").write_text("{}")


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
    await gate_cases(harness, monkeypatch)
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
# What an Ollama answer binding and result record: the model's settings and identity, and the failure policy.
ANSWER_MODEL = {**OLLAMA_MODEL.settings(), "identity": IDENTITY, "failure_policy": baselines.FAILURE_POLICY}
NOTHING_UNSCORED = {"reader_retries": 0, "judge_retries": 0, "verdicts_normalized": 0, "truncated": 0,
                    "verdict_unresolved": 0, "unscored": 0, "unscored_limit_percent": 1, "within_limit": True}
TWO_LME = [LONGMEM[0], {**LONGMEM[0], "question_id": "q2", "question": "Where do I live?", "answer": "Porto"}]


LOOP = "The answer. " * 3


def ollama_provider(monkeypatch, *, verdict: str = "yes", fail: str | None = None, identities=None,
                    wrong_when: str | None = None, verdicts: list | None = None,
                    truncated: dict[str, int] | None = None, empty_when: str | None = None,
                    fail_requests: set[int] | None = None):
    """Replace the Ollama server; any attempt to build or use the paid OpenAI path fails the test.

    ``identities`` is the sequence of identities the server reports, one per lookup; the last one repeats. A
    reader prompt containing ``wrong_when`` gets the authored wrong answer, which the judge rejects. ``verdicts``
    is the sequence of the judge's replies (the last one repeats) in place of ``verdict``; a reply given as
    ``(text, finish_reason)`` ends for that reason. A reader prompt
    containing a key of ``truncated`` gets that many answers cut off at the token limit before a whole one. A
    reader prompt containing ``empty_when`` gets an empty answer, which is a final failure. The requests at the
    1-based positions in ``fail_requests`` get HTTP 404, as do prompts containing ``fail``.
    """
    requests = []
    reported = list(identities or [IDENTITY])
    loops = dict(truncated or {})
    replies = list(verdicts or [verdict])

    def handler(request):
        body = json.loads(request.content)
        prompt = body["messages"][0]["content"]
        requests.append((str(request.url), body, request.headers.get("authorization")))
        if (fail is not None and fail in prompt) or len(requests) in (fail_requests or ()):
            return httpx.Response(404, json={"error": "model not found"})
        judge = prompt.startswith(("Evaluate the response", "OFFICIAL JUDGE"))
        finish = "stop"
        if judge:
            text = "no" if baselines.AUTHORED_WRONG_ANSWER in prompt else (
                replies.pop(0) if len(replies) > 1 else replies[0])
            if isinstance(text, tuple):
                text, finish = text
        else:
            text = baselines.AUTHORED_WRONG_ANSWER if wrong_when is not None and wrong_when in prompt else "The answer."
            looping = next((key for key, left in loops.items() if left and key in prompt), None)
            if looping is not None:
                loops[looping] -= 1
                text, finish = LOOP, "length"
            elif empty_when is not None and empty_when in prompt:
                text = ""
        return httpx.Response(200, json={
            "object": "chat.completion", "model": "deepseek-v4.1-flash", "usage": OLLAMA_USAGE,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}]})

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


def run_pair(harness, before="prme", after="prme", benchmark="locomo", **kwargs):
    return baselines.run_pair(before, after, benchmark, model=OLLAMA_MODEL, data=harness["data"],
                              results=harness["results"], **kwargs)


def published_paths(harness, before="prme", after="prme", benchmark="locomo", number=1, suffix="") -> list[Path]:
    """Both sides' published result files of one pair, before side first; empty when nothing was published."""
    return [path for side in baselines.PAIR_SIDES for path in harness["results"].glob(
        f"*/{OLLAMA_MODEL.track}-{before}-vs-{after}-{benchmark}-pair-{number}-{side}{suffix}-result.json")]


def pair_published(harness, before="prme", after="prme", benchmark="locomo", number=1, suffix=""):
    """Both sides' published results of one pair, before side first; empty when nothing was published."""
    return [json.loads(path.read_text()) for path in published_paths(harness, before, after, benchmark, number, suffix)]


def use_longmemeval(harness, rows):
    """Register a different LongMemEval-S question set in the harness."""
    study.LONGMEM.write_text(json.dumps(rows))
    registration = json.loads(harness["reg"].read_text())
    registration["datasets"]["longmemeval"]["sha256"] = digest(study.LONGMEM)
    registration["cohort_ids"]["longmemeval"] = [row["question_id"] for row in rows]
    harness["reg"].write_text(json.dumps(registration))
    amendment = study.PUBLIC / "gpt54-official-loader-amendment.json"
    amendment.write_text(json.dumps({**json.loads(amendment.read_text()), "registration_sha256": digest(harness["reg"])}))
    amend_registration(harness["reg"])
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
    assert result["answer_model"] == ANSWER_MODEL
    assert result["answer_model"]["provider"] == "ollama" and result["answer_model"]["seed"] == 20260923
    # The track's amended failure policy (#132), which asked nothing again here.
    assert result["retry_policy"] == baselines.OLLAMA_RETRY_POLICY
    assert result["failure_policy"] == {**baselines.failure_amendment(), **NOTHING_UNSCORED}
    assert {row["outcome"] for row in result["rows"]} == {"judged"}
    assert result["calibration"] == {"attempt": "attempt-1", "attempts": 1, "attempts_before_pass": 0,
                                     "sha256": digest(harness["data"] / "authored-calibration/attempt-1/result.json")}
    assert result["cost"]["usd"] == 0 and "max_usd" not in result
    provenance = json.loads((arm_folder(harness) / "prepared.json").read_text())["provenance"]
    assert result["prepared"] == {"commit": provenance["commit"], "dirty": provenance["dirty"],
                                  "worktree_sha256": provenance["worktree_sha256"], "overrides": {},
                                  "variant_settings": None, "tokenizer": "cl100k_base",
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
    assert log[1]["prepared_commit"] == provenance["commit"]
    # The live server version at the start and at the finish, which the result lists.
    assert [event["server_version"] for event in log] == ["0.34.3", "0.34.3"] and result["server_versions"] == ["0.34.3"]
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
    ollama_provider(monkeypatch, empty_when="Caroline")  # An empty answer is final: the arm can never finish.
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
    for arm, kwargs in (("prme", {}), ("prme@0123abcd", {}), ("prme-rrf", {}), ("full-context", {"sample": 1})):
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
async def test_prme_arms_prepare_the_defaults_or_a_named_variant_through_the_gate(harness, monkeypatch, capsys):
    await gate_cases(harness, monkeypatch)
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
    # A variant that keeps the 4K budget, which the default-change rule reads (#125).
    fusion = gate.parse_overrides(['scoring.fusion="rrf"'])
    kept = await asyncio.to_thread(baselines.prepare, "prme-rrf", "locomo", data=harness["data"],
                                   archive=harness["archive"], overrides=fusion)
    assert kept["context_budget"] == baselines.RULE_BUDGET and kept["tokenizer"] == baselines.RULE_TOKENIZER
    # Every variant preparation stays on record with what it changes, outside the arm's folder; the defaults change
    # nothing (#130). Rank fusion also fills in its rank constant.
    fused = {"scoring.fusion": "rrf", "scoring.rrf_k": 60}
    assert (variant["variant_settings"], kept["variant_settings"]) == ({"packing.token_budget": 2048}, fused)
    assert "variant_settings" not in prepared
    # Variants are also one variant when they send the reader the same text on every question, whatever their
    # settings, as these two may over the test pack's two short contexts; prepare says so.
    same_text = contexts_sha256(harness, "prme-small") == contexts_sha256(harness, "prme-rrf")
    assert ("prme-small locomo was prepared with the same settings or context text, so prme-rrf is the same variant"
            in capsys.readouterr().err) is same_text
    [event] = [json.loads(line) for line in (harness["data"] / "runs/prme-rrf-locomo.jsonl").read_text().splitlines()]
    assert event == {"at": event["at"], "event": "prepared", "variant_settings": fused,
                     "contexts_sha256": contexts_sha256(harness, "prme-rrf"),
                     "prepared_commit": kept["provenance"]["commit"],
                     "prepared_sha256": digest(arm_folder(harness, "prme-rrf") / "prepared.json")}
    assert not (harness["data"] / "runs/prme-locomo.jsonl").exists()
    # The same settings under another name, spelled another way, are the same variant, and prepare says so.
    spelled = gate.parse_overrides(['packing.token_budget="2048"'])
    again = await asyncio.to_thread(baselines.prepare, "prme-small-again", "locomo", data=harness["data"],
                                    archive=harness["archive"], overrides=spelled)
    assert again["variant_settings"] == variant["variant_settings"] and again["provenance"]["overrides"] == spelled
    replaying, notice = [line for line in capsys.readouterr().err.splitlines()
                         if line.startswith(("Replaying the defaults", "prme-"))]
    assert replaying.startswith("Replaying the defaults at this commit as well")
    assert "prepared with the same settings or context text, so prme-small-again is the same variant" in notice
    assert notice.startswith("prme-rrf, prme-small locomo were" if same_text else "prme-small locomo was")
    # Each variant's preparation also replays the defaults at its commit, without captures, and records their text
    # on every question, which is the defaults' own preparation at this commit, question by question (#139). The
    # test pack's contexts may read the same under every setting, so what is recorded where is checked separately.
    defaults = [entry["text_sha256"] for entry in prepared["contexts"]]
    for name, found in (("prme-small", variant), ("prme-rrf", kept), ("prme-small-again", again)):
        assert [entry["defaults_text_sha256"] for entry in found["contexts"]] == defaults
        defaults_report = arm_folder(harness, name) / "defaults-gate.json"
        assert digest(defaults_report) == found["defaults_gate_report_sha256"]
        report = json.loads(defaults_report.read_text())
        assert report["provenance"]["overrides"] == {} and "capture_sha256" not in report["rows"][0]
        assert report["provenance"]["commit"] == found["provenance"]["commit"]
        assert len(list((arm_folder(harness, name) / "contexts").rglob("*.json"))) == len(found["contexts"])
    # The defaults' own preparation replays nothing more.
    assert not (folder / "defaults-gate.json").exists() and "defaults_gate_report_sha256" not in prepared
    assert not any("defaults_text_sha256" in entry for entry in prepared["contexts"])
    # A variant whose every setting is a default would answer the defaults again, so it is refused before any work.
    with pytest.raises(ValueError, match="settings of the prme-noop variant are the defaults' at this commit"):
        baselines.prepare("prme-noop", "locomo", data=harness["data"], archive=harness["archive"],
                          overrides=gate.parse_overrides(["packing.token_budget=4096"]))
    assert not arm_folder(harness, "prme-noop").exists()
    assert not (harness["data"] / "runs/prme-noop-locomo.jsonl").exists()
    requests = ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    requests.clear()
    # A variant is never answered on its own: only alongside a fresh run of the defaults.
    with pytest.raises(ValueError, match="use run-pair"):
        await run_ollama(harness, "prme-rrf")
    with pytest.raises(ValueError, match="use run-pair"):
        await run_ollama(harness, "prme-rrf", sample=1)
    assert requests == []
    # A baseline is recorded by its own answer run before any pair uses it.
    with pytest.raises(ValueError, match="no complete answer run of its own"):
        await run_pair(harness, "prme", "prme-rrf")
    await run_ollama(harness, "prme")
    requests.clear()
    # compare would refuse a variant that changes the budget, so it is never answered, and no pair is opened.
    with pytest.raises(ValueError, match=r"The after arm, prme-small, was prepared with a context budget of 1948 "
                                         r"tokens\. The default-change rule in CLAUDE\.md reads PRME's arms only at "
                                         r"the 4K budget \(3,996 cl100k_base tokens\)"):
        await run_pair(harness, "prme", "prme-small")
    assert requests == [] and not (harness["data"] / "pairs" / "prme" / "prme-small").exists()
    # A variant's pair is answered only under a recorded A/A check (#137).
    harness_aa_checked(harness)
    paired = await run_pair(harness, "prme", "prme-rrf")
    before, after = paired["before"], paired["after"]
    # The first request is the variant's reader, on the variant's own context.
    assert before["complete"] and after["complete"] and requests[0][1]["messages"][0]["content"] == \
        study.reader_prompt("locomo", study.question_rows("locomo")[0],
                            baselines._context(arm_folder(harness, "prme-rrf"), kept["contexts"][0]))
    [published_before, published_after] = pair_published(harness, "prme", "prme-rrf")
    # Each gate replay's capture holds a new receipt id, so no capture hash is shared. Every row, private and
    # published, also carries the hash of the context text alone, next to the capture's (#125).
    for result, entries in ((before, prepared["contexts"]), (after, kept["contexts"])):
        assert [row["context_text_sha256"] for row in result["rows"]] == [entry["text_sha256"] for entry in entries]
    assert list(before["rows"][0])[4:7] == ["context_sha256", "context_text_sha256", "context_tokens"]
    # The variant's rows also carry the defaults' text at its commit (#139).
    assert list(after["rows"][0])[4:8] == ["context_sha256", "context_text_sha256", "defaults_text_sha256",
                                           "context_tokens"]
    assert [row["defaults_text_sha256"] for row in after["rows"]] == defaults
    assert published_before["rows"] == before["rows"] and published_after["rows"] == after["rows"]
    assert all(old["context_sha256"] != new["context_sha256"] for old, new in zip(before["rows"], after["rows"]))
    differing = sum(old["text_sha256"] != new["text_sha256"]
                    for old, new in zip(prepared["contexts"], kept["contexts"]))
    # Whether this test's own tree is committed does not matter here.
    before, after = ({**result, "prepared": {**result["prepared"], "dirty": False}} for result in (before, after))
    comparison = baselines.compare(before, after, data=harness["data"])
    assert comparison["arms"] == {"before": "prme", "after": "prme-rrf"} and comparison["warnings"] == []
    assert (comparison["variant"]["variant_settings"], comparison["variant"]["role"]) == (fused, "first")
    assert comparison["baseline"]["current"] == "prme"
    assert comparison["pair"]["number"] == 1 and comparison["repeat"] is None
    # The defaults at the variant's commit read the before side's text on every question, so no other code changed
    # what the two sides read (#139).
    assert {key: value for key, value in comparison["contexts"].items() if key != "note"} == {
        "questions": 2, "differing": differing, "shown_by": "text hashes", "changed_by_other_code": 0}
    assert comparison["prepared"]["after"]["tokenizer"] == baselines.RULE_TOKENIZER
    # Another commit's contexts are not flagged while the defaults at that commit read the before side's text.
    moved = {**after, "prepared": {**after["prepared"], "commit": "0" * 40, "dirty": True}}
    warnings = baselines.compare(before, moved, data=harness["data"])["warnings"]
    assert any("uncommitted changes" in warning for warning in warnings)
    assert not any("different commits" in warning for warning in warnings)
    # The rows the defaults' replay would change are refused (#139).
    other = {**after, "rows": [{**row, "defaults_text_sha256": "0" * 64} for row in after["rows"]]}
    with pytest.raises(ValueError, match="On 2 of 2 questions the defaults at the commit that prepared prme-rrf"):
        baselines.compare(before, other, data=harness["data"])
    assert comparison["accuracy"]["delta"] == 0 and comparison["gained"] == comparison["lost"] == []
    assert comparison["prepared"]["after"]["overrides"] == fusion
    # A result relabeled with another budget or tokenizer is refused too.
    with pytest.raises(ValueError, match=r"after result, prme-rrf, was prepared with a context budget of 1948"):
        baselines.compare(before, {**after, "context_budget": 1948})
    with pytest.raises(ValueError, match=r"prepared with its budget counted by the 'o200k_base' tokenizer"):
        baselines.compare(before, {**after, "prepared": {**after["prepared"], "tokenizer": "o200k_base"}})
    with pytest.raises(ValueError, match="different readers and judges"):
        baselines.compare(before, {**after, "answer_model": {**after["answer_model"], "seed": 1}})
    with pytest.raises(ValueError, match="different questions"):
        baselines.compare(before, {**after, "rows": after["rows"][::-1]})


@pytest.mark.usefixtures("mock_embeddings")
async def test_a_later_defaults_baseline_is_filed_under_its_commit_and_leaves_the_first_alone(harness, monkeypatch):
    await gate_cases(harness, monkeypatch)
    data = harness["data"]
    # The earlier commit below is not a real git object, so the ancestry git would report is given here (#127).
    ancestry = []
    monkeypatch.setattr(baselines, "_descends", lambda commit, ancestor: ancestry.append((commit, ancestor)) or True)

    def prepare(arm, **kwargs):
        return asyncio.to_thread(baselines.prepare, arm, "locomo", data=data, archive=harness["archive"], **kwargs)

    first = await prepare("prme")
    head = first["provenance"]["commit"]
    arm = f"prme@{head[:8]}"
    # A later baseline needs a complete first one; the refusal comes before any work.
    with pytest.raises(ValueError, match=f"are the prme locomo baseline, not {arm}"):
        await prepare(arm)
    assert not arm_folder(harness, arm).exists()
    # The first baseline came from an earlier commit on main, before a default changed.
    earlier = "0" * 40
    (arm_folder(harness, "prme") / "prepared.json").write_text(
        json.dumps({**first, "provenance": {**first["provenance"], "commit": earlier}}))
    requests = ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=data)
    before = await run_ollama(harness, "prme")
    first_log = data / "runs" / "prme-locomo.jsonl"
    recorded = digest(first_log)
    assert before["complete"] and before["prepared"]["commit"] == earlier
    assert baselines.baseline_arm("locomo", earlier, data=data) == "prme"
    assert baselines.baseline_arm("locomo", head, data=data) == arm
    with pytest.raises(ValueError, match="name a variant"):
        await prepare(arm, overrides=gate.parse_overrides(["packing.token_budget=2048"]))
    # A name other than the checked-out commit's is refused before any work, and again against the commit the
    # gate records, so a later baseline's name always matches the commit that prepared its contexts.
    wrong_commit = f"{(int(head[:8], 16) + 1) % 16 ** 8:08x}{head[8:]}"
    wrong = f"prme@{wrong_commit[:8]}"
    with pytest.raises(ValueError, match=f"are the {arm} locomo baseline, not {wrong}"):
        await prepare(wrong)
    assert not arm_folder(harness, wrong).exists()
    with monkeypatch.context() as checked_out:
        checked_out.setattr(baselines, "_provenance", lambda: {"commit": wrong_commit})
        with pytest.raises(ValueError, match=f"The defaults at {head} are the {arm} locomo baseline, not {wrong}"):
            await prepare(wrong)
    assert not (arm_folder(harness, wrong) / "prepared.json").exists()
    assert not (data / "runs" / f"{wrong}-locomo.jsonl").exists()
    ancestry.clear()
    later = await prepare(arm)
    assert later["arm"] == arm and later["provenance"]["commit"] == head
    # The new baseline was checked against the complete one, before and after its contexts were built.
    assert ancestry == [(head, earlier), (head, earlier)]
    assert (later["context_budget"], later["context_rule"]) == (first["context_budget"], first["context_rule"])
    [event] = [json.loads(line) for line in (data / "runs" / f"{arm}-locomo.jsonl").read_text().splitlines()]
    assert event == {"at": event["at"], "event": "new-baseline", "prepared_commit": head,
                     "first_baseline_commit": earlier, "abandoned": []}
    requests.clear()
    after = await run_ollama(harness, arm)
    assert after["complete"] and after["arm"] == arm and len(requests) == 4
    assert after["run_log"]["new_baseline"] == {key: value for key, value in event.items() if key != "event"}
    assert (after["run_log"]["runs_started"], after["run_log"]["prepared_again"]) == (1, 0)
    assert ollama_published(harness, arm) and ollama_published(harness, "prme")
    # The first baseline's record is untouched, and a variant pairs with the later baseline as usual.
    assert digest(first_log) == recorded and "new_baseline" not in before["run_log"]
    # These test cases name no saved context, so matching the saved run shows nothing, but the rows' text hashes
    # show both baselines read the same text, so two baselines answered on their own compare as a repeat (#125).
    assert before["prepared"]["contexts_matching_saved_run"] == after["prepared"]["contexts_matching_saved_run"] == 0
    repeat = baselines.compare(before, after, data=data)
    assert repeat["repeat"] is not None and repeat["contexts"]["shown_by"] == "text hashes"
    # The later baseline is now the current one; a repeat may still pair it with the first (#127).
    assert [baseline["arm"] for baseline in repeat["baseline"]["complete"]] == ["prme", arm]
    assert repeat["baseline"]["current"] == arm
    assert repeat["contexts"]["differing"] == 0
    assert [row["context_sha256"] for row in before["rows"]] != [row["context_sha256"] for row in after["rows"]]
    # Without the text hashes, as in results published before them, nothing shows it and they are refused.
    legacy = [{**result, "rows": [{key: value for key, value in row.items() if key != "context_text_sha256"}
                                  for row in result["rows"]]} for result in (before, after)]
    with pytest.raises(ValueError, match="alongside it in one pair"):
        baselines.compare(*legacy)
    # Neither baseline is prepared again, so each commit keeps one baseline.
    for name in ("prme", arm):
        shutil.rmtree(arm_folder(harness, name))
        with pytest.raises(ValueError, match="complete answer run"):
            await prepare(name)
    assert baselines.baseline_arm("locomo", head, data=data) == arm


@pytest.mark.usefixtures("mock_embeddings")
async def test_an_unfinished_later_baseline_is_finished_or_given_up_on_record(harness, monkeypatch):
    await gate_cases(harness, monkeypatch)
    data = harness["data"]
    monkeypatch.setattr(baselines, "_descends", lambda commit, ancestor: True)

    def prepare(arm):
        return asyncio.to_thread(baselines.prepare, arm, "locomo", data=data, archive=harness["archive"])

    head = baselines._provenance()["commit"]
    arm = f"prme@{head[:8]}"
    answered(data, "locomo", "0" * 40)
    # Another later baseline, prepared at an earlier commit, stopped before it finished.
    stopped = "prme@11111111" if not head.startswith("11111111") else "prme@22222222"
    (data / stopped / "locomo").mkdir(parents=True)
    (data / stopped / "locomo" / "prepared.json").write_text("{}")
    for event in ({"event": "new-baseline"}, {"event": "started", "sample": None},
                  {"event": "finished", "sample": None, "complete": False}):
        baselines._log_run(data, stopped, "locomo", event)
    # Restarting after main moved on does not quietly start a second baseline.
    with pytest.raises(ValueError, match=f"{stopped} locomo is a baseline that is prepared and not complete"):
        await prepare(arm)
    assert not arm_folder(harness, arm).exists()
    # Giving it up means moving its folder aside, and the next baseline's run log records it.
    shutil.rmtree(data / stopped)
    await prepare(arm)
    # Preparing again before any run is neither a restart nor another new baseline.
    shutil.rmtree(arm_folder(harness, arm))
    await prepare(arm)
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=data)
    ollama_provider(monkeypatch, empty_when="Caroline")  # An empty answer is final: the arm can never finish.
    with pytest.raises(RuntimeError, match="final failures"):
        await run_ollama(harness, arm)
    shutil.rmtree(arm_folder(harness, arm))
    await prepare(arm)
    ollama_provider(monkeypatch)
    result = await run_ollama(harness, arm)
    log = [json.loads(line) for line in (data / "runs" / f"{arm}-locomo.jsonl").read_text().splitlines()]
    assert [event["event"] for event in log] == [
        "new-baseline", "started", "finished", "prepared-again", "started", "finished"]
    assert result["complete"] and result["run_log"]["new_baseline"]["abandoned"] == [
        {"arm": stopped, "runs_started": 1}]
    assert (result["run_log"]["runs_started"], result["run_log"]["prepared_again"]) == (2, 1)


def test_arm_names_are_checked():
    assert baselines.arm_name("prme", "rrf-k30") == "prme-rrf-k30" and baselines.is_prme("prme-rrf-k30")
    for arm, variant in (("prme", "RRF"), ("prme", "../x"), ("plain-rrf", "x"), ("prme", "")):
        with pytest.raises(ValueError, match="--variant"):
            baselines.arm_name(arm, variant)
    assert not baselines.is_prme("prme-../x")
    assert baselines.is_baseline("prme") and baselines.is_baseline("prme@0123abcd")
    assert baselines.is_prme("prme@0123abcd") and not baselines.is_baseline("prme-rrf")
    for arm in ("prme@0123ABCD", "prme@0123abc", "prme@0123abcde", "prme@", "prme@../x"):
        assert not baselines.is_baseline(arm) and not baselines.is_prme(arm)


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
    ["run", "prme", "--benchmark", "locomo", "--provider", "ollama", "--variant", "rrf"],
    ["run", "prme", "--benchmark", "locomo", "--provider", "ollama", "--baseline", "prme"],
    ["run-pair", "prme", "--benchmark", "locomo", "--baseline", "prme"],
    ["run-pair", "prme", "--benchmark", "locomo", "--provider", "ollama"],
    ["run-pair", "prme", "--benchmark", "locomo", "--provider", "ollama", "--baseline", "prme-rrf"],
    ["run-pair", "prme", "--benchmark", "locomo", "--provider", "ollama", "--baseline", "prme@0123ABCD"],
    ["run-pair", "prme", "--benchmark", "locomo", "--provider", "ollama", "--baseline", "prme", "--archive", "x"],
    ["run-pair", "prme", "--benchmark", "locomo", "--provider", "ollama", "--baseline", "prme", "--set", "a=1"],
    ["run-pair", "prme", "--benchmark", "locomo", "--provider", "ollama", "--baseline", "prme", "--sample", "0"],
    ["run-pair", "--benchmark", "locomo", "--provider", "ollama", "--baseline", "prme"],
    ["compare", "--before", "a.json", "--after", "b.json", "--baseline", "prme"],
    ["prepare", "prme", "--benchmark", "locomo", "--provider", "ollama", "--baseline", "prme"],
])
def test_cli_refuses_invalid_ollama_prme_and_compare_arguments(argv, monkeypatch):
    for name in ("run", "run_pair", "calibrate", "prepare", "compare"):
        monkeypatch.setattr(baselines, name, lambda *args, **kwargs: pytest.fail("started work"))
    monkeypatch.setattr(baselines, "_provenance", lambda: {"dirty": False})
    monkeypatch.setattr(baselines, "_on_main", lambda: True)
    with pytest.raises(SystemExit):
        baselines.main(argv)


def test_cli_prepares_the_defaults_only_from_main_and_variants_anywhere(monkeypatch):
    seen = []
    monkeypatch.setattr(baselines, "_provenance", lambda: {"dirty": False, "commit": "1" * 40})
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


def answered(data: Path, benchmark: str, commit: str | None, arm: str = "prme", *, at: str | None = None) -> None:
    """A complete answer run in the arm's run log, finished ``at`` (by default now).

    ``commit`` None writes an older finished event, without the commit.
    """
    baselines._log_run(data, arm, benchmark, {"event": "finished", "sample": None, "complete": True, "completed": 1,
                                              "total": 1, **({} if commit is None else {"prepared_commit": commit}),
                                              **({} if at is None else {"at": at})})


def test_the_defaults_at_another_commit_are_a_new_baseline_once_prme_is_answered(tmp_path, monkeypatch):
    monkeypatch.setattr(baselines, "_descends", lambda commit, ancestor: True)
    first, later = "a" * 40, "0123abcd" + "9" * 32
    assert baselines.baseline_arm("locomo", later, data=tmp_path) == "prme"
    # A sample, an incomplete run or the other benchmark's complete run leaves the prme arm open.
    for event in ({"sample": 2, "complete": True}, {"sample": None, "complete": False}):
        baselines._log_run(tmp_path, "prme", "locomo", {"event": "finished", "prepared_commit": first, **event})
    answered(tmp_path, "longmemeval", first)
    assert baselines.baseline_arm("locomo", later, data=tmp_path) == "prme"
    answered(tmp_path, "locomo", first)
    # At the first baseline's own commit the arm stays prme, which prepare and run refuse once complete.
    assert baselines.baseline_arm("locomo", first, data=tmp_path) == "prme"
    assert baselines.baseline_arm("locomo", later, data=tmp_path) == "prme@0123abcd"
    for commit in (None, "", "0123", "0123ABCD" + "9" * 32):
        with pytest.raises(ValueError, match="cannot name a new one"):
            baselines.baseline_arm("locomo", commit, data=tmp_path)
    # A prepared later baseline without a complete run holds every other commit until it finishes or is moved.
    (tmp_path / "prme@0123abcd" / "locomo").mkdir(parents=True)
    (tmp_path / "prme@0123abcd" / "locomo" / "prepared.json").write_text("{}")
    assert baselines.baseline_arm("locomo", later, data=tmp_path) == "prme@0123abcd"
    with pytest.raises(ValueError, match="Check out commit 0123abcd to answer it"):
        baselines.baseline_arm("locomo", "4567cdef" + "9" * 32, data=tmp_path)
    assert baselines.baseline_arm("longmemeval", "4567cdef" + "9" * 32, data=tmp_path) == "prme@4567cdef"
    answered(tmp_path, "locomo", later, arm="prme@0123abcd")
    assert baselines.baseline_arm("locomo", "4567cdef" + "9" * 32, data=tmp_path) == "prme@4567cdef"
    # Run logs written before the finished event recorded the commit: the complete arm's manifest has it.
    legacy = tmp_path / "legacy"
    answered(legacy, "locomo", None)
    with pytest.raises(ValueError, match="records the commit"):
        baselines.baseline_arm("locomo", later, data=legacy)
    (legacy / "prme" / "locomo").mkdir(parents=True)
    (legacy / "prme" / "locomo" / "prepared.json").write_text(json.dumps({"provenance": {"commit": first}}))
    assert baselines.baseline_arm("locomo", first, data=legacy) == "prme"
    assert baselines.baseline_arm("locomo", later, data=legacy) == "prme@0123abcd"


def test_the_current_baseline_is_the_one_whose_own_answer_run_completed_last(tmp_path):
    assert baselines._complete_baselines(tmp_path, "locomo") == []
    assert baselines.current_baseline(tmp_path, "locomo") is None
    first, second, third = "a" * 40, "0123abcd" + "9" * 32, "4567cdef" + "9" * 32
    answered(tmp_path, "locomo", first, at="2026-09-24T03:07:22+00:00")
    # A sample, an incomplete run, a baseline that is only prepared and the other benchmark's runs record nothing.
    for event in ({"sample": 2, "complete": True, "at": "2026-09-24T04:00:00+00:00"},
                  {"sample": None, "complete": False, "at": "2026-09-24T04:30:00+00:00"}):
        baselines._log_run(tmp_path, "prme@0123abcd", "locomo", {"event": "finished", "prepared_commit": second,
                                                                 **event})
    (tmp_path / "prme@89abcdef" / "locomo").mkdir(parents=True)
    (tmp_path / "prme@89abcdef" / "locomo" / "prepared.json").write_text("{}")
    answered(tmp_path, "longmemeval", third, arm="prme@4567cdef", at="2026-09-25T00:00:00+00:00")
    assert baselines._complete_baselines(tmp_path, "locomo") == [
        {"arm": "prme", "commit": first, "finished_at": "2026-09-24T03:07:22+00:00"}]
    assert baselines.current_baseline(tmp_path, "locomo") == "prme"
    # The order the runs completed in decides, not the names.
    answered(tmp_path, "locomo", third, arm="prme@4567cdef", at="2026-09-24T05:27:14.042424+00:00")
    answered(tmp_path, "locomo", second, arm="prme@0123abcd", at="2026-09-24T05:51:15+00:00")
    assert [(baseline["arm"], baseline["commit"]) for baseline in baselines._complete_baselines(tmp_path, "locomo")] \
        == [("prme", first), ("prme@4567cdef", third), ("prme@0123abcd", second)]
    assert baselines.current_baseline(tmp_path, "locomo") == "prme@0123abcd"
    assert baselines.current_baseline(tmp_path, "longmemeval") == "prme@4567cdef"
    # A complete pair answers a baseline again, but only the baseline's own answer run records it.
    baselines._append_event(baselines._pair_log_path(tmp_path, "prme@ffffffff", "prme-x", "locomo"), {
        "event": "finished", "pair": 1, "sample": None, "complete": True, "at": "2026-09-26T00:00:00+00:00"})
    assert baselines.current_baseline(tmp_path, "locomo") == "prme@0123abcd"
    # An older run log without the commit, whose manifest was moved aside, still orders; its commit is unknown.
    legacy = tmp_path / "legacy"
    answered(legacy, "locomo", None)
    assert [baseline["commit"] for baseline in baselines._complete_baselines(legacy, "locomo")] == [None]
    assert baselines.current_baseline(legacy, "locomo") == "prme"
    # A finish time without a time zone cannot be ordered against the others, so nothing is chosen from it.
    for at in ("2026-09-24T06:00:00", "yesterday"):
        answered(tmp_path / at, "locomo", first, at=at)
        with pytest.raises(ValueError, match="does not record when its complete run finished"):
            baselines.current_baseline(tmp_path / at, "locomo")


def test_a_later_baseline_must_descend_from_every_complete_baseline(tmp_path, monkeypatch):
    first, older, mid, side, newer = ("a" * 40, *(f"{prefix}{'9' * 32}" for prefix in (
        "4567cdef", "0123abcd", "89abcdef", "cdef0123")))
    # older is first's parent, mid and side each descend from first apart from each other, and newer from mid.
    parents = {first: older, mid: first, side: first, newer: mid}

    def descends(commit, ancestor):
        while commit is not None and commit != ancestor:
            commit = parents.get(commit)
        return commit is not None

    monkeypatch.setattr(baselines, "_descends", descends)
    answered(tmp_path, "locomo", first)
    # An older commit on main is not a newer baseline of the defaults (#127).
    with pytest.raises(ValueError, match=f"The defaults at {older} do not descend from {first}, which prepared the "
                                         "complete prme locomo baseline"):
        baselines.baseline_arm("locomo", older, data=tmp_path)
    # Nor is it the other benchmark's first baseline, which would then hold older defaults than this one's.
    with pytest.raises(ValueError, match="complete prme locomo baseline"):
        baselines.baseline_arm("longmemeval", older, data=tmp_path)
    assert baselines.baseline_arm("longmemeval", first, data=tmp_path) == "prme"
    assert baselines.baseline_arm("locomo", mid, data=tmp_path) == "prme@0123abcd"
    answered(tmp_path, "locomo", mid, arm="prme@0123abcd")
    # Every complete baseline counts, not only the first.
    with pytest.raises(ValueError, match=f"do not descend from {mid}, which prepared the complete prme@0123abcd "
                                         "locomo baseline"):
        baselines.baseline_arm("locomo", side, data=tmp_path)
    assert baselines.baseline_arm("locomo", newer, data=tmp_path) == "prme@cdef0123"
    answered(tmp_path, "locomo", newer, arm="prme@cdef0123")
    # A complete baseline's own commit still names it, so prepare and run give their own refusals for it.
    assert baselines.baseline_arm("locomo", mid, data=tmp_path) == "prme@0123abcd"
    assert baselines.baseline_arm("locomo", first, data=tmp_path) == "prme"
    # The other benchmark's baselines must descend from this one's newest too.
    with pytest.raises(ValueError, match="complete prme@cdef0123 locomo baseline"):
        baselines.baseline_arm("longmemeval", mid, data=tmp_path)
    assert baselines.baseline_arm("longmemeval", newer, data=tmp_path) == "prme"
    # A complete baseline that records no commit leaves nothing to check against, so nothing new is recorded.
    answered(tmp_path, "longmemeval", None, arm="prme@cdef0123")
    with pytest.raises(ValueError, match="The prme@cdef0123 longmemeval baseline records no commit"):
        baselines.baseline_arm("longmemeval", newer, data=tmp_path)


def test_descends_asks_git_and_refuses_what_git_cannot_tell(tmp_path, monkeypatch):
    import subprocess

    def git(*args):
        return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "-c",
                               "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args], cwd=tmp_path,
                              check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    commits = {}
    for name in ("base", "main"):
        git("commit", "-q", "--allow-empty", "-m", name)
        commits[name] = git("rev-parse", "HEAD")
    git("checkout", "-q", "-b", "side", commits["base"])
    git("commit", "-q", "--allow-empty", "-m", "side")
    commits["side"] = git("rev-parse", "HEAD")
    monkeypatch.setattr(study, "ROOT", tmp_path)
    descends = baselines._descends
    assert descends(commits["main"], commits["base"]) and descends(commits["main"], commits["main"])
    assert not descends(commits["base"], commits["main"]) and not descends(commits["side"], commits["main"])
    with pytest.raises(ValueError, match="git cannot tell whether .* Run git fetch first"):
        descends(commits["main"], "0" * 40)
    # Only a full commit name reaches git, so a recorded value can never pass as an option, a branch or a tag.
    git("tag", commits["side"][:8], commits["side"])
    for value in ("--output=x", "HEAD", "", None, "0" * 6, commits["side"][:8], commits["main"].upper()):
        with pytest.raises(ValueError, match="is not a full commit name"):
            descends(commits["main"], value)
    # Without git on the path, the check says so instead of failing with an unrelated error.
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    with pytest.raises(ValueError, match="git is needed"):
        descends(commits["main"], commits["base"])


async def test_run_never_completes_a_baseline_older_than_a_complete_one(harness, monkeypatch):
    recorded_baseline(harness, "locomo", DEFAULTS_TEXT)
    stale = "prme@0123abcd"
    fabricate_plain(harness["data"], "locomo", DEFAULTS_TEXT, arm=stale)
    requests = ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    requests.clear()
    monkeypatch.setattr(baselines, "_descends", lambda commit, ancestor: False)
    # Called directly, run checks the commit that prepared the baseline, which must be on record (#127).
    with pytest.raises(ValueError, match=f"The {stale} locomo baseline records no commit"):
        await run_ollama(harness, stale)
    manifest = arm_folder(harness, stale) / "prepared.json"
    manifest.write_text(json.dumps({**json.loads(manifest.read_text()),
                                    "provenance": {"commit": "0123abcd" + "9" * 32}}))
    with pytest.raises(ValueError, match="do not descend from a{40}, which prepared the complete prme locomo baseline"):
        await run_ollama(harness, stale)
    assert requests == [] and [event["event"] for event in baselines._run_events(
        harness["data"] / "runs" / f"{stale}-locomo.jsonl")] == []
    # A sample records no baseline, so it is not refused.
    sample = await run_ollama(harness, stale, sample=1)
    assert sample["kind"].endswith("-sample") and sample["completed"] == sample["total"] == 2
    # One that descends from it is answered as usual, and becomes the current baseline.
    monkeypatch.setattr(baselines, "_descends", lambda commit, ancestor: True)
    assert (await run_ollama(harness, stale))["complete"]
    assert baselines.current_baseline(harness["data"], "locomo") == stale


def test_cli_prepares_and_runs_the_baseline_of_the_checked_out_commit(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(baselines, "_on_main", lambda: True)
    monkeypatch.setattr(baselines, "_descends", lambda commit, ancestor: True)
    monkeypatch.setattr(baselines.gate, "_quiet_offline_cli", lambda: None)
    monkeypatch.setattr(baselines, "prepare", lambda arm, benchmark, **kwargs: seen.append(arm) or {
        "arm": arm, "benchmark": benchmark, "questions": 0, "context_budget": 3996})

    async def fake_run(arm, benchmark, **kwargs):
        seen.append(arm)
        return {"complete": True, "rows": [], "failures": []}

    monkeypatch.setattr(baselines, "run", fake_run)

    def checked_out(commit):
        monkeypatch.setattr(baselines, "_provenance", lambda: {"dirty": False, "commit": commit})

    answered(baselines.data_root(OLLAMA_MODEL), "locomo", "a" * 40)
    checked_out("0123abcd" + "9" * 32)
    for argv in (["prepare", "prme", "--benchmark", "locomo"], ["run", "prme", "--benchmark", "locomo"],
                 ["prepare", "prme", "--benchmark", "longmemeval"]):
        baselines.main([*argv, "--provider", "ollama"])
    assert seen == ["prme@0123abcd", "prme@0123abcd", "prme"]
    output = capsys.readouterr()
    assert output.err.count("are the baseline prme@0123abcd") == 2 and '"arm": "prme@0123abcd"' in output.out
    with pytest.raises(SystemExit):
        baselines.main(["run", "prme", "--benchmark", "locomo", "--variant", "rrf", "--provider", "ollama"])
    assert "use run-pair" in capsys.readouterr().err
    checked_out("a" * 40)
    baselines.main(["prepare", "prme", "--benchmark", "locomo", "--provider", "ollama"])
    assert seen[-1] == "prme"
    checked_out(None)
    for command in ("prepare", "run"):
        with pytest.raises(SystemExit):
            baselines.main([command, "prme", "--benchmark", "locomo", "--provider", "ollama"])
    assert len(seen) == 4 and "cannot name a new one" in capsys.readouterr().err


def test_cli_ollama_run_needs_no_terminal_confirmation_or_cap(monkeypatch, capsys):
    monkeypatch.setattr(baselines.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("asked for a spending confirmation"))
    seen = {}

    async def fake_run(arm, benchmark, **kwargs):
        seen.update(arm=arm, benchmark=benchmark, **kwargs)
        return {"complete": True, "rows": [], "failures": []}

    monkeypatch.setattr(baselines, "run", fake_run)
    baselines.main(["run", "plain-rrf", "--benchmark", "longmemeval", "--provider", "ollama", "--sample", "2"])
    assert seen == {"arm": "plain-rrf", "benchmark": "longmemeval", "model": OLLAMA_MODEL, "sample": 2}
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
    before.write_text(json.dumps(answer_result("prme", [True, False])))
    after.write_text(json.dumps(answer_result("prme@0123abcd", [False, False], commit="b" * 40)))
    baselines.main(["compare", "--before", str(before), "--after", str(after), "--provider", "ollama"])
    assert json.loads(capsys.readouterr().out)["repeat"]["changed_verdicts"] == 1


def answer_result(arm: str, verdicts: list[bool], *, commit: str = "a" * 40, matching: int | None = None,
                  budget: int | None = baselines.RULE_BUDGET, identity: dict | None = None, modules: dict | None = None,
                  texts: list[str] | None = None, defaults: list[str | None] | None = None) -> dict:
    """A complete Ollama answer result with one row per verdict, as compare() reads it.

    ``texts`` gives each row's context text hash; without it the rows have none, as before #125. ``defaults`` gives
    each row's hash of the defaults' text at the variant's commit, as a variant prepared since #139 has.
    """
    rows = [{"question_id": f"q{number}", "question_type": "single-hop", "cluster": f"conv-{number % 2}",
             "correct": verdict, "reader_sha256": f"{arm}-{number}"} for number, verdict in enumerate(verdicts)]
    for row, text in (zip(rows, texts, strict=True) if texts is not None else ()):
        row["context_text_sha256"] = text
    for row, text in (zip(rows, defaults, strict=True) if defaults is not None else ()):
        row["defaults_text_sha256"] = text
    return {"kind": "ollama-answer-result", "arm": arm, "benchmark": "locomo", "model": OLLAMA_MODEL.model,
            "registration_sha256": "r" * 64, "complete": True, "context_budget": budget,
            "answer_model": {**OLLAMA_MODEL.settings(), "identity": IDENTITY if identity is None else identity},
            "modules": {path: "m" * 64 for path in baselines.ANSWER_MODULES} if modules is None else modules,
            "rows": rows, "prepared": {"commit": commit, "dirty": False, "overrides": {}, "tokenizer": "cl100k_base",
                                       "contexts_matching_saved_run": len(rows) if matching is None else matching}}


def test_two_runs_of_the_defaults_with_the_same_inputs_compare_as_a_repeat():
    first = answer_result("prme", [True, True, False, False])
    again = answer_result("prme@0123abcd", [True, False, True, False], commit="b" * 40)
    comparison = baselines.compare(first, again)
    # Both preparations reproduce every saved context, so the commits that prepared them change nothing.
    assert comparison["warnings"] == []
    assert comparison["accuracy"]["delta"] == 0 and comparison["gained"] == ["q2"] and comparison["lost"] == ["q1"]
    assert {key: value for key, value in comparison["repeat"].items() if key != "note"} == {
        "changed_verdicts": 2, "interval_excludes_zero": False, "interleaved": False}
    # Every verdict moving the same way, in either direction, is more than run-to-run variation should give.
    for before, after in (([False] * 4, [True] * 4), ([True] * 4, [False] * 4)):
        drift = baselines.compare(answer_result("prme", before),
                                  answer_result("prme@0123abcd", after, commit="b" * 40))
        assert drift["repeat"] == {**drift["repeat"], "changed_verdicts": 4, "interval_excludes_zero": True}


def test_two_baselines_answered_on_their_own_are_compared_only_as_a_repeat_with_the_same_inputs():
    first = answer_result("prme", [True, False])
    modules = {path: "m" * 64 for path in baselines.ANSWER_MODULES}
    moved_code = {**modules, "benchmarks/integrations/ollama_answers.py": "n" * 64}
    for after in (answer_result("prme@0123abcd", [False, False], commit="b" * 40, matching=1),
                  answer_result("prme@0123abcd", [False, False], commit="b" * 40, modules=moved_code),
                  answer_result("prme@0123abcd", [False, False], commit="b" * 40, modules={})):
        with pytest.raises(ValueError, match="alongside it in one pair"):
            baselines.compare(first, after)
    # A variant answered on its own is never paired with the defaults (#129).
    with pytest.raises(ValueError, match="alongside it in one pair"):
        baselines.compare(first, answer_result("prme-rrf", [False, False]))


def one_pair(before: dict, after: dict) -> tuple[dict, dict]:
    """Two results marked as the sides of one run-pair pair."""
    mark = {"id": "p", "number": 1, "before": before["arm"], "after": after["arm"], "sha256": "s"}
    return {**before, "pair": {**mark, "side": "before"}}, {**after, "pair": {**mark, "side": "after"}}


def logged_pair(before: str = "prme", after: str = "prme-rrf", *, settings: dict | None = None,
                contexts: str | None = None, pair_id: str | None = None, started: str | None = None,
                abandoned: str | None = None, **finished) -> None:
    """Pair 1 of the two arms in the track's pair run log: complete, unless ``finished`` says otherwise or it was
    ``abandoned`` for that reason.

    Its start records the pair id and the variant identity given, as a variant's pair start does since #130, at
    ``started``; ``finished`` can set when it finished (``at``).
    """
    log = baselines._pair_log_path(baselines.data_root(OLLAMA_MODEL), before, after, "locomo")
    recorded = {"id": pair_id, "variant_settings": settings, "contexts_sha256": contexts, "at": started}
    baselines._append_event(log, {"event": "started", "pair": 1, "sample": None,
                                  **{key: value for key, value in recorded.items() if value is not None}})
    baselines._append_event(log, {"event": "finished", "pair": 1, "sample": None, "complete": abandoned is None,
                                  **finished})
    if abandoned is not None:
        baselines._append_event(log, {"event": "abandoned", "pair": 1, "reason": abandoned})


AA_FINISHED = "2026-09-24T09:00:00+00:00"
LATER_AA = "2026-09-24T10:00:00+00:00"


def aa_published(like: dict, benchmark: str, *, baseline: str = "prme@00aa00aa", number: int = 1,
                 excludes_zero: bool = False, **changes) -> list[Path]:
    """Both sides of an A/A pair of ``baseline`` under the conditions of the result ``like``, published under
    ``RESULTS`` as run-pair publishes them, before side first.

    Both sides give ``like``'s verdicts, or with ``excludes_zero`` the after side gets every question right and
    the before side none. ``changes`` replace fields of both sides.
    """
    mark = {"id": f"aa-{baseline}-{benchmark}-{number}", "number": number, "before": baseline, "after": baseline,
            "sha256": "s"}
    paths = []
    for side in baselines.PAIR_SIDES:
        rows = [{**row, "reader_sha256": f"{side}-{index}",
                 "correct": side == "after" if excludes_zero else row["correct"],
                 **({"outcome": "judged", "reader_retry_sha256": None, "judge_retry_sha256": None,
                     "verdict_normalized": False} if "outcome" in row else {})}
                for index, row in enumerate(like["rows"])]
        result = {**like, "arm": baseline, "benchmark": benchmark, "prepared_sha256": "p" * 64, "rows": rows,
                  "pair": {**mark, "side": side}, **changes}
        path = (baselines.RESULTS / "2026-09-24" / f"{OLLAMA_MODEL.track}-{baseline}-vs-{baseline}-{benchmark}-"
                                                   f"pair-{number}-{side}-result.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result))
        paths.append(path)
    return paths


def aa_logged(data: Path, benchmark: str, *, baseline: str = "prme@00aa00aa", number: int = 1,
              finished: str = AA_FINISHED, pair_id: str | None = None, **fields) -> None:
    """The A/A pair's start and complete finish in the track's pair run log; ``fields`` join the finish."""
    log = baselines._pair_log_path(data, baseline, baseline, benchmark)
    baselines._append_event(log, {"event": "started", "pair": number, "sample": None,
                                  "id": pair_id or f"aa-{baseline}-{benchmark}-{number}"})
    baselines._append_event(log, {"event": "finished", "pair": number, "sample": None, "complete": True,
                                  "at": finished, **fields})


def aa_checked(like: dict, data: Path | None = None, *, baseline: str = "prme@00aa00aa", number: int = 1,
               benchmarks=gate.GATE_BENCHMARKS, finished: str = AA_FINISHED, excludes_zero: bool = False,
               invalid: str | None = None, **changes) -> list[dict]:
    """An A/A pair on each benchmark under the conditions of ``like``, as run-pair records one (#137): published
    (``aa_published``), complete in the track's run logs under ``data``, and added to the A/A record.

    ``invalid`` marks the pair complete but invalid in its run log. Returns the lines added to the record.
    """
    data = data or baselines.data_root(OLLAMA_MODEL)
    added = []
    for benchmark in benchmarks:
        paths = aa_published(like, benchmark, baseline=baseline, number=number, excludes_zero=excludes_zero,
                             **changes)
        aa_logged(data, benchmark, baseline=baseline, number=number, finished=finished,
                  **({} if invalid is None else {"invalid": invalid}))
        added.append(baselines.record_aa_check(*paths, data=data))
    return added


def test_the_rules_budget_is_the_registered_runs_context_ceiling():
    # The 4K budget of the default-change rule: the registered run's 4,096 tokens less the 100 reserved.
    packing = json.loads(study.REG.read_text())["defaults"]["packing"]
    assert packing["token_budget"] - packing["overhead_tokens"] == baselines.RULE_BUDGET
    assert packing["tokenizer"] == baselines.RULE_TOKENIZER
    assert baselines.REFERENCE_ARMS == {"full-context", "plain-vector", "plain-bm25", "plain-rrf"}


def test_compare_reads_the_defaults_and_their_variants_only_at_the_rules_4k_budget():
    answered(baselines.data_root(OLLAMA_MODEL), "locomo", "a" * 40)
    logged_pair(settings=MARKED)
    aa_checked(answer_result("prme", [True, False]))
    # A variant pair at the 4K budget is compared.
    assert baselines.compare(*one_pair(answer_result("prme", [True, False]),
                                       answer_result("prme-rrf", [True, True])))["arms"]["after"] == "prme-rrf"
    # A variant that changes the budget, on either side of a pair, is refused, and so is a repeat outside it (#125).
    for before, after, side, arm, packed in (
            (answer_result("prme", [True, False]), answer_result("prme-wide", [True, True], budget=8092),
             "after", "prme-wide", "a context budget of 8092 tokens"),
            (answer_result("prme", [True, False], budget=1948), answer_result("prme-rrf", [True, True]),
             "before", "prme", "a context budget of 1948 tokens"),
            (answer_result("prme", [True, False]), answer_result("prme-none", [True, True], budget=None),
             "after", "prme-none", "no context budget")):
        with pytest.raises(ValueError, match=rf"The {side} result, {arm}, was prepared with {packed}\. The "
                                             r"default-change rule in CLAUDE\.md reads PRME's arms only at the 4K "
                                             r"budget \(3,996 cl100k_base tokens\)"):
            baselines.compare(*one_pair(before, after))
    with pytest.raises(ValueError, match="prme@0123abcd, was prepared with a context budget of 2048 tokens"):
        baselines.compare(answer_result("prme", [True, False]),
                          answer_result("prme@0123abcd", [False, False], commit="b" * 40, budget=2048))
    # The same budget counted by another tokenizer is another budget. A result published before its summary named
    # the tokenizer used the registered one.
    other = answer_result("prme-rrf", [True, True])
    other["prepared"]["tokenizer"] = "o200k_base"
    with pytest.raises(ValueError, match="The after result, prme-rrf, was prepared with its budget counted by the "
                                         "'o200k_base' tokenizer"):
        baselines.compare(*one_pair(answer_result("prme", [True, False]), other))
    other["prepared"]["tokenizer"] = baselines.RULE_TOKENIZER
    assert baselines.compare(*one_pair(answer_result("prme", [True, False]), other))["contexts"]["differing"] == 0
    # The plain and full-context arms are reference points, not defaults, and are paired with the defaults at any
    # budget; an arm name the harness never makes is not one of them.
    for reference, budget in (("full-context", None), ("plain-rrf", 8092)):
        comparison = baselines.compare(*one_pair(answer_result("prme", [True, False]),
                                                 answer_result(reference, [True, True], budget=budget)))
        assert comparison["arms"] == {"before": "prme", "after": reference}
    for unknown in ("prme_wide", "PRME-wide", "wide"):
        with pytest.raises(ValueError, match=f"The after result, {unknown}, was prepared with a context budget"):
            baselines.compare(*one_pair(answer_result("prme", [True, False]),
                                        answer_result(unknown, [True, True], budget=8092)))


def test_compare_reports_how_many_questions_each_side_asked_on_different_context_text():
    answered(baselines.data_root(OLLAMA_MODEL), "locomo", "a" * 40)
    logged_pair(settings=MARKED)
    aa_checked(answer_result("prme", [True, False]))
    texts = ["t0", "t1", "t2", "t3"]
    before = answer_result("prme", [True, True, False, False], texts=texts)
    after = answer_result("prme-rrf", [True, False, True, False], texts=["t0", "x1", "t2", "x3"])
    comparison = baselines.compare(*one_pair(before, after))
    # A variant prepared before #139 records no replay of the defaults at its commit, so nothing splits the count.
    assert {key: value for key, value in comparison["contexts"].items() if key != "note"} == {
        "questions": 4, "differing": 2, "shown_by": "text hashes", "changed_by_other_code": None}
    assert comparison["warnings"] == []
    # Contexts from another commit are flagged with how many questions read different text.
    moved = {**after, "prepared": {**after["prepared"], "commit": "b" * 40}}
    assert baselines.compare(*one_pair(before, moved))["warnings"] == [
        "The contexts were prepared from different commits, so code changes are part of the difference: 2 of 4 "
        "questions read different context text, from the after side's settings and from any other change between "
        "the commits. The variant was prepared before prepare replayed the defaults at its commit, so nothing shows "
        "how many of them other code changed (#139)"]
    # With the same text on every question, the commits that built it changed nothing the reader saw.
    same = answer_result("prme-rrf", [True, False, True, False], commit="b" * 40, texts=texts)
    assert baselines.compare(*one_pair(before, same))["warnings"] == []
    # Results published before the text hashes (#125), on either side, show the same text only when both read one
    # preparation or both reproduce every saved context; otherwise nothing shows it, and compare says so.
    legacy = answer_result("prme-rrf", [True, False, True, False], matching=3)
    unknown = baselines.compare(*one_pair(before, legacy))
    assert (unknown["contexts"]["differing"], unknown["contexts"]["shown_by"]) == (None, None)
    assert unknown["warnings"] == ["Nothing shows which questions the two sides asked on different context text: a "
                                   "result was published before rows carried text hashes (#125)"]
    moved = {**legacy, "prepared": {**legacy["prepared"], "commit": "b" * 40}}
    assert baselines.compare(*one_pair(before, moved))["warnings"][1] == (
        "The contexts were prepared from different commits, so code changes are part of the difference")
    shared = {**legacy, "prepared_sha256": "p" * 64}
    assert baselines.compare(*one_pair({**before, "prepared_sha256": "p" * 64}, shared))["contexts"] == {
        **comparison["contexts"], "differing": 0, "shown_by": "one preparation"}
    reproduced = answer_result("prme-rrf", [True, False, True, False])
    assert baselines.compare(*one_pair(before, reproduced))["contexts"]["shown_by"] == "saved run"
    # Hashes that are missing or empty on every row show nothing; on some rows only, the result is refused.
    for blank in (None, ""):
        empty = answer_result("prme-rrf", [True, False, True, False], matching=3, texts=[blank] * 4)
        assert baselines.compare(*one_pair(answer_result("prme", [True, True, False, False], matching=3,
                                                         texts=[blank] * 4), empty))["contexts"]["differing"] is None
    partly = answer_result("prme-rrf", [True, False, True, False], texts=[*texts[:3], None])
    with pytest.raises(ValueError, match="context text hashes on some of its rows only"):
        baselines.compare(*one_pair(before, partly))
    # A question listed twice would pair the wrong rows.
    doubled = {**after, "rows": [after["rows"][0], *after["rows"]]}
    doubled["rows"][1] = {**doubled["rows"][1], "reader_sha256": "other"}
    with pytest.raises(ValueError, match="lists a question more than once"):
        baselines.compare(*one_pair({**before, "rows": [before["rows"][0], *before["rows"]]}, doubled))


def test_compare_refuses_a_variants_pair_whose_defaults_at_its_commit_read_other_text_than_its_baseline(monkeypatch):
    answered(baselines.data_root(OLLAMA_MODEL), "locomo", "a" * 40)
    logged_pair(settings=MARKED)
    aa_checked(answer_result("prme", [True, False]))
    texts = ["t0", "t1", "t2", "t3"]
    before = answer_result("prme", [True, True, False, False], texts=texts)

    def variant(defaults: list[str | None] | None, **changes) -> dict:
        return {**answer_result("prme-rrf", [True, False, True, False], commit="b" * 40,
                                texts=["t0", "x1", "t2", "x3"], defaults=defaults), **changes}

    # The defaults at the variant's commit read the baseline's text on every question, so every difference is the
    # variant's settings, and contexts from another commit are not flagged (#139).
    comparison = baselines.compare(*one_pair(before, variant(texts)))
    assert {key: value for key, value in comparison["contexts"].items() if key != "note"} == {
        "questions": 4, "differing": 2, "shown_by": "text hashes", "changed_by_other_code": 0}
    assert comparison["warnings"] == []
    # Something between the two preparations changed the defaults' text on a question, where the variant reads the
    # new defaults' text (q3), or its settings changed that text again (q1). The pair would credit that change to
    # the variant, so it is refused.
    for defaults in (["t0", "t1", "t2", "x3"], ["t0", "y1", "t2", "t3"]):
        assert baselines._context_changes(before, variant(defaults))["changed_by_other_code"] == 1
        with pytest.raises(ValueError, match=r"On 1 of 4 questions the defaults at the commit that prepared prme-rrf "
                                             r"\(b{40}\) read other context text than prme, prepared at a{40}\. .* "
                                             r"record a new baseline at a commit on main that includes the change"):
            baselines.compare(*one_pair(before, variant(defaults)))
    # So is a change at one commit, which can only come from the dependencies or the saved run.
    with pytest.raises(ValueError, match=r"On 4 of 4 questions the defaults at the commit that prepared prme-rrf "
                                         r"\(a{40}\)"):
        baselines.compare(*one_pair(before, variant(["d"] * 4, prepared=before["prepared"])))
    with pytest.raises(ValueError, match="hashes of the defaults' context text on some of its rows only"):
        baselines.compare(*one_pair(before, variant([*texts[:3], None])))
    # Without the before side's text hashes, nothing splits the count either.
    legacy = answer_result("prme", [True, True, False, False], matching=3)
    assert baselines._context_changes(legacy, variant(texts))["changed_by_other_code"] is None
    # A variant pair without the split is accepted only when it started before #139, as every variant pair on record
    # did (the last, pair 2 of prme-reader-rrf, at 14:35 UTC), and compare warns that nothing splits it. A later one
    # came from a checkout without #139. These results use the registered failure policy, so they start before its
    # amendment (#132), and #139's time is moved before them.
    assert baselines.DEFAULTS_REPLAYED_SINCE > datetime.fromisoformat("2026-09-24T14:35:10.543804+00:00")
    since = datetime.fromisoformat("2026-09-24T01:00:00+00:00")
    monkeypatch.setattr(baselines, "DEFAULTS_REPLAYED_SINCE", since)
    earlier = baselines.compare(*one_pair(before, variant(None, started_at="2026-09-24T00:59:59+00:00")))
    assert earlier["contexts"]["changed_by_other_code"] is None
    assert earlier["warnings"] == [
        "The contexts were prepared from different commits, so code changes are part of the difference: 2 of 4 "
        "questions read different context text, from the after side's settings and from any other change between "
        "the commits. The variant was prepared before prepare replayed the defaults at its commit, so nothing shows "
        "how many of them other code changed (#139)"]
    for started in (since.isoformat(), "2026-09-24T02:00:00+00:00"):
        with pytest.raises(ValueError, match=r"The prme-rrf result's rows do not record the defaults' context text at "
                                             r"the variant's commit, .* checkout without #139"):
            baselines.compare(*one_pair(before, variant(None, started_at=started)))
    # Other arms record no replay of the defaults, and are never split or refused for it.
    plain = baselines.compare(*one_pair(before, answer_result("plain-rrf", [True, False, True, False],
                                                              commit="b" * 40, texts=["t0", "x1", "t2", "x3"])))
    assert plain["contexts"]["changed_by_other_code"] is None
    assert plain["warnings"][0].startswith("The contexts were prepared from different commits") and \
        "(#139)" not in plain["warnings"][0]


def test_two_baselines_that_no_longer_reproduce_the_saved_run_are_a_repeat_when_their_texts_match():
    # After a default changes, no preparation reproduces the saved run, and the text hashes show the same inputs.
    texts = ["t0", "t1", "t2", "t3"]
    first = answer_result("prme", [True, True, False, False], matching=0, texts=texts)
    again = answer_result("prme@0123abcd", [True, False, True, False], commit="b" * 40, matching=0, texts=texts)
    comparison = baselines.compare(first, again)
    assert comparison["repeat"] is not None and comparison["warnings"] == []
    assert (comparison["contexts"]["differing"], comparison["contexts"]["shown_by"]) == (0, "text hashes")
    # One question asked on other text is not a repeat, and two baselines answered on their own are then refused.
    moved = answer_result("prme@0123abcd", [True, False, True, False], commit="b" * 40, matching=0,
                          texts=[*texts[:3], "x3"])
    with pytest.raises(ValueError, match="alongside it in one pair"):
        baselines.compare(first, moved)


def test_compare_refuses_the_same_answer_run_on_both_sides():
    first = answer_result("prme", [True, False])
    with pytest.raises(ValueError, match="same answer run"):
        baselines.compare(first, json.loads(json.dumps(first)))


def test_compare_refuses_another_model_identity_or_another_server_version():
    first = answer_result("prme", [True, False])
    moved = answer_result("prme@0123abcd", [True, True], identity={**IDENTITY, "manifest_digest_sha256": "f" * 64})
    with pytest.raises(ValueError, match="model identities"):
        baselines.compare(first, moved)
    # An Ollama upgrade keeps the model identity, but the pair is refused (#129), between two runs or within one.
    with pytest.raises(ValueError, match=r"server version changed during or between these runs \(0.34.3, 0.35.0\)"):
        baselines.compare(first, answer_result("prme@0123abcd", [True, True],
                                               identity={**IDENTITY, "server_version": "0.35.0"}))
    with pytest.raises(ValueError, match="server version changed"):
        baselines.compare(first, {**answer_result("prme@0123abcd", [True, True]),
                                  "server_versions": ["0.34.3", "0.35.0"]})
    # A start that recorded no version is flagged, not refused, on a repeat.
    partly = baselines.compare(first, {**answer_result("prme@0123abcd", [True, True]),
                                       "server_versions": ["0.34.3", None]})
    assert partly["repeat"] is not None and partly["server_versions"] == ["0.34.3", None]
    assert any("not recorded at every start" in warning for warning in partly["warnings"])


PUBLISHED = Path(__file__).parents[1] / "benchmarks/results/research"
# The first DeepSeek baseline of the defaults, and the repeat that answered them again (#118).
REPEAT = "prme@46647825"


def published_deepseek(arm: str, benchmark: str) -> dict:
    return json.loads((PUBLISHED / "2026-09-24" / f"{OLLAMA_MODEL.track}-{arm}-{benchmark}-result.json").read_text())


def check_published_defaults_run(result: dict, arm: str, benchmark: str) -> None:
    """A published DeepSeek answer run of the defaults is complete, adds up from its rows, and read unchanged contexts."""
    rows = result["rows"]
    assert (result["kind"], result["arm"], result["benchmark"]) == ("ollama-answer-result", arm, benchmark)
    assert result["registration_sha256"] == digest(study.REG)
    assert [row["question_id"] for row in rows] == json.loads(study.REG.read_text())["cohort_ids"][benchmark]
    assert result["complete"] and result["total"] == result["completed"] == len(rows)
    assert not result["failures"] and result["final_failures"] == result["unreplaced_failures"] == 0
    assert result["correct"] == sum(row["correct"] for row in rows)
    assert result["accuracy"] == result["correct"] / len(rows)
    assert result["categories"] == {
        category: {"correct": sum(row["correct"] for row in rows if row["question_type"] == category),
                   "total": sum(row["question_type"] == category for row in rows)}
        for category in sorted({row["question_type"] for row in rows})}
    assert result["prepared"]["overrides"] == {} and result["prepared"]["dirty"] is False
    assert result["cost"]["usd"] == 0


@pytest.mark.parametrize("arm", ["prme", REPEAT])
@pytest.mark.parametrize("benchmark", ["locomo", "longmemeval"])
def test_the_published_deepseek_baseline_is_complete_and_matches_the_current_answer_settings(arm, benchmark):
    result = published_deepseek(arm, benchmark)
    check_published_defaults_run(result, arm, benchmark)
    # compare() pairs a variant with this baseline only when both were answered with the same settings, so a
    # change to the answer settings needs a new baseline.
    assert {key: value for key, value in result["answer_model"].items() if key != "identity"} == \
        OLLAMA_MODEL.settings()
    # It was answered under the registered failure policy, before the amendment (#132), and compares only with
    # results answered under that policy too.
    assert result["retry_policy"] == baselines.RETRY_POLICY and "failure_policy" not in result


@pytest.mark.parametrize(("benchmark", "gained", "lost", "excludes_zero"), [
    ("locomo", 27, 34, False), ("longmemeval", 17, 6, True)])
def test_the_published_repeat_of_the_defaults_is_the_run_to_run_floor_in_benchmarks_md(
        benchmark, gained, lost, excludes_zero):
    first, again = published_deepseek("prme", benchmark), published_deepseek(REPEAT, benchmark)
    # Both preparations reproduce every saved context, which is what makes the pair a repeat.
    assert all(result["prepared"]["contexts_matching_saved_run"] == result["total"] for result in (first, again))
    comparison = baselines.compare(first, again)
    assert comparison["warnings"] == [] and comparison["repeat"] is not None
    assert comparison["repeat"]["interleaved"] is False and comparison["server_versions"] == ["0.34.3"]
    # Published before the rows carried text hashes (#125), so the saved-run match is what shows the same text.
    assert "context_text_sha256" not in first["rows"][0]
    assert (comparison["contexts"]["differing"], comparison["contexts"]["shown_by"]) == (0, "saved run")
    # BENCHMARKS.md quotes these intervals: LoCoMo's 10 conversations, and LongMemEval-S's questions.
    accuracy = comparison["accuracy"]
    if benchmark == "locomo":
        assert comparison["interval_unit"] == "conversations and questions" and accuracy["groups"] == 10
        assert accuracy["interval_95_conversations"] == pytest.approx([-0.0136, 0.0039], abs=5e-5)
        # The narrower conversation bootstrap never decides alone: the interval spans both.
        assert accuracy["interval_95"] == accuracy["interval_95_questions"] == pytest.approx([-0.0143, 0.0052],
                                                                                              abs=5e-5)
    else:
        assert comparison["interval_unit"] == "questions" and accuracy["interval_95"] == pytest.approx([0.004, 0.042])
    assert (len(comparison["gained"]), len(comparison["lost"])) == (gained, lost)
    assert comparison["repeat"]["changed_verdicts"] == gained + lost
    # BENCHMARKS.md reports the floor, and the default-change rule in CLAUDE.md depends on this flag.
    assert comparison["repeat"]["interval_excludes_zero"] is excludes_zero


# The interleaved A/A check of the revised test: the repeat answered against itself as one pair on each benchmark,
# under the amended failure policy (#129, #132). Each is the first pair of its benchmark to complete, and every earlier
# one was given up: under the registered policy at a final failure, and the last as started under that policy.
AA_PAIRS = {"locomo": 3, "longmemeval": 5}
FAILED = "abandoned: a question failed finally"
OTHER_POLICY = "abandoned: it was started under another failure policy"


def published_deepseek_pair(before: str, after: str, benchmark: str, number: int) -> list[dict]:
    """Both sides of a published DeepSeek pair, before side first."""
    stem = f"{OLLAMA_MODEL.track}-{before}-vs-{after}-{benchmark}-pair-{number}"
    return [json.loads((PUBLISHED / "2026-09-24" / f"{stem}-{side}-result.json").read_text())
            for side in baselines.PAIR_SIDES]


@pytest.mark.parametrize("benchmark", ["locomo", "longmemeval"])
def test_the_published_aa_pair_is_complete_and_matches_the_current_answer_settings(benchmark):
    number = AA_PAIRS[benchmark]
    sides = published_deepseek_pair(REPEAT, REPEAT, benchmark, number)
    for side, result in zip(baselines.PAIR_SIDES, sides):
        check_published_defaults_run(result, REPEAT, benchmark)
        assert result["provenance"]["dirty"] is False
        # Both sides read the repeat's own prepared contexts, which reproduce every saved context.
        assert result["prepared_sha256"] == published_deepseek(REPEAT, benchmark)["prepared_sha256"]
        assert result["prepared"]["contexts_matching_saved_run"] == result["total"]
        # The model identity and server version the check was measured with, as BENCHMARKS.md and CLAUDE.md record
        # them, and the current answer settings under the amendment.
        assert result["answer_model"]["identity"]["manifest_digest_sha256"].startswith("e04da138")
        assert result["server_versions"] == ["0.34.3"]
        assert {key: value for key, value in result["answer_model"].items() if key != "identity"} == \
            {**OLLAMA_MODEL.settings(), "failure_policy": baselines.FAILURE_POLICY}
        # Nothing was asked again, repaired or left unscored on either side.
        assert result["retry_policy"] == baselines.OLLAMA_RETRY_POLICY
        assert result["failure_policy"] == {**baselines.failure_amendment(), **NOTHING_UNSCORED}
        assert {row["outcome"] for row in result["rows"]} == {"judged"}
        assert (result["pair"]["side"], result["pair"]["number"]) == (side, number)
        assert result["pair"]["earlier_pairs"] == [{"number": earlier, "state": FAILED if earlier < number - 1
                                                     else OTHER_POLICY} for earlier in range(1, number)]
    assert {key: value for key, value in sides[0]["pair"].items() if key != "side"} == \
        {key: value for key, value in sides[1]["pair"].items() if key != "side"}


@pytest.mark.parametrize(("benchmark", "correct", "gained", "lost", "interval"), [
    ("locomo", (1018, 1012), 22, 28, [-0.0130, 0.0052]), ("longmemeval", (432, 431), 4, 5, [-0.014, 0.010])])
def test_the_published_interleaved_aa_pairs_hold_the_check_in_benchmarks_md(
        benchmark, correct, gained, lost, interval):
    before, after = published_deepseek_pair(REPEAT, REPEAT, benchmark, AA_PAIRS[benchmark])
    assert (before["correct"], after["correct"]) == correct
    comparison = baselines.compare(before, after)
    assert comparison["warnings"] == [] and comparison["server_versions"] == ["0.34.3"]
    assert comparison["repeat"]["interleaved"] is True
    assert (comparison["contexts"]["differing"], comparison["contexts"]["shown_by"]) == (0, "one preparation")
    # compare() counts each side's retries and unscored questions again from its rows.
    assert comparison["failure_policy"]["before"] == comparison["failure_policy"]["after"] == NOTHING_UNSCORED
    # BENCHMARKS.md and CLAUDE.md quote these intervals: LoCoMo's 10 conversations, and LongMemEval-S's questions.
    accuracy = comparison["accuracy"]
    if benchmark == "locomo":
        assert comparison["interval_unit"] == "conversations and questions" and accuracy["groups"] == 10
        assert accuracy["interval_95_conversations"] == pytest.approx([-0.0093, 0.0019], abs=5e-5)
        assert accuracy["interval_95"] == accuracy["interval_95_questions"]
    else:
        assert comparison["interval_unit"] == "questions"
    assert accuracy["interval_95"] == pytest.approx(interval, abs=5e-5)
    assert (len(comparison["gained"]), len(comparison["lost"])) == (gained, lost)
    assert comparison["repeat"]["changed_verdicts"] == gained + lost
    # No category's interval excludes zero either, as BENCHMARKS.md says.
    assert all(value["interval_95"][0] <= 0 <= value["interval_95"][1] for value in comparison["categories"].values())
    # Both intervals include zero, so under the default-change rule in CLAUDE.md the revised test applies without
    # the extra margin taken from the largest A/A difference.
    assert comparison["repeat"]["interval_excludes_zero"] is False


# Interleaved pairs (#129) ---------------------------------------------------------

DEFAULTS_TEXT = {"conv-1-q0000": "(8 May, 2023) Caroline: I painted a sunset.",
                 "conv-1-q0001": "(20 May, 2023) Melanie: I adopted a puppy named Oscar."}


def recorded_baseline(harness, benchmark: str, contexts: dict[str, str], arm: str = "prme",
                      commit: str = "a" * 40) -> None:
    """A prepared defaults baseline whose own answer run is complete, as a pair's before side needs."""
    fabricate_plain(harness["data"], benchmark, contexts, arm=arm)
    manifest = harness["data"] / arm / benchmark / "prepared.json"
    manifest.write_text(json.dumps({**json.loads(manifest.read_text()), "provenance": {"commit": commit}}))
    answered(harness["data"], benchmark, commit, arm=arm)


# The settings the fabricated prme-marked variant changes, as variant_settings records them.
MARKED = {"scoring.fusion": "rrf", "scoring.rrf_k": 60}


def fabricate_variant(harness, arm: str = "prme-marked", settings: dict | None = None, marker: str = "VARIANT",
                      defaults: dict[str, str] | None = DEFAULTS_TEXT) -> None:
    """A variant prepared as prepare records one since #130: its manifest and a prepared event name its identity.

    Each entry also records the text of the defaults replayed at its commit, ``defaults`` (#139), which by default
    is the baseline's, next to the defaults' gate report; with None the manifest records none, as before #139.
    """
    settings = MARKED if settings is None else settings
    fabricate_plain(harness["data"], "locomo", {qid: f"{text} {marker}" for qid, text in DEFAULTS_TEXT.items()},
                    arm=arm)
    manifest = harness["data"] / arm / "locomo" / "prepared.json"
    prepared = {**json.loads(manifest.read_text()), "variant_settings": settings,
                "provenance": {"commit": "a" * 40, "overrides": {"scoring": {"fusion": "rrf"}}}}
    if defaults is not None:
        prepared["contexts"] = [{**entry, "defaults_text_sha256": text_sha256(defaults[entry["question_id"]])}
                                for entry in prepared["contexts"]]
        report = manifest.parent / "defaults-gate.json"
        report.write_text(json.dumps({"provenance": {"commit": "a" * 40, "overrides": {}}, "rows": [
            {"question_id": entry["question_id"], "context_sha256": entry["defaults_text_sha256"]}
            for entry in prepared["contexts"]]}))
        prepared["defaults_gate_report_sha256"] = digest(report)
    manifest.write_text(json.dumps(prepared))
    baselines._log_prepared_variant(harness["data"], arm, "locomo", json.loads(manifest.read_text()), digest(manifest))



def contexts_sha256(harness, arm: str) -> str:
    return baselines._contexts_sha256(json.loads((arm_folder(harness, arm) / "prepared.json").read_text())["contexts"])


def pair_arms(harness, *, marked: bool = True) -> None:
    """A recorded defaults baseline and a variant whose contexts carry a marker, for the two LoCoMo questions, with
    the A/A check that a variant's pair needs under the harness's conditions (#137)."""
    recorded_baseline(harness, "locomo", DEFAULTS_TEXT)
    if marked:
        fabricate_variant(harness)
    harness_aa_checked(harness)


def harness_conditions() -> dict:
    """A result answered under the conditions the harness answers pairs under: the model's settings and identity at
    Ollama 0.34.3, the harness's amended failure policy and the 4K budget (#137)."""
    return {**amended_result("prme", [True, False]), "answer_model": ANSWER_MODEL, "server_versions": ["0.34.3"],
            "failure_policy": {"id": baselines.FAILURE_POLICY, "sha256": digest(baselines.FAILURE_AMENDMENT)}}


def harness_aa_checked(harness, **changes) -> list[dict]:
    """An accepted A/A check on both benchmarks under the harness's conditions (``harness_conditions``)."""
    return aa_checked(harness_conditions(), harness["data"], **changes)


def one_request_at_a_time(harness) -> None:
    """Register a concurrency of one, so the order of requests is the order of the queue."""
    registration = {**json.loads(harness["reg"].read_text()), "provider_concurrency": 1}
    harness["reg"].write_text(json.dumps(registration))
    amendment = study.PUBLIC / "gpt54-official-loader-amendment.json"
    amendment.write_text(json.dumps({**json.loads(amendment.read_text()), "registration_sha256": digest(harness["reg"])}))
    amend_registration(harness["reg"])


def pair_folder(harness, before="prme", after="prme", benchmark="locomo", number=1) -> Path:
    return harness["data"] / "pairs" / before / after / benchmark / f"pair-{number}"


def pair_log(harness, before="prme", after="prme", benchmark="locomo") -> list[dict]:
    path = harness["data"] / "runs" / "pairs" / before / f"{after}-{benchmark}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


async def test_a_pair_answers_the_variant_and_a_fresh_defaults_run_interleaved_in_one_session(harness, monkeypatch):
    one_request_at_a_time(harness)
    pair_arms(harness)
    requests = ollama_provider(monkeypatch, wrong_when="VARIANT")
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    requests.clear()
    paired = await run_pair(harness, "prme", "prme-marked")
    prompts = [body["messages"][0]["content"] for _, body, _ in requests]
    # One queue: each question's variant reader and judge, then its defaults reader and judge.
    questions = study.question_rows("locomo")
    assert prompts[0::2] == [study.reader_prompt("locomo", question, text) for question in questions
                             for text in (DEFAULTS_TEXT[question["question_id"]] + " VARIANT",
                                          DEFAULTS_TEXT[question["question_id"]])]
    assert all(prompt.startswith("Evaluate the response") for prompt in prompts[1::2])
    before, after = paired["before"], paired["after"]
    assert (before["arm"], after["arm"]) == ("prme", "prme-marked") and before["complete"] and after["complete"]
    assert (before["correct"], after["correct"]) == (2, 0)
    folder = pair_folder(harness, after="prme-marked")
    record = json.loads((folder / "pair.json").read_text())
    assert record == {**record, "number": 1, "before": "prme", "after": "prme-marked", "benchmark": "locomo",
                      "order": baselines.PAIR_ORDER, "prepared_sha256": {
                          "before": digest(arm_folder(harness, "prme") / "prepared.json"),
                          "after": digest(arm_folder(harness, "prme-marked") / "prepared.json")}}
    mark = {"id": record["id"], "number": 1, "before": "prme", "after": "prme-marked",
            "sha256": digest(folder / "pair.json"), "order": baselines.PAIR_ORDER, "pairs_started": 1,
            "earlier_pairs": []}
    assert paired["pair"] == mark and before["pair"] == {**mark, "side": "before"}
    assert after["pair"] == {**mark, "side": "after"}
    assert before["server_versions"] == after["server_versions"] == ["0.34.3"]
    assert before["answer_model"] == after["answer_model"] == ANSWER_MODEL
    assert before["retry_policy"] == after["retry_policy"] == baselines.OLLAMA_RETRY_POLICY
    assert before["run_log"] == {"sha256": digest(harness["data"] / "runs/pairs/prme/prme-marked-locomo.jsonl"),
                                 "runs_started": 1, "arm": {"sha256": digest(harness["data"] / "runs/prme-locomo.jsonl"),
                                                            "runs_started": 0, "prepared_again": 0}}
    # The variant was never answered on its own, so its own run log holds only its preparation (#130).
    assert after["run_log"]["arm"] == {"sha256": digest(harness["data"] / "runs/prme-marked-locomo.jsonl"),
                                       "runs_started": 0, "prepared_again": 0}
    log = pair_log(harness, after="prme-marked")
    assert [(event["event"], event["pair"], event["server_version"]) for event in log] == [
        ("started", 1, "0.34.3"), ("finished", 1, "0.34.3")]
    # The start records the pair's id and the variant's identity, which tie the pair to every other pair of the
    # variant (#130).
    identity = {"variant_settings": MARKED, "contexts_sha256": contexts_sha256(harness, "prme-marked")}
    assert log[0] == {**log[0], "id": record["id"], **identity} and "variant_settings" not in log[1]
    assert log[1]["complete"] is True and log[1]["completed"] == {"before": 2, "after": 2}
    # The prepared arms keep no answers; each side's answers and result live in the pair.
    assert not (arm_folder(harness, "prme") / "execution").exists()
    assert json.loads((folder / "after" / "result.json").read_text())["rows"] == after["rows"]
    assert [result["pair"]["side"] for result in pair_published(harness, after="prme-marked")] == ["before", "after"]
    # The receipts replay from the pair's own folders.
    folder_before, prepared, entries = baselines.load_prepared("prme", "locomo", questions, data=harness["data"])
    replayed = baselines.report("prme", "locomo", folder_before, questions, prepared, entries, None,
                                model=OLLAMA_MODEL, answers=folder / "before")
    assert replayed["rows"] == before["rows"]
    comparison = baselines.compare(before, after, data=harness["data"])
    assert comparison["pair"] == mark and comparison["repeat"] is None
    assert comparison["variant"]["role"] == "first" and comparison["variant"]["confirmation"] is None
    assert comparison["accuracy"]["delta"] == -1.0 and comparison["lost"] == ["conv-1-q0000", "conv-1-q0001"]
    # One conversation has no conversation-level interval.
    assert comparison["interval_unit"] == "conversations and questions" and comparison["accuracy"]["groups"] == 1
    assert comparison["accuracy"]["interval_95"] is comparison["accuracy"]["interval_95_conversations"] is None
    assert comparison["accuracy"]["interval_95_questions"]
    assert comparison["server_versions"] == ["0.34.3"]
    # The sides are fixed: the defaults are the before side, and the two must come from one pair.
    for flipped, message in (((after, before), "before side"),
                             ((before, {**after, "pair": {**after["pair"], "id": "0" * 32}}), "different pairs"),
                             ((before, {**after, "pair": {**after["pair"], "number": None}}), "different pairs"),
                             ((before, {key: value for key, value in after.items() if key != "pair"}), "Only one"),
                             (({**before, "arm": "prme-marked"}, after), "arms are not the pair's")):
        with pytest.raises(ValueError, match=message):
            baselines.compare(*flipped)
    with pytest.raises(ValueError, match="record the Ollama server version at every start"):
        baselines.compare(before, {**after, "server_versions": ["0.34.3", None]})
    # A defaults run answered on its own is not the one answered alongside the variant.
    ollama_provider(monkeypatch)
    baselines.prepare("full-context", "locomo", data=harness["data"])
    alone = await run_ollama(harness)
    with pytest.raises(ValueError, match="Only one of these results"):
        baselines.compare({**alone, "arm": "prme", "context_budget": baselines.RULE_BUDGET}, after)


async def test_run_pair_answers_a_variant_only_when_the_defaults_at_its_commit_read_the_baselines_text(
        harness, monkeypatch):
    pair_arms(harness, marked=False)
    requests = ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    requests.clear()
    # A variant prepared before #139 records no replay of the defaults, so nothing would split its pair (#139).
    fabricate_variant(harness, "prme-legacy", defaults=None)
    for sample in (None, 1):
        with pytest.raises(ValueError, match="The prme-legacy locomo preparation does not record the defaults' "
                                             "context text at its commit, which prepare records since #139"):
            await run_pair(harness, "prme", "prme-legacy", sample=sample)
    # A variant whose first pair and confirmation are complete hears that first, since preparing it again would not
    # help (#130).
    with monkeypatch.context() as patched:
        patched.setattr(baselines, "_check_uncounted", lambda *args: pytest.fail("the #130 check runs first"))
        with pytest.raises(pytest.fail.Exception, match="the #130 check runs first"):
            await run_pair(harness, "prme", "prme-legacy")
    # Something changed the defaults' text on a question between the baseline's preparation and the variant's, so
    # the pair would credit it to the variant; it is refused before any question is asked, naming what else the two
    # preparations differ in.
    moved = {**DEFAULTS_TEXT, "conv-1-q0001": "(20 May, 2023) Melanie: I adopted a kitten named Oscar."}
    fabricate_variant(harness, "prme-moved", marker="MOVED", defaults=moved)
    manifest = arm_folder(harness, "prme-moved") / "prepared.json"
    prepared = json.loads(manifest.read_text())
    manifest.write_text(json.dumps({**prepared, "provenance": {**prepared["provenance"], "dependencies": {"x": "2"}}}))
    with pytest.raises(ValueError, match=r"On 1 of 2 questions the defaults at the commit that prepared prme-moved "
                                         r"\(a{40}\) read other context text than prme, prepared at a{40}\. They were "
                                         r"also prepared with different dependencies\."):
        await run_pair(harness, "prme", "prme-moved")
    # The recorded text must be the defaults' gate report's: kept, unchanged, and from the defaults at that commit.
    fabricate_variant(harness, "prme-unbacked", marker="UNBACKED")
    report = arm_folder(harness, "prme-unbacked") / "defaults-gate.json"
    kept = json.loads(report.read_text())
    for changed in ({**kept, "provenance": {**kept["provenance"], "overrides": {"scoring": {"fusion": "rrf"}}}},
                    {**kept, "provenance": {**kept["provenance"], "commit": "b" * 40}},
                    {**kept, "rows": kept["rows"][::-1]}, None):
        if changed is None:
            report.unlink()
        else:
            report.write_text(json.dumps(changed))
            manifest = arm_folder(harness, "prme-unbacked") / "prepared.json"
            manifest.write_text(json.dumps({**json.loads(manifest.read_text()),
                                            "defaults_gate_report_sha256": digest(report)}))
        with pytest.raises(ValueError, match="does not match the defaults' gate report it kept"):
            await run_pair(harness, "prme", "prme-unbacked")
    assert requests == []
    assert not any((harness["data"] / "pairs" / "prme" / arm).exists()
                   for arm in ("prme-legacy", "prme-moved", "prme-unbacked"))
    # When they read the same text, the pair is answered, and compare credits every difference to the settings.
    fabricate_variant(harness)
    paired = await run_pair(harness, "prme", "prme-marked")
    comparison = baselines.compare(paired["before"], paired["after"], data=harness["data"])
    assert (comparison["contexts"]["differing"], comparison["contexts"]["changed_by_other_code"]) == (2, 0)
    assert all("defaults_text_sha256" not in row for row in paired["before"]["rows"])


@pytest.mark.usefixtures("mock_embeddings")
async def test_a_variant_records_the_defaults_replayed_on_the_same_code_and_questions(harness, monkeypatch):
    await gate_cases(harness, monkeypatch)
    replay = gate.run_gate
    fusion = gate.parse_overrides(['scoring.fusion="rrf"'])

    def changing(change):
        async def run_gate(*args, **kwargs):
            report = await replay(*args, **kwargs)
            # Only the defaults' replay writes no captures.
            return report if kwargs.get("capture_dir") is not None else change(report)
        return run_gate

    # Each question's entry records the defaults' replay's text for that question, whatever the variant's own is.
    marked = {qid: text_sha256(f"defaults {qid}") for qid in ("conv-1-q0000", "conv-1-q0001")}
    monkeypatch.setattr(gate, "run_gate", changing(lambda report: {**report, "rows": [
        {**row, "context_sha256": marked[row["question_id"]]} for row in report["rows"]]}))
    prepared = await asyncio.to_thread(baselines.prepare, "prme-rrf", "locomo", data=harness["data"],
                                       archive=harness["archive"], overrides=fusion)
    assert {entry["question_id"]: entry["defaults_text_sha256"] for entry in prepared["contexts"]} == marked
    assert all(entry["defaults_text_sha256"] != entry["text_sha256"] for entry in prepared["contexts"])
    kept = json.loads((arm_folder(harness, "prme-rrf") / "defaults-gate.json").read_text())
    assert [row["context_sha256"] for row in kept["rows"]] == list(marked.values())
    # The two replays must run on the same code and saved run, over the same questions in order.
    for arm, change, message in (
            ("prme-edited", lambda report: {**report, "provenance": {**report["provenance"], "worktree_sha256": "0"}},
             r"The worktree_sha256 changed between the variant's replay and the defaults' replay, .* Remove "
             r".*prme-edited/locomo and prepare the variant again"),
            ("prme-reordered", lambda report: {**report, "rows": report["rows"][::-1]},
             "does not cover the variant's questions in order")):
        monkeypatch.setattr(gate, "run_gate", changing(change))
        with pytest.raises(ValueError, match=message):
            await asyncio.to_thread(baselines.prepare, arm, "locomo", data=harness["data"],
                                    archive=harness["archive"], overrides=fusion)
        # Nothing is recorded as prepared, so the arm is never answered.
        assert not (arm_folder(harness, arm) / "prepared.json").exists()
        assert not (arm_folder(harness, arm) / "defaults-gate.json").exists()
        assert not (harness["data"] / f"runs/{arm}-locomo.jsonl").exists()


async def test_run_pair_resumes_an_unfinished_pair_and_starts_the_next_after_a_complete_one(harness, monkeypatch):
    pair_arms(harness)
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    ollama_provider(monkeypatch, fail="Melanie: I adopted")
    with pytest.raises(RuntimeError, match="before 1/2 answered, 0 final failures; after 1/2 answered"):
        await run_pair(harness, "prme", "prme-marked")
    assert not pair_published(harness, after="prme-marked")
    requests = ollama_provider(monkeypatch)
    first = await run_pair(harness, "prme", "prme-marked")
    # The same pair: only the two questions that got no answer are asked, one per side.
    assert first["pair"]["number"] == 1 and len(requests) == 4 and first["before"]["run_log"]["runs_started"] == 2
    # The replaced failure stays on record; its message, which can name local paths, stays private.
    published_sides = pair_published(harness, after="prme-marked")
    assert len(published_sides) == 2 and first["after"]["failures"][0]["message"].startswith("Provider HTTP 404")
    assert [failure["replaced"] for failure in published_sides[1]["failures"]] == [True]
    assert all("message" not in failure for result in published_sides for failure in result["failures"])
    # A complete pair is never answered again; the next call is a confirmation with both sides drawn afresh.
    requests.clear()
    second = await run_pair(harness, "prme", "prme-marked")
    assert second["pair"]["number"] == 2 and second["pair"]["pairs_started"] == 2 and len(requests) == 8
    assert second["pair"]["id"] != first["pair"]["id"] and pair_folder(harness, after="prme-marked").is_dir()
    confirmed = baselines.compare(second["before"], second["after"], data=harness["data"])
    assert confirmed["pair"]["number"] == 2 and confirmed["variant"]["role"] == "confirmation"
    with pytest.raises(ValueError, match="different pairs"):
        baselines.compare(first["before"], second["after"])
    assert [(event["event"], event["pair"]) for event in pair_log(harness, after="prme-marked")] == [
        ("started", 1), ("finished", 1), ("started", 1), ("finished", 1), ("started", 2), ("finished", 2)]


async def test_a_pair_that_can_never_finish_stays_on_record_and_the_next_one_starts(harness, monkeypatch):
    pair_arms(harness)
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    ollama_provider(monkeypatch, empty_when="Caroline")  # An empty answer is final: the pair can never finish.
    with pytest.raises(RuntimeError, match="final failures"):
        await run_pair(harness, "prme", "prme-marked")
    ollama_provider(monkeypatch)
    paired = await run_pair(harness, "prme", "prme-marked")
    assert paired["pair"]["number"] == 2 and paired["pair"]["pairs_started"] == 2
    assert paired["pair"]["earlier_pairs"] == [
        {"baseline": "prme", "number": 1, "state": "abandoned: a question failed finally"}]
    # The first question's variant side got the final failure, which stopped the queue.
    assert json.loads((pair_folder(harness, after="prme-marked") / "after/result.json").read_text())[
        "final_failures"] == 1
    # A pair folder moved aside keeps its number, which the run log holds, and a complete pair stays complete: it is
    # never given up once its folder is gone (#130).
    shutil.rmtree(pair_folder(harness, after="prme-marked", number=2))
    # An unfinished pair without its record is given up.
    ollama_provider(monkeypatch, fail="Melanie: I adopted")
    with pytest.raises(RuntimeError, match="pair 3 is incomplete"):
        await run_pair(harness, "prme", "prme-marked")
    (pair_folder(harness, after="prme-marked", number=3) / "pair.json").unlink()
    ollama_provider(monkeypatch)
    last = await run_pair(harness, "prme", "prme-marked")
    assert last["pair"]["number"] == 4 and last["pair"]["earlier_pairs"] == [
        {"baseline": "prme", "number": 1, "state": "abandoned: a question failed finally"},
        {"baseline": "prme", "number": 2, "state": "complete"},
        {"baseline": "prme", "number": 3, "state": "abandoned: its pair.json record is missing"}]
    assert [event["event"] for event in pair_log(harness, after="prme-marked") if event["pair"] == 2] == [
        "started", "finished"]


async def test_an_unfinished_pair_is_never_finished_on_other_contexts_or_under_another_model(harness, monkeypatch):
    pair_arms(harness)
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    ollama_provider(monkeypatch, fail="Melanie: I adopted")
    with pytest.raises(RuntimeError, match="incomplete"):
        await run_pair(harness, "prme", "prme-marked")
    # The variant is prepared again: the unfinished pair stays as it is, and the next pair reads the new contexts.
    shutil.rmtree(arm_folder(harness, "prme-marked"))
    fabricate_variant(harness, marker="CHANGED")
    ollama_provider(monkeypatch, fail="Melanie: I adopted")
    with pytest.raises(RuntimeError, match="pair 2 is incomplete"):
        await run_pair(harness, "prme", "prme-marked")
    assert not (pair_folder(harness, after="prme-marked") / "after/result.json").read_text().count("CHANGED")
    # A model pulled again with a new manifest never finishes a pair the old one started.
    changed = {**IDENTITY, "manifest_digest_sha256": "f" * 64}
    requests = ollama_provider(monkeypatch, identities=[changed])
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    # The new model identity needs its own A/A check before a variant's pair is answered under it (#137).
    harness_aa_checked(harness, number=2, finished=LATER_AA, answer_model={**ANSWER_MODEL, "identity": changed})
    requests.clear()
    paired = await run_pair(harness, "prme", "prme-marked")
    assert paired["pair"]["number"] == 3 and len(requests) == 8
    assert paired["before"]["answer_model"]["identity"] == changed


async def test_a_model_change_during_a_pair_publishes_nothing_and_the_next_pair_starts_fresh(harness, monkeypatch):
    pair_arms(harness)
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    changed = {**IDENTITY, "manifest_digest_sha256": "f" * 64}
    ollama_provider(monkeypatch, identities=[IDENTITY, changed])
    with pytest.raises(RuntimeError, match="identity changed during prme and prme-marked locomo pair 1"):
        await run_pair(harness, "prme", "prme-marked")
    assert not pair_published(harness, after="prme-marked")
    assert not (pair_folder(harness, after="prme-marked") / "before/result.json").exists()
    event = pair_log(harness, after="prme-marked")[-1]
    assert (event["event"], event["pair"], event["server_version"]) == ("model-changed", 1, "0.34.3")
    ollama_provider(monkeypatch, identities=[changed])
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    harness_aa_checked(harness, number=2, finished=LATER_AA, answer_model={**ANSWER_MODEL, "identity": changed})
    assert (await run_pair(harness, "prme", "prme-marked"))["pair"]["number"] == 2


async def test_the_a_a_check_pairs_the_baseline_with_itself_as_an_interleaved_repeat(harness, monkeypatch):
    use_longmemeval(harness, TWO_LME)
    recorded_baseline(harness, "longmemeval", {"q1": LME_CONTEXT, "q2": "(2023/05/21) user: I live in Porto."})
    requests = ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    requests.clear()
    paired = await run_pair(harness, benchmark="longmemeval")
    before, after = paired["before"], paired["after"]
    assert (before["arm"], after["arm"]) == ("prme", "prme") and before["prepared_sha256"] == after["prepared_sha256"]
    # Both sides send the same requests, each answered on its own.
    assert len(requests) == 8 and before["rows"] != after["rows"]
    assert pair_folder(harness, benchmark="longmemeval").is_dir()
    assert len(pair_published(harness, benchmark="longmemeval")) == 2
    comparison = baselines.compare(before, after)
    assert {key: value for key, value in comparison["repeat"].items() if key != "note"} == {
        "changed_verdicts": 0, "interval_excludes_zero": False, "interleaved": True}
    assert "interleaved pair" in comparison["repeat"]["note"] and comparison["warnings"] == []
    with pytest.raises(ValueError, match="baseline of the defaults"):
        await run_pair(harness, "prme-marked", "prme")


async def test_a_pair_sample_is_a_labelled_smoke_check_that_the_full_pair_reuses(harness, monkeypatch):
    use_longmemeval(harness, TWO_LME)
    recorded_baseline(harness, "longmemeval", {"q1": LME_CONTEXT, "q2": "(2023/05/21) user: I live in Porto."})
    requests = ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    requests.clear()
    sample = await run_pair(harness, benchmark="longmemeval", sample=1)
    assert sample["pair"]["number"] == 1 and len(requests) == 4
    assert all(sample[side]["kind"] == "ollama-answer-result-sample" and "accuracy" not in sample[side]
               for side in baselines.PAIR_SIDES)
    assert len(pair_published(harness, benchmark="longmemeval", suffix="-sample-1")) == 2
    with pytest.raises(ValueError, match="not a complete answer run"):
        baselines.compare(sample["before"], sample["after"])
    with pytest.raises(ValueError, match="never rerun"):
        await run_pair(harness, benchmark="longmemeval", sample=1)
    full = await run_pair(harness, benchmark="longmemeval")
    # The same pair: only the question the sample did not ask is asked now, on both sides.
    assert full["pair"]["number"] == 1 and len(requests) == 8
    assert full["before"]["rows"][:1] == sample["before"]["rows"]
    assert baselines.compare(full["before"], full["after"])["interval_unit"] == "questions"
    # After a complete pair, a sample opens the next pair, whose full run reuses it.
    assert (await run_pair(harness, benchmark="longmemeval", sample=1))["pair"]["number"] == 2


async def test_server_versions_are_recorded_at_every_start_and_finish_and_a_change_is_refused(harness, monkeypatch):
    pair_arms(harness)
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    ollama_provider(monkeypatch, fail="Melanie: I adopted")
    with pytest.raises(RuntimeError, match="incomplete"):
        await run_pair(harness, "prme", "prme-marked")
    # The server is upgraded before the pair is resumed; the model identity is unchanged, but the unfinished pair
    # is left as it is and the next one starts under the new version.
    upgraded = {**IDENTITY, "server_version": "0.35.0"}
    # Each server version needs its own A/A check before a variant's pair is answered under it (#137).
    harness_aa_checked(harness, number=2, finished=LATER_AA, server_versions=["0.35.0"])
    requests = ollama_provider(monkeypatch, identities=[upgraded])
    paired = await run_pair(harness, "prme", "prme-marked")
    assert paired["pair"]["number"] == 2 and len(requests) == 8
    assert paired["pair"]["earlier_pairs"] == [
        {"baseline": "prme", "number": 1, "state": "abandoned: it was answered under another Ollama server version"}]
    assert paired["before"]["server_versions"] == paired["after"]["server_versions"] == ["0.35.0"]
    assert [(event["event"], event["pair"], event.get("server_version"))
            for event in pair_log(harness, after="prme-marked")] == [
        ("started", 1, "0.34.3"), ("finished", 1, "0.34.3"), ("abandoned", 1, None), ("started", 2, "0.35.0"),
        ("finished", 2, "0.35.0")]
    # An upgrade in the middle of a pair is seen at its finish: nothing is published, and the next pair starts.
    ollama_provider(monkeypatch, identities=[upgraded, {**IDENTITY, "server_version": "0.36.0"}])
    with pytest.raises(RuntimeError, match=r"server version changed \(0.35.0, 0.36.0\) while it was answered"):
        await run_pair(harness, "prme", "prme-marked")
    assert not pair_published(harness, after="prme-marked", number=3)
    finished = pair_log(harness, after="prme-marked")[-1]
    assert (finished["event"], finished["complete"]) == ("finished", False) and "0.36.0" in finished["reason"]
    kept = [json.loads((pair_folder(harness, after="prme-marked", number=3) / side / "result.json").read_text())
            for side in baselines.PAIR_SIDES]
    with pytest.raises(ValueError, match=r"server version changed during or between these runs \(0.35.0, 0.36.0\)"):
        baselines.compare(*kept)
    ollama_provider(monkeypatch, identities=[{**IDENTITY, "server_version": "0.36.0"}])
    harness_aa_checked(harness, number=3, finished="2026-09-24T11:00:00+00:00", server_versions=["0.36.0"])
    later = await run_pair(harness, "prme", "prme-marked")
    assert later["pair"]["number"] == 4 and [pair["state"] for pair in later["pair"]["earlier_pairs"]] == [
        "abandoned: it was answered under another Ollama server version", "complete",
        "not published: the Ollama server version changed (0.35.0, 0.36.0)"]
    # An upgrade in the middle of one run is seen at its finish, on the standalone arms too.
    baselines.prepare("full-context", "locomo", data=harness["data"])
    ollama_provider(monkeypatch, identities=[IDENTITY, {**IDENTITY, "server_version": "0.35.0"}])
    result = await run_ollama(harness)
    assert result["complete"] and result["server_versions"] == ["0.34.3", "0.35.0"]
    log = [json.loads(line) for line in (harness["data"] / "runs/full-context-locomo.jsonl").read_text().splitlines()]
    assert [(event["event"], event["server_version"]) for event in log] == [
        ("started", "0.34.3"), ("finished", "0.35.0")]


async def test_an_arm_answered_in_pairs_is_prepared_again_on_record_and_never_after_a_complete_pair(
        harness, monkeypatch):
    pair_arms(harness, marked=False)
    baselines.prepare("full-context", "locomo", data=harness["data"])
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    ollama_provider(monkeypatch, fail="What is the puppy called?")
    with pytest.raises(RuntimeError, match="incomplete"):
        await run_pair(harness, "prme", "full-context")
    # Preparing again after an unfinished pair is allowed, and the arm's own run log records it.
    shutil.rmtree(arm_folder(harness))
    baselines.prepare("full-context", "locomo", data=harness["data"])
    arm_log = harness["data"] / "runs/full-context-locomo.jsonl"
    assert [json.loads(line)["event"] for line in arm_log.read_text().splitlines()] == ["prepared-again"]
    ollama_provider(monkeypatch)
    paired = await run_pair(harness, "prme", "full-context")
    assert paired["after"]["run_log"]["arm"]["prepared_again"] == 1
    shutil.rmtree(arm_folder(harness))
    with pytest.raises(ValueError, match="complete answer run in a pair"):
        baselines.prepare("full-context", "locomo", data=harness["data"])


def test_locomo_intervals_resample_conversations_and_longmemeval_intervals_resample_questions():
    # Every gain sits in one of three conversations: question draws never miss it, conversation draws often do.
    before = answer_result("prme", [False] * 12)
    after = answer_result("prme", [True] * 4 + [False] * 8)
    for result in (before, after):
        for number, row in enumerate(result["rows"]):
            row["cluster"] = f"conv-{number // 4}"
    before, after = one_pair(before, after)
    locomo = baselines.compare(before, after)
    assert locomo["interval_unit"] == "conversations and questions" and locomo["accuracy"]["groups"] == 3
    assert locomo["accuracy"]["interval_95_questions"][0] > 0 and locomo["accuracy"]["interval_95_conversations"][0] == 0
    assert locomo["accuracy"]["interval_95"] == [0, max(locomo["accuracy"]["interval_95_conversations"][1],
                                                     locomo["accuracy"]["interval_95_questions"][1])]
    assert locomo["repeat"]["interval_excludes_zero"] is False
    lme = baselines.compare(*({**result, "benchmark": "longmemeval"} for result in (before, after)))
    assert lme["interval_unit"] == "questions" and "groups" not in lme["accuracy"]
    assert lme["accuracy"]["interval_95"] == locomo["accuracy"]["interval_95_questions"]
    assert lme["repeat"]["interval_excludes_zero"] is True
    for row in after["rows"][:1]:
        row["cluster"] = None
    with pytest.raises(ValueError, match="must name its conversation"):
        baselines.compare(before, after)


def test_cli_run_pair_answers_an_arm_or_the_baseline_itself_alongside_the_baseline(monkeypatch, capsys):
    seen = []

    async def fake_run_pair(before, after, benchmark, **kwargs):
        seen.append((before, after, benchmark, kwargs))
        side = {"complete": True, "rows": [], "failures": []}
        return {"pair": {"number": 1}, "before": side, "after": side}

    monkeypatch.setattr(baselines, "run_pair", fake_run_pair)
    data = baselines.data_root(OLLAMA_MODEL)
    answered(data, "locomo", "a" * 40, at="2026-09-24T03:07:22+00:00")
    answered(data, "locomo", "4" * 40, arm="prme@46647825", at="2026-09-24T05:51:15+00:00")
    answered(data, "longmemeval", "a" * 40)
    for argv in (["run-pair", "prme", "--benchmark", "locomo", "--variant", "rrf", "--baseline", "prme@46647825"],
                 ["run-pair", "prme", "--benchmark", "longmemeval", "--baseline", "prme", "--sample", "2"],
                 ["run-pair", "full-context", "--baseline", "prme@46647825"]):
        baselines.main([*argv, "--provider", "ollama"])
    assert seen == [("prme@46647825", "prme-rrf", "locomo", {"model": OLLAMA_MODEL, "sample": None}),
                    ("prme", "prme", "longmemeval", {"model": OLLAMA_MODEL, "sample": 2}),
                    ("prme@46647825", "full-context", "locomo", {"model": OLLAMA_MODEL, "sample": None})]
    printed = capsys.readouterr().out.splitlines()
    assert json.loads(printed[0]) == {"pair": {"number": 1}, "before": {"complete": True},
                                      "after": {"complete": True}}
    # A baseline that is no longer current is refused as a usage error, before anything is sent (#127).
    with pytest.raises(SystemExit):
        baselines.main(["run-pair", "prme", "--benchmark", "locomo", "--variant", "rrf", "--baseline", "prme",
                        "--provider", "ollama"])
    error = capsys.readouterr().err
    assert "prme is not the current locomo baseline of the defaults: prme@46647825 is" in error
    assert "Reader and judge" not in error and len(seen) == 3


async def test_a_pair_that_fails_its_calibration_check_leaves_no_pair_on_record(harness, monkeypatch):
    pair_arms(harness)
    requests = ollama_provider(monkeypatch)
    with pytest.raises(ValueError, match="calibrate first"):
        await run_pair(harness, "prme", "prme-marked")
    assert requests == [] and not pair_folder(harness, after="prme-marked").exists()
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    paired = await run_pair(harness, "prme", "prme-marked")
    assert (paired["pair"]["number"], paired["pair"]["pairs_started"]) == (1, 1)


async def test_one_pair_of_these_arms_runs_at_a_time(harness, monkeypatch):
    pair_arms(harness)
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    root = harness["data"] / "pairs/prme/prme-marked/locomo"
    root.mkdir(parents=True)
    with (root / "run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with pytest.raises(ValueError, match="Another run of the prme and prme-marked locomo pairs is in progress"):
            await run_pair(harness, "prme", "prme-marked")
        # The A/A pair of the same baseline has its own lock.
        assert (await run_pair(harness))["pair"]["number"] == 1


async def test_a_standalone_run_reports_the_server_versions_of_its_current_preparation_only(harness, monkeypatch):
    baselines.prepare("full-context", "locomo", data=harness["data"])
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    ollama_provider(monkeypatch, empty_when="Caroline")  # An empty answer is final: the arm can never finish.
    with pytest.raises(RuntimeError, match="final failures"):
        await run_ollama(harness)
    shutil.rmtree(arm_folder(harness))
    baselines.prepare("full-context", "locomo", data=harness["data"])
    ollama_provider(monkeypatch, identities=[{**IDENTITY, "server_version": "0.35.0"}])
    result = await run_ollama(harness)
    assert result["complete"] and result["server_versions"] == ["0.35.0"]


def test_gpt54_results_answered_on_their_own_are_still_compared():
    before, after = (answer_result(arm, verdicts) for arm, verdicts in (("plain-rrf", [True, False]),
                                                                        ("full-context", [True, True])))
    for result in (before, after):
        result.update(kind="gpt54-baseline-result", answer_model=baselines.OPENAI_ANSWER_MODEL)
    comparison = baselines.compare(before, after)
    assert comparison["pair"] is None and comparison["server_versions"] == [None] and comparison["warnings"] == []
    # That track answers no baseline of the defaults, so it has none to report.
    assert comparison["baseline"] is None
    assert comparison["gained"] == ["q1"]


def test_a_locomo_difference_excludes_zero_only_when_both_its_intervals_do():
    # Every conversation gains on one of its four questions: both intervals exclude zero.
    before = answer_result("prme", [False] * 40)
    after = answer_result("prme", [number % 4 == 0 for number in range(40)])
    for result in (before, after):
        for number, row in enumerate(result["rows"]):
            row["cluster"] = f"conv-{number // 4}"
    before, after = one_pair(before, after)
    accuracy = baselines.compare(before, after)["accuracy"]
    assert accuracy["interval_95_conversations"][0] > 0 and accuracy["interval_95_questions"][0] > 0
    assert accuracy["interval_95"] == [min(accuracy["interval_95_conversations"][0], accuracy["interval_95_questions"][0]),
                                       max(accuracy["interval_95_conversations"][1], accuracy["interval_95_questions"][1])]


async def test_run_pair_answers_on_the_ollama_track_only_and_from_a_baseline(harness):
    with pytest.raises(ValueError, match="Ollama track only"):
        await baselines.run_pair("prme", "prme", "locomo", model=None, data=harness["data"])
    with pytest.raises(ValueError, match="baseline of the defaults"):
        await run_pair(harness, "plain-rrf", "prme")


async def test_pairs_are_answered_and_compared_only_against_the_current_baseline(harness, monkeypatch):
    pair_arms(harness)
    newer, newest = "prme@0123abcd", "prme@4567cdef"
    recorded_baseline(harness, "locomo", DEFAULTS_TEXT, arm=newer, commit="0123abcd" + "9" * 32)
    requests = ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    requests.clear()
    # The first baseline is complete but no longer current, so nothing is answered alongside it, not even the A/A
    # check, and no pair is opened (#127).
    for after in ("prme-marked", "prme"):
        with pytest.raises(ValueError, match=f"prme is not the current locomo baseline of the defaults: {newer} is"):
            await run_pair(harness, "prme", after)
    assert requests == [] and not (harness["data"] / "pairs" / "prme").exists()
    assert not (harness["data"] / "runs" / "pairs" / "prme").exists()
    paired = await run_pair(harness, newer, "prme-marked")
    before, after = paired["before"], paired["after"]
    comparison = baselines.compare(before, after, data=harness["data"])
    assert comparison["repeat"] is None and comparison["baseline"]["current"] == newer
    assert [(baseline["arm"], baseline["commit"]) for baseline in comparison["baseline"]["complete"]] == [
        ("prme", "a" * 40), (newer, "0123abcd" + "9" * 32)]
    # A before side with the current baseline's name but another preparation's contexts is not the current one.
    moved = {**before, "prepared": {**before["prepared"], "commit": "b" * 40}}
    with pytest.raises(ValueError, match=f"read contexts prepared at b{{40}}, but the current locomo baseline, {newer}"):
        baselines.compare(moved, after, data=harness["data"])
    # A newer baseline that completes while a pair is answered stops it from being published.
    answer = baselines._answer

    async def answered_while_a_baseline_completes(*args, **kwargs):
        await answer(*args, **kwargs)
        recorded_baseline(harness, "locomo", DEFAULTS_TEXT, arm=newest, commit="4567cdef" + "9" * 32)

    monkeypatch.setattr(baselines, "_answer", answered_while_a_baseline_completes)
    with pytest.raises(RuntimeError, match=f"pair 2: {newest} became the current locomo baseline while it was "
                                           f"answered, so nothing is published.* alongside {newest} from now on"):
        await run_pair(harness, newer, "prme-marked")
    assert not pair_published(harness, newer, "prme-marked", number=2)
    finished = pair_log(harness, newer, "prme-marked")[-1]
    assert (finished["complete"], finished["reason"]) == (False, f"{newest} became the current locomo baseline")
    # So is the pair answered before it: its defaults may have changed since.
    with pytest.raises(ValueError, match=f"{newer} is not the current locomo baseline of the defaults: {newest} "
                                         "is, the most recent to complete its own answer run"):
        baselines.compare(before, after, data=harness["data"])
    # Where no baseline's own run is on record, nothing shows the before side is current, so the pair is refused.
    with pytest.raises(ValueError, match="No locomo baseline of the defaults has a complete answer run on record"):
        baselines.compare(before, after)
    # The benchmark a result names is checked before it reaches a path.
    with pytest.raises(ValueError, match="unknown benchmark, '../x'"):
        baselines.compare(*({**result, "benchmark": "../x"} for result in (before, after)), data=harness["data"])
    # A repeat of the defaults may pair any two baselines, wherever it is compared.
    repeat = baselines.compare(*one_pair(answer_result("prme", [True, False]), answer_result("prme", [False, False])))
    assert repeat["repeat"] is not None and repeat["baseline"]["current"] is None


def test_pair_logs_find_each_arm_on_either_side_of_its_own_pairs_only(tmp_path):
    for before, after, benchmark in (("prme", "prme-x", "locomo"), ("prme", "prme-x-2", "locomo"),
                                     ("prme", "prme-x", "longmemeval"), ("prme@0123abcd", "prme-x", "locomo"),
                                     ("prme", "prme", "locomo")):
        baselines._append_event(baselines._pair_log_path(tmp_path, before, after, benchmark), {"event": "started"})
    found = {arm: [str(path.relative_to(tmp_path / "runs/pairs")) for path in baselines._pair_logs(tmp_path, arm,
                                                                                                    "locomo")]
             for arm in ("prme-x", "prme")}
    assert found == {"prme-x": ["prme/prme-x-locomo.jsonl", "prme@0123abcd/prme-x-locomo.jsonl"],
                     "prme": ["prme/prme-locomo.jsonl", "prme/prme-x-2-locomo.jsonl", "prme/prme-x-locomo.jsonl"]}


# Confirmations (#130) -----------------------------------------------------------------

def test_variant_settings_are_what_the_overrides_change_from_the_defaults():
    # parse_overrides keeps what it cannot read as JSON as a string, so these spell the same settings two ways.
    spelled = (["packing.token_budget=2048", "enable_reranker=true"],
               ['packing.token_budget="2048"', "enable_reranker=True"])
    assert [baselines.variant_settings(gate.parse_overrides(items)) for items in spelled] == [
        {"enable_reranker": True, "packing.token_budget": 2048}] * 2
    # Rank fusion fills in its rank constant, so naming the default constant too changes nothing more.
    assert baselines.variant_settings(gate.parse_overrides(['scoring={"fusion": "rrf"}'])) == MARKED
    assert baselines.variant_settings(gate.parse_overrides(["scoring.fusion=rrf", "scoring.rrf_k=60"])) == MARKED
    # An override that repeats a default changes nothing.
    assert baselines.variant_settings(gate.parse_overrides(["packing.token_budget=4096", "scoring.w_paths=0.0"])) == {}
    # A mapping replaced whole records the entries it drops as None.
    assert baselines.variant_settings(gate.parse_overrides(['scoring.node_type_boost={"fact": 1.2}'])) == {
        "scoring.node_type_boost.decision": None, "scoring.node_type_boost.fact": 1.2,
        "scoring.node_type_boost.instruction": None, "scoring.node_type_boost.preference": None,
        "scoring.node_type_boost.summary": None}
    with pytest.raises(ValueError, match="Unknown configuration key: nope"):
        baselines.variant_settings({"nope": 1})


async def test_compare_reads_only_a_variants_first_pair_and_confirmation_under_any_arm_name_and_baseline(
        harness, monkeypatch):
    pair_arms(harness)
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])

    async def compared(before="prme", after="prme-marked"):
        paired = await run_pair(harness, before, after)
        return paired, baselines.compare(paired["before"], paired["after"], data=harness["data"])

    _, comparison = await compared()
    record = comparison["variant"]
    assert (record["variant_settings"], record["contexts_sha256"], record["role"], record["confirmation"]) == (
        MARKED, contexts_sha256(harness, "prme-marked"), "first", None)
    assert record["first"] == {"arm": "prme-marked", "baseline": "prme", "number": 1, "state": "complete",
                               "finished_at": record["first"]["finished_at"], "commit": "a" * 40,
                               "server_version": "0.34.3"}
    [arm] = record["arms"]
    assert (arm["arm"], [entry["commit"] for entry in arm["preparations"]]) == ("prme-marked", ["a" * 40])
    assert arm["pairs"] == [{key: value for key, value in record["first"].items() if key != "arm"}]
    assert (record["related"], record["unknown"], record["dropped"], comparison["warnings"]) == ([], [], [], [])
    # A pair that a final failure stops counts as neither a pass nor a fail: the next complete pair is the
    # confirmation.
    ollama_provider(monkeypatch, empty_when="Caroline")
    with pytest.raises(RuntimeError, match="final failures"):
        await run_pair(harness, "prme", "prme-marked")
    ollama_provider(monkeypatch)
    _, comparison = await compared()
    record = comparison["variant"]
    assert (record["role"], record["first"]["number"], record["confirmation"]["number"]) == ("confirmation", 1, 3)
    assert [(entry["number"], entry["state"]) for entry in record["arms"][0]["pairs"]] == [
        (1, "complete"), (2, "abandoned: a question failed finally"), (3, "complete")]
    assert comparison["warnings"] == []
    # Another arm with the variant's settings, or with its context text under other settings, is the same variant.
    # Its first pair and confirmation are complete, so no later pair is answered under any of its names.
    fabricate_variant(harness, "prme-marked-again", marker="AGAIN")
    fabricate_variant(harness, "prme-renamed", {"duckdb_threads": 2})
    requests = ollama_provider(monkeypatch)
    for later in ("prme-marked", "prme-marked-again", "prme-renamed"):
        with pytest.raises(ValueError, match=rf"The {later} locomo variant already has its first pair \(pair 1 of prme "
                                             r"and prme-marked\) and its confirmation \(pair 3 of prme and "
                                             r"prme-marked\)"):
            await run_pair(harness, "prme", later)
    assert requests == [] and not (harness["data"] / "pairs/prme/prme-renamed").exists()
    assert [event["pair"] for event in pair_log(harness, after="prme-marked")][-1] == 3
    # A variant that changes one of the same settings to another value is a variant of its own, listed as related.
    nudged = {"scoring.fusion": "rrf", "scoring.rrf_k": 30}
    fabricate_variant(harness, "prme-nudged", nudged, marker="NUDGED")
    _, comparison = await compared(after="prme-nudged")
    record = comparison["variant"]
    assert (record["variant_settings"], record["role"], [arm["arm"] for arm in record["arms"]]) == (
        nudged, "first", ["prme-nudged"])
    assert [(group["variant_settings"], [(arm["arm"], len(arm["pairs"])) for arm in group["arms"]])
            for group in record["related"]] == [(MARKED, [("prme-marked", 3), ("prme-marked-again", 0)])]
    assert comparison["warnings"] == [
        "Other variants that change some of the same settings have complete pairs: pair 1 of prme and prme-marked, "
        "pair 3 of prme and prme-marked. Each is a variant of its own, with its own first pair and confirmation "
        "(#130)"]
    # A new baseline numbers the arm's pairs from 1 again, and its earlier pairs stay with it. A confirmation
    # answered alongside another baseline than the first pair is flagged, since it did not repeat the same test.
    newer = "prme@0123abcd"
    recorded_baseline(harness, "locomo", DEFAULTS_TEXT, arm=newer, commit="0123abcd" + "9" * 32)
    again, comparison = await compared(newer, "prme-nudged")
    assert (again["pair"]["number"], again["pair"]["pairs_started"]) == (1, 2)
    assert again["pair"]["earlier_pairs"] == [{"baseline": "prme", "number": 1, "state": "complete"}]
    assert comparison["variant"]["role"] == "confirmation"
    assert comparison["warnings"][0] == ("The first pair and the confirmation were answered alongside different "
                                         f"baselines (prme and {newer}), so the confirmation did not repeat the same "
                                         "test (#130)")


def test_a_variants_pairs_count_in_the_order_they_completed_and_any_later_pair_is_refused():
    data = baselines.data_root(OLLAMA_MODEL)
    answered(data, "locomo", "a" * 40)
    aa_checked(answer_result("prme", [True, False]))

    def results(arm: str) -> tuple[dict, dict]:
        return one_pair(answer_result("prme", [True, False]), answer_result(arm, [True, True]))

    def at(hour: int, minute: int = 0) -> str:
        return f"2026-09-24T{hour:02d}:{minute:02d}:00+00:00"

    # A pair that is not on record, not complete, not the one the results were answered in, or with no identity
    # recorded, is refused.
    with pytest.raises(ValueError, match="Pair 1 of prme and prme-rrf is not in the track's pair run logs"):
        baselines.compare(*results("prme-rrf"))
    for arm, logged, message in (
            ("prme-open", {"complete": False}, "is on record as unfinished, not complete"),
            ("prme-other", {"pair_id": "other"}, "has another pair id than these results"),
            ("prme-blank", {}, "Nothing records the settings or the contexts pair 1 of prme and prme-blank")):
        logged_pair(after=arm, **{"settings": MARKED if arm != "prme-blank" else None, **logged},
                    started=at(9), at=at(9, 30))
        with pytest.raises(ValueError, match=message):
            baselines.compare(*results(arm))
    # The first pair: a complete pair invalid under the 1% limit counts as neither, even though it completed first,
    # and an arm prepared before preparations were logged gives its pairs the settings its manifest reads as.
    logged_pair(after="prme-bad", settings=MARKED, started=at(0, 30), at=at(1), invalid="more than 1%")
    old = data / "prme-old" / "locomo" / "prepared.json"
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps({"complete": True, "arm": "prme-old", "contexts": [{"question_id": "q0",
                                                                                  "text_sha256": "t"}],
                               "provenance": {"commit": "c" * 40, "overrides": {"scoring": {"fusion": "rrf"}}}}))
    logged_pair(after="prme-old", started=at(1, 30), at=at(2))
    # A pair that records no identity might be an earlier pair of the variant.
    logged_pair(after="prme-lost", started=at(0), at=at(0, 10))
    # A pair that started before the first pair completed is not a fresh confirmation, even if it completes next.
    logged_pair(after="prme-early", settings=MARKED, started=at(1, 45), at=at(2, 30))
    # This pair is the confirmation. One of the variant's pairs was given up by hand while it ran.
    logged_pair(after="prme-rrf", settings=MARKED, pair_id="p", started=at(2, 45), at=at(3))
    logged_pair(after="prme-stopped", settings=MARKED, started=at(2, 50), abandoned="an arm was prepared again")
    # A pair that completes after the confirmation is a later pair. Once complete, a pair stays as it completed, even
    # when older code gives it up after its folder was moved aside, or its results are written again.
    logged_pair(after="prme-late", settings=MARKED, started=at(3, 30), at=at(4))
    late = baselines._pair_log_path(data, "prme", "prme-late", "locomo")
    baselines._append_event(late, {"event": "abandoned", "pair": 1, "reason": "its pair.json record is missing"})
    baselines._append_event(late, {"event": "finished", "pair": 1, "sample": None, "complete": True, "at": at(8)})
    # Pairs whose arm has no identity on record, or more than one, record none either: a pair folder its run log
    # never names, and a pair of an arm prepared twice with other settings.
    (data / "pairs" / "prme" / "prme-ghost" / "locomo" / "pair-1").mkdir(parents=True)
    for settings in (MARKED, {"duckdb_threads": 2}):
        baselines._log_run(data, "prme-twice", "locomo", {"event": "prepared", "variant_settings": settings})
    logged_pair(after="prme-twice", started=at(6), at=at(6, 30))
    comparison = baselines.compare(*results("prme-rrf"))
    record = comparison["variant"]
    assert (record["role"], record["first"]["arm"], record["confirmation"]["arm"]) == (
        "confirmation", "prme-old", "prme-rrf")
    # Every arm of the variant: those with a pair of it, and those prepared with its settings (prme-twice once).
    assert [(arm["arm"], [entry["commit"] for entry in arm["preparations"]], [entry["state"] for entry in arm["pairs"]])
            for arm in record["arms"]] == [
        ("prme-bad", [], ["complete but invalid: more than 1%"]), ("prme-early", [], ["complete"]),
        ("prme-late", [], ["complete"]), ("prme-old", ["c" * 40], ["complete"]), ("prme-open", [], ["unfinished"]),
        ("prme-other", [], ["complete"]), ("prme-rrf", [], ["complete"]),
        ("prme-stopped", [], ["abandoned: an arm was prepared again"]), ("prme-twice", [None], [])]
    assert record["arms"][2]["pairs"][0]["finished_at"] == at(4)
    assert [(entry["arm"], entry["state"]) for entry in record["unknown"]] == [
        ("prme-blank", "complete"), ("prme-ghost", "unfinished"), ("prme-lost", "complete"), ("prme-twice", "complete")]
    assert [entry["arm"] for entry in record["dropped"]] == ["prme-stopped"]
    assert comparison["warnings"] == [
        "Pairs of this variant that started before this one finished never completed, for a reason other than a "
        "final failure: pair 1 of prme and prme-stopped (abandoned: an arm was prepared again). A pair left "
        "unfinished or given up by hand can hide a result (#130)",
        "Variant pairs that completed before this one record neither settings nor contexts, so any of them may be an "
        "earlier pair of this variant: pair 1 of prme and prme-lost (complete) (#130)"]
    for later in ("prme-early", "prme-late"):
        with pytest.raises(ValueError, match="neither the variant's first pair nor its confirmation: the first is "
                                             "pair 1 of prme and prme-old and the confirmation is pair 1 of prme and "
                                             "prme-rrf, and a confirmation must start after the first pair "
                                             "completed"):
            baselines.compare(*results(later))
    # A manifest from before #130 whose overrides this checkout no longer accepts records no settings.
    assert baselines._prepared_identity({"provenance": {"overrides": {"nope": 1}}, "contexts": []})[
        "variant_settings"] is None
    # The order is the order the pairs completed, which needs a time zone to read.
    logged_pair(after="prme-naive", settings=MARKED, started=at(5), at="2026-09-24T05:30:00")
    with pytest.raises(ValueError, match="does not record when pair 1 finished, with a time zone"):
        baselines.compare(*results("prme-rrf"))


# A/A record (#137) ----------------------------------------------------------------

def aa_record(results: Path | None = None) -> list[dict]:
    return baselines._run_events(baselines._aa_record_path(results or baselines.RESULTS, OLLAMA_MODEL.model))


def variant_results(identity: dict | None = None, **changes) -> tuple[dict, dict]:
    """Both sides of pair 1 of prme and prme-rrf, logged complete as the variant's first pair by logged_pair()."""
    return one_pair(*({**answer_result(arm, verdicts, identity=identity), **changes}
                      for arm, verdicts in (("prme", [True, False]), ("prme-rrf", [True, True]))))


async def test_run_pair_records_each_published_aa_pair_and_answers_a_variant_only_under_a_recorded_check(
        harness, monkeypatch):
    use_longmemeval(harness, TWO_LME)
    recorded_baseline(harness, "longmemeval", {"q1": LME_CONTEXT, "q2": "(2023/05/21) user: I live in Porto."})
    recorded_baseline(harness, "locomo", DEFAULTS_TEXT)
    fabricate_variant(harness)
    requests = ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    requests.clear()
    # compare would refuse a variant's pair that no recorded A/A check covers, and it would still count as the
    # variant's first pair, so it is never answered and no pair is opened.
    with pytest.raises(ValueError, match=r"No A/A check on locomo .* A/A pairs recorded on locomo: none\."):
        await run_pair(harness, "prme", "prme-marked")
    assert requests == [] and not pair_folder(harness, after="prme-marked").exists()
    # A sample is a smoke check, not an A/A check, so nothing is recorded until the full pair is published.
    await run_pair(harness, benchmark="longmemeval", sample=1)
    assert aa_record() == []
    paired = await run_pair(harness, benchmark="longmemeval")
    [entry] = aa_record()
    log = pair_log(harness, benchmark="longmemeval")
    # Every start of the pair records its id, which binds the run log to the record.
    assert {event["id"] for event in log if event["event"] == "started"} == {paired["pair"]["id"]}
    assert entry == {**entry, "kind": "ollama-aa-check", "benchmark": "longmemeval", "baseline": "prme",
                     "pair": {key: paired["pair"][key] for key in ("id", "number", "sha256")},
                     "finished_at": log[-1]["at"], "accepted": True, "refused": None, "first": True,
                     "questions": 2, "correct": {"before": 2, "after": 2}, "difference": 0.0,
                     "interval_95": [0.0, 0.0], "interval_excludes_zero": False, "changed_verdicts": 0,
                     "warnings": []}
    assert entry["conditions"] == {
        "answer_model": ANSWER_MODEL, "server_versions": ["0.34.3"], "context_budget": baselines.RULE_BUDGET,
        "failure_policy": {"id": baselines.FAILURE_POLICY, "sha256": digest(baselines.FAILURE_AMENDMENT)},
        "tokenizer": baselines.RULE_TOKENIZER}
    assert entry["results"] == {side: {"path": str(path.relative_to(harness["results"])), "sha256": digest(path)}
                                for side, path in zip(baselines.PAIR_SIDES,
                                                      published_paths(harness, benchmark="longmemeval"))}
    # The rule reads both benchmarks, so a check on LongMemEval-S alone is not enough.
    with pytest.raises(ValueError, match=r"No A/A check on locomo .* A/A pairs recorded on locomo: none\."):
        await run_pair(harness, "prme", "prme-marked")
    await run_pair(harness)
    variant = await run_pair(harness, "prme", "prme-marked")
    comparison = baselines.compare(variant["before"], variant["after"], data=harness["data"])
    checks = comparison["aa_check"]["checks"]
    assert {benchmark: (found["check"]["baseline"], found["check"]["number"], found["other_pairs"])
            for benchmark, found in checks.items()} == {"locomo": ("prme", 1, []), "longmemeval": ("prme", 1, [])}
    assert checks["longmemeval"]["check"] == {
        "baseline": "prme", "number": 1, "pair_id": entry["pair"]["id"],
        **{key: entry[key] for key in ("finished_at", "accepted", "refused", "difference", "interval_95",
                                       "interval_excludes_zero", "changed_verdicts", "results")}}
    assert comparison["aa_check"]["conditions"] == {
        "manifest_digest_sha256": "e" * 64, "server_versions": ["0.34.3"], "context_budget": baselines.RULE_BUDGET,
        "failure_policy": entry["conditions"]["failure_policy"], "tokenizer": baselines.RULE_TOKENIZER}
    record = baselines._aa_record_path(harness["results"], OLLAMA_MODEL.model)
    assert comparison["aa_check"]["record"] == {"path": record.name, "sha256": digest(record)}
    assert comparison["warnings"] == []
    # A later A/A pair under the same conditions never replaces the check, and compare shows it.
    await run_pair(harness)
    assert [(entry["benchmark"], entry["pair"]["number"], entry["first"]) for entry in aa_record()] == [
        ("longmemeval", 1, True), ("locomo", 1, True), ("locomo", 2, False)]
    again = baselines.compare(variant["before"], variant["after"], data=harness["data"])
    assert again["aa_check"]["checks"]["locomo"]["check"]["number"] == 1
    assert [pair["number"] for pair in again["aa_check"]["checks"]["locomo"]["other_pairs"]] == [2]
    assert again["warnings"] == [
        "Other A/A pairs on locomo were answered under the conditions of this pair: A/A pair 2 of prme (accepted). "
        "The A/A check is the first that compare accepted, A/A pair 1 of prme, and a later pair never replaces it "
        "(#137)"]
    # After an Ollama update, no variant pair is answered until a new A/A pair has been recorded on both benchmarks.
    upgraded = {**IDENTITY, "server_version": "0.34.4"}
    ollama_provider(monkeypatch, identities=[upgraded])
    with pytest.raises(ValueError, match=r"Ollama server version 0\.34\.4 and context budget\).* A/A pairs recorded on "
                                         r"locomo: A/A pair 1 of prme \(differs in Ollama server version\); A/A pair 2 "
                                         r"of prme \(differs in Ollama server version\)\."):
        await run_pair(harness, "prme", "prme-marked")
    await run_pair(harness)
    with pytest.raises(ValueError, match="No A/A check on longmemeval"):
        await run_pair(harness, "prme", "prme-marked")
    await run_pair(harness, benchmark="longmemeval")
    confirmation = await run_pair(harness, "prme", "prme-marked")
    assert (confirmation["pair"]["number"], confirmation["after"]["server_versions"]) == (2, ["0.34.4"])
    confirmed = baselines.compare(confirmation["before"], confirmation["after"], data=harness["data"])
    assert confirmed["variant"]["role"] == "confirmation"
    assert {benchmark: found["check"]["number"] for benchmark, found in confirmed["aa_check"]["checks"].items()} == {
        "locomo": 3, "longmemeval": 2}
    # The first pair still relies on the check measured under its own server version.
    assert baselines.compare(variant["before"], variant["after"], data=harness["data"])["aa_check"]["checks"][
        "locomo"]["check"]["number"] == 1


async def test_an_aa_pair_starts_only_when_the_record_lists_every_complete_one_and_is_recorded_after_them(
        harness, monkeypatch):
    recorded_baseline(harness, "locomo", DEFAULTS_TEXT)
    requests = ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    # An A/A pair published in another checkout, or before the record existed, is on record in the run logs only.
    # The record could not decide which pair is first without it, so no A/A pair is answered until it is added.
    other = "prme@0c0c0c0c"
    elsewhere = aa_published(harness_conditions(), "locomo", baseline=other)
    aa_logged(harness["data"], "locomo", baseline=other)
    requests.clear()
    with pytest.raises(ValueError, match=f"A/A pair 1 of {other} on locomo is on record in the track's run logs as "
                                         "complete, but the A/A record .* does not list it"):
        await run_pair(harness)
    assert requests == [] and not pair_folder(harness).exists()
    baselines.record_aa_check(*elsewhere, data=harness["data"])
    await run_pair(harness)
    assert [(entry["baseline"], entry["first"]) for entry in aa_record()] == [(other, True), ("prme", False)]
    # A pair that is published but cannot be added to the record says so, and how to add it once that is resolved.

    def unrecorded(*args, **kwargs):
        raise ValueError("The record is out of reach.")

    monkeypatch.setattr(baselines, "record_aa_check", unrecorded)
    with pytest.raises(RuntimeError, match=r"prme and prme locomo pair 2 is complete and published, but it was not "
                                           r"added to the A/A record: The record is out of reach\. Once that is "
                                           r"resolved, add it with record-aa-check --before .*pair-2-before-result\.json "
                                           r"--after .*pair-2-after-result\.json \(#137\)"):
        await run_pair(harness)
    assert len(pair_published(harness, number=2)) == 2


def test_an_aa_check_covers_only_its_own_model_identity_settings_policy_server_and_budget():
    before, after = variant_results()
    conditions = baselines._pair_conditions(before, after)
    assert baselines._condition_differences(conditions, conditions) == []
    # An Ollama update alone does not change the model identity (same_model), but it is another condition.
    for changed, differs in (
            ({"answer_model": {**after["answer_model"], "identity": {**IDENTITY, "manifest_digest_sha256": "f" * 64}}},
             ["model identity"]),
            ({"answer_model": {**after["answer_model"], "seed": 1}}, ["answer settings"]),
            ({"answer_model": {**after["answer_model"], "failure_policy": baselines.FAILURE_POLICY},
              "failure_policy": {"sha256": "a" * 64}}, ["failure policy"]),
            ({"failure_policy": {"sha256": "b" * 64}}, ["failure policy"]),
            ({"server_versions": ["0.34.4"]}, ["Ollama server version"]),
            ({"context_budget": 2048}, ["context budget"]),
            ({"prepared": {**after["prepared"], "tokenizer": "o200k_base"}}, ["context budget"]),
            ({"answer_model": {**after["answer_model"], "identity": {**IDENTITY, "server_version": "0.34.4"}}},
             ["Ollama server version"])):
        moved = baselines._pair_conditions({**before, **changed}, {**after, **changed})
        assert baselines._condition_differences(conditions, moved) == differs, changed
    # A result published before #125 names no tokenizer and was packed with the registered one.
    unnamed = {**after, "prepared": {**after["prepared"], "tokenizer": None}}
    assert baselines._condition_differences(conditions, baselines._pair_conditions(before, unnamed)) == []


def test_compare_refuses_a_variants_pair_outside_every_recorded_aa_check():
    answered(baselines.data_root(OLLAMA_MODEL), "locomo", "a" * 40)
    logged_pair(settings=MARKED)
    with pytest.raises(ValueError, match=r"No A/A check on locomo .* A/A pairs recorded on locomo: none\. Answer an "
                                         r"A/A pair under these conditions on both benchmarks first"):
        baselines.compare(*variant_results())
    # The rule reads both benchmarks, so an A/A check on one of them is not enough.
    aa_checked(answer_result("prme", [True, False]), benchmarks=("locomo",))
    with pytest.raises(ValueError, match="No A/A check on longmemeval"):
        baselines.compare(*variant_results())
    aa_checked(answer_result("prme", [True, False]), benchmarks=("longmemeval",))
    assert baselines.compare(*variant_results())["aa_check"]["checks"]["locomo"]["check"]["baseline"] == \
        "prme@00aa00aa"
    # compare reads the record of the checkout whose results it is given, which must list every complete A/A pair.
    with pytest.raises(ValueError, match="A/A pair 1 of prme@00aa00aa on locomo is on record in the track's run logs "
                                         "as complete, but the A/A record .* does not list it"):
        baselines.compare(*variant_results(), results=baselines.RESULTS / "elsewhere")
    # The pair was answered under another model identity, other settings or another server version.
    for identity, changes, differs in (
            ({**IDENTITY, "manifest_digest_sha256": "f" * 64}, {}, "model identity"),
            (None, {"answer_model": {**OLLAMA_MODEL.settings(), "identity": IDENTITY, "seed": 1}}, "answer settings"),
            ({**IDENTITY, "server_version": "0.34.4"}, {}, "Ollama server version")):
        with pytest.raises(ValueError, match=rf"A/A pair 1 of prme@00aa00aa \(differs in {differs}\)"):
            baselines.compare(*variant_results(identity, **changes))
    # A reference arm paired with the defaults is not a default change, so it needs no A/A check.
    plain = one_pair(answer_result("prme", [True, False], identity={**IDENTITY, "server_version": "0.34.4"}),
                     answer_result("plain-rrf", [True, True], identity={**IDENTITY, "server_version": "0.34.4"}))
    assert baselines.compare(*plain)["aa_check"] is None


def test_the_aa_record_must_list_exactly_the_complete_aa_pairs_in_the_run_logs_and_match_their_results():
    data = baselines.data_root(OLLAMA_MODEL)
    answered(data, "locomo", "a" * 40)
    logged_pair(settings=MARKED)
    like = answer_result("prme", [True, False])
    aa_checked(like)
    record = baselines._aa_record_path(baselines.RESULTS, OLLAMA_MODEL.model)
    kept = record.read_text()
    # A complete A/A pair that the record leaves out, valid or not, could hide a result that did not hold.
    for invalid in ({}, {"invalid": "more than 1%"}):
        log = baselines._pair_log_path(data, "prme@0b0b0b0b", "prme@0b0b0b0b", "longmemeval")
        log.unlink(missing_ok=True)
        aa_logged(data, "longmemeval", baseline="prme@0b0b0b0b", number=4, **invalid)
        with pytest.raises(ValueError, match=r"A/A pair 4 of prme@0b0b0b0b on longmemeval is on record in the track's "
                                             r"run logs as complete.*, but the A/A record .* does not list it"):
            baselines.compare(*variant_results())
    log.unlink()
    assert baselines.compare(*variant_results())["aa_check"] is not None
    # A listed pair the run logs do not show complete, with its id and finish time, is refused too, and so is a pair
    # taken from another machine's record, which these run logs never saw, and a pair listed twice.
    entries = [json.loads(line) for line in kept.splitlines()]
    stranger = {**entries[0], "pair": {**entries[0]["pair"], "id": "x", "number": 7}}
    for lines, message in (
            ([{**entries[0], "pair": {**entries[0]["pair"], "id": "other"}}, *entries[1:]],
             "lists A/A pair 1 of prme@00aa00aa on locomo, which the track's run logs do not show complete with that "
             "pair id and finish time"),
            ([{**entries[0], "finished_at": "2026-09-24T08:00:00+00:00"}, *entries[1:]], "do not show complete"),
            ([*entries, stranger], "lists A/A pair 7 of prme@00aa00aa on locomo, which the track's run logs do not"),
            ([*entries, entries[0]], "lists A/A pair 1 of prme@00aa00aa on locomo 2 times")):
        record.write_text("".join(json.dumps(entry) + "\n" for entry in lines))
        with pytest.raises(ValueError, match=message):
            baselines.compare(*variant_results())
    # A line must match the published results it names, so an edited line cannot move the check to other
    # conditions, and a result edited or moved after it was recorded is refused.
    moved = {**entries[0], "conditions": {**entries[0]["conditions"], "server_versions": ["0.34.4"]}}
    record.write_text("".join(json.dumps(entry) + "\n" for entry in (moved, *entries[1:])))
    with pytest.raises(ValueError, match="line for A/A pair 1 of prme@00aa00aa on locomo does not match the pair and "
                                         "conditions its published results record"):
        baselines.compare(*variant_results(identity={**IDENTITY, "server_version": "0.34.4"}))
    for path in ("../outside.json", entries[0]["results"]["after"]["path"] + ".moved"):
        away = {**entries[0], "results": {**entries[0]["results"], "after": {**entries[0]["results"]["after"],
                                                                              "path": path}}}
        record.write_text("".join(json.dumps(entry) + "\n" for entry in (away, *entries[1:])))
        with pytest.raises(ValueError, match="which is not published in this checkout with the recorded digest"):
            baselines.compare(*variant_results())
    record.write_text(kept)
    published = baselines.RESULTS / entries[0]["results"]["after"]["path"]
    original = published.read_text()
    published.write_text(original.replace('"arm"', '"arm" ', 1))
    with pytest.raises(ValueError, match="with the recorded digest"):
        baselines.compare(*variant_results())
    published.write_text(original)
    # The check is the first accepted pair, which the record must mark as first, and no other.
    record.write_text("".join(json.dumps({**entry, "first": False}) + "\n" for entry in entries))
    with pytest.raises(ValueError, match="must mark A/A pair 1 of prme@00aa00aa on locomo, the first A/A pair under its "
                                         "conditions that compare accepted, and no other"):
        baselines.compare(*variant_results())
    record.write_text(kept)
    aa_checked(like, number=2, finished=LATER_AA, benchmarks=("locomo",))
    lines = record.read_text().splitlines()
    assert [json.loads(line)["first"] for line in lines] == [True, True, False]
    record.write_text("".join(line + "\n" for line in (*lines[:2], json.dumps({**json.loads(lines[2]), "first": True}))))
    with pytest.raises(ValueError, match="and no other, as the first A/A check under them"):
        baselines.compare(*variant_results())
    # Pairs must be listed in the order they finished.
    record.write_text("".join(line + "\n" for line in (lines[2], *lines[:2])))
    with pytest.raises(ValueError, match="does not list the A/A pairs on locomo in the order they finished"):
        baselines.compare(*variant_results())


def test_a_refused_aa_pair_never_counts_and_one_that_excludes_zero_brings_the_rules_margin():
    answered(baselines.data_root(OLLAMA_MODEL), "locomo", "a" * 40)
    logged_pair(settings=MARKED)
    like = answer_result("prme", [True, False, True, False])
    # An A/A pair its run log records complete but invalid is recorded as refused, and it never becomes the check.
    [invalid] = aa_checked(like, benchmarks=("locomo",), invalid="more than 1%")
    assert (invalid["accepted"], invalid["first"], invalid["interval_95"]) == (False, False, None)
    assert invalid["refused"] == "its run log records it complete but invalid: more than 1%"
    with pytest.raises(ValueError, match=r"A/A pair 1 of prme@00aa00aa \(refused\)"):
        baselines.compare(*variant_results())
    aa_checked(like, number=2, finished=LATER_AA, excludes_zero=True)
    comparison = baselines.compare(*variant_results())
    locomo = comparison["aa_check"]["checks"]["locomo"]
    assert (locomo["check"]["number"], locomo["check"]["interval_excludes_zero"]) == (2, True)
    assert [(pair["number"], pair["accepted"]) for pair in locomo["other_pairs"]] == [(1, False)]
    margin = ("A/A pair 2 of prme@00aa00aa under the conditions of this pair excludes zero, so under the "
              "default-change rule in CLAUDE.md a variant's gain there must also be larger than the largest absolute "
              "A/A difference measured so far on that benchmark, the #118 repeat included (#137)")
    assert comparison["warnings"] == [
        "Other A/A pairs on locomo were answered under the conditions of this pair: A/A pair 1 of prme@00aa00aa "
        "(refused). The A/A check is the first that compare accepted, A/A pair 2 of prme@00aa00aa, and a later pair "
        "never replaces it (#137)", f"On locomo, {margin}", f"On longmemeval, {margin}"]


def test_record_aa_check_takes_a_published_aa_pair_on_record_as_complete_once_in_finish_order(tmp_path):
    data = baselines.data_root(OLLAMA_MODEL)
    like = answer_result("prme", [True, False])
    [entry] = aa_checked(like, benchmarks=("locomo",))
    paths = [baselines.RESULTS / entry["results"][side]["path"] for side in baselines.PAIR_SIDES]
    with pytest.raises(ValueError, match="A/A pair 1 of prme@00aa00aa on locomo is already in the A/A record"):
        baselines.record_aa_check(*paths, data=data)
    # Only a pair of a baseline with itself is an A/A check, and only a complete answer run of one.
    elsewhere = [tmp_path / f"{side}.json" for side in baselines.PAIR_SIDES]
    for path, result in zip(elsewhere, variant_results()):
        path.write_text(json.dumps(result))
    with pytest.raises(ValueError, match="An A/A check is the two sides of one pair of a baseline"):
        baselines.record_aa_check(*elsewhere, data=data)
    for path, source in zip(elsewhere, paths):
        result = json.loads(source.read_text())
        path.write_text(json.dumps({**result, "kind": result["kind"] + "-sample"}))
    with pytest.raises(ValueError, match="The before result is not a complete answer run on the Ollama track"):
        baselines.record_aa_check(*elsewhere, data=data)
    for path, source in zip(elsewhere, paths):
        path.write_text(json.dumps({**json.loads(source.read_text()), "benchmark": "../x"}))
    with pytest.raises(ValueError, match="unknown benchmark, '../x'"):
        baselines.record_aa_check(*elsewhere, data=data)
    # A pair its run logs do not show complete, with the same id, is refused before anything is written.
    other, early = "prme@0c0c0c0c", "prme@0d0d0d0d"
    second = aa_published(like, "locomo", baseline=other, number=2)
    with pytest.raises(ValueError, match=f"A/A pair 2 of {other} on locomo is not on record as complete"):
        baselines.record_aa_check(*second, data=data)
    aa_logged(data, "locomo", baseline=other, number=2, pair_id="other", finished=LATER_AA)
    with pytest.raises(ValueError, match="on record has another pair id than these results"):
        baselines.record_aa_check(*second, data=data)
    baselines._pair_log_path(data, other, other, "locomo").unlink()
    # Only a published result is recorded, and only in the order the pairs finished.
    third = aa_published(like, "locomo", baseline=early, number=3)
    aa_logged(data, "locomo", baseline=early, number=3, finished="2026-09-24T08:00:00+00:00")
    for path, source in zip(elsewhere, third):
        path.write_text(source.read_text())
    with pytest.raises(ValueError, match="The before result is not published under"):
        baselines.record_aa_check(*elsewhere, data=data)
    with pytest.raises(ValueError, match=f"A/A pair 3 of {early} on locomo finished before A/A pair 1 of "
                                         "prme@00aa00aa, which the A/A record .* already lists"):
        baselines.record_aa_check(*third, data=data)
    baselines._pair_log_path(data, early, early, "locomo").unlink()
    # A record that leaves out an earlier complete A/A pair takes no later one, so first is never decided without
    # it. Two pairs left out are added in the order they finished, whichever is tried first.
    aa_logged(data, "locomo", baseline=other, number=2, finished=LATER_AA)
    fourth = aa_published(like, "locomo", number=4)
    aa_logged(data, "locomo", number=4, finished="2026-09-24T11:00:00+00:00")
    with pytest.raises(ValueError, match=f"A/A pair 2 of {other} on locomo is on record in the track's run logs as "
                                         "complete, but the A/A record .* does not list it"):
        baselines.record_aa_check(*fourth, data=data)
    baselines.record_aa_check(*second, data=data)
    baselines.record_aa_check(*fourth, data=data)
    assert [(line["baseline"], line["pair"]["number"], line["first"]) for line in aa_record()] == [
        ("prme@00aa00aa", 1, True), (other, 2, False), ("prme@00aa00aa", 4, False)]
    # Any other refusal by compare records nothing: a passing problem never becomes a permanent refusal.
    same = aa_published(like, "locomo", number=5, rows=like["rows"])
    aa_logged(data, "locomo", number=5, finished="2026-09-24T12:00:00+00:00")
    with pytest.raises(ValueError, match="compare refuses A/A pair 5 of prme@00aa00aa on locomo, which its run log "
                                         "records complete, so nothing was recorded: The before and after results "
                                         "are the same answer run"):
        baselines.record_aa_check(*same, data=data)
    assert len(aa_record()) == 3


def test_cli_record_aa_check_adds_a_published_aa_pair(monkeypatch, tmp_path, capsys):
    recorded = baselines.record_aa_check
    seen = []
    monkeypatch.setattr(baselines, "record_aa_check",
                        lambda before, after: seen.append((before, after)) or {"kind": "ollama-aa-check"})
    baselines.main(["record-aa-check", "--before", "b.json", "--after", "a.json"])
    assert seen == [(Path("b.json"), Path("a.json"))]
    assert json.loads(capsys.readouterr().out) == {"kind": "ollama-aa-check"}
    for argv in (["record-aa-check", "--before", "b.json"],
                 ["record-aa-check", "prme", "--before", "b.json", "--after", "a.json"],
                 ["record-aa-check", "--benchmark", "locomo", "--before", "b.json", "--after", "a.json"],
                 ["run-pair", "prme", "--benchmark", "locomo", "--provider", "ollama", "--baseline", "prme",
                  "--before", "b.json", "--after", "a.json"]):
        with pytest.raises(SystemExit):
            baselines.main(argv)
    assert len(seen) == 1
    # A file it cannot read, or a result it refuses, is a usage error rather than a traceback.
    monkeypatch.setattr(baselines, "record_aa_check", recorded)
    with pytest.raises(SystemExit):
        baselines.main(["record-aa-check", "--before", str(tmp_path / "missing.json"), "--after", "a.json"])
    for side in ("before", "after"):
        (tmp_path / f"{side}.json").write_text(json.dumps({"kind": "ollama-answer-result", "complete": False}))
    with pytest.raises(SystemExit):
        baselines.main(["record-aa-check", "--before", str(tmp_path / "before.json"),
                        "--after", str(tmp_path / "after.json")])
    assert "not a complete answer run on the Ollama track" in capsys.readouterr().err


def test_every_line_of_the_tracked_aa_record_matches_its_published_aa_pair():
    entries = aa_record(PUBLISHED)
    # The A/A checks of 2026-09-24 that CLAUDE.md and BENCHMARKS.md cite come first, under the conditions they
    # name: model identity e04da138, Ollama 0.34.3, the amended failure policy, the answer settings and the 4K budget.
    assert [(entry["benchmark"], entry["baseline"], entry["pair"]["number"], entry["accepted"], entry["first"])
            for entry in entries[:2]] == [("longmemeval", REPEAT, AA_PAIRS["longmemeval"], True, True),
                                          ("locomo", REPEAT, AA_PAIRS["locomo"], True, True)]
    for entry in entries[:2]:
        conditions = entry["conditions"]
        assert conditions["answer_model"]["identity"]["manifest_digest_sha256"].startswith("e04da138")
        assert (conditions["server_versions"], conditions["context_budget"], conditions["tokenizer"]) == (
            ["0.34.3"], baselines.RULE_BUDGET, baselines.RULE_TOKENIZER)
        assert {key: value for key, value in conditions["answer_model"].items() if key != "identity"} == \
            {**OLLAMA_MODEL.settings(), "failure_policy": baselines.FAILURE_POLICY}
    # Every line, including any added later, names its published results with their digests and conditions, keeps
    # its benchmark's finish order and first flags, and gives compare's own numbers.
    for benchmark in gate.GATE_BENCHMARKS:
        listed = [entry for entry in entries if entry["benchmark"] == benchmark]
        assert [baselines._finish_time(entry) for entry in listed] == sorted(map(baselines._finish_time, listed))
        for number, entry in enumerate(listed):
            assert entry["first"] == (entry["accepted"] and not any(
                earlier["accepted"] and not baselines._condition_differences(earlier["conditions"],
                                                                             entry["conditions"])
                for earlier in listed[:number]))
    for entry in entries:
        baselines._check_aa_results(PUBLISHED, entry)
        before, after = (json.loads((PUBLISHED / entry["results"][side]["path"]).read_text())
                         for side in baselines.PAIR_SIDES)
        assert entry["pair"] == {key: before["pair"][key] for key in ("id", "number", "sha256")}
        assert entry["finished_at"] > after["finished_at"]
        if entry["accepted"]:
            comparison = baselines.compare(before, after)
            assert (entry["difference"], entry["interval_95"]) == (comparison["accuracy"]["delta"],
                                                                   comparison["accuracy"]["interval_95"])
            assert (entry["changed_verdicts"], entry["interval_excludes_zero"]) == (
                comparison["repeat"]["changed_verdicts"], comparison["repeat"]["interval_excludes_zero"])
            assert (entry["refused"], entry["warnings"]) == (None, comparison["warnings"])


# Amended failure policy (#132) --------------------------------------------------------

def attempt_calls(folder: Path, qid: str, attempt: int = 1) -> list[str]:
    """The reader and judge records one question attempt holds, by name."""
    return sorted(path.name for path in (folder / "execution" / qid / f"attempt-{attempt}").glob("*.json")
                  if path.name.count(".") == 1 and path.name not in {"result.json", "failure.json"})


def test_the_recorded_amendment_states_the_failure_policy_this_module_applies(monkeypatch, tmp_path):
    amendment = json.loads(RECORDED_AMENDMENT.read_text())
    assert (amendment["id"], amendment["issue"]) == (baselines.FAILURE_POLICY, 132)
    assert amendment["decided_at"].startswith("2026-09-24") and amendment["defaults_changed"] is False
    # It amends the registered comparison, whose frozen sources stay as registered.
    registration = json.loads(study.REG.read_text())
    assert amendment["registration_sha256"] == digest(study.REG)
    assert all(digest(study.ROOT / path) == registration["sources"][path] for path in baselines.FROZEN_SOURCES)
    assert amendment["retry_policy"] == baselines.OLLAMA_RETRY_POLICY
    # The values that score answers are pinned as well as the text: one retry per call and a 1% limit.
    assert amendment["parameters"] == baselines.policy_parameters() == {
        "verdict_pattern": "[^A-Za-z]*(yes|no)[^A-Za-z]*", "verdict_flags": 274,
        "reader_calls": ["reader.json", "reader-retry.json"], "judge_calls": ["judge.json", "judge-retry.json"],
        "unscored_outcomes": ["truncated", "verdict_unresolved"], "unscored_limit_percent": 1}
    assert baselines.failure_amendment() == {"id": baselines.FAILURE_POLICY, "issue": 132,
                                             "registered_at": amendment["registered_at"],
                                             "sha256": digest(RECORDED_AMENDMENT)}
    # A record that states other rules, or amends another registration, is refused before any question is asked.
    changed = tmp_path / "amendment.json"
    monkeypatch.setattr(baselines, "FAILURE_AMENDMENT", changed)
    for edit in ({"id": "other"}, {"parameters": {**amendment["parameters"], "unscored_limit_percent": 2}},
                 {"registration_sha256": "0" * 64},
                 {"kind": "other"}, {"issue": "132"}, {"retry_policy": "Other rules."}):
        changed.write_text(json.dumps({**amendment, **edit}))
        with pytest.raises(ValueError, match="does not state the failure policy"):
            baselines.failure_amendment()
    changed.write_text(json.dumps(amendment))
    policy = baselines.OLLAMA_RETRY_POLICY
    monkeypatch.setattr(baselines, "OLLAMA_RETRY_POLICY", policy + " Changed.")
    with pytest.raises(ValueError, match="does not state the failure policy"):
        baselines.failure_amendment()
    # A wider verdict rule under the same policy id is refused too.
    monkeypatch.setattr(baselines, "OLLAMA_RETRY_POLICY", policy)
    monkeypatch.setattr(baselines, "_VERDICT", baselines.re.compile(r"\W*(yes|no)\W*", baselines.re.IGNORECASE))
    with pytest.raises(ValueError, match="does not state the failure policy"):
        baselines.failure_amendment()


@pytest.mark.parametrize(("text", "expected"), [
    ("yes", True), ("No", False), (" Yes.\n", True), ("姫Yes", True), ('"no"', False), ("**YES**", True),
    ("yes and no", None), ("Yes, it matches.", None), ("maybe", None), ("", None), ("noo", None),
    # The two garbled LoCoMo verdicts of 2026-09-24 are not repaired: the judge is asked once more instead.
    ("姘斿€欙紵\n\n<answer>no</answer>", None), ("姉\nA. yes\nB. no\n\n<answer>B</answer>", None)])
def test_a_verdict_is_accepted_once_stray_characters_around_it_are_removed(text, expected):
    assert baselines.normalized_verdict(text) is expected


def test_verdict_normalization_takes_linear_time_and_folds_only_a_to_z():
    import time

    started = time.perf_counter()
    assert baselines.normalized_verdict("." * 50_000 + "x") is None
    assert time.perf_counter() - started < 1
    # Unicode case folding would read the long s and the Kelvin sign as s and k.
    assert baselines.normalized_verdict("ye\u017f") is None and baselines.normalized_verdict("YE\u212a") is None


def test_only_the_calls_the_amended_policy_makes_are_scored(tmp_path):
    for name in (*baselines.READER_CALLS, *baselines.JUDGE_CALLS):
        (tmp_path / name).write_text(json.dumps(name))
    whole, cut = {"text": "The answer."}, {"text": "The answer. The", "truncated": True}
    yes, garbled, cut_verdict = {"text": "yes"}, {"text": "maybe"}, {"text": "ye", "truncated": True}
    scored = baselines._scored(tmp_path, [cut, whole], [garbled, yes])
    assert scored == {"correct": True, "outcome": "judged", "reader_sha256": digest(tmp_path / "reader.json"),
                      "judge_sha256": digest(tmp_path / "judge.json"),
                      "reader_retry_sha256": digest(tmp_path / "reader-retry.json"),
                      "judge_retry_sha256": digest(tmp_path / "judge-retry.json"), "verdict_normalized": False}
    assert baselines._scored(tmp_path, [cut, cut], []) == {
        "correct": False, "outcome": "truncated", "reader_sha256": digest(tmp_path / "reader.json"),
        "judge_sha256": None, "reader_retry_sha256": digest(tmp_path / "reader-retry.json"),
        "judge_retry_sha256": None, "verdict_normalized": False}
    unresolved = baselines._scored(tmp_path, [whole], [cut_verdict, {"text": ""}])
    assert (unresolved["outcome"], unresolved["correct"]) == ("verdict_unresolved", False)
    assert baselines._scored(tmp_path, [whole], [{"text": "No!"}])["verdict_normalized"] is True
    # Only a flag that is exactly true marks a call as truncated.
    assert baselines._scored(tmp_path, [{**whole, "truncated": 1}], [yes])["outcome"] == "judged"
    for readers, judges, detail in (
            ([], [], "no reader call"), ([whole, whole], [yes], "a reader retry without"),
            ([cut], [], "a truncated answer that was not asked again"),
            ([cut, cut], [yes], "a judge call after an answer that stayed truncated"),
            ([whole], [], "an answer that was never judged"), ([whole], [yes, yes], "a judge retry after"),
            ([whole], [garbled], "a verdict that was not accepted and not judged again"), ([cut, cut, whole], [yes], "a reader retry"),
            ([whole], [garbled, garbled, yes], "a judge retry after")):
        with pytest.raises(ValueError, match=f"do not follow the amended failure policy: {detail}"):
            baselines._scored(tmp_path, readers, judges)
    # A retry is never recorded without the call it repeats.
    (tmp_path / "reader.json").unlink()
    with pytest.raises(ValueError, match="a retry recorded without the call it repeats"):
        baselines._recorded_calls(tmp_path, baselines.READER_CALLS)


async def test_a_truncated_reader_answer_is_asked_once_more_then_scored_as_truncated(harness, monkeypatch, capsys):
    baselines.prepare("full-context", "locomo", data=harness["data"])
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    requests = ollama_provider(monkeypatch, truncated={"What did Caroline paint?": 1, "What is the puppy called?": 2})
    result = await run_ollama(harness)
    # The first question's answer loops once and then completes; the second loops twice and is never judged.
    assert len(requests) == 3 + 2
    judged = [body["messages"][0]["content"] for _, body, _ in requests
              if body["messages"][0]["content"].startswith("Evaluate the response")]
    # The judge sees the whole answer of the retry, never the looping one.
    assert len(judged) == 1 and "What did Caroline paint?" in judged[0] and LOOP not in judged[0]
    assert result["complete"] and (result["completed"], result["correct"]) == (2, 1)
    first, second = result["rows"]
    assert (first["outcome"], first["correct"], second["outcome"], second["correct"]) == (
        "judged", True, "truncated", False)
    folder = arm_folder(harness)
    assert attempt_calls(folder, "conv-1-q0000") == ["judge.json", "reader-retry.json", "reader.json"]
    assert attempt_calls(folder, "conv-1-q0001") == ["reader-retry.json", "reader.json"]
    assert second["judge_sha256"] is None and second["reader_retry_sha256"] == digest(
        folder / "execution/conv-1-q0001/attempt-1/reader-retry.json")
    assert result["failure_policy"] == {**baselines.failure_amendment(), **NOTHING_UNSCORED, "reader_retries": 2,
                                        "truncated": 1, "unscored": 1, "within_limit": False}
    # Every call counts, the truncated ones included.
    assert result["provider_tokens"]["successful_calls"] == 5 and not result["failures"]
    # The run is complete and published, but one question in two is far past the 1% compare allows.
    [path] = ollama_published(harness)
    assert json.loads(path.read_text())["failure_policy"]["truncated"] == 1
    log = [json.loads(line) for line in (harness["data"] / "runs/full-context-locomo.jsonl").read_text().splitlines()]
    assert log[0]["failure_policy"] == baselines.FAILURE_POLICY
    assert log[1]["unscored"] == {"full-context": 1} and log[1]["invalid"] == (
        "more than 1% of the questions were truncated or left without a verdict (full-context 1 of 2)")
    assert "is complete but invalid" in capsys.readouterr().err
    # The records replay, and a truncated answer that was never asked again is refused.
    questions = study.question_rows("locomo")
    _, prepared, entries = baselines.load_prepared("full-context", "locomo", questions, data=harness["data"])
    assert baselines.report("full-context", "locomo", folder, questions, prepared, entries, None,
                            model=OLLAMA_MODEL)["rows"] == result["rows"]
    for path in (folder / "execution/conv-1-q0001/attempt-1").glob("reader-retry.*"):
        path.unlink()
    with pytest.raises(ValueError, match="a truncated answer that was not asked again"):
        baselines.report("full-context", "locomo", folder, questions, prepared, entries, None, model=OLLAMA_MODEL)


async def test_a_garbled_verdict_is_repaired_or_judged_once_more(harness, monkeypatch):
    one_request_at_a_time(harness)
    baselines.prepare("full-context", "locomo", data=harness["data"])
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    # The first verdict has a stray character in front; the second is no verdict, and the judge's retry is.
    requests = ollama_provider(monkeypatch, verdicts=["姫Yes", "maybe", "No"])
    result = await run_ollama(harness)
    assert len(requests) == 2 + 3
    first, second = result["rows"]
    assert (first["outcome"], first["correct"], first["verdict_normalized"], first["judge_retry_sha256"]) == (
        "judged", True, True, None)
    assert (second["outcome"], second["correct"], second["verdict_normalized"]) == ("judged", False, False)
    assert attempt_calls(arm_folder(harness), "conv-1-q0001") == ["judge-retry.json", "judge.json", "reader.json"]
    assert result["failure_policy"] == {**baselines.failure_amendment(), **NOTHING_UNSCORED, "judge_retries": 1,
                                        "verdicts_normalized": 1}


async def test_a_retry_that_gets_no_answer_asks_the_question_again_on_a_later_run(harness, monkeypatch):
    one_request_at_a_time(harness)
    baselines.prepare("full-context", "locomo", data=harness["data"])
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    # The first answer loops, and the server fails the request that retries it.
    ollama_provider(monkeypatch, truncated={"What did Caroline paint?": 1}, fail_requests={2})
    with pytest.raises(RuntimeError, match="0/2 answered, 0 final failures"):
        await run_ollama(harness)
    folder = arm_folder(harness)
    assert attempt_calls(folder, "conv-1-q0000") == ["reader.json"]
    # No answer came back, so a later run asks the question again in a new attempt, and the first stays on record.
    ollama_provider(monkeypatch)
    result = await run_ollama(harness)
    assert result["complete"] and result["failure_policy"]["reader_retries"] == 0
    [failure] = result["failures"]
    assert (failure["attempt"], failure["retryable"], failure["replaced"]) == ("attempt-1", True, True)


async def test_a_pair_survives_a_looping_answer_and_an_unresolved_verdict_on_either_side(harness, monkeypatch):
    one_request_at_a_time(harness)
    pair_arms(harness)
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    # The variant's first answer loops twice; the defaults' first verdict is garbled twice.
    requests = ollama_provider(monkeypatch, truncated={"VARIANT": 2}, verdicts=["maybe", "maybe", "yes"])
    paired = await run_pair(harness, "prme", "prme-marked")
    # q0: variant reader and its retry; defaults reader, judge and its retry. q1: a reader and a judge per side.
    assert len(requests) == 2 + 3 + 4
    before, after = paired["before"], paired["after"]
    assert before["complete"] and after["complete"]
    published_sides = pair_published(harness, after="prme-marked")
    assert [side["failure_policy"]["unscored"] for side in published_sides] == [1, 1]
    assert [row["outcome"] for row in after["rows"]] == ["truncated", "judged"]
    assert [row["outcome"] for row in before["rows"]] == ["verdict_unresolved", "judged"]
    assert (after["failure_policy"]["truncated"], before["failure_policy"]["verdict_unresolved"]) == (1, 1)
    assert before["answer_model"] == after["answer_model"] == ANSWER_MODEL
    log = pair_log(harness, after="prme-marked")
    assert log[0]["failure_policy"] == baselines.FAILURE_POLICY
    # One question in two is unscored on each side: the pair is complete, invalid and refused by compare.
    finished = log[-1]
    assert finished["complete"] is True and finished["unscored"] == {"before": 1, "after": 1}
    assert finished["invalid"] == ("more than 1% of the questions were truncated or left without a verdict "
                                   "(before 1, after 1 of 2)")
    with pytest.raises(ValueError, match=r"1 of the before result's 2 questions .* the pair is invalid"):
        baselines.compare(before, after)
    # The next pair is a fresh one, and it lists this one as complete but invalid.
    ollama_provider(monkeypatch)
    later = await run_pair(harness, "prme", "prme-marked")
    assert later["pair"]["number"] == 2 and later["pair"]["earlier_pairs"][0]["state"].startswith(
        "complete but invalid: more than 1%")
    assert baselines.compare(later["before"], later["after"], data=harness["data"])["failure_policy"] == {
        "id": baselines.FAILURE_POLICY, "sha256": digest(baselines.FAILURE_AMENDMENT), "before": NOTHING_UNSCORED,
        "after": NOTHING_UNSCORED}


async def test_a_pair_started_under_the_registered_policy_is_given_up_even_after_it_was_moved_aside(
        harness, monkeypatch):
    pair_arms(harness)
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    # Pair 1 was started and stopped before the amendment, so its start names no failure policy, and its folder
    # was moved aside with the other discarded attempts.
    log = harness["data"] / "runs/pairs/prme/prme-marked-locomo.jsonl"
    for event in ({"event": "started", "pair": 1, "sample": None, "server_version": "0.34.3"},
                  {"event": "finished", "pair": 1, "sample": None, "complete": False, "server_version": "0.34.3"}):
        baselines._append_event(log, event)
    paired = await run_pair(harness, "prme", "prme-marked")
    assert paired["pair"]["number"] == 2 and paired["pair"]["earlier_pairs"] == [
        {"baseline": "prme", "number": 1, "state": "abandoned: it was started under another failure policy"}]
    assert json.loads((pair_folder(harness, after="prme-marked", number=2) / "pair.json").read_text())[
        "failure_policy"] == baselines.FAILURE_POLICY
    assert [event["event"] for event in pair_log(harness, after="prme-marked")] == [
        "started", "finished", "abandoned", "started", "finished"]


async def test_answers_given_before_the_amendment_keep_the_registered_rules(harness, monkeypatch):
    baselines.prepare("full-context", "locomo", data=harness["data"])
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    # An arm answered before the amendment: bound without a failure policy, with the registered rows.
    folder = arm_folder(harness)
    registered = {**OLLAMA_MODEL.settings(), "identity": IDENTITY}
    baselines._bind_answer_model(folder, registered)
    questions = study.question_rows("locomo")
    _, prepared, entries = baselines.load_prepared("full-context", "locomo", questions, data=harness["data"])
    async with OLLAMA.client_for(OLLAMA_MODEL) as client:
        await baselines._answer("before #132", "locomo", [baselines._Side(folder, folder, entries)], questions[:1],
                                1, client, partial(OLLAMA.call, model=OLLAMA_MODEL), None)
    replayed = baselines.report("full-context", "locomo", folder, questions, prepared, entries, None,
                                model=OLLAMA_MODEL)
    assert replayed["completed"] == 1 and "failure_policy" not in replayed
    # The reported row adds the context text's hash next to the capture's; the attempt's record does not (#125).
    assert list(replayed["rows"][0]) == ["question_id", "question_type", "cluster", "correct", "context_sha256",
                                         "context_text_sha256", "context_tokens", "retrieval_seconds",
                                         "reader_sha256", "judge_sha256"]
    # Answering the rest under the amendment would mix two policies in one arm.
    with pytest.raises(ValueError, match="answered under another failure policy"):
        await run_ollama(harness)


def amended_result(arm: str, verdicts: list[bool], *, unscored: int = 0, commit: str = "a" * 40) -> dict:
    """A complete Ollama answer result under the amended policy, with its first ``unscored`` questions truncated."""
    result = answer_result(arm, verdicts, commit=commit)
    result["answer_model"] = {**result["answer_model"], "failure_policy": baselines.FAILURE_POLICY}
    result["failure_policy"] = {"id": baselines.FAILURE_POLICY, "sha256": "a" * 64}
    for number, row in enumerate(result["rows"]):
        cut = number < unscored
        row.update(correct=row["correct"] and not cut, outcome="truncated" if cut else "judged",
                   reader_retry_sha256="r" * 64 if cut else None, judge_retry_sha256=None, verdict_normalized=False)
    return result


def test_compare_refuses_mixed_failure_policies_and_a_result_with_more_than_one_percent_unscored():
    # One unscored question in 100 is within the limit; the counts are reported for each side.
    first = amended_result("prme", [True] * 100, unscored=1)
    again = amended_result("prme@0123abcd", [True] * 100, commit="b" * 40)
    comparison = baselines.compare(first, again)
    assert comparison["repeat"] is not None and comparison["failure_policy"] == {
        "id": baselines.FAILURE_POLICY, "sha256": "a" * 64,
        "before": {**NOTHING_UNSCORED, "reader_retries": 1, "truncated": 1, "unscored": 1}, "after": NOTHING_UNSCORED}
    with pytest.raises(ValueError, match=r"2 of the after result's 100 questions were truncated or left without a "
                                         r"verdict, more than the 1%"):
        baselines.compare(first, amended_result("prme@0123abcd", [True] * 100, unscored=2, commit="b" * 40))
    with pytest.raises(ValueError, match=r"different failure policies \(ollama-failure-policy-2026-09-24 and "
                                         r"registered\)"):
        baselines.compare(first, answer_result("prme@0123abcd", [True] * 100, commit="b" * 40))
    # Both sides must record the same amendment, name a known policy and carry the rows it counts.
    with pytest.raises(ValueError, match="same failure policy amendment"):
        baselines.compare(first, {**again, "failure_policy": {"sha256": "b" * 64}})
    with pytest.raises(ValueError, match="Unknown failure policy"):
        baselines.compare(first, {**again, "answer_model": {**again["answer_model"], "failure_policy": "other"}})
    stripped = json.loads(json.dumps(again))
    del stripped["rows"][0]["outcome"]
    with pytest.raises(ValueError, match="rows without their outcome and retries"):
        baselines.compare(first, stripped)
    # Results answered under the registered rules after the amendment came from a checkout without it.
    registered_at = json.loads(RECORDED_AMENDMENT.read_text())["registered_at"]
    earlier = {**answer_result("prme", [True, False]), "started_at": "2026-09-24T05:16:22.351836+00:00"}
    later = {**answer_result("prme@0123abcd", [True, True], commit="b" * 40), "started_at": registered_at}
    with pytest.raises(ValueError, match="after result was answered under the registered failure policy after"):
        baselines.compare(earlier, later)
    # Results from before the amendment still compare with each other, with nothing to count.
    assert baselines.compare(answer_result("prme", [True, False]),
                             answer_result("prme@0123abcd", [True, True], commit="b" * 40))["failure_policy"] is None
    assert [baselines._within_limit(count, total) for count, total in ((5, 500), (6, 500), (15, 1540), (16, 1540))] == [
        True, False, True, False]


async def test_an_empty_or_cut_off_verdict_is_judged_once_more(harness, monkeypatch):
    one_request_at_a_time(harness)
    baselines.prepare("full-context", "locomo", data=harness["data"])
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    # The first question's verdict is empty and then accepted; the second's is cut off and then empty.
    requests = ollama_provider(monkeypatch, verdicts=["", "yes", ("ye", "length"), ""])
    result = await run_ollama(harness)
    assert len(requests) == 3 + 3
    assert [(row["outcome"], row["correct"]) for row in result["rows"]] == [
        ("judged", True), ("verdict_unresolved", False)]
    assert result["failure_policy"]["judge_retries"] == 2 and result["failure_policy"]["verdict_unresolved"] == 1
    folder = arm_folder(harness)
    records = [json.loads((folder / "execution/conv-1-q0001/attempt-1" / name).read_text())
               for name in baselines.JUDGE_CALLS]
    assert [(record["text"], record.get("truncated")) for record in records] == [("ye", True), ("", None)]
    questions = study.question_rows("locomo")
    _, prepared, entries = baselines.load_prepared("full-context", "locomo", questions, data=harness["data"])
    assert baselines.report("full-context", "locomo", folder, questions, prepared, entries, None,
                            model=OLLAMA_MODEL)["rows"] == result["rows"]


async def test_an_arm_is_never_moved_to_another_failure_policy_once_a_question_was_asked(harness, monkeypatch):
    baselines.prepare("full-context", "locomo", data=harness["data"])
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    # Bound under the registered policy, with only a failed attempt: its failure stands under those rules.
    folder = arm_folder(harness)
    baselines._bind_answer_model(folder, {**OLLAMA_MODEL.settings(), "identity": IDENTITY})
    attempt = baselines._next_attempt(folder / "execution" / "conv-1-q0000")
    (attempt / "failure.json").write_text(json.dumps({"retryable": False, "message": "Incomplete response"}))
    requests = ollama_provider(monkeypatch)
    with pytest.raises(ValueError, match="answered under another failure policy"):
        await run_ollama(harness)
    assert requests == []
    # A binding with nothing asked yet follows the amended policy.
    shutil.rmtree(folder / "execution")
    assert (await run_ollama(harness))["answer_model"] == ANSWER_MODEL


def test_a_binding_names_a_known_failure_policy(tmp_path):
    (tmp_path / "answer-model.json").write_text(json.dumps({**ANSWER_MODEL, "failure_policy": "other"}))
    with pytest.raises(ValueError, match="Unknown failure policy 'other'"):
        baselines._bound_policy(tmp_path)
    with pytest.raises(ValueError, match="No answer model is bound"):
        baselines._bound_policy(tmp_path / "missing")
    (tmp_path / "answer-model.json").write_text(json.dumps({**OLLAMA_MODEL.settings(), "identity": IDENTITY}))
    assert baselines._bound_policy(tmp_path) is None
