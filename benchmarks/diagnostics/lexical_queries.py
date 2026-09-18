"""Compare literal lexical query preprocessing on a fixed development corpus.

Only retrieval queries change. Stored text, en_stem index analysis and the
shared whole-turn packer remain fixed; this does not evaluate hybrid retrieval.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import tantivy
import tiktoken

from benchmarks.compare_evidence import paired_statistics
from benchmarks.diagnostics.packing_reader import canonical, digest, write
from benchmarks.evidence import evidence_metrics, longmemeval_sources, pack_sources
from prme.storage.lexical_index import LexicalIndex

STOP_PATH = Path(__file__).parents[1] / "fixtures/postgres-english-stopwords/english.stop"
STOP_SHA = "b3f772a000465cb76e23adb03b47073c591c156fad8f7af09c8b8e80d6bd8eac"
POLICIES = ("parser", "literal", "literal_stopwords")
BUDGETS = (2048, 4096, 8192)


def analyzer(stopwords=False):
    builder = (tantivy.TextAnalyzerBuilder(tantivy.Tokenizer.simple())
               .filter(tantivy.Filter.remove_long(40)).filter(tantivy.Filter.lowercase()))
    if stopwords:
        raw = STOP_PATH.read_bytes()
        if digest(raw) != STOP_SHA:
            raise ValueError("Stopword fixture differs")
        builder = builder.filter(tantivy.Filter.custom_stopword(raw.decode().splitlines()))
    return builder.filter(tantivy.Filter.stemmer("english")).build()


def literal_query(index, text, *, stopwords):
    tokens = list(dict.fromkeys(analyzer(stopwords).analyze(text)))
    # An all-stopword title/query must not silently become match-all.
    if not tokens and stopwords:
        tokens = list(dict.fromkeys(analyzer().analyze(text)))
    terms = [tantivy.Query.term_query(index._schema, "content", token) for token in tokens]
    return tantivy.Query.boolean_query([(tantivy.Occur.Should, term) for term in terms])


def search_literal(index, text, user_id, *, stopwords, limit):
    query = tantivy.Query.boolean_query([
        (tantivy.Occur.Must, literal_query(index, text, stopwords=stopwords)),
        (tantivy.Occur.Must, tantivy.Query.term_query(index._schema, "user_id", user_id)),
        (tantivy.Occur.Must, tantivy.Query.term_query(index._schema, "scope", "personal")),
    ])
    searcher = index._index.searcher()
    return [{"node_id": searcher.doc(address)["node_id"][0], "score": float(score)}
            for score, address in searcher.search(query, limit).hits]


async def rank_sources(turns, query):
    """The retrieval adapter receives only neutral sources and the query."""
    with tempfile.TemporaryDirectory(prefix="prme-lexical-query-") as tmp:
        index = LexicalIndex(tmp, commit_interval=max(1, len(turns)+1), commit_max_delay_s=0)
        try:
            for turn in turns:
                await index.index(turn.id, turn.content, "evaluation", "note", "personal")
            await index.flush()
            rankings = {}
            for policy in POLICIES:
                hits = (await index.search(query, "evaluation", limit=max(1, len(turns)), scope=["personal"])
                        if policy == "parser" else search_literal(index, query, "evaluation",
                            stopwords=policy == "literal_stopwords", limit=max(1, len(turns))))
                # Apply a common deterministic tie policy before the 100-result cap.
                rankings[policy] = [hit["node_id"] for hit in sorted(hits, key=lambda h: (-h["score"], h["node_id"]))][:100]
            return rankings
        finally:
            await index.close()


def compare(details):
    def group(rows):
        result = {}
        for policy in POLICIES[1:]:
            result[policy] = {}
            for budget in BUDGETS:
                pairs = [(r["methods"]["parser"]["packing"][str(budget)]["evidence_recall"],
                          r["methods"][policy]["packing"][str(budget)]["evidence_recall"]) for r in rows]
                result[policy][str(budget)] = paired_statistics([(a,b) for a,b in pairs if a is not None and b is not None])
        return result
    return {"overall": group(details), "categories": {cat: group([r for r in details if r["category"] == cat])
                                                      for cat in sorted({r["category"] for r in details})}}


async def run(dataset_path, baseline_path, output):
    raw = dataset_path.read_bytes()
    baseline = json.loads(baseline_path.read_bytes())
    if (not baseline.get("complete") or baseline.get("errors") or baseline.get("process_exit_code") != 0
            or baseline["dataset"]["split"] != "dev" or digest(raw) != baseline["dataset"]["sha256"]):
        raise ValueError("Complete matching development dataset required")
    selected = baseline["dataset"]["selected_question_ids"]
    data = json.loads(raw)
    questions = {q["question_id"]: q for q in data}
    if len(questions) != len(data) or len(set(selected)) != len(selected) or not selected or not set(selected) <= questions.keys():
        raise ValueError("Invalid question identities")
    encoding = tiktoken.get_encoding("cl100k_base")
    def count(text):
        return len(encoding.encode(text, disallowed_special=()))
    report = {"kind": "development-lexical-query-ablation", "complete": False, "started_at": datetime.now(timezone.utc).isoformat(),
              "dataset": baseline["dataset"], "baseline_sha256": digest(baseline_path.read_bytes()),
              "runner_sha256": digest(Path(__file__).read_bytes()), "stopwords_sha256": STOP_SHA,
              "versions": {name: version(name) for name in ["tantivy", "tiktoken"]}, "policies": list(POLICIES),
              "candidate_limit": 100, "budgets": list(BUDGETS), "details": [], "errors": 0,
              "limits": ["Post-hoc development exploration after preference failures; not independent confirmation.",
                         "Fresh shared en_stem lexical indexes, one buffered commit and stable source-ID ties. Historical index layouts are not reproduced.",
                         "Literal policies use OR over distinct stemmed terms, with or without the frozen PostgreSQL English stop list. Parser control retains query-language interpretation.",
                         "No model, hybrid scoring, temporal interpretation, product packing or answer judging. Unlabelled evidence scores are null, not successful abstention.",
                         "Query bootstrap ignores overlapping histories. No default-changing gate or speed claim."]}
    write(output, report)
    for qid in selected:
        try:
            question = questions[qid]
            turns, gold = longmemeval_sources(question)
            ranked = await rank_sources(turns, question["question"])
            if set(ranked) != set(POLICIES):
                raise ValueError("Missing or unexpected query policy")
            by_id = {turn.id: turn for turn in turns}
            row = {"question_id": qid, "category": question["question_type"], "source_count": len(turns),
                   "sources_sha256": digest(canonical([vars(turn) for turn in turns])), "evidence_source_ids": sorted(gold), "methods": {}}
            for policy, ids in ranked.items():
                if len(ids) != len(set(ids)) or not set(ids) <= by_id.keys():
                    raise ValueError("Invalid source ranking")
                packing = {}
                for budget in BUDGETS:
                    context, packed, tokens = pack_sources([by_id[sid] for sid in ids], token_budget=budget, count_tokens=count)
                    packing[str(budget)] = {"tokens": tokens, "source_ids": packed, "context_sha256": digest(context.encode()),
                                            "evidence_recall": len(set(packed) & gold)/len(gold) if gold else None}
                row["methods"][policy] = {"ranked_source_ids": ids, "metrics": evidence_metrics(ids, gold), "packing": packing}
        except Exception as exc:
            row = {"question_id": qid, "error_type": type(exc).__name__}
            report["errors"] += 1
        report["details"].append(row)
        write(output, report)
        print(f"Evaluated {len(report['details'])}/{len(selected)}; errors={report['errors']}", flush=True)
    report["workflow_complete"] = report["errors"] == 0
    if report["workflow_complete"]:
        report["comparison"] = compare(report["details"])
    write(output, report)
    return 0 if report["workflow_complete"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["dataset", "baseline", "output"]:
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        raise SystemExit(asyncio.run(run(args.dataset, args.baseline, args.output)))
    if args.output.exists():
        raise ValueError("Refusing to overwrite an existing study")
    exit_code = subprocess.run([sys.executable, "-m", __spec__.name, *sys.argv[1:], "--worker"]).returncode
    result = json.loads(args.output.read_bytes())
    result["process_exit_code"] = exit_code
    result["complete"] = exit_code == 0 and result.get("workflow_complete") is True
    write(args.output, result)
    raise SystemExit(0 if result["complete"] else 1)


if __name__ == "__main__":
    main()
