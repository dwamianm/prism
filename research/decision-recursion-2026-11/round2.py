"""Round 2 (post-hoc, designed by the lead after the first two rounds): W3, W3b and R5.

W3 asks whether the write errors come from the candidate scope rather than the judge. Every claim's subject is
"I", so W1 showed the model every other fact. W3 shows it only the old facts at the same address: the same
subject entity and (the same typed relation when both carry one outside "has", otherwise the same attribute), or
the same object entity. Two predicates count as one attribute when their content-word stems meet.

W3b: when a new fact has no address partner, one closed question first picks the earlier statement it is about
("Which of these earlier statements is about the same thing as the new one?", up to 8 live facts of the same
subject, newest first, plus "none of them"), and W1 runs only on the chosen one.

R5 decides "unknown" with one question instead of ten judgments. PRME's order (R0) is kept. One question over the
top 5 records plus "none of them": a chosen record moves to rank 1; on "none of them" the R2b lookup step runs once
(pick a record, pick a word, retrieve, ask the same question on the new top 5); "unknown" only when both rounds say
"none of them".

Prompts: `W3b.topic` and `R5.any_answer` are new, in `prompts.ROUND2_TEMPLATES`, tried once on made-up cases and
frozen under their own hash. The earlier prompts are untouched.

    uv run python research/decision-recursion-2026-11/round2.py --backend systemone --model clef-flash \
        --run-dir research/decision-recursion-2026-11/results/round2-clef-flash
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

import write_experiment as W  # noqa: E402
from analyze import CacheDecider, probabilities  # noqa: E402
from decide import Decider, loaded_models, ollama_identity, warm  # noqa: E402
from prompts import (OPTIONS, POSTHOC_NONE, POSTHOC_TEMPLATES, ROUND2_NONE, ROUND2_TEMPLATES, TEMPLATES,  # noqa: E402
                     check_frozen, check_posthoc_frozen, check_round2_frozen, content_words, fmt_time,
                     round2_sha256)
from read_experiment import build_questions, make_provider, records, retrieve  # noqa: E402

COPUAS = frozenset("is are was were be been being am has have had".split())
SUFFIXES = ("ing", "ed", "es", "s")


def stem(word: str) -> str:
    word = re.sub(r"[^a-z0-9]", "", word.lower())
    for suffix in SUFFIXES:
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            word = word[: -len(suffix)]
            break
    return word.rstrip("e")


def attribute_stems(predicate: str) -> set[str]:
    words = [w for w in re.split(r"[^a-z0-9]+", predicate.lower()) if w and w not in COPUAS]
    return {stem(w) for w in words if w}


def typed_relation(fact: W.Fact) -> str | None:
    relation = getattr(fact, "relation", None)
    return relation if relation and relation != "has" else None


def same_address(old: W.Fact, new: W.Fact) -> bool:
    old_relation, new_relation = typed_relation(old), typed_relation(new)
    if old_relation and new_relation:
        return old_relation == new_relation
    old_stems, new_stems = attribute_stems(old.predicate), attribute_stems(new.predicate)
    if old_stems and new_stems:
        return bool(old_stems & new_stems)
    return old.predicate == new.predicate


def scoped_neighbors(live: list[W.Fact], new: W.Fact) -> list[W.Fact]:
    subject = W.norm(new.subject)
    return [f for f in live if f.id != new.id and f.turn != new.turn
            and ((W.norm(f.subject) == subject and same_address(f, new)) or W.norm(f.object) == W.norm(new.object))]


def topic_pool(live: list[W.Fact], new: W.Fact) -> list[W.Fact]:
    """Up to eight live facts of the same subject, newest first."""
    subject = W.norm(new.subject)
    same = [f for f in live if f.turn != new.turn and W.norm(f.subject) == subject]
    return sorted(same, key=lambda f: f.time, reverse=True)[:8]


def simulate_scoped(decider, facts: list[W.Fact], ask_topic: bool) -> dict:
    live: list[W.Fact] = []
    retired: dict[str, dict] = {}
    per_fact = []
    for new in facts:
        mark = decider.mark()
        candidates = scoped_neighbors(live, new)
        if ask_topic and not candidates:
            pool = topic_pool(live, new)
            if pool:
                options = [f"({fmt_time(f.time)}) {f.text()}" for f in pool] + [ROUND2_NONE["W3b.topic"]]
                choice, _ = decider.decide(ROUND2_TEMPLATES["W3b.topic"].format(date_new=fmt_time(new.time), new=new.text()), options,
                                           tag="W3b.topic")
                if choice < len(pool):
                    candidates = [pool[choice]]
        for old in candidates:
            fields = {"date_old": fmt_time(old.time), "old": old.text(), "date_new": fmt_time(new.time),
                      "new": new.text()}
            choice, confidence = decider.decide(TEMPLATES["W1.still_true"].format(**fields),
                                                OPTIONS["W1.still_true"], tag="W3.judge")
            if choice == 1:
                retired[old.id] = {"by": new.id, "stage": "W3", "confidence": confidence}
        live = [f for f in live if f.id not in retired]
        live.append(new)
        made = decider.since(mark)
        per_fact.append({"id": new.id, "calls": len(made), "cost_ms": round(sum(d.cost_ms for d in made), 1)})
    return {"retired": retired, "per_fact": per_fact}


# R5 ------------------------------------------------------------------------------------------------------------

def run_r5(decider: Decider, questions) -> list[dict]:
    rows = []
    for q in questions:
        top_all = records(q.scene.ask(q.question), n=10)
        top5 = top_all[:5]
        mark = decider.mark()
        options = [r.text for r in top5] + [ROUND2_NONE["R5.any_answer"]]
        start = time.perf_counter()
        choice, _ = decider.decide(ROUND2_TEMPLATES["R5.any_answer"].format(question=q.question), options,
                                   tag="R5.round1")
        order, unknown, stopped, follow_ms = None, False, "", 0.0
        if choice < len(top5):
            chosen = top5[choice]
            order = [chosen] + [r for r in top_all if r.id != chosen.id]
            stopped = "round 1 (answer chosen)"
        else:
            pool = [r.text for r in top5]
            pick, _ = decider.decide(POSTHOC_TEMPLATES["R2b.pick_record"].format(question=q.question),
                                     pool + [POSTHOC_NONE["R2b.pick_record"]], tag="R5.pick_record")
            word = None
            if pick < len(pool):
                rec = top5[pick]
                words = content_words(rec.text, q.question) + ["none"]
                word_choice, _ = decider.decide(TEMPLATES["R2.pick_word"].format(question=q.question, record=rec.text),
                                                words, tag="R5.pick_word")
                word = words[word_choice]
            if word in (None, "none"):
                unknown, stopped = True, "unknown (no record or word picked)"
            else:
                started = time.perf_counter()
                response = retrieve(q.scene, word)
                follow_ms = (time.perf_counter() - started) * 1000
                fresh = [r for r in records(response, n=5) if r.id not in {t.id for t in top_all}]
                fresh = fresh or records(response, n=5)
                options2 = [r.text for r in fresh] + [ROUND2_NONE["R5.any_answer"]]
                second, _ = decider.decide(ROUND2_TEMPLATES["R5.any_answer"].format(question=q.question), options2,
                                           tag="R5.round2")
                if second < len(fresh):
                    chosen = fresh[second]
                    order = [chosen] + [r for r in top_all if r.id != chosen.id]
                    stopped = "round 2 (answer chosen)"
                else:
                    unknown, stopped = True, "unknown (rounds 1 and 2 none)"
        decided = decider.since(mark)
        row = {
            "group": q.group, "id": q.id, "question": q.question, "unknown": unknown,
            "rank1": bool(order) and not unknown and bool(q.answers)
                    and any(_hit(q, order[0].text, a) for a in q.answers),
            "top3": bool(order) and not unknown and bool(q.answers)
                    and all((r := _rank(q, order, a)) is not None and r <= 3 for a in q.answers),
            "answer_ranks": {a: _rank(q, order or [], a) for a in q.answers},
            "stale_above": _stale_above(q, order or [], unknown),
            "calls": len(decided), "added_ms": round(sum(d.cost_ms for d in decided) + follow_ms, 1),
            "stopped": stopped, "word": word if stopped.endswith("round 2 (answer chosen)") else None,
            "top5": [r.text[:110] for r in (order or top5)[:5]],
        }
        if "request" in q.extra:
            top5_now = [r.text for r in (order or [])[:5]]
            row["both_in_top5"] = q.extra["request"] in top5_now and q.answers[0] in top5_now
        rows.append(row)
    return rows


def _hit(q, text: str, needle: str) -> bool:
    return text == needle if q.exact else needle.lower() in text.lower()


def _rank(q, order, needle: str):
    return next((i for i, r in enumerate(order, start=1) if _hit(q, r.text, needle)), None)


def _stale_above(q, order, unknown: bool) -> bool:
    if unknown or not q.stale or not q.answers:
        return False
    worst = max((_rank(q, order, a) or 999) for a in q.answers)
    return any((r := _rank(q, order, s)) is not None and r < worst for s in q.stale)


def summarize_r5(rows: list[dict]) -> dict:
    def group(name):
        return [r for r in rows if r["group"] == name]

    def count(name, key):
        return sum(bool(r.get(key)) for r in group(name))

    answerable = ("chat_store", "chat_ingest", "hops", "churn10", "flood")
    ms = sorted(r["added_ms"] for r in rows)
    calls = [r["calls"] for r in rows]
    result = {
        "chat_store": {"n": len(group("chat_store")), "rank1": count("chat_store", "rank1"),
                       "top3": count("chat_store", "top3"), "stale_above": count("chat_store", "stale_above")},
        "chat_ingest": {"n": len(group("chat_ingest")), "rank1": count("chat_ingest", "rank1"),
                        "top3": count("chat_ingest", "top3"), "stale_above": count("chat_ingest", "stale_above")},
        "hops": {"n": len(group("hops")), "rank1": count("hops", "rank1"), "both_in_top5": count("hops", "both_in_top5")},
        "churn10": {"n": len(group("churn10")), "rank1": count("churn10", "rank1")},
        "flood": {"n": len(group("flood")), "top5": sum(1 for r in group("flood") if r["rank1"])},
        "unknown_right": {"of_7": sum(1 for r in rows if r["unknown"] and r["group"] in ("cat_store", "unseen")),
                          "cat_ingest": any(r["unknown"] for r in rows if r["group"] == "cat_ingest")},
        "false_unknown": [r["id"] for r in rows if r["unknown"] and r["group"] in answerable],
        "calls_per_question": {"median": statistics.median(calls), "max": max(calls)},
        "added_ms_per_question": {"median": round(statistics.median(ms), 1),
                                  "p95": round(ms[min(len(ms) - 1, int(0.95 * len(ms)))], 1)},
    }
    result["false_unknown_total"] = len(result["false_unknown"])
    gain = result["chat_store"]["rank1"] - 5
    bars = {"unknown_right_at_least_6_of_7": result["unknown_right"]["of_7"] >= 6,
            "false_unknowns_at_most_1": len(result["false_unknown"]) <= 1,
            "rank1_chat_store_gain_at_least_4": gain >= 4,
            "median_added_ms_at_most_300": result["added_ms_per_question"]["median"] <= 300}
    result["rank1_chat_store_gain_over_R0"] = gain
    result["bars"] = bars
    result["READ_PASS"] = all(bars.values())
    return result


TRIAL = [
    # (prompt, fields, options without the "none" line, expected)
    ("W3b.topic", {"date_new": "2026-06-01 09:00", "new": "I moved to Linz"},
     ["(2026-02-01 09:00) I plays chess", "(2026-01-15 09:00) I lives in Graz"], None),
    ("W3b.topic", {"date_new": "2026-06-01 09:00", "new": "I bought a red bike"},
     ["(2026-02-01 09:00) I works at night bakery", "(2026-03-01 09:00) I bought a blue bike"], None),
    ("W3b.topic", {"date_new": "2026-06-01 09:00", "new": "I likes jazz"},
     ["(2026-02-01 09:00) I plays chess", "(2026-02-01 09:00) I works at night bakery"], None),
    ("R5.any_answer", {"question": "Where does Greta keep her bicycle?"},
     ["The train to Bern was late again.", "Greta keeps her bicycle in the shed behind the bakery."], None),
    ("R5.any_answer", {"question": "What did Yusuf cook for the party?"},
     ["Yusuf hosted a party on Saturday.", "The bakery opens at 7."], None),
    ("R5.any_answer", {"question": "Where is the kite Greta asked for?"},
     ["Yusuf likes chess.", "The train to Bern was late again."], None),
]
TRIAL_EXPECTED = ["(2026-01-15 09:00) I lives in Graz", "(2026-03-01 09:00) I bought a blue bike", "none of them",
                  "Greta keeps her bicycle in the shed behind the bakery.", "Yusuf hosted a party on Saturday.",
                  "none of them"]


def trial(args, run_dir: Path) -> None:
    """One try of each new prompt on three made-up cases, expected answers fixed above, before the freeze."""
    decider = Decider(args.backend, args.model, run_dir / "trial-cache", run_dir / "trial_decisions.jsonl",
                      seed=args.seed)
    warm(decider)
    right = 0
    for (name, fields, options, _), expected in zip(TRIAL, TRIAL_EXPECTED):
        options = options + [ROUND2_NONE[name]]
        choice, confidence = decider.decide(ROUND2_TEMPLATES[name].format(**fields), options, tag=name)
        chosen = options[choice]
        right += chosen == expected
        print(f"{'ok ' if chosen == expected else 'BAD'} {name:14} chose {chosen!r} (p={confidence}) "
              f"expected {expected!r}")
    print(f"{right} of {len(TRIAL)} right; round2 templates {round2_sha256()}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("ollama", "random", "systemone"), required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--trial", action="store_true", help="try the two new prompts on made-up cases only")
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    templates = check_frozen()
    posthoc = check_posthoc_frozen()
    round2 = check_round2_frozen()
    if args.trial:
        trial(args, run_dir)
        return
    decider = Decider(args.backend, args.model, Path(args.cache_dir or run_dir / "cache"),
                      run_dir / "round2_decisions.jsonl", seed=args.seed)

    # W3 and W3b
    facts = W.load_facts()
    key = W.load_key(facts)
    loaded_before = loaded_models() if args.backend != "random" else []
    warm_ms = warm(decider)
    started = time.perf_counter()
    write = {}
    for name, ask_topic in (("W3", False), ("W3b", True)):
        mark = decider.mark()
        sim = simulate_scoped(decider, facts, ask_topic)
        write[name] = W.score(sim, facts, key)
        write[name]["decisions"] = len(decider.since(mark))
    write_wall = time.perf_counter() - started
    # The pair and probabilities behind each wrong retirement (from the cache, no new call).
    base = CacheDecider(run_dir, args.model or args.backend)
    base_result = simulate_scoped(base, facts, True)
    by_id = {f.id: f for f in facts}
    wrong_pairs = {}
    for fact_id in sorted(set(base_result["retired"]) - set(key["summary"]["retired"])):
        info = base_result["retired"][fact_id]
        old, new = by_id[fact_id], by_id[info["by"]]
        question = TEMPLATES["W1.still_true"].format(date_old=fmt_time(old.time), old=old.text(),
                                                    date_new=fmt_time(new.time), new=new.text())
        path = base._entry_path(question, OPTIONS["W1.still_true"])
        probs = probabilities(run_dir, {"key": path.stem}, 3) if path.exists() else None
        entry = json.loads(path.read_text()) if path.exists() else {}
        wrong_pairs[fact_id] = {"triple": old.triple, "by": info["by"], "by_text": new.text(),
                                "chosen": OPTIONS["W1.still_true"][entry["choice"]] if entry else None,
                                "probabilities": None if probs is None else [round(probs[i], 4) for i in range(3)]}

    # R5
    started = time.perf_counter()
    provider = make_provider()
    questions, built = build_questions(provider)
    for q in questions:
        q.scene.ask(q.question)
    rows = run_r5(decider, questions)
    read_wall = time.perf_counter() - started
    for scene in built:
        scene.close()

    output = {
        "round": 2, "backend": args.backend, "model": args.model, "seed": args.seed,
        **ollama_identity(args.model), "loaded_before_warm": loaded_before, "warm_ms": warm_ms,
        "templates_sha256": templates, "posthoc_sha256": posthoc, "round2_sha256": round2,
        "key_sha256": W.KEY_SHA256, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "write": write, "wrong_pairs": wrong_pairs, "read": summarize_r5(rows), "rows": rows,
        "write_wall_seconds": round(write_wall, 1), "read_wall_seconds": round(read_wall, 1),
        "model_calls": sum(not d.cached for d in decider.decisions),
        "cache_hits": sum(d.cached for d in decider.decisions),
        "decisions": [[d.tag, d.key, d.choice] for d in decider.decisions],
    }
    (run_dir / "round2.json").write_text(json.dumps(output, indent=1, default=str) + "\n")
    for name in ("W3", "W3b"):
        a = write[name]
        print(f"{name}: retired {a['retired_correctly']}/10 wrongly {a['retired_wrongly']} "
              f"{[w['id'] for w in a['retired_wrongly_list']]} | calls/fact {a['calls_per_new_fact']['mean']:g} "
              f"max {a['calls_per_new_fact']['max']} | ms/fact {a['ms_per_new_fact']['mean']:g} | "
              f"PASS {a['WRITE_PASS']}")
    for fact_id, d in wrong_pairs.items():
        print(f"   wrong {fact_id} {d['triple']!r} by {d['by']} {d['by_text']!r}: {d['chosen']} p={d['probabilities']}")
    r = output["read"]
    print(f"R5: store r1 {r['chat_store']['rank1']}/13 top3 {r['chat_store']['top3']} | ingest r1 "
          f"{r['chat_ingest']['rank1']} top3 {r['chat_ingest']['top3']} | hops r1 {r['hops']['rank1']} both5 "
          f"{r['hops']['both_in_top5']} | churn r1 {r['churn10']['rank1']}/18 | flood r1 {r['flood']['top5']} | "
          f"unknown {r['unknown_right']['of_7']}/7 false {r['false_unknown_total']} | calls "
          f"{r['calls_per_question']['median']:g} | ms {r['added_ms_per_question']['median']:g} | "
          f"PASS {r['READ_PASS']}")
    print(f"   false unknowns: {r['false_unknown']}")
    stops: dict[str, list[float]] = {}
    for row in rows:
        stops.setdefault(row["stopped"], []).append(row["added_ms"])
    for stop, values in sorted(stops.items()):
        print(f"   stopped {stop}: {len(values)} questions, median {round(statistics.median(values), 1)} ms")
    print(f"wall: write {write_wall:.0f}s, read {read_wall:.0f}s, model calls {output['model_calls']}, "
          f"cache hits {output['cache_hits']}")


if __name__ == "__main__":
    main()
