"""Collect the read-time signals of every LoCoMo and LongMemEval-S question for the unknown status study (task P1).

One retrieve() per question, under one memory mode, on a clone of the question's pack that is checked against the
recorded pack identity. No model call is made: run it with providers blocked.

    OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
    ANTHROPIC_BASE_URL=http://127.0.0.1:9 PRME_EXTRACTION_API_KEY= \
    uv run python research/unknown-status-2026-11/collect.py --benchmark locomo --mode classic --out <file.jsonl>

Each output line is one question: its id, set (unknown or answerable), the signals, and nothing else. The output is
appended to and resumes after the last finished question.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import tantivy

REPO = Path(__file__).resolve().parents[2]
MAIN = Path("/Users/dwamianm/Sites/prism")
sys.path.insert(0, str(REPO))

from benchmarks.diagnostics import product_packing as pp  # noqa: E402
from benchmarks.diagnostics.longmemeval_s_compact import _clone_pack  # noqa: E402
from prme.retrieval.candidates import SEED_STOPWORDS  # noqa: E402
from prme.retrieval.relations import _STOPWORDS as ROUTE_STOPWORDS  # noqa: E402
from prme.storage.engine import MemoryEngine  # noqa: E402

LOCOMO_PACKS = MAIN / "data/mid-tier-v1/builds/main-0.17.0a6-locomo"
LME_CAPTURES = MAIN / "data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline"
LOCOMO = MAIN / "data/benchmarks/locomo/locomo10.json"
TOP = 50

# The lexical index's own analyzer (en_stem), so a content word is a term the index can hold.
ANALYZER = (tantivy.TextAnalyzerBuilder(tantivy.Tokenizer.simple()).filter(tantivy.Filter.remove_long(40))
            .filter(tantivy.Filter.lowercase()).filter(tantivy.Filter.stemmer("english")).build())
PLAIN = tantivy.TextAnalyzerBuilder(tantivy.Tokenizer.simple()).filter(tantivy.Filter.lowercase()).build()
EXTRA_STOP = frozenset("i me my mine we us our ours am do".split())
STOP = frozenset(SEED_STOPWORDS) | ROUTE_STOPWORDS | EXTRA_STOP | frozenset(
    tantivy.TextAnalyzerBuilder(tantivy.Tokenizer.simple()).filter(tantivy.Filter.lowercase())
    .filter(tantivy.Filter.stopword("english")).build().analyze(
        " ".join(["a an and are as at be but by for if in into is it no not of on or such that the their then there "
                  "these they this to was will with"])))


def content_terms(text: str) -> list[str]:
    """Stemmed content words of a question, in order, without repeats."""
    words = PLAIN.analyze(text)
    stems = ANALYZER.analyze(text)
    if len(words) != len(stems):  # remove_long dropped a token; keep the stems alone
        words = stems
    out: dict[str, None] = {}
    for word, term in zip(words, stems, strict=True):
        if word in STOP or len(word) < 3 or word.isdigit():
            continue
        out.setdefault(term, None)
    return list(out)


def locomo_cases() -> list[dict]:
    """LoCoMo questions over the ingest packs: categories 1 to 4 with evidence, and category 5 without an answer."""
    data = json.loads(LOCOMO.read_text())
    built = {case.question_id: case for case in pp._built_cases(LOCOMO_PACKS, "locomo")}
    any_case = {case.question_id.split("-q")[0]: case for case in built.values()}
    speakers = {sample["sample_id"]: [sample["conversation"]["speaker_a"], sample["conversation"]["speaker_b"]]
                for sample in data}
    rows = []
    for sample in data:
        sid = sample["sample_id"]
        base = any_case[sid]
        for n, question in enumerate(sample["qa"]):
            qid = f"{sid}-q{n:04d}"
            if question["category"] == 5:
                if str(question.get("answer", "")).strip():
                    continue  # answerable from memory (Yes or No about a stored fact): in neither set
                label, category = "unknown", "adversarial"
            elif qid in built and built[qid].evidence:
                label, category = "answerable", built[qid].category
            else:
                continue
            rows.append({"question_id": qid, "unit": sid, "label": label, "category": category,
                         "question": question["question"], "user_id": base.user_id,
                         "reference_time": base.reference_time, "pack": base.pack,
                         "pack_sha256": base.pack_sha256, "speakers": speakers[sid]})
    return rows


def lme_cases() -> list[dict]:
    cases = pp._longmemeval_cases(pp.default_archive(), LME_CAPTURES)
    return [{"question_id": case.question_id, "unit": case.question_id,
             "label": "unknown" if case.question_id.endswith("_abs") else "answerable",
             "category": case.category, "question": case.question, "user_id": case.user_id,
             "reference_time": case.reference_time, "pack": case.pack, "pack_sha256": case.pack_sha256,
             "speakers": []} for case in cases]


async def speaker_of(engine, user_id: str, cache: dict) -> dict[str, str]:
    """Speaker by event id (LoCoMo), so a derived record can be credited to the speaker of its source turn."""
    if user_id not in cache:
        events = await engine.get_events(user_id, limit=pp._ALL_EVENTS)
        cache[user_id] = {str(event.id): (event.metadata or {}).get("prme_speaker_v1") for event in events}
    return cache[user_id]


def record_speaker(node, events: dict[str, str]) -> str | None:
    metadata = node.metadata or {}
    if metadata.get("prme_speaker_v1"):
        return metadata["prme_speaker_v1"]
    for ref in node.evidence_refs or []:
        if events.get(str(ref)):
            return events[str(ref)]
    return None


async def signals(engine, row: dict, events_cache: dict) -> dict:
    response = await engine.retrieve(row["question"], user_id=row["user_id"], reference_time=row["reference_time"])
    receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=row["user_id"])
    features = dict(receipt.execution.features) if receipt is not None and receipt.execution is not None else {}
    terms = content_terms(row["question"])
    searcher = engine._lexical_index._index.searcher()
    df = {term: searcher.doc_freq("content", term) for term in terms}
    events = await speaker_of(engine, row["user_id"], events_cache) if row["speakers"] else {}
    top = []
    for result in response.results[:TOP]:
        text_terms = set(ANALYZER.analyze(result.node.content or ""))
        top.append({
            "cos": round(float(result.semantic_score), 5),
            "type": getattr(result.node.node_type, "value", str(result.node.node_type)),
            "paths": list(result.paths),
            "covered": sum(1 for term in terms if term in text_terms),
            "speaker": record_speaker(result.node, events) if row["speakers"] else None,
        })
    routing = features.get("relation_routing") or {}
    belief = features.get("belief_state") or {}
    return {
        "terms": terms, "df": df, "top": top, "results": len(response.results),
        "route": bool(routing), "route_candidates": len(routing.get("candidates", [])),
        "belief_source": belief.get("source"), "belief_claims": len(belief.get("claims", [])),
        "entities": list(engine_entities(row["question"])),
    }


def engine_entities(question: str) -> list[str]:
    from prme.retrieval.query_analysis import _extract_entities
    return _extract_entities(question)


async def run(benchmark: str, mode: str, out: Path, limit: int | None) -> None:
    rows = locomo_cases() if benchmark == "locomo" else lme_cases()
    done = set()
    if out.exists():
        done = {json.loads(line)["question_id"] for line in out.read_text().splitlines() if line.strip()}
    overrides = {} if mode == "classic" else {"memory_mode": "address_first"}
    by_unit: dict[str, list[dict]] = {}
    for row in rows:
        if row["question_id"] not in done:
            by_unit.setdefault(row["unit"], []).append(row)
    units = list(by_unit.items())[:limit] if limit else list(by_unit.items())
    scratch = Path(tempfile.mkdtemp(prefix="p1-unknown-"))
    try:
        with out.open("a") as sink:
            for count, (unit, unit_rows) in enumerate(units, start=1):
                copy = scratch / unit
                _clone_pack(unit_rows[0]["pack"], copy)
                pp._verify_copy(copy, unit_rows[0]["pack_sha256"])
                config = pp.gate_config(copy, overrides)
                async with MemoryEngine.open(config) as engine:
                    await pp._warm_up(engine, unit_rows[0]["user_id"])
                    cache: dict = {}
                    for row in unit_rows:
                        record = {key: row[key] for key in ("question_id", "unit", "label", "category", "question",
                                                            "speakers")}
                        record.update(benchmark=benchmark, mode=mode, **await signals(engine, row, cache))
                        sink.write(json.dumps(record) + "\n")
                    sink.flush()
                shutil.rmtree(copy, ignore_errors=True)
                print(f"{benchmark} {mode} {count}/{len(units)} {unit} {len(unit_rows)}", flush=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", choices=("locomo", "longmemeval"), required=True)
    parser.add_argument("--mode", choices=("classic", "address_first"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None, help="at most this many packs (a smoke run)")
    args = parser.parse_args()
    if os.environ.get("OPENAI_BASE_URL") != "http://127.0.0.1:9/v1":
        raise SystemExit("Run with providers blocked (OPENAI_BASE_URL=http://127.0.0.1:9/v1 and the rest).")
    asyncio.run(run(args.benchmark, args.mode, args.out, args.limit))


if __name__ == "__main__":
    main()
