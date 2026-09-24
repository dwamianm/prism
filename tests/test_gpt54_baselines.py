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


@pytest.fixture(autouse=True)
def private_data(tmp_path, monkeypatch):
    """No test reads or writes the main checkout's private contexts, answers or run logs."""
    monkeypatch.setattr(baselines, "DATA", tmp_path / "gpt54-baselines")
    monkeypatch.setattr(baselines, "OLLAMA_DATA", tmp_path / "ollama-answers")


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


def fabricate_plain(data: Path, benchmark: str, contexts: dict[str, str], arm: str = "plain-rrf") -> None:
    """Prepared contexts of an arm (plain-rrf by default) in the layout prepare writes, without replaying packs."""
    folder = data / arm / benchmark
    entries = []
    for qid, context in contexts.items():
        path = folder / "contexts" / benchmark / f"{qid}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"question_id": qid, "context": context}))
        entries.append({"question_id": qid, "path": str(path.relative_to(folder)), "sha256": digest(path),
                        "text_sha256": hashlib.sha256(context.encode()).hexdigest(),
                        "context_tokens": count_tokens(context), "retrieval_seconds": .1})
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
TWO_LME = [LONGMEM[0], {**LONGMEM[0], "question_id": "q2", "question": "Where do I live?", "answer": "Porto"}]


def ollama_provider(monkeypatch, *, verdict: str = "yes", fail: str | None = None, identities=None,
                    wrong_when: str | None = None):
    """Replace the Ollama server; any attempt to build or use the paid OpenAI path fails the test.

    ``identities`` is the sequence of identities the server reports, one per lookup; the last one repeats. A
    reader prompt containing ``wrong_when`` gets the authored wrong answer, which the judge rejects.
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
        text = ("no" if baselines.AUTHORED_WRONG_ANSWER in prompt else verdict) if judge else (
            baselines.AUTHORED_WRONG_ANSWER if wrong_when is not None and wrong_when in prompt else "The answer.")
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


def run_pair(harness, before="prme", after="prme", benchmark="locomo", **kwargs):
    return baselines.run_pair(before, after, benchmark, model=OLLAMA_MODEL, data=harness["data"],
                              results=harness["results"], **kwargs)


def pair_published(harness, before="prme", after="prme", benchmark="locomo", number=1, suffix=""):
    """Both sides' published results of one pair, before side first; empty when nothing was published."""
    found = [list(harness["results"].glob(f"*/{OLLAMA_MODEL.track}-{before}-vs-{after}-{benchmark}-pair-{number}-"
                                          f"{side}{suffix}-result.json")) for side in baselines.PAIR_SIDES]
    return [json.loads(path.read_text()) for paths in found for path in paths]


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
async def test_prme_arms_prepare_the_defaults_or_a_named_variant_through_the_gate(harness, monkeypatch):
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
    requests = ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    requests.clear()
    # A variant is never answered on its own: only alongside a fresh run of the defaults.
    with pytest.raises(ValueError, match="use run-pair"):
        await run_ollama(harness, "prme-small")
    with pytest.raises(ValueError, match="use run-pair"):
        await run_ollama(harness, "prme-small", sample=1)
    assert requests == []
    # A baseline is recorded by its own answer run before any pair uses it.
    with pytest.raises(ValueError, match="no complete answer run of its own"):
        await run_pair(harness, "prme", "prme-small")
    await run_ollama(harness, "prme")
    requests.clear()
    paired = await run_pair(harness, "prme", "prme-small")
    before, after = paired["before"], paired["after"]
    # The first request is the variant's reader, on the variant's own context.
    assert before["complete"] and after["complete"] and requests[0][1]["messages"][0]["content"] == \
        study.reader_prompt("locomo", study.question_rows("locomo")[0],
                            baselines._context(arm_folder(harness, "prme-small"), variant["contexts"][0]))
    assert pair_published(harness, "prme", "prme-small")
    # Whether this test's own tree is committed does not matter here.
    before, after = ({**result, "prepared": {**result["prepared"], "dirty": False}} for result in (before, after))
    comparison = baselines.compare(before, after)
    assert comparison["arms"] == {"before": "prme", "after": "prme-small"} and comparison["warnings"] == []
    assert comparison["pair"]["number"] == 1 and comparison["repeat"] is None
    moved = {**after, "prepared": {**after["prepared"], "commit": "0" * 40, "dirty": True}}
    assert len(baselines.compare(before, moved)["warnings"]) == 2
    assert comparison["accuracy"]["delta"] == 0 and comparison["gained"] == comparison["lost"] == []
    assert comparison["prepared"]["after"]["overrides"] == budget
    with pytest.raises(ValueError, match="different readers and judges"):
        baselines.compare(before, {**after, "answer_model": {**after["answer_model"], "seed": 1}})
    with pytest.raises(ValueError, match="different questions"):
        baselines.compare(before, {**after, "rows": after["rows"][::-1]})


@pytest.mark.usefixtures("mock_embeddings")
async def test_a_later_defaults_baseline_is_filed_under_its_commit_and_leaves_the_first_alone(harness, monkeypatch):
    await gate_cases(harness, monkeypatch)
    data = harness["data"]

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
    later = await prepare(arm)
    assert later["arm"] == arm and later["provenance"]["commit"] == head
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
    # These test cases name no saved context, so nothing shows the two baselines read the same text, and two
    # baselines answered on their own are compared only as a repeat.
    with pytest.raises(ValueError, match="alongside it in one pair"):
        baselines.compare(before, after)
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
    ollama_provider(monkeypatch, verdict="maybe")  # A malformed verdict is final: the arm can never finish.
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


def answered(data: Path, benchmark: str, commit: str | None, arm: str = "prme") -> None:
    """A complete answer run in the arm's run log; ``commit`` None writes an older finished event, without it."""
    baselines._log_run(data, arm, benchmark, {"event": "finished", "sample": None, "complete": True, "completed": 1,
                                              "total": 1, **({} if commit is None else {"prepared_commit": commit})})


def test_the_defaults_at_another_commit_are_a_new_baseline_once_prme_is_answered(tmp_path):
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


def test_cli_prepares_and_runs_the_baseline_of_the_checked_out_commit(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(baselines, "_on_main", lambda: True)
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
                  budget: int = 3996, identity: dict | None = None, modules: dict | None = None) -> dict:
    """A complete Ollama answer result with one row per verdict, as compare() reads it."""
    rows = [{"question_id": f"q{number}", "question_type": "single-hop", "cluster": f"conv-{number % 2}",
             "correct": verdict, "reader_sha256": f"{arm}-{number}"} for number, verdict in enumerate(verdicts)]
    return {"kind": "ollama-answer-result", "arm": arm, "benchmark": "locomo", "model": OLLAMA_MODEL.model,
            "registration_sha256": "r" * 64, "complete": True, "context_budget": budget,
            "answer_model": {**OLLAMA_MODEL.settings(), "identity": IDENTITY if identity is None else identity},
            "modules": {path: "m" * 64 for path in baselines.ANSWER_MODULES} if modules is None else modules,
            "rows": rows, "prepared": {"commit": commit, "dirty": False, "overrides": {},
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
                  answer_result("prme@0123abcd", [False, False], commit="b" * 40, budget=2048),
                  answer_result("prme@0123abcd", [False, False], commit="b" * 40, modules=moved_code),
                  answer_result("prme@0123abcd", [False, False], commit="b" * 40, modules={})):
        with pytest.raises(ValueError, match="alongside it in one pair"):
            baselines.compare(first, after)
    # A variant answered on its own is never paired with the defaults (#129).
    with pytest.raises(ValueError, match="alongside it in one pair"):
        baselines.compare(first, answer_result("prme-rrf", [False, False]))


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


@pytest.mark.parametrize("arm", ["prme", REPEAT])
@pytest.mark.parametrize("benchmark", ["locomo", "longmemeval"])
def test_the_published_deepseek_baseline_is_complete_and_matches_the_current_answer_settings(arm, benchmark):
    result = published_deepseek(arm, benchmark)
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
    # compare() pairs a variant with this baseline only when both were answered with the same settings, so a
    # change to the answer settings needs a new baseline.
    assert {key: value for key, value in result["answer_model"].items() if key != "identity"} == \
        OLLAMA_MODEL.settings()


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


# Interleaved pairs (#129) ---------------------------------------------------------

DEFAULTS_TEXT = {"conv-1-q0000": "(8 May, 2023) Caroline: I painted a sunset.",
                 "conv-1-q0001": "(20 May, 2023) Melanie: I adopted a puppy named Oscar."}


def recorded_baseline(harness, benchmark: str, contexts: dict[str, str]) -> None:
    """A prepared defaults baseline whose own answer run is complete, as a pair's before side needs."""
    fabricate_plain(harness["data"], benchmark, contexts, arm="prme")
    answered(harness["data"], benchmark, "a" * 40)


def pair_arms(harness, *, marked: bool = True) -> None:
    """A recorded defaults baseline and a variant whose contexts carry a marker, for the two LoCoMo questions."""
    recorded_baseline(harness, "locomo", DEFAULTS_TEXT)
    if marked:
        fabricate_plain(harness["data"], "locomo", {qid: f"{text} VARIANT" for qid, text in DEFAULTS_TEXT.items()},
                        arm="prme-marked")


def one_request_at_a_time(harness) -> None:
    """Register a concurrency of one, so the order of requests is the order of the queue."""
    registration = {**json.loads(harness["reg"].read_text()), "provider_concurrency": 1}
    harness["reg"].write_text(json.dumps(registration))
    amendment = study.PUBLIC / "gpt54-official-loader-amendment.json"
    amendment.write_text(json.dumps({**json.loads(amendment.read_text()), "registration_sha256": digest(harness["reg"])}))


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
    assert before["answer_model"] == after["answer_model"] == {**OLLAMA_MODEL.settings(), "identity": IDENTITY}
    assert before["run_log"] == {"sha256": digest(harness["data"] / "runs/pairs/prme/prme-marked-locomo.jsonl"),
                                 "runs_started": 1, "arm": {"sha256": digest(harness["data"] / "runs/prme-locomo.jsonl"),
                                                            "runs_started": 0, "prepared_again": 0}}
    # The variant was never answered on its own, so it has no run log of its own.
    assert after["run_log"]["arm"] == {"sha256": None, "runs_started": 0, "prepared_again": 0}
    log = pair_log(harness, after="prme-marked")
    assert [(event["event"], event["pair"], event["server_version"]) for event in log] == [
        ("started", 1, "0.34.3"), ("finished", 1, "0.34.3")]
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
    comparison = baselines.compare(before, after)
    assert comparison["pair"] == mark and comparison["repeat"] is None
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
        baselines.compare({**alone, "arm": "prme"}, after)


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
    assert baselines.compare(second["before"], second["after"])["pair"]["number"] == 2
    with pytest.raises(ValueError, match="different pairs"):
        baselines.compare(first["before"], second["after"])
    assert [(event["event"], event["pair"]) for event in pair_log(harness, after="prme-marked")] == [
        ("started", 1), ("finished", 1), ("started", 1), ("finished", 1), ("started", 2), ("finished", 2)]


async def test_a_pair_that_can_never_finish_stays_on_record_and_the_next_one_starts(harness, monkeypatch):
    pair_arms(harness)
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    ollama_provider(monkeypatch, verdict="maybe")  # A malformed verdict is final: the pair can never finish.
    with pytest.raises(RuntimeError, match="final failures"):
        await run_pair(harness, "prme", "prme-marked")
    ollama_provider(monkeypatch)
    paired = await run_pair(harness, "prme", "prme-marked")
    assert paired["pair"]["number"] == 2 and paired["pair"]["pairs_started"] == 2
    assert paired["pair"]["earlier_pairs"] == [{"number": 1, "state": "abandoned: a question failed finally"}]
    # The first question's variant side got the final failure, which stopped the queue.
    assert json.loads((pair_folder(harness, after="prme-marked") / "after/result.json").read_text())[
        "final_failures"] == 1
    # A pair folder moved aside keeps its number, which the run log holds.
    shutil.rmtree(pair_folder(harness, after="prme-marked", number=2))
    assert (await run_pair(harness, "prme", "prme-marked"))["pair"]["number"] == 3
    # An unfinished pair without its record is given up the same way.
    ollama_provider(monkeypatch, fail="Melanie: I adopted")
    with pytest.raises(RuntimeError, match="pair 4 is incomplete"):
        await run_pair(harness, "prme", "prme-marked")
    (pair_folder(harness, after="prme-marked", number=4) / "pair.json").unlink()
    ollama_provider(monkeypatch)
    assert (await run_pair(harness, "prme", "prme-marked"))["pair"]["earlier_pairs"][-1] == {
        "number": 4, "state": "abandoned: its pair.json record is missing"}


async def test_an_unfinished_pair_is_never_finished_on_other_contexts_or_under_another_model(harness, monkeypatch):
    pair_arms(harness)
    ollama_provider(monkeypatch)
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
    ollama_provider(monkeypatch, fail="Melanie: I adopted")
    with pytest.raises(RuntimeError, match="incomplete"):
        await run_pair(harness, "prme", "prme-marked")
    # The variant is prepared again: the unfinished pair stays as it is, and the next pair reads the new contexts.
    shutil.rmtree(arm_folder(harness, "prme-marked"))
    fabricate_plain(harness["data"], "locomo", {qid: f"{text} CHANGED" for qid, text in DEFAULTS_TEXT.items()},
                    arm="prme-marked")
    ollama_provider(monkeypatch, fail="Melanie: I adopted")
    with pytest.raises(RuntimeError, match="pair 2 is incomplete"):
        await run_pair(harness, "prme", "prme-marked")
    assert not (pair_folder(harness, after="prme-marked") / "after/result.json").read_text().count("CHANGED")
    # A model pulled again with a new manifest never finishes a pair the old one started.
    changed = {**IDENTITY, "manifest_digest_sha256": "f" * 64}
    requests = ollama_provider(monkeypatch, identities=[changed])
    await baselines.calibrate(OLLAMA_MODEL, data=harness["data"])
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
    requests = ollama_provider(monkeypatch, identities=[upgraded])
    paired = await run_pair(harness, "prme", "prme-marked")
    assert paired["pair"]["number"] == 2 and len(requests) == 8
    assert paired["pair"]["earlier_pairs"] == [
        {"number": 1, "state": "abandoned: it was answered under another Ollama server version"}]
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
    after["pair"] = {"id": "p", "number": 1, "before": "prme", "after": "prme", "sha256": "s", "side": "after"}
    before["pair"] = {**after["pair"], "side": "before"}
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
    for argv in (["run-pair", "prme", "--benchmark", "locomo", "--variant", "rrf", "--baseline", "prme@46647825"],
                 ["run-pair", "prme", "--benchmark", "longmemeval", "--baseline", "prme", "--sample", "2"],
                 ["run-pair", "full-context", "--baseline", "prme"]):
        baselines.main([*argv, "--provider", "ollama"])
    assert seen == [("prme@46647825", "prme-rrf", "locomo", {"model": OLLAMA_MODEL, "sample": None}),
                    ("prme", "prme", "longmemeval", {"model": OLLAMA_MODEL, "sample": 2}),
                    ("prme", "full-context", "locomo", {"model": OLLAMA_MODEL, "sample": None})]
    printed = capsys.readouterr().out.splitlines()
    assert json.loads(printed[0]) == {"pair": {"number": 1}, "before": {"complete": True},
                                      "after": {"complete": True}}


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
    ollama_provider(monkeypatch, verdict="maybe")  # A malformed verdict is final: the arm can never finish.
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
    assert comparison["gained"] == ["q1"]


def test_a_locomo_difference_excludes_zero_only_when_both_its_intervals_do():
    # Every conversation gains on one of its four questions: both intervals exclude zero.
    before = answer_result("prme", [False] * 40)
    after = answer_result("prme", [number % 4 == 0 for number in range(40)])
    for result in (before, after):
        for number, row in enumerate(result["rows"]):
            row["cluster"] = f"conv-{number // 4}"
    after["pair"] = {"id": "p", "number": 1, "before": "prme", "after": "prme", "sha256": "s", "side": "after"}
    before["pair"] = {**after["pair"], "side": "before"}
    accuracy = baselines.compare(before, after)["accuracy"]
    assert accuracy["interval_95_conversations"][0] > 0 and accuracy["interval_95_questions"][0] > 0
    assert accuracy["interval_95"] == [min(accuracy["interval_95_conversations"][0], accuracy["interval_95_questions"][0]),
                                       max(accuracy["interval_95_conversations"][1], accuracy["interval_95_questions"][1])]


async def test_run_pair_answers_on_the_ollama_track_only_and_from_a_baseline(harness):
    with pytest.raises(ValueError, match="Ollama track only"):
        await baselines.run_pair("prme", "prme", "locomo", model=None, data=harness["data"])
    with pytest.raises(ValueError, match="baseline of the defaults"):
        await run_pair(harness, "plain-rrf", "prme")


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
