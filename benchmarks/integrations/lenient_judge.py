"""A lenient second judge for LoCoMo answers the strict judge has already scored (#96).

The registered LoCoMo judge is strict: "When the reference lists required items,
all are required." Vendor LoCoMo scores are graded by Mem0-style judges, which
tell the grader to be generous and to accept an answer that touches the same
topic as the reference; Hindsight's benchmark harness uses a variant of the same
prompt. The strict score stays the primary one. This module adds a secondary
score beside it: the saved answers of one complete LoCoMo answer run are sent to
Mem0's published LoCoMo judge prompt (``LENIENT_JUDGE``, copied verbatim from
``LENIENT_JUDGE_SOURCE``).

No reader is called, so both scores grade the same answers, and the judge is the
model, with the settings, that gave the run's strict verdicts: GPT-5.4 for the
registered 2026-09-23 run, and the run's own Ollama model for a result on the
DeepSeek track. That is not Mem0's judge: Mem0 sent the prompt to gpt-4o-mini in
JSON mode, which the request bodies here cannot ask for, so the label is read
from the text (``lenient_verdict``). The strict verdicts are the published ones,
given in an earlier session, and a hosted judge does not repeat every verdict, so
the difference between the two scores is the prompt's effect plus that judge's
run-to-run variation.

``estimate`` scales the run's recorded strict judge usage to the lenient prompt
and makes no calls. ``run`` on the GPT-5.4 source makes paid judge calls, so start
it only after the owner approves the spend: the CLI asks for confirmation at an
interactive terminal, and ``--max-usd`` is the pass's cap, which its ledger
enforces. On an Ollama source it makes no paid calls, and the model identity must
be the one recorded with the strict verdicts, before and after the pass.

The calls are asked and kept as the answer runs keep theirs
(``gpt54_baselines.drain_pending``): every call and attempt is kept, a later run
asks again only the questions whose verdict never arrived, and a finished pass is
never rerun, which the pass's run log outside its folder remembers. Both tracks
follow ``FAILURE_POLICY``: a label that cannot be read is judged once more, and
the question is then scored incorrect as ``verdict_unresolved``, so one odd reply
cannot strand a paid pass. The published result reports both scores, each
category under its upstream LoCoMo number, and how many verdicts the two judges
disagree on.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from functools import partial
import hashlib
import json
import math
from pathlib import Path
import sys

from benchmarks.diagnostics import product_packing as gate
from benchmarks.integrations import gpt54_baselines as baselines
from benchmarks.integrations import gpt54_budget, ollama_answers
from benchmarks.integrations import run_gpt54_comparison as study
from benchmarks.integrations.gpt54_budget import JUDGE_LIMIT, MODEL, digest, usage_cost
from prme.retrieval.tokenization import count_tokens

BENCHMARK = "locomo"
# Mem0's LoCoMo judge (ACCURACY_PROMPT), character for character, trailing spaces and curly quotes included. The
# lines are spelled out so an editor that trims trailing whitespace cannot change it. LENIENT_JUDGE_SHA256 is the
# hash of ACCURACY_PROMPT in the upstream file at LENIENT_JUDGE_SOURCE, and run checks the copy against it.
LENIENT_JUDGE = (
    "\n"
    "Your task is to label an answer to a question as ’CORRECT’ or ’WRONG’. You will be given the following data:\n"
    "    (1) a question (posed by one user to another user), \n"
    "    (2) a ’gold’ (ground truth) answer, \n"
    "    (3) a generated answer\n"
    "which you will score as CORRECT/WRONG.\n"
    "\n"
    "The point of the question is to ask about something one user should know about the other user based on their "
    "prior conversations.\n"
    "The gold answer will usually be a concise and short answer that includes the referenced topic, for example:\n"
    "Question: Do you remember what I got the last time I went to Hawaii?\n"
    "Gold answer: A shell necklace\n"
    "The generated answer might be much longer, but you should be generous with your grading - as long as it touches "
    "on the same topic as the gold answer, it should be counted as CORRECT. \n"
    "\n"
    "For time related questions, the gold answer will be a specific date, month, year, etc. The generated answer "
    "might be much longer or use relative time references (like \"last Tuesday\" or \"next month\"), but you should "
    "be generous with your grading - as long as it refers to the same date or time period as the gold answer, it "
    "should be counted as CORRECT. Even if the format differs (e.g., \"May 7th\" vs \"7 May\"), consider it CORRECT "
    "if it's the same date.\n"
    "\n"
    "Now it's time for the real question:\n"
    "Question: {question}\n"
    "Gold answer: {gold_answer}\n"
    "Generated answer: {generated_answer}\n"
    "\n"
    "First, provide a short (one sentence) explanation of your reasoning, then finish with CORRECT or WRONG. \n"
    "Do NOT include both CORRECT and WRONG in your response, or it will break the evaluation script.\n"
    "\n"
    "Just return the label CORRECT or WRONG in a json format with the key as \"label\".\n"
)
LENIENT_JUDGE_SHA256 = "62395dd312a631dfd9355026a0b69cc936018274c3198b6365b5c2a5c9bca9e0"
LENIENT_JUDGE_SOURCE = ("https://github.com/mem0ai/mem0/blob/aae5989e78a6188b3b047c104d960c9ad0927e75/"
                        "evaluation/metrics/llm_judge.py")
VERDICT_RULE = ("The label of every JSON object in the reply that has a label key, which must all agree and read "
                "CORRECT or WRONG in any case. Mem0 reads the label of the whole reply or its fenced code block in JSON "
                "mode; without JSON mode, judges also write the one-sentence explanation the prompt asks for.")
FAILURE_POLICY = "lenient-judge-policy-2026-09-25"
RETRY_POLICY = (
    "The lenient judge's policy on both tracks: at most four identical HTTP attempts for transient 429/5xx. A reply "
    "whose label cannot be read is sent once more as the same request, and if that label cannot be read either, the "
    "question is scored incorrect and recorded as verdict_unresolved. On Ollama a reply that ends early or is empty "
    "counts as one whose label cannot be read; on GPT-5.4 the registered client never records one, so it is final. A "
    "question whose reader answer stayed truncated has no answer to grade and is scored incorrect as truncated, as "
    "the strict judge scored it. Any other invalid or ambiguous response is final, and the first failure stops new "
    "questions. A later run asks a question again, in a new attempt, only when no verdict was received: a budget "
    "stop, a provider HTTP error, an ambiguous transport failure or an interrupted process. More than 1% of "
    "questions unscored puts the result outside the limit (within_limit). Every attempt is kept."
)
# The registered run's own name in its records, outside the arms' names.
REGISTERED_RUN = "gpt54-comparison-v1"
REGISTERED_RESULT = "gpt54-locomo-v1-result.json"
REGISTERED_VERIFICATION = "gpt54-comparison-v1-verification.json"
# The explanation sentence the prompt asks for, which the strict judge's recorded replies did not contain.
EXPLANATION_TOKENS = 48
_LABELS = {"CORRECT": True, "WRONG": False}
_DECODER = json.JSONDecoder()


def lenient_prompt(question: dict, answer: str) -> str:
    """The lenient judge's prompt for one saved answer, with the question and reference as the strict judge saw them."""
    return LENIENT_JUDGE.format(question=question["question"], gold_answer=question["answer"], generated_answer=answer)


def lenient_verdict(text: str) -> bool | None:
    """CORRECT or WRONG from the JSON label the prompt asks for; None when the reply does not give exactly one."""
    labels = set()
    start = text.find("{")
    while start != -1:
        try:
            value, _ = _DECODER.raw_decode(text, start)
        except ValueError:
            value = None
        if isinstance(value, dict) and "label" in value:
            label = value["label"]
            labels.add(label.strip().upper() if isinstance(label, str) else None)
        start = text.find("{", start + 1)
    return _LABELS.get(labels.pop()) if len(labels) == 1 else None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# Saved answers ----------------------------------------------------------------

@dataclass(frozen=True)
class Source:
    """A complete LoCoMo answer run: its published result, the judge that scored it, and each saved answer.

    ``answers`` maps each question id to the reader text the strict judge graded
    (None when the reader answer stayed truncated, which no judge saw), the
    strict verdict and outcome, the reader records' hashes and the strict judge
    call.
    """

    name: str
    path: Path
    published: str
    sha256: str
    result: dict
    model: ollama_answers.AnswerModel | None
    registration: dict
    questions: tuple[dict, ...]
    answers: dict[str, dict]

    @property
    def paid(self) -> bool:
        """Whether its judge is GPT-5.4, whose calls are paid, rather than an Ollama model."""
        return self.model is None


def registered_result() -> Path:
    """The published result of the registered 2026-09-23 LoCoMo run."""
    return study.PUBLIC / REGISTERED_RESULT


def load_source(path: Path, *, archive: Path | None = None, data: Path | None = None) -> Source:
    """A complete LoCoMo result and its saved answers, after checking each answer against the published row.

    The result must be published in this checkout. The registered 2026-09-23
    run must be the verified one, with its answers in ``archive`` (the saved
    run's archive). A DeepSeek arm's result finds its answers in the attempt
    that recorded each row, under ``data`` (the track's data folder). Samples
    and pair sides are refused: grade an arm's own complete run.
    """
    path = Path(path)
    published = _published(path)
    result = json.loads(path.read_text())
    registration, questions = baselines.registered_protocol(BENCHMARK)
    rows = result.get("rows") or []
    if result.get("benchmark") != BENCHMARK:
        raise ValueError(f"{path} is not a LoCoMo result; the lenient judge grades LoCoMo answers only")
    if "pair" in result:
        raise ValueError(f"{path} is one side of a pair; grade an arm's own complete run instead")
    if not result.get("complete") or [row["question_id"] for row in rows] != [q["question_id"] for q in questions]:
        raise ValueError(f"{path} is not a complete result over the registered LoCoMo questions")
    kind = result.get("kind")
    if kind is None:
        verification = json.loads((study.PUBLIC / REGISTERED_VERIFICATION).read_text())
        if (not verification.get("complete") or verification.get("registration_sha256") != digest(study.REG)
                or verification["benchmarks"][BENCHMARK].get("result_sha256") != digest(path)):
            raise ValueError(f"{path} is not the verified result of the registered 2026-09-23 LoCoMo run")
        name, model = REGISTERED_RUN, None
        execution = (archive or gate.default_archive()) / BENCHMARK / "execution"
        records = {row["question_id"]: execution / row["question_id"] for row in rows}
    elif kind == "ollama-answer-result":
        if archive is not None:
            raise ValueError("--archive names the registered run's saved answers; a DeepSeek result has its own")
        name, model = result["arm"], ollama_answers.AnswerModel()
        # The arm names the folders its answers are read from, so it must be an arm's name.
        baselines._check_arm(name, BENCHMARK)
        if result["answer_model"]["model"] != model.model:
            raise ValueError(f"{path} was answered by {result['answer_model']['model']}, not {model.model}")
        execution = (data or baselines.data_root(model)) / name / BENCHMARK / "execution"
        records = {row["question_id"]: _recorded_attempt(execution / row["question_id"], row) for row in rows}
    else:
        raise ValueError(f"{path} is a {kind}; the lenient judge grades the registered GPT-5.4 run or a complete "
                         "DeepSeek answer run")
    answers = {row["question_id"]: _saved_answer(records[row["question_id"]], row) for row in rows}
    return Source(name=name, path=path, published=published, sha256=digest(path), result=result, model=model,
                  registration=registration, questions=tuple(questions), answers=answers)


def _published(path: Path) -> str:
    """Where a source result is published, relative to the research results folder of this checkout."""
    resolved = path.resolve()
    for root in (baselines.RESULTS.resolve(), study.PUBLIC.parent.resolve()):
        if resolved.is_relative_to(root):
            return str(resolved.relative_to(root))
    raise ValueError(f"{path} is not published under this checkout's {baselines.RESULTS}; grade a published result")


def _recorded_attempt(folder: Path, row: dict) -> Path:
    """The attempt whose recorded result is the published row."""
    answered, _ = baselines.recorded_attempts(folder)
    if answered is None or json.loads((answered / "result.json").read_text()).get("reader_sha256") != (
            row["reader_sha256"]):
        raise ValueError(f"No saved attempt in {folder} recorded the published answer to {row['question_id']}")
    return answered


def _saved_answer(record: Path, row: dict) -> dict:
    """The answer the strict judge graded, checked against the reader and judge records' hashes in the published row.

    Under the DeepSeek track's amended policy a truncated reader answer was asked
    once more, and the strict judge graded the retry, if it ended normally.
    """
    retried = row.get("reader_retry_sha256") is not None
    reader = record / ("reader-retry.json" if retried else "reader.json")
    if digest(reader) != row["reader_retry_sha256" if retried else "reader_sha256"]:
        raise ValueError(f"The saved answer to {row['question_id']} ({reader}) differs from the published result")
    judge = record / baselines.JUDGE_CALLS[0]
    if row.get("judge_sha256") is not None and digest(judge) != row["judge_sha256"]:
        raise ValueError(f"The strict judge call for {row['question_id']} ({judge}) differs from the published result")
    truncated = row.get("outcome") == "truncated"
    return {"text": None if truncated else json.loads(reader.read_text())["text"], "strict": row["correct"],
            "strict_outcome": row.get("outcome", "judged"), "reader_sha256": row["reader_sha256"],
            "reader_retry_sha256": row.get("reader_retry_sha256"),
            "strict_judge": judge if row.get("judge_sha256") is not None else None}


def judge_folder(source: Source, data: Path | None = None) -> Path:
    """Where the lenient judge's calls for this source are kept: apart from every answer run and pair."""
    return (data or baselines.data_root(source.model)) / "lenient-judge" / source.name / BENCHMARK


def _pass_name(source: Source) -> str:
    """The pass's name in its run log and ledger, which live outside its folder, and in the spending confirmation."""
    return f"lenient-judge-{source.name}"


# Estimate -----------------------------------------------------------------------

def estimate(source: Source) -> dict:
    """Scale each question's recorded strict judge usage to the lenient prompt. GPT-5.4 source only; no calls."""
    if not source.paid:
        raise ValueError(f"{source.path} was judged through Ollama, which has no API cost to estimate")
    total = recorded = largest = 0
    for question in source.questions:
        answer = source.answers[question["question_id"]]
        judged = json.loads(answer["strict_judge"].read_text())["response"]
        usage = judged["usage"]
        strict = study.judge_prompt(BENCHMARK, question, answer["text"])
        lenient = lenient_prompt(question, answer["text"])
        largest = max(largest, len(lenient.encode()))
        # Tokenizer counts stand in for the provider's for the difference between the two prompts.
        inputs = usage["input_tokens"] - count_tokens(strict) + count_tokens(lenient)
        recorded += usage_cost(judged)
        total += usage_cost({"service_tier": "flex", "usage": {
            "input_tokens": inputs, "output_tokens": usage["output_tokens"] + EXPLANATION_TOKENS,
            "input_tokens_details": {"cached_tokens": 0}}})
    # A question's first judge call and its retry are sent one after the other, so each worker reserves one at a time.
    headroom = source.registration["provider_concurrency"] * baselines._reservation(largest, JUDGE_LIMIT)
    return {
        "source": source.name, "benchmark": BENCHMARK, "questions": len(source.questions),
        "recorded_strict_judge_usd": recorded / 1e9, "estimated_usd": total / 1e9,
        "reservation_headroom_usd": headroom / 1e9, "minimum_cap_usd": (total + headroom) / 1e9,
        "basis": (f"Flex prices, with every input token priced as uncached. Each question keeps its recorded strict "
                  f"judge usage, with the input changed by the difference between the two prompts in tokenizer "
                  f"tokens and {EXPLANATION_TOKENS} more output tokens for the explanation sentence the lenient "
                  "prompt asks for. A label that cannot be read is asked once more, which this leaves out. "
                  "minimum_cap_usd adds the ledger's in-flight reservations. A planning estimate, not a quote."),
    }


# Run ------------------------------------------------------------------------------

async def run(source: Source, *, max_usd: float | None = None, data: Path | None = None,
              results: Path | None = None, api_key: str | None = None) -> dict:
    """Grade every saved answer that has no lenient verdict yet, then report the whole pass beside the strict score.

    The GPT-5.4 source needs the owner-approved ``max_usd``, which the pass's
    ledger enforces across runs. An Ollama source makes no paid calls, takes no
    cap or key, and must be judged by the model identity that gave its strict
    verdicts, before and after the pass; a change publishes nothing.
    """
    if _sha256(LENIENT_JUDGE) != LENIENT_JUDGE_SHA256:
        raise ValueError("LENIENT_JUDGE differs from Mem0's published prompt")
    if source.paid:
        if not (max_usd is not None and math.isfinite(max_usd) and max_usd > 0):
            raise ValueError("Pass the owner-approved spending cap as --max-usd")
        api_key = api_key or baselines._api_key()
    elif max_usd is not None or api_key is not None:
        raise ValueError("An Ollama source is graded without paid calls; it takes no spending cap or API key")
    data = data or baselines.data_root(source.model)
    results = results or baselines.RESULTS
    folder = judge_folder(source, data)
    folder.mkdir(parents=True, exist_ok=True)
    name = _pass_name(source)
    label = f"the lenient judge of {source.name}"
    private = folder / "result.json"
    log = baselines._run_log_path(data, name, BENCHMARK)
    started = study.utc()
    with baselines._run_lock(folder, label):
        if baselines._complete_result(private) or any(baselines._complete_run(event)
                                                      for event in baselines._run_events(log)):
            raise ValueError(f"{label} is already complete; a finished pass is never rerun, even after its folder is "
                             f"moved ({log})")
        judge_model = _judge_model(source)
        _bind(folder, source, judge_model)
        baselines._append_event(log, {"event": "started", "server_version": baselines._version_of(judge_model),
                                      "source_sha256": source.sha256})
        if source.paid:
            ledger = baselines._ledger(baselines._ledger_path(data, name, BENCHMARK), max_usd)
            ask = partial(gpt54_budget.call, ledger=ledger)
            opened = baselines.client_for(api_key)
        else:
            ledger = None
            ask = partial(ollama_answers.call, model=source.model)
            opened = ollama_answers.client_for(source.model)
        async with opened as client:
            async def grade(question: dict, attempt: Path, semaphore: asyncio.Semaphore) -> dict:
                answer = source.answers[question["question_id"]]
                return _row(question, answer, await _grade(ask, client, semaphore, question, answer, attempt,
                                                           paid=source.paid))

            await baselines.drain_pending(label, [(folder / "execution" / question["question_id"], question)
                                                  for question in source.questions],
                                          source.registration["provider_concurrency"], grade, ledger,
                                          done="graded", every=100)
        extra: dict = {"max_usd": max_usd} if source.paid else {}
        end_version = None
        if not source.paid:
            ended = ollama_answers.describe(source.model)
            end_version = baselines._version_of(ended)
            if not ollama_answers.same_model(judge_model, ended):
                baselines._append_event(log, {"event": "model-changed", "server_version": end_version})
                raise RuntimeError(f"The Ollama model identity changed during {label}; nothing is reported. Its "
                                   "verdicts stay in its folder, which refuses another model.")
            # Every server version the verdicts were given under, over every run of the pass.
            extra["server_versions"] = baselines._server_versions(judge_model, baselines._run_events(log), end_version)
        result = report(source, folder, ledger)
        result.update(started_at=started, finished_at=study.utc(), retry_policy=RETRY_POLICY,
                      provenance=baselines._provenance(), modules=_module_identity(),
                      answer_model={**judge_model, "failure_policy": FAILURE_POLICY}, **extra)
        baselines._write_json(private, result)
        invalid = baselines._invalid_reason({source.name: result}, result["total"]) if result["complete"] else None
        baselines._append_event(log, {"event": "finished", "complete": result["complete"],
                                      "completed": result["completed"], "total": result["total"],
                                      "unscored": result["failure_policy"]["unscored"], "server_version": end_version,
                                      **({"invalid": invalid} if invalid else {})})
        result["run_log"] = _run_history(log)
        if not result["complete"]:
            next_step = ("It can never complete: move its folder aside to start the pass again, which its run log "
                         "records" if result["final_failures"] else "Run it again to grade the rest")
            raise RuntimeError(f"{label} is incomplete: {result['completed']}/{result['total']} graded, "
                               f"{result['final_failures']} final failures. {next_step}; no partial score is "
                               "reported.")
        if invalid:
            print(f"{label} is complete but outside the limit: {invalid}. It is published with within_limit false.",
                  file=sys.stderr, flush=True)
        published = source.path.name.removesuffix("-result.json") + "-lenient-judge-result.json"
        baselines._publish(results / started[:10] / published, result)
    return result


def _judge_model(source: Source) -> dict:
    """The judge's settings: GPT-5.4's, or the Ollama model's, which must be the one that gave the strict verdicts."""
    if source.paid:
        return baselines.OPENAI_ANSWER_MODEL
    recorded = {key: value for key, value in source.result["answer_model"].items() if key != "failure_policy"}
    current = ollama_answers.describe(source.model)
    if not ollama_answers.same_model(recorded, current):
        raise ValueError(f"The Ollama model or its settings differ from the ones that gave {source.path}'s strict "
                         "verdicts, so the two scores would not grade with the same judge")
    return current


def _bind(folder: Path, source: Source, judge_model: dict) -> None:
    """The pass's source, prompt, verdict rule, policy and judge. Verdicts under any other are never mixed in."""
    binding = {"source_sha256": source.sha256, "prompt_sha256": LENIENT_JUDGE_SHA256,
               "verdict_rule_sha256": _sha256(VERDICT_RULE), "failure_policy": FAILURE_POLICY, "judge": judge_model}
    path = folder / "source.json"
    if not path.exists():
        baselines._write_json(path, binding)
        return
    bound = json.loads(path.read_text())
    differing = [key for key in binding if key != "judge" and bound.get(key) != binding[key]]
    if not ollama_answers.same_model(bound.get("judge") or {}, judge_model):
        differing.append("judge")
    if differing:
        raise ValueError(f"{folder} holds lenient verdicts under another {', '.join(differing)}; they are never "
                         "mixed. Move the folder aside to grade under the new one, which the pass's run log records.")


async def _grade(ask, client, semaphore, question: dict, answer: dict, attempt: Path, *, paid: bool) -> dict:
    """One saved answer's lenient verdict: a judge call, and one more when its label cannot be read."""
    if answer["text"] is None:
        return _scored(attempt, [], truncated=True)
    prompt = lenient_prompt(question, answer["text"])
    # The registered GPT-5.4 client never returns a reply that ended early; Ollama's records one when asked to.
    options = {} if paid else {"truncated_ok": True, "empty_ok": True}
    judges: list[dict] = []
    for name in baselines.JUDGE_CALLS:
        judges.append(await ask(client, semaphore, prompt=prompt, limit=JUDGE_LIMIT, path=attempt / name, **options))
        if _verdict(judges[-1]) is not None:
            break
    return _scored(attempt, judges, truncated=False)


def _verdict(judge: dict) -> bool | None:
    """A judge call's lenient verdict, or None when the reply ended early or its label cannot be read."""
    return None if judge.get("truncated") is True else lenient_verdict(judge["text"])


def _scored(attempt: Path, judges: list[dict], *, truncated: bool) -> dict:
    """A question's lenient scoring from its judge calls in the order they were made; refuses calls no policy makes."""
    if truncated:
        if judges:
            raise ValueError("The recorded lenient judge calls judged an answer that stayed truncated")
        return {"correct": False, "outcome": "truncated", "judge_sha256": None, "judge_retry_sha256": None}
    if not judges:
        raise ValueError("The recorded lenient verdict has no judge call for an answer that was not truncated")
    if len(judges) > len(baselines.JUDGE_CALLS):
        raise ValueError("The recorded lenient judge calls are more than the policy makes")
    verdicts = [_verdict(judge) for judge in judges]
    if any(verdict is not None for verdict in verdicts[:-1]):
        raise ValueError("The recorded lenient judge calls include a retry after an accepted label")
    if verdicts[-1] is None and len(judges) < len(baselines.JUDGE_CALLS):
        raise ValueError("The recorded lenient judge calls stop at a label that was not judged again")
    return {"correct": bool(verdicts[-1]), "outcome": "judged" if verdicts[-1] is not None else "verdict_unresolved",
            "judge_sha256": digest(attempt / baselines.JUDGE_CALLS[0]),
            "judge_retry_sha256": digest(attempt / baselines.JUDGE_CALLS[1]) if len(judges) > 1 else None}


def _row(question: dict, answer: dict, scored: dict) -> dict:
    """A question's lenient result: its upstream category, both verdicts and the records behind them."""
    return {"question_id": question["question_id"], "category": question["category"],
            "question_type": question["question_type"], "cluster": question["conversation_id"],
            "strict_correct": answer["strict"], "strict_outcome": answer["strict_outcome"], "correct": scored["correct"],
            "reader_sha256": answer["reader_sha256"], "reader_retry_sha256": answer["reader_retry_sha256"],
            **{key: value for key, value in scored.items() if key != "correct"}}


def _run_history(log: Path) -> dict:
    """What the pass's run log holds, so a result shows every earlier run, including those of a folder moved aside."""
    events = baselines._run_events(log)
    return {"sha256": digest(log), "runs_started": sum(event["event"] == "started" for event in events),
            "model_changes": sum(event["event"] == "model-changed" for event in events),
            "sources": sorted({event["source_sha256"] for event in events if event["event"] == "started"})}


# Report ---------------------------------------------------------------------------

def report(source: Source, folder: Path, ledger: gpt54_budget.Ledger | None) -> dict:
    """Authenticate every recorded verdict and summarize the pass beside the strict score.

    Each judge call is checked against the prompt the saved answer makes and the
    judge's settings, and each row is scored again from its calls, so the
    receipts replay.
    """
    from benchmarks.integrations.analyze_gpt54_comparison import verify_call

    if source.paid:
        verify, tally = verify_call, baselines._tally_gpt54
    else:
        verify = partial(ollama_answers.verify_call, model=source.model, truncated_ok=True, empty_ok=True)
        tally = baselines._tally_ollama
    rows, failures = [], []
    usage = baselines.new_usage(paid=source.paid)
    for question in source.questions:
        qid = question["question_id"]
        graded, found = baselines.recorded_attempts(folder / "execution" / qid)
        failures += found
        if graded is None:
            continue
        answer = source.answers[qid]
        paths = baselines._recorded_calls(graded, baselines.JUDGE_CALLS)
        calls = [verify(path, lenient_prompt(question, answer["text"]), JUDGE_LIMIT) for path in paths]
        row = _row(question, answer, _scored(graded, calls, truncated=answer["text"] is None))
        if json.loads((graded / "result.json").read_text()) != row:
            raise ValueError(f"The recorded lenient verdict for {qid} does not match its saved answer or calls")
        baselines.count_calls(usage, calls, tally)
        rows.append(row)
    total = len(source.questions)
    unscored = {outcome: sum(row["outcome"] == outcome for row in rows) for outcome in baselines.UNSCORED}
    source_model = source.result.get("answer_model") or {}
    result = {
        "kind": "lenient-judge-result", "benchmark": BENCHMARK, "model": MODEL if source.paid else source.model.model,
        "registration_sha256": digest(study.REG),
        "source": {"name": source.name, "path": source.published, "sha256": source.sha256,
                   "kind": source.result.get("kind") or "gpt54-registered-result",
                   # The policy and Ollama server versions the strict verdicts were given under.
                   "failure_policy": source_model.get("failure_policy"),
                   "server_versions": source.result.get("server_versions")
                   or [(source_model.get("identity") or {}).get("server_version")]},
        "judges": {
            "strict": {"role": "primary", "prompt": "benchmarks/integrations/run_gpt54_comparison.py (LOCO_JUDGE)",
                       "prompt_sha256": _sha256(study.LOCO_JUDGE)},
            "lenient": {"role": "secondary", "prompt": "Mem0 LoCoMo judge (ACCURACY_PROMPT)",
                        "source": LENIENT_JUDGE_SOURCE, "prompt_sha256": LENIENT_JUDGE_SHA256,
                        "verdict_rule": VERDICT_RULE},
            "note": "Both judges grade the same saved answers with the same judge model and settings. The strict "
                    "verdicts are the published ones, given in an earlier session, so the difference is the prompt's "
                    "effect plus the judge's run-to-run variation. Mem0 itself judged with gpt-4o-mini in JSON mode."},
        "calibration": {"strict_judge": source.result.get("calibration") or "the registered authored calibration",
                        "lenient_prompt": None,
                        "note": "The lenient prompt has no authored calibration of its own; the judge_retries and "
                                "verdict_unresolved counts show how often its label could not be read."},
        **baselines.coverage(rows, failures, total),
        "failure_policy": {"id": FAILURE_POLICY,
                           "judge_retries": sum(row["judge_retry_sha256"] is not None for row in rows), **unscored,
                           "unscored": sum(unscored.values()), "unscored_limit_percent": baselines.UNSCORED_PERCENT,
                           "within_limit": baselines._within_limit(sum(unscored.values()), total)},
        "cost": baselines.provider_cost(ledger, usage, "pass"),
        "provider_tokens": {**usage, "http_status_counts": dict(usage["http_status_counts"]),
                            "note": "Successful lenient judge calls of graded questions."},
        "rows": rows,
    }
    if result["complete"]:
        result.update(_scores(rows))
    return result


def _scores(rows: list[dict]) -> dict:
    """Both scores with their intervals, each category under its upstream LoCoMo number, and where the judges differ."""
    def tally(chosen: list[dict]) -> dict:
        strict = sum(row["strict_correct"] for row in chosen)
        lenient = sum(row["correct"] for row in chosen)
        return {"total": len(chosen), "strict_correct": strict, "strict_accuracy": strict / len(chosen),
                "lenient_correct": lenient, "lenient_accuracy": lenient / len(chosen),
                "accepted_only_by_lenient": sum(row["correct"] and not row["strict_correct"] for row in chosen),
                "accepted_only_by_strict": sum(row["strict_correct"] and not row["correct"] for row in chosen)}

    strict_rows = [{"correct": row["strict_correct"], "cluster": row["cluster"]} for row in rows]
    return {"scores": {**tally(rows),
                       "strict_ci95_questions": study.confidence(strict_rows),
                       "strict_ci95_source_clusters": study.confidence(strict_rows, True),
                       "lenient_ci95_questions": study.confidence(rows),
                       "lenient_ci95_source_clusters": study.confidence(rows, True)},
            "categories": [{"category": number, "name": name, **tally(chosen)}
                           for number, name in sorted(study.CATEGORIES.items())
                           if (chosen := [row for row in rows if row["category"] == number])]}


def _module_identity() -> dict:
    """The code that loads the answers and sends, checks and scores the lenient judge's calls."""
    return {path: digest(study.ROOT / path) for path in (
        "benchmarks/integrations/lenient_judge.py", "benchmarks/integrations/gpt54_baselines.py",
        "benchmarks/integrations/analyze_gpt54_comparison.py", "benchmarks/diagnostics/product_packing.py",
        *baselines.ANSWER_MODULES)}


# CLI ------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.integrations.lenient_judge", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["estimate", "run"])
    parser.add_argument("--result", type=Path,
                        help="A complete LoCoMo answer run's published result (default: the registered 2026-09-23 "
                             "GPT-5.4 run). Its saved answers are graded by the judge that scored it.")
    parser.add_argument("--archive", type=Path,
                        help="The registered run only: its saved archive (default: the main checkout's "
                             "data/gpt54-comparison-v1)")
    parser.add_argument("--max-usd", type=float,
                        help="run on the GPT-5.4 source only: the owner-approved spending cap for the pass")
    args = parser.parse_args(argv)
    path = args.result or registered_result()
    try:
        source = load_source(path, archive=args.archive)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(f"cannot grade {path}: {exc}")
    if args.command == "estimate":
        if args.max_usd is not None or not source.paid:
            parser.error("estimate takes no --max-usd and applies to the GPT-5.4 source, whose calls are paid")
        print(json.dumps(estimate(source), indent=2))
        return
    if source.paid:
        if args.max_usd is None or not (math.isfinite(args.max_usd) and args.max_usd > 0):
            parser.error("the GPT-5.4 source makes paid calls: pass the owner-approved cap as a positive --max-usd")
        try:
            baselines._api_key()
        except ValueError as exc:
            parser.error(str(exc))
        baselines._confirm_spend(parser, _pass_name(source), BENCHMARK, args.max_usd, kind="pass")
    elif args.max_usd is not None:
        parser.error("an Ollama source is graded without paid calls; --max-usd applies to the GPT-5.4 source only")
    else:
        baselines._announce(source.model, "Judge")
    result = asyncio.run(run(source, max_usd=args.max_usd))
    print(json.dumps(baselines._summary(result), indent=2))


if __name__ == "__main__":
    main()
