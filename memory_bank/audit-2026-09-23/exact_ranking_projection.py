"""Exact LoCoMo ranking and re-pack projections from the replay capture.

Input: data/audit-2026-09-23-replay/captured.jsonl, written by replay_locomo_candidates.py.
Each candidate there carries its own semantic and lexical scores and its true path count.

Orders compared:
  current       the product's composite-score order (before packing tiers)
  rrf           reciprocal rank fusion (k=60) of each candidate's semantic and BM25 ranks
  rrf_session   RRF over the pre-expansion pool, then session expansion as the product
                does it (top 20 triggers, window 3, decay 0.85)

Projections are planning estimates: plain one-line records packed greedily, combined with
the real run's accuracy when all annotated evidence was (or was not) in context.
"""
from __future__ import annotations

import collections
import json
import statistics
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import offline_evidence_audit as A  # noqa: E402
from benchmarks.integrations import run_gpt54_comparison as gpt54  # noqa: E402

CAPTURE = Path("data/audit-2026-09-23-replay/captured.jsonl")
BUDGETS = (A.BUDGET, 8000, 16000)


def load_nodes():
    prepared = json.loads((A.RUN / "locomo/prepared.json").read_text())
    info, sess = {}, {}
    for rec in prepared["packs"]:
        with duckdb.connect(rec["config"]["db_path"], read_only=True) as con:
            rows = con.execute("SELECT id, metadata, content, session_id, created_at FROM nodes").fetchall()
        by_session = collections.defaultdict(list)
        for nid, raw, content, sid, created in rows:
            md = (json.loads(raw) if isinstance(raw, str) else raw) or {}
            info[str(nid)] = (md.get("source_dialog_id"), content or "")
            by_session[sid].append((created, str(nid)))
        for sid, items in by_session.items():
            items.sort()
            ids = [i for _, i in items]
            for pos, nid in enumerate(ids):
                sess[nid] = (pos, ids)
    return info, sess


def main() -> None:
    info, sess = load_nodes()
    questions = {q["question_id"]: q for q in gpt54.question_rows("locomo")}
    diag = A.load_diagnostics("locomo")
    _, acc_all, acc_miss = A.conditional_accuracy(diag)
    header = A.tokens("Conversation memory, one line per message:")
    cost_cache: dict[str, int] = {}

    def cost(nid: str) -> int:
        if nid not in cost_cache:
            cost_cache[nid] = A.tokens("- " + info.get(nid, (None, ""))[1]) + 1
        return cost_cache[nid]

    top_hits = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    proj = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    nrec = collections.defaultdict(list)
    stats = collections.Counter()
    for line in CAPTURE.open():
        row = json.loads(line)
        qid = row["qid"]
        stats["contexts_identical"] += row["same_context"]
        cands = sorted(row["cands"], key=lambda c: (-c["score"], c["id"]))
        saved = json.loads((A.RUN / "locomo/contexts" / f"{qid}.json").read_text())["receipt"]["candidates"]
        packed = {c["node_id"] for c in saved if c["in_context"]}
        for c in cands:
            stats["candidates"] += 1
            if c["pc"] < 2:
                stats["single_path"] += 1
                stats["single_path_packed"] += c["id"] in packed
        wanted = set(questions[qid].get("evidence", []))
        if not wanted:
            continue
        cat = diag[qid]["question_type"]
        ids = [c["id"] for c in cands]
        sem_r = A.ranks([c["sem"] for c in cands])
        lex_r = A.ranks([c["lex"] for c in cands])
        rrf = {c["id"]: 1 / (60 + a) + 1 / (60 + b) for c, a, b in zip(cands, sem_r, lex_r)}
        orig = [c for c in cands if c["paths"] != ["SESSION_CONTEXT"]]
        o_sem = A.ranks([c["sem"] for c in orig])
        o_lex = A.ranks([c["lex"] for c in orig])
        expanded = {c["id"]: 1 / (60 + a) + 1 / (60 + b) for c, a, b in zip(orig, o_sem, o_lex)}
        for trigger in sorted(expanded, key=lambda i: (-expanded[i], i))[:20]:
            pos, session_ids = sess[trigger]
            for neighbor in session_ids[max(0, pos - 3): pos + 4]:
                if neighbor != trigger:
                    expanded[neighbor] = max(expanded.get(neighbor, 0.0), expanded[trigger] * 0.85)
        orders = {
            "current": ids,
            "rrf": sorted(ids, key=lambda i: (-rrf[i], i)),
            "rrf_session": sorted(expanded, key=lambda i: (-expanded[i], i)),
        }
        for name, order in orders.items():
            position = {}
            for p, nid in enumerate(order, 1):
                did = info.get(nid, (None, ""))[0]
                if did and did not in position:
                    position[did] = p
            worst = max(position.get(w, 10**9) for w in wanted)
            top_hits[name][cat]["n"] += 1
            for top in (25, 75):
                top_hits[name][cat][top] += worst <= top
            for budget in BUDGETS:
                used, got, n = header, set(), 0
                for nid in order:
                    c = cost(nid)
                    if used + c <= budget:
                        used += c
                        n += 1
                        did = info.get(nid, (None, ""))[0]
                        if did:
                            got.add(did)
                key = f"{name} @{budget}"
                proj[key][cat][0] += 1
                proj[key][cat][1] += wanted <= got
                nrec[key].append(n)

    print("== Replay:", dict(stats))
    print("\n== Questions with ALL annotated evidence inside the top N (exact per-candidate scores)")
    for name in ("current", "rrf", "rrf_session"):
        parts = [
            f"{cat} @25 {top_hits[name][cat][25] / top_hits[name][cat]['n']:.1%}"
            f" @75 {top_hits[name][cat][75] / top_hits[name][cat]['n']:.1%}"
            for cat in A.LOCOMO_CATS
        ]
        print(f"  {name:12s} " + " | ".join(parts))
    print("\n== Plain-line re-pack projections (planning estimates)")
    total = sum(v[0] for v in proj[f"current @{A.BUDGET}"].values())
    for key in proj:
        score, parts = 0.0, []
        for cat in A.LOCOMO_CATS:
            n, all_in = proj[key][cat]
            share = all_in / n
            score += (share * acc_all[cat] + (1 - share) * acc_miss[cat]) * n
            parts.append(f"{cat} {share:.0%}")
        print(f"  {key:20s} records {statistics.mean(nrec[key]):5.1f} projected {score / total:.1%} | " + ", ".join(parts))


if __name__ == "__main__":
    main()
