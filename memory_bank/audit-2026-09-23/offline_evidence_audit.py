"""Offline evidence audit of the completed GPT-5.4 benchmark run (2026-09-23).

Reproduces the receipt-based tables in memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md
from the local run archive. It makes no model calls and changes no files.

Its RRF rows are approximate: saved receipts copy a trigger's score trace onto
session-expansion neighbors (about 10% of candidates). The audit's RRF numbers come
from replay_locomo_candidates.py and exact_ranking_projection.py in this folder.

Inputs (local only, gitignored):
  data/gpt54-comparison-v1/            saved contexts, receipts, answers, packs
  data/benchmarks/locomo/locomo10.json
  data/benchmarks/longmemeval/longmemeval_s_cleaned.json
  benchmarks/results/research/2026-09-23/gpt54-posthoc-evidence-diagnostics.json

Run from the repository root:
  PYTHONPATH=. .venv/bin/python memory_bank/audit-2026-09-23/offline_evidence_audit.py

The re-pack projections are planning estimates, not answer scores. They take the
saved candidate list for each question, render records in another format, pack
greedily to a budget, and combine the resulting evidence-retention rate with the
real run's accuracy when all annotated evidence was (or was not) in context.
They ignore distractor effects and annotation gaps.
"""
from __future__ import annotations

import collections
import json
import statistics
from pathlib import Path

import duckdb
import tiktoken

from benchmarks.integrations import run_gpt54_comparison as gpt54

ENC = tiktoken.get_encoding("cl100k_base")
RUN = Path("data/gpt54-comparison-v1")
DIAG = Path(
    "benchmarks/results/research/2026-09-23/gpt54-posthoc-evidence-diagnostics.json"
)
LOCOMO_CATS = ["single-hop", "temporal", "multi-hop", "open-domain"]
BUDGET = 3996  # 4,096 configured tokens minus 100 reserved overhead


def tokens(text: str) -> int:
    return len(ENC.encode(text, disallowed_special=()))


def ranks(values: list[float]) -> list[int]:
    order = sorted(range(len(values)), key=lambda i: -values[i])
    out = [0] * len(values)
    for position, index in enumerate(order):
        out[index] = position + 1
    return out


def load_diagnostics(benchmark: str) -> dict[str, dict]:
    rows = json.loads(DIAG.read_text())["benchmarks"][benchmark]["rows"]
    return {row["question_id"]: row for row in rows}


def conditional_accuracy(diag: dict[str, dict]) -> tuple[dict, dict, dict]:
    """Accuracy when all annotated evidence was packed versus when some was missing."""
    counts = collections.defaultdict(lambda: [0, 0, 0, 0])
    for row in diag.values():
        c = counts[row["question_type"]]
        if row["coverage"] == "all_annotated_turns_packed":
            c[0] += 1
            c[1] += row["correct"]
        elif row["coverage"] == "annotated_turns_missing":
            c[2] += 1
            c[3] += row["correct"]
    acc_all = {k: v[1] / v[0] for k, v in counts.items() if v[0]}
    acc_miss = {k: (v[3] / v[2] if v[2] else 0.0) for k, v in counts.items()}
    return counts, acc_all, acc_miss


def context_envelope(benchmark: str) -> None:
    ctx_tokens, text_tokens, records = [], [], []
    for path in sorted((RUN / benchmark / "contexts").glob("*.json")):
        context = json.loads(path.read_text())["context"]
        ctx_tokens.append(tokens(context))
        texts = []
        for line in context.split("\n"):
            if line.startswith("{"):
                try:
                    texts.append(json.loads(line).get("text", ""))
                except json.JSONDecodeError:
                    continue
        text_tokens.append(sum(tokens(t) for t in texts))
        records.append(len(texts))
    ctx, txt, n = statistics.mean(ctx_tokens), statistics.mean(text_tokens), statistics.mean(records)
    print(
        f"{benchmark}: context {ctx:.0f} tokens, text {txt:.0f} ({txt / ctx:.0%}), "
        f"records {n:.1f}, envelope per record {(ctx - txt) / n:.0f}"
    )


def load_locomo():
    prepared = json.loads((RUN / "locomo/prepared.json").read_text())
    nodes: dict[str, dict[str, tuple[str | None, str]]] = {}
    for record in prepared["packs"]:
        with duckdb.connect(record["config"]["db_path"], read_only=True) as con:
            rows = con.execute("SELECT id, metadata, content FROM nodes").fetchall()
        mapped = {}
        for node_id, raw, content in rows:
            metadata = (json.loads(raw) if isinstance(raw, str) else raw) or {}
            mapped[str(node_id)] = (metadata.get("source_dialog_id"), content or "")
        nodes[record["conversation_id"]] = mapped
    questions = {q["question_id"]: q for q in gpt54.question_rows("locomo")}
    return nodes, questions


def locomo_audit() -> None:
    nodes, questions = load_locomo()
    diag = load_diagnostics("locomo")
    counts, acc_all, acc_miss = conditional_accuracy(diag)

    print("\n== LoCoMo accuracy by evidence state (real run)")
    for cat in LOCOMO_CATS:
        c = counts[cat]
        print(f"  {cat:12s} all packed {c[1]}/{c[0]} = {acc_all[cat]:.1%} | missing {c[3]}/{c[2]} = {acc_miss[cat]:.1%}")

    print("\n== LoCoMo multi-hop by number of annotated evidence turns (real run)")
    buckets = collections.defaultdict(lambda: [0, 0, 0])
    for row in diag.values():
        if row["question_type"] != "multi-hop":
            continue
        b = buckets[min(row["required_turns"], 5)]
        b[0] += 1
        b[1] += row["correct"]
        b[2] += row["annotated_turns_packed"] == row["required_turns"]
    for k in sorted(buckets):
        n, ok, packed = buckets[k]
        print(f"  {k}{'+' if k == 5 else ' '} turns: {n:3d} questions, all packed {packed / n:.0%}, accuracy {ok / n:.1%}")

    pool, rank_by_cat = [], collections.defaultdict(list)
    projections = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    rank_hits = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    records_per_ctx = collections.defaultdict(list)
    header = tokens("Conversation memory, one line per message:")
    for qid, q in questions.items():
        info = nodes[q["conversation_id"]]
        receipt = json.loads((RUN / "locomo/contexts" / f"{qid}.json").read_text())["receipt"]
        cands, prov = receipt["candidates"], receipt["score_provenance"]
        pool.append(len(cands))
        wanted = set(q.get("evidence", []))
        cat = diag[qid]["question_type"]
        if not wanted:
            continue
        traces = [((prov.get(c["node_id"]) or {}).get("trace") or {}) for c in cands]
        sem = [t.get("semantic_similarity") or 0.0 for t in traces]
        lex = [t.get("lexical_relevance") or 0.0 for t in traces]
        sem_rank, lex_rank = ranks(sem), ranks(lex)
        rrf = [1 / (60 + a) + 1 / (60 + b) for a, b in zip(sem_rank, lex_rank)]
        orders = {
            "current": list(range(len(cands))),
            "rrf60": sorted(range(len(cands)), key=lambda i: -rrf[i]),
        }
        # Rank of each annotated turn under the current composite order.
        first_rank = {}
        for position, c in enumerate(cands, start=1):
            dialog_id = info.get(c["node_id"], (None, ""))[0]
            if dialog_id and dialog_id not in first_rank:
                first_rank[dialog_id] = position
        rank_by_cat[cat] += [first_rank[w] for w in wanted if w in first_rank]
        for name, order in orders.items():
            position_of = {}
            for position, index in enumerate(order, start=1):
                dialog_id = info.get(cands[index]["node_id"], (None, ""))[0]
                if dialog_id and dialog_id not in position_of:
                    position_of[dialog_id] = position
            worst = max(position_of.get(w, 10**9) for w in wanted)
            rank_hits[name][cat]["n"] += 1
            for top in (25, 75):
                rank_hits[name][cat][top] += worst <= top
        # Approximate the product's `balanced` policy under plain rendering: keep the
        # top-scored candidate first, then order by score / plain_token_cost ** 0.25.
        plain_cost = [tokens("- " + info.get(c["node_id"], (None, ""))[1]) + 1 for c in cands]
        orders["balanced"] = [0] + sorted(
            range(1, len(cands)), key=lambda i: -(cands[i]["score"] / max(plain_cost[i], 1) ** 0.25)
        )
        # Plain one-line rendering, greedy pack.
        for name, order in orders.items():
            budgets = (BUDGET,) if name == "balanced" else (BUDGET, 8000, 16000)
            for budget in budgets:
                used, packed, n = header, set(), 0
                for index in order:
                    dialog_id, content = info.get(cands[index]["node_id"], (None, ""))
                    cost = tokens("- " + content) + 1
                    if used + cost <= budget:
                        used += cost
                        n += 1
                        if dialog_id:
                            packed.add(dialog_id)
                key = f"plain lines, {name} order, {budget} tokens"
                projections[key][cat][0] += 1
                projections[key][cat][1] += wanted <= packed
                records_per_ctx[key].append(n)

    print(f"\n== Candidate pool per question: mean {statistics.mean(pool):.0f}, min {min(pool)}, max {max(pool)}")
    print("\n== Rank of annotated evidence under the current composite score")
    for cat in LOCOMO_CATS:
        r = rank_by_cat[cat]
        print(
            f"  {cat:12s} median {statistics.median(r):5.0f} | top-25 {sum(x <= 25 for x in r) / len(r):.1%}"
            f" | beyond 150 {sum(x > 150 for x in r) / len(r):.1%}"
        )

    print("\n== Questions with ALL annotated evidence inside the top N (current vs RRF k=60; RRF approximate, see exact_ranking_projection.py)")
    for name in ("current", "rrf60"):
        parts = []
        for cat in LOCOMO_CATS:
            h = rank_hits[name][cat]
            parts.append(f"{cat} @25 {h[25] / h['n']:.1%} @75 {h[75] / h['n']:.1%}")
        print(f"  {name:8s} " + " | ".join(parts))

    print("\n== Re-pack projections (planning estimates; rrf60 rows approximate, see exact_ranking_projection.py)")
    total = sum(projections[next(iter(projections))][c][0] for c in LOCOMO_CATS)
    for key in sorted(projections):
        projected, parts = 0.0, []
        for cat in LOCOMO_CATS:
            n, all_in = projections[key][cat]
            share = all_in / n
            estimate = share * acc_all[cat] + (1 - share) * acc_miss[cat]
            projected += estimate * n
            parts.append(f"{cat} {share:.0%}")
        print(
            f"  {key:38s} records {statistics.mean(records_per_ctx[key]):4.0f} "
            f"projected {projected / total:.1%} | all evidence in: " + ", ".join(parts)
        )


def signal_variation(sample_every: int = 40) -> None:
    values = collections.defaultdict(set)
    paths = sorted((RUN / "locomo/contexts").glob("*.json"))[::sample_every]
    for path in paths:
        provenance = json.loads(path.read_text())["receipt"]["score_provenance"]
        for entry in provenance.values():
            for key, value in (entry.get("trace") or {}).items():
                if isinstance(value, (int, float)):
                    values[key].add(round(value, 4))
    print("\n== Distinct values per scoring signal (LoCoMo sample)")
    for key, distinct in values.items():
        print(f"  {key:20s} {len(distinct):5d} distinct, range {min(distinct)} to {max(distinct)}")


def longmemeval_repack() -> None:
    data = {q["question_id"]: q for q in json.load(open("data/benchmarks/longmemeval/longmemeval_s_cleaned.json"))}
    diag = load_diagnostics("longmemeval")
    _, acc_all, acc_miss = conditional_accuracy(diag)
    abstention_ok = sum(r["correct"] for r in diag.values() if r["coverage"] == "abstention")
    results = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    for qid, q in data.items():
        capture = json.loads((RUN / "longmemeval/contexts" / f"{qid}.json").read_text())
        retrieval = json.loads(Path(capture["source_capture"]).read_text())["retrievals"][0]
        cost, wanted = {}, set()
        for position, (sid, session, date) in enumerate(
            zip(q["haystack_session_ids"], q["haystack_sessions"], q["haystack_dates"])
        ):
            for index, turn in enumerate(session):
                key = (sid, position, index)
                cost[key] = tokens(f"- [{date}] {turn['role']}: {turn['content']}") + 1
                if turn.get("has_answer") is True:
                    wanted.add(key)
        if not wanted:
            continue
        returned = [
            (i["source_session_id"], i["source_session_position"], i["source_turn_index"])
            for i in retrieval["returned"]
        ]
        cat = diag[qid]["question_type"]
        for budget in (BUDGET, 8000, 16000, 32000):
            used, packed = 20, set()
            for key in returned:
                c = cost.get(key, 10**9)
                if used + c <= budget:
                    used += c
                    packed.add(key)
            results[budget][cat][0] += 1
            results[budget][cat][1] += wanted <= packed
    print("\n== LongMemEval plain-line re-pack projections (score order; planning estimates)")
    for budget, cats in sorted(results.items()):
        projected = abstention_ok
        for cat, (n, all_in) in cats.items():
            share = all_in / n
            projected += (share * acc_all[cat] + (1 - share) * acc_miss.get(cat, 0.0)) * n
        print(f"  {budget:6d} tokens: projected {projected / len(diag):.1%}")


if __name__ == "__main__":
    print("== Context envelope (real run)")
    context_envelope("locomo")
    context_envelope("longmemeval")
    locomo_audit()
    signal_variation()
    longmemeval_repack()
