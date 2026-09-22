"""Fail-closed LongMemEval-S execution for the registered opt-in matrix.

Writes private per-question artifacts. No incomplete matrix is scored, and an
existing execution directory is never reused. Later benchmark families require
their own prepared-input registration and are deliberately not auto-launched.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import time
import traceback

import httpx
import numpy as np
from dotenv import dotenv_values

from benchmarks.diagnostics.register_opt_in_interactions import sha, file_sha, write_new
from benchmarks.diagnostics.longmemeval_s_compact import _clone_pack
from benchmarks.integrations import run_longmemeval_s_baseline as base
from prme import MemoryEngine, PRMEConfig, NodeType, Scope
from prme.retrieval.tokenization import count_tokens


ERRORS: ContextVar[list | None] = ContextVar("opt_in_errors", default=None)
MODEL = "deepseek-v4.1-flash:cloud"
DIGEST = "e04da138d31e0c9468e982e1ae9503d06cb7e170caa16a90c17d931c4aa140f8"
PROVIDER_LIMIT = 4


class ResearchFailure(RuntimeError):
    pass


class FailureObserver(logging.Handler):
    def emit(self, record):
        rows = ERRORS.get()
        if rows is not None and record.name.startswith("prme.") and (
            record.exc_info or record.levelno >= logging.ERROR or
            (record.levelno >= logging.WARNING and any(s in str(record.msg).lower()
             for s in ("failed", "failure", "pending", "unavailable", "fallback")))
        ):
            rows.append({"logger": record.name, "level": record.levelname,
                         "exception_type": record.exc_info[0].__name__ if record.exc_info else None,
                         "message_template": str(record.msg)})


def validate_complete(expected, rows):
    identifiers = [row["question_id"] for row in rows]
    if identifiers != expected or any(row.get("status") != "complete" for row in rows):
        raise ResearchFailure("Incomplete, duplicate, unordered, or failed question coverage")


def package_identity():
    return dict(sorted((dist.metadata["Name"], dist.version)
                       for dist in importlib.metadata.distributions()
                       if dist.metadata["Name"].lower() != "prme"))


def validate_registration(reg, root, cases):
    if reg["registration_sha256"] != sha({k: v for k, v in reg.items() if k != "registration_sha256"}):
        raise ResearchFailure("Registration checksum differs")
    for name, digest in {**reg["source_sha256"], **reg["production_python_sources_sha256"]}.items():
        if file_sha(root / name) != digest:
            raise ResearchFailure(f"Registered source differs: {name}")
    if package_identity() != reg["local_validation_environment"]["packages"]:
        raise ResearchFailure("Pinned execution dependencies differ")
    if [c["question_id"] for c in cases] != reg["longmemeval_s"]["ordered_question_ids"]:
        raise ResearchFailure("Question cohort differs")


async def model_digest(client):
    response = await client.get("/api/tags")
    response.raise_for_status()
    digest = next(m["digest"] for m in response.json()["models"] if m["name"] == MODEL)
    if digest != DIGEST:
        raise ResearchFailure("Provider digest differs")


async def chat(client, semaphore, prompt, limit, path):
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0, "seed": 42, "num_ctx": 65536, "num_predict": limit},
            "think": False, "stream": False}
    record = {"request": body, "request_sha256": sha(body), "attempts": []}
    started = time.perf_counter()
    async with semaphore:
        try:
            await model_digest(client)
            for attempt in range(1, 5):
                stamp = time.perf_counter()
                response = await client.post("/api/chat", json=body)
                row = {"attempt": attempt, "http_status": response.status_code,
                       "elapsed_seconds": time.perf_counter() - stamp,
                       "response_sha256": hashlib.sha256(response.content).hexdigest()}
                record["attempts"].append(row)
                if response.status_code in {429, 500, 502, 503, 504, 529} and attempt < 4:
                    await asyncio.sleep(min(8, .5 * 2 ** (attempt - 1)))
                    continue
                response.raise_for_status()
                value = response.json()
                record["response"] = value
                if (value.get("model") not in {MODEL, MODEL.removesuffix(":cloud")}
                    or value.get("done") is not True or value.get("done_reason") != "stop"
                    or not value.get("message", {}).get("content", "").strip()
                    or type(value.get("prompt_eval_count")) is not int
                    or type(value.get("eval_count")) is not int):
                    raise ResearchFailure("Provider completion invalid or truncated")
                await model_digest(client)
                record.update(status="complete", elapsed_seconds=time.perf_counter()-started)
                write_new(path, record)
                return value["message"]["content"].strip(), record
            raise ResearchFailure("Provider attempts exhausted")
        except Exception as exc:
            record.update(status="failed", exception_type=type(exc).__name__, elapsed_seconds=time.perf_counter()-started)
            write_new(path, record)
            raise


def config_for(arm, pack, key):
    data = deepcopy(arm["config"])
    for name in ("db_path", "vector_path", "lexical_path"):
        data[name] = data[name].replace("{pack}", str(pack))
    data["temporal_relation"]["gate_api_key"] = key
    return PRMEConfig(_env_file=None, **data)


async def ingest(case, config):
    started = time.perf_counter()
    count = empty = 0
    async with MemoryEngine.open(config) as engine:
        for position, (session_id, date, session) in enumerate(zip(case["haystack_session_ids"], case["haystack_dates"], case["haystack_sessions"])):
            for index, turn in enumerate(session):
                if not turn["content"].strip():
                    empty += 1
                    continue
                await engine.store(turn["content"], user_id=base.USER_ID,
                    session_id=f"{position:05d}:{session_id}", role=turn["role"],
                    node_type=NodeType.FACT, scope=Scope.PERSONAL,
                    metadata={"benchmark": "longmemeval-s", "source_session_id": session_id,
                              "source_session_position": position, "source_turn_index": index,
                              "source_role": turn["role"]}, event_time=base._parse_date(date))
                count += 1
        status = await engine.processing_status(user_id=base.USER_ID)
        if status.pending:
            raise ResearchFailure("Fresh materialization backlog")
    return {"kind": "fresh", "seconds": time.perf_counter()-started,
            "stored_turns": count, "empty_turns_omitted": empty}


async def capture(case, arm, folder, master, key):
    pack = folder / "pack"
    config = config_for(arm, pack, key)
    fresh = any(arm["ingestion_flags"].values())
    if fresh:
        pack.mkdir()
        ingestion = await ingest(case, config)
        input_tree = base._tree_identity(pack)
    else:
        source_pack = master / "packs" / case["question_id"]
        saved = json.loads((master / "captures" / f"{case['question_id']}.json").read_text())
        identity = json.loads((master / "identity.json").read_text())
        base._validate_saved_capture(saved, identity={"registration_sha256": identity["registration_sha256"],
                                     "case_sha256": base._case_identity(case)}, pack_path=source_pack)
        input_tree = saved["pack"]
        await asyncio.to_thread(_clone_pack, source_pack, pack)
        if base._tree_identity(pack) != input_tree:
            raise ResearchFailure("Clone differs from immutable source")
        ingestion = {"kind": "reused_historical", "seconds": None,
                     "historical_seconds": saved["timing"]["ingestion_seconds"]}
    started = time.perf_counter()
    engine = await MemoryEngine.create(config)
    open_seconds = time.perf_counter()-started
    try:
        responses = []
        for mode in ("cold", "warm"):
            started = time.perf_counter()
            response = await engine.retrieve(case["question"], user_id=base.USER_ID,
                                              reference_time=base._parse_date(case["question_date"]))
            seconds = time.perf_counter()-started
            receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=base.USER_ID)
            if (not response.metadata.receipt_persisted or receipt is None or
                response.metadata.backend_failures or
                receipt.replay_ranking() != tuple(x.node.id for x in response.results)):
                raise ResearchFailure("Retrieval availability or receipt replay failed")
            temporal = response.metadata.temporal_relation
            if temporal is not None and (temporal.status == "provider_error" or not temporal.confirmation_protocol_aligned):
                raise ResearchFailure("Temporal provider/protocol failure")
            context = response.bundle.render()
            tokens = count_tokens(context, config.packing.tokenizer)
            if tokens != response.bundle.tokens_used or tokens > 3996:
                raise ResearchFailure("Context token accounting failed")
            returned, packed = base._response_sources(response, receipt)
            responses.append({"mode": mode, "seconds": seconds, "context": context,
                "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
                "context_tokens": tokens, "receipt": receipt.model_dump(mode="json"),
                "metadata": response.metadata.model_dump(mode="json"), "returned": returned,
                "packed": packed, "evidence": base._evidence_metrics(case, packed),
                "conflicts": sum(bool(x.conflict_flag) for x in response.results)})
        equal = responses[0]["context"] == responses[1]["context"]
        stochastic = config.enable_query_reformulation or config.temporal_relation.enabled
        if not stochastic and (not equal or [r["node_id"] for r in responses[0]["returned"]] != [r["node_id"] for r in responses[1]["returned"]]):
            raise ResearchFailure("Deterministic cold/warm mismatch")
    finally:
        await engine.close()
    if not fresh and base._tree_identity(source_pack) != input_tree:
        raise ResearchFailure("Immutable master changed")
    result = {"input_pack": input_tree, "final_pack": base._tree_identity(pack),
              "ingestion": ingestion, "open_seconds": open_seconds,
              "cold_warm_context_equal": equal, "retrievals": responses}
    write_new(folder / "capture.json", result)
    return result


def paired_stats(control, candidate, cases):
    delta = np.array([int(b["correct"])-int(a["correct"]) for a,b in zip(control,candidate)])
    rng = np.random.default_rng(20260922)
    boot = np.array([rng.choice(delta, size=len(delta), replace=True).mean() for _ in range(10000)])
    groups = defaultdict(list)
    for index, case in enumerate(cases):
        # Full source histories only: no answers/labels enter clustering.
        history = [[date, [{"role": t["role"], "content": t["content"]} for t in session]]
                   for date,session in zip(case["haystack_dates"],case["haystack_sessions"])]
        groups[sha(history)].append(index)
    clusters = list(groups.values())
    rng = np.random.default_rng(20260922)
    cb = []
    for _ in range(10000):
        indices = np.concatenate([clusters[i] for i in rng.integers(len(clusters),size=len(clusters))])
        cb.append(delta[indices].mean())
    return {"difference": float(delta.mean()), "wins": int(sum(delta>0)), "losses": int(sum(delta<0)),
            "ties": int(sum(delta==0)), "question_ci95": np.quantile(boot,[.025,.975]).tolist(),
            "full_history_cluster_ci95": np.quantile(cb,[.025,.975]).tolist(), "clusters": len(clusters)}


async def execute(args):
    root = Path.cwd()
    reg = json.loads(args.registration.read_text())
    cases = base._load_dataset(args.dataset)
    if file_sha(args.dataset) != base.DATASET_SHA256:
        raise ResearchFailure("Dataset checksum differs")
    validate_registration(reg, root, cases)
    assets = json.loads((root / "benchmarks/results/research/2026-09-22/opt-in-model-assets.json").read_text())
    for asset in assets["assets"]:
        if base._tree_identity(Path(asset["root"])) != asset["tree"]:
            raise ResearchFailure("Pinned model assets differ")
    for name in ("deepseek", "reformulation"):
        value=json.loads((root/f"benchmarks/results/research/2026-09-22/opt-in-interactions-{name}-preflight-result.json").read_text())
        if value["status"]!="passed":
            raise ResearchFailure("Provider preflight failed")
    workflow=json.loads((root/"benchmarks/results/research/2026-09-22/opt-in-explicit-workflow-preflight-result.json").read_text())
    if not workflow["passed"]:
        raise ResearchFailure("Explicit Jev/temporal preflight failed")
    os.environ["HF_HUB_OFFLINE"]="1"
    os.environ["TRANSFORMERS_OFFLINE"]="1"
    base._official_identity(args.official_root)
    judge_prompt = base._load_official_prompt_function(args.official_root)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    key = os.environ.get("JEV_API_KEY") or dotenv_values(args.env_file).get("JEV_API_KEY")
    stop = asyncio.Event()
    memory_sem = asyncio.Semaphore(5)
    provider_sem = asyncio.Semaphore(PROVIDER_LIMIT)
    handler = FailureObserver()
    logging.getLogger("prme").addHandler(handler)
    logging.getLogger("prme").setLevel(logging.DEBUG)
    state = {"kind": "opt-in-interactions-execution", "registration_sha256": reg["registration_sha256"],
             "started_at": datetime.now(timezone.utc).isoformat(), "complete": False, "arms": {}, "failures": []}
    write_new(args.output_dir / "started.json", state)
    selected = [a for a in reg["arms"] if a.get("config") and not a.get("alias_of")]
    # Fixed controls and retrieval-only arms first, then fresh-ingestion arms.
    selected.sort(key=lambda a: (any(a["ingestion_flags"].values()), list(reg["arms"]).index(a)))
    try:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:11434", timeout=180) as client:
            await model_digest(client)
            for arm in selected:
                print(json.dumps({"event":"arm_started", "arm":arm["id"], "time":datetime.now(timezone.utc).isoformat()}),flush=True)
                arm_folder = args.output_dir / arm["id"]
                arm_folder.mkdir()
                async def one(case):
                    async with memory_sem:
                        if stop.is_set():
                            return {"question_id":case["question_id"], "status":"not_started"}
                        folder=arm_folder/case["question_id"]
                        folder.mkdir()
                        write_new(folder/"started.json", {"arm":arm["id"],"question_id":case["question_id"],"config_sha256":arm["config_sha256"]})
                        errors=[]
                        token=ERRORS.set(errors)
                        row={"question_id":case["question_id"],"question_type":case["question_type"],"status":"failed"}
                        try:
                            capture_result=await capture(case,arm,folder,args.master_root,key)
                            if errors:
                                raise ResearchFailure("A product feature failed or fell back")
                            text,reader=await chat(client,provider_sem,base._reader_prompt(capture_result["retrievals"][0]["context"],case["question_date"],case["question"]),1024,folder/"reader.json")
                            prompt=judge_prompt(case["question_type"],case["question"],case["answer"],text,abstention=case["question_id"].endswith("_abs"))
                            verdict,judge=await chat(client,provider_sem,prompt,64,folder/"judge.json")
                            row.update(status="complete",correct="yes" in verdict.lower(),
                                capture_sha256=file_sha(folder/"capture.json"),
                                reader_sha256=file_sha(folder/"reader.json"),judge_sha256=file_sha(folder/"judge.json"))
                        except Exception as exc:
                            stop.set()
                            row.update(exception_type=type(exc).__name__,errors=errors)
                            row["failure_code"]=str(exc) if isinstance(exc,ResearchFailure) else type(exc).__name__
                            row["trace_frames"]=[{"file":Path(frame.filename).name,"line":frame.lineno,"function":frame.name} for frame in traceback.extract_tb(exc.__traceback__)]
                        finally:
                            ERRORS.reset(token)
                        write_new(folder/"result.json",row)
                        print(json.dumps({"event":"case_finished","arm":arm["id"],"question_id":case["question_id"],"status":row["status"]}),flush=True)
                        return row
                rows=await asyncio.gather(*(one(case) for case in cases))
                state["arms"][arm["id"]]={"rows":rows,"complete":all(r["status"]=="complete" for r in rows)}
                write_new(arm_folder/"execution.json",state["arms"][arm["id"]])
                validate_complete(reg["longmemeval_s"]["ordered_question_ids"],rows)
            # No answer-quality summary is emitted before every fixed arm completes.
            state["fixed_matrix_complete"]=True
            state["complete"]=False
            state["next_stage"]="Validate all artifact chains; select and register best-arm combination before confirmation."
    except Exception as exc:
        state["failures"].append({"exception_type":type(exc).__name__})
        state["status"]="failed_closed"
    finally:
        logging.getLogger("prme").removeHandler(handler)
        state["finished_at"]=datetime.now(timezone.utc).isoformat()
        write_new(args.output_dir/"execution.json",state)
    print(json.dumps({"event":"execution_stopped","status":state.get("status","fixed_matrix_complete"),"complete":state["complete"]}),flush=True)
    return state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration",type=Path,required=True)
    parser.add_argument("--dataset",type=Path,required=True)
    parser.add_argument("--master-root",type=Path,required=True)
    parser.add_argument("--official-root",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--env-file",type=Path,required=True)
    args=parser.parse_args()
    result=asyncio.run(execute(args))
    if result["failures"]:
        raise SystemExit(1)


if __name__=="__main__":
    main()
