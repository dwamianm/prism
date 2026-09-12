"""Sequential MemConflict replay with explicit input and evaluation boundaries.

Consumes a local dataset; does not download or redistribute upstream dialogues.
Answers and retrieval traces are diagnostics, not official judged accuracy.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import time
import urllib.request

from benchmarks.evidence import SourceTurn, pack_sources
from benchmarks.llm_judge import GENERATION_SYSTEM_PROMPT

UPSTREAM_REVISION = "ec51d5d36e87f7665d1337f3a88cbde95fc2a964"
REFERENCE_DATASET_SHA256 = "8ef9ec8589eccb86f63ab3a819a9180217405351a8d5846866721ea74babe092"
SPLIT_SEED = "prme-memconflict-v1"
# This profile was inspected while designing the adapter; never call it held out.
EXPOSED_DEVELOPMENT_IDS = frozenset({"3c2e5fe5-a0fc-7e3c-b05c-7104ad748705"})
CONFLICT_TYPES = ("dynamic_conflict", "static_conflict", "conditional_conflict")


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    answer: str
    category: str


@dataclass(frozen=True)
class Session:
    date: str
    sources: tuple[SourceTurn, ...]
    questions: tuple[Question, ...]
    skipped_messages: int


@dataclass(frozen=True)
class Profile:
    id: str
    sessions: tuple[Session, ...]


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Expected nonempty {label}")
    return value


def parse_profile(raw: dict, *, invalid_messages: str = "error") -> Profile:
    """Allowlist dialogue/date inputs; retain gold only in evaluator questions.

    Reject structural ambiguities. Explicit 'skip' matches upstream omission of
    unusable messages and counts every omission, rather than repairing labels.
    """
    if invalid_messages not in {"error", "skip"}:
        raise ValueError("invalid_messages must be error or skip")
    pid = nonempty(raw["ID"], "profile ID")
    sessions, seen_sessions = [], set()
    previous_date = date.min
    chain = raw["Full_Session_Chain"]
    if not isinstance(chain, list) or not chain:
        raise ValueError("Expected nonempty session chain")
    for position, raw_session in enumerate(chain):
        sid = raw_session["Session_ID"]
        if type(sid) is not int or sid < 0 or sid in seen_sessions:
            raise ValueError("Session IDs must be unique nonnegative integers")
        seen_sessions.add(sid)
        stamp = date.fromisoformat(raw_session["Date"])
        if stamp < previous_date:
            raise ValueError("Session chain must be chronological")
        previous_date = stamp
        dialogue = raw_session["Session_Dialogue"]
        if not isinstance(dialogue, dict):
            raise ValueError("Expected dialogue turn mapping")
        turns = []
        for key, messages in dialogue.items():
            match = re.fullmatch(r"dialogue_turn_(\d+)", key)
            if not match or not isinstance(messages, list):
                raise ValueError("Invalid dialogue turn")
            turns.append((int(match[1]), messages))
        if len({number for number, _ in turns}) != len(turns):
            raise ValueError("Ambiguous dialogue turn order")
        sources, skipped, message_position = [], 0, 0
        for _, messages in sorted(turns):
            for message in messages:
                source_id = f"s{position}:t{message_position}"
                message_position += 1
                if (not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}
                        or not isinstance(message.get("content"), str) or not message["content"].strip()):
                    if invalid_messages == "error":
                        raise ValueError(f"Unusable dialogue message at session {position}, message {message_position - 1}")
                    skipped += 1
                    continue
                sources.append(SourceTurn(
                    source_id, f"session-{position}", message["role"], message["content"], stamp.isoformat(),
                ))
        questions, seen_questions = [], set()
        for item in raw_session["Session_Questions"]:
            qid = nonempty(item["question_id"], "question ID")
            if qid in seen_questions:
                raise ValueError("Duplicate question ID within session")
            seen_questions.add(qid)
            if item["conflict_type"] not in CONFLICT_TYPES:
                raise ValueError("Unknown conflict type")
            questions.append(Question(
                json.dumps([pid, sid, qid], separators=(",", ":")),
                nonempty(item["question"], "question"), nonempty(item["answer"], "answer"), item["conflict_type"],
            ))
        sessions.append(Session(stamp.isoformat(), tuple(sources), tuple(questions), skipped))
    return Profile(pid, tuple(sessions))


def load_profiles(path: Path, *, invalid_messages="error") -> tuple[list[Profile], str]:
    data = path.read_bytes()
    profiles = [parse_profile(json.loads(line), invalid_messages=invalid_messages)
                for line in data.splitlines() if line.strip()]
    if not profiles or len({p.id for p in profiles}) != len(profiles):
        raise ValueError("Dataset requires unique profiles")
    return profiles, hashlib.sha256(data).hexdigest()


def profile_split(pid: str) -> str:
    digest = hashlib.sha256(f"{SPLIT_SEED}:{pid}".encode()).hexdigest()
    return "dev" if pid in EXPOSED_DEVELOPMENT_IDS or int(digest, 16) % 5 == 0 else "test"


def select_questions(profile: Profile, limit: int) -> set[str]:
    """Round-robin conflict strata, chronological within each, never by answers."""
    if limit < 0:
        raise ValueError("Question limit must be nonnegative")
    groups = [[q for s in profile.sessions for q in s.questions if q.category == category]
              for category in CONFLICT_TYPES]
    ordered = [group[i].id for i in range(max(map(len, groups), default=0))
               for group in groups if i < len(group)]
    return set(ordered[:limit] if limit else ordered)


def audit(profiles: list[Profile], checksum: str) -> dict:
    counts = Counter(q.category for p in profiles for s in p.sessions for q in s.questions)
    return {
        "reference_release_revision": UPSTREAM_REVISION,
        "reference_dataset_sha256": REFERENCE_DATASET_SHA256,
        "matches_reference_dataset": checksum == REFERENCE_DATASET_SHA256,
        "dataset_sha256": checksum, "profiles": len(profiles),
        "sessions": sum(len(p.sessions) for p in profiles),
        "usable_messages": sum(len(s.sources) for p in profiles for s in p.sessions),
        "skipped_messages": sum(s.skipped_messages for p in profiles for s in p.sessions),
        "questions": sum(counts.values()), "conflict_types": dict(counts),
        "split_seed": SPLIT_SEED,
        "profile_splits": {split: [p.id for p in profiles if profile_split(p.id) == split]
                           for split in ("dev", "test")},
        "exposed_development_ids": sorted(EXPOSED_DEVELOPMENT_IDS),
        "limits": "Structural audit only; does not establish that answer labels are supported by dialogues.",
    }


async def replay(profile, engine, reader, *, question_limit, token_budget, count_tokens):
    """Insert each prefix before its questions; gold never reaches either service."""
    chosen = select_questions(profile, question_limit)
    history, details = {}, []
    inserted, skipped = 0, 0
    start = time.perf_counter()
    for session in profile.sessions:
        if len(details) == len(chosen):
            break
        skipped += session.skipped_messages
        for source in session.sources:
            await engine.store(
                source.content, user_id="memconflict", session_id=source.session_id,
                role=source.role, event_time=datetime.fromisoformat(source.date).replace(tzinfo=timezone.utc),
                metadata={"source_turn": source.id}, ttl_days=None,
            )
            history[source.id] = source
            inserted += 1
        for question in session.questions:
            if question.id not in chosen:
                continue
            stamp = datetime.fromisoformat(session.date).replace(tzinfo=timezone.utc)
            response = await engine.retrieve(
                question.text, user_id="memconflict", reference_time=stamp, token_budget=token_budget,
            )
            ranked = [c.node.metadata["source_turn"] for c in response.results]
            if any(source_id not in history for source_id in ranked):
                raise ValueError("Retrieved source is outside the ingested prefix")
            hits = await engine._lexical_index.search(question.text, "memconflict", limit=100)
            # Baseline uses the same persisted raw notes; this is a component API.
            nodes = await engine.query_nodes(user_id="memconflict", limit=inserted + 1)
            sources_by_node = {str(n.id): n.metadata["source_turn"] for n in nodes}
            if len(sources_by_node) != inserted:
                raise ValueError("Raw source materialization is incomplete")
            bm25_ids = list(dict.fromkeys(sources_by_node[h["node_id"]] for h in hits))
            bm25_context, packed_ids, tokens = pack_sources(
                [history[sid] for sid in bm25_ids], token_budget=token_budget, count_tokens=count_tokens,
            )
            methods = {
                "prme_product": {"context": response.bundle.render(), "ranked_source_ids": ranked,
                                 "tokens": response.bundle.tokens_used},
                "bm25": {"context": bm25_context, "ranked_source_ids": bm25_ids,
                         "packed_source_ids": packed_ids, "tokens": tokens},
                "none": {"context": "", "ranked_source_ids": [], "tokens": 0},
            }
            for result in methods.values():
                result["answer"] = await reader(question.text, session.date, result["context"])
            details.append({
                "question_id": question.id, "question": question.text, "gold_answer": question.answer,
                "category": question.category, "reference_date": session.date,
                "source_count": inserted, "skipped_messages": skipped, "methods": methods,
            })
    if len(details) != len(chosen):
        raise ValueError("Not every selected question was evaluated")
    return {"profile_id": profile.id, "details": details, "elapsed_seconds": time.perf_counter() - start}


def local_reader(base_url, model, timeout):
    def generate(question, reference_date, context):
        payload = {
            "model": model, "stream": False, "think": False,
            "options": {"temperature": 0, "seed": 42, "num_ctx": 16384, "num_predict": 512},
            "messages": [
                {"role": "system", "content": GENERATION_SYSTEM_PROMPT},
                {"role": "user", "content": f"Reference date: {reference_date}\nMEMORY:\n{context}\n\nQUESTION:\n{question}"},
            ],
        }
        request = urllib.request.Request(base_url.rstrip("/") + "/api/chat",
                                         data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
        if not result.get("done") or result.get("done_reason") == "length":
            raise ValueError("Incomplete reader response")
        return nonempty(result["message"]["content"], "reader answer")

    async def read(question, reference_date, context):
        return await asyncio.to_thread(generate, question, reference_date, context)
    return read


async def run(args):
    from prme import MemoryEngine, PRMEConfig
    from benchmarks.retrieval_eval import provenance
    import tiktoken

    profiles, checksum = load_profiles(args.dataset, invalid_messages=args.invalid_messages)
    report = audit(profiles, checksum)
    if args.audit_only:
        return report
    selected = [p for p in profiles if profile_split(p.id) == args.split]
    selected.sort(key=lambda p: (p.id not in EXPOSED_DEVELOPMENT_IDS, p.id))
    selected = selected[:args.profiles] if args.profiles else selected
    if not selected:
        raise ValueError("No profiles selected")
    tokenizer = tiktoken.get_encoding("cl100k_base")
    config = PRMEConfig(
        database_url=None, encryption_enabled=False,
        embedding={"provider": "fastembed", "model_name": "BAAI/bge-small-en-v1.5", "dimension": 384, "api_key": None},
        organizer={"opportunistic_enabled": False},
        enable_store_supersedence=False, enable_surprise_gating=False, enable_reranker=False,
        enable_qa_pairing=False, reinforce_similarity_threshold=None,
    )
    report.update({
        "complete": False, "kind": "memconflict_raw_dialogue_diagnostic", "split": args.split,
        "selected_profile_ids": [p.id for p in selected], "question_limit_per_profile": args.questions,
        "invalid_message_policy": args.invalid_messages, "token_budget": args.token_budget,
        "baseline_tokenizer": "cl100k_base",
        "reader": {"model": args.model, "system_prompt": GENERATION_SYSTEM_PROMPT,
                   "temperature": 0, "seed": 42, "num_ctx": 16384, "num_predict": 512},
        "source": provenance(config), "results": [], "judge_status": "not_run", "accuracy": None,
        "limits": "Raw NOTE memory with all usable user and assistant messages; QA pairing, reinforcement, supersedence and maintenance disabled. PRME uses its product formatter; BM25 uses a whole-turn evaluator packer. This confounds ranking and packing in comparisons. No extraction, official scoring, human label audit, or competitive accuracy claim.",
    })
    with urllib.request.urlopen(args.base_url.rstrip("/") + "/api/tags", timeout=args.timeout) as response:
        models = json.load(response)["models"]
    matches = [model for model in models if args.model in (model.get("name"), model.get("model"))]
    if len(matches) != 1 or not matches[0].get("digest"):
        raise ValueError("Reader model must have a unique local digest")
    report["reader"]["digest"] = matches[0]["digest"]
    reader = local_reader(args.base_url, args.model, args.timeout)
    for profile in selected:
        with tempfile.TemporaryDirectory(prefix="prme-memconflict-") as directory:
            root = Path(directory)
            local = config.model_copy(update={"db_path": str(root / "memory.duckdb"),
                                              "vector_path": str(root / "vectors.usearch"),
                                              "lexical_path": str(root / "lexical")})
            async with MemoryEngine.open(local) as engine:
                result = await replay(profile, engine, reader, question_limit=args.questions,
                                      token_budget=args.token_budget,
                                      count_tokens=lambda text: len(tokenizer.encode(text, disallowed_special=())))
            report["results"].append(result)
    report["complete"] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--invalid-messages", choices=("error", "skip"), default="error")
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--profiles", type=int, default=1, help="0 means all selected profiles")
    parser.add_argument("--questions", type=int, default=3, help="Per profile, round-robin strata; 0 means all")
    parser.add_argument("--token-budget", type=int, default=2048)
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--process-timeout", type=float, default=3600)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.profiles < 0 or args.questions < 0 or args.token_budget < 1 or args.timeout <= 0 or args.process_timeout <= 0:
        parser.error("Limits must be nonnegative; budget and timeout must be positive")
    if args.worker or args.audit_only:
        report = asyncio.run(run(args))
        if args.worker:
            report["passed"] = report["complete"]
    else:
        from benchmarks.diagnostics._process import checked_report
        report = checked_report(
            [sys.executable, "-m", "benchmarks.integrations.memconflict", *sys.argv[1:], "--worker"],
            timeout=args.process_timeout,
        )
        report["complete"] = report["passed"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("profiles", "questions", "skipped_messages", "complete") if k in report}))
    if report.get("passed") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
