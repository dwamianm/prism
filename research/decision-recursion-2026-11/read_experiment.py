"""Experiment R (read): PRME retrieves, a small decision model only judges.

For each question, PRME's top 10 (the engine built as tests/contract/harness.py builds it: product defaults,
memory_mode classic) is reordered by four arms:

- R0: PRME's order as is.
- R1: each record judged yes / partly / no; ordered yes, partly, no, ties by PRME rank; "unknown" when nothing is
  yes or partly.
- R2: R1, then while nothing is yes and fewer than 3 follow-ups ran: pick a partly record (not yet expanded),
  pick one of its content words (not in the question, at most 8, plus "none"), retrieve that word with PRME,
  judge its top 5 (records not judged yet) as in R1, merge after the records already judged. Stops at the first
  yes, after 3 follow-ups, or on "none".
- R3: R2, then when two or more records are yes: a newest-first tournament. The newest yes record is the
  champion; each older yes record is compared with it: "Do these two records give conflicting answers?" and,
  if so, "Which of these is true now?" with both dated; the loser is demoted below the partly records. Options
  are listed in R2's order, never by date, so the position of an option does not tell the model which is newer.

Choices made where the brief left room (also in the README):
- "Answer at rank 1" for a question with several answer strings: the rank-1 record holds at least one of them.
  "Answer in top 3": every answer string is in the top 3 (the contract's check).
- An arm that says "unknown" on an answerable question scores a miss on rank 1 and top 3 and no old-above-new.
- Robot answers match by exact sentence, as the contract tests do; chat answers by case-insensitive substring.
- Added milliseconds per question: the model time of every decision the arm needed (taken from the cache entry,
  so a prompt shared between questions is charged each time) plus PRME follow-up retrieves. R0 adds 0.

    uv run python research/decision-recursion-2026-11/read_experiment.py --backend ollama --model qwen3.5:4b \
        --run-dir research/decision-recursion-2026-11/results/<label>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from decide import Decider, loaded_models, ollama_identity, warm  # noqa: E402
from prompts import (OPTIONS, POSTHOC_NONE, POSTHOC_TEMPLATES, TEMPLATES, check_frozen,  # noqa: E402
                     check_posthoc_frozen, content_words, fmt_time)

from prme.storage.embedding import create_embedding_provider  # noqa: E402
from tests.contract import scenes, world  # noqa: E402
from tests.contract.harness import (  # noqa: E402
    RECORDING_NAME,
    ExtractionReplay,
    RobotScenes,
    Scene,
    no_network,
    product_config,
)

TOP_N = 10
FOLLOW_N = 5
MAX_DEPTH = 3
ARMS = ("R0", "R1", "R2", "R3", "R2b", "R4")


@dataclass
class Rec:
    id: str
    text: str
    time: datetime | None
    order: int          # position in the merged list: PRME rank for the first 10, then follow-ups in order


@dataclass
class Q:
    group: str
    id: str
    question: str
    scene: Scene
    answers: tuple[str, ...] = ()
    stale: tuple[str, ...] = ()
    exact: bool = False                 # robot sentences match exactly
    should_be_unknown: bool = False
    extra: dict = field(default_factory=dict)


def records(response, start: int = 0, n: int = TOP_N) -> list[Rec]:
    return [Rec(str(c.node.id), c.node.content, c.node.event_time, start + i)
            for i, c in enumerate(response.results[:n])]


def retrieve(scene: Scene, text: str):
    with no_network():
        return scene.run(scene.engine.retrieve(text, user_id=scene.user, reference_time=scene.now))


# Questions -----------------------------------------------------------------------------------------------------

def build_questions(provider) -> tuple[list[Q], list]:
    chat = world.CHAT
    robot_world = world.ROBOT
    built = []
    store = Scene(provider, user=chat.user, now=chat.now, stores=scenes.chat_messages(chat))
    built.append(store)
    recording = json.loads((ROOT / "tests/contract/fixtures" / RECORDING_NAME).read_text())
    ingest = Scene(provider, user=chat.user, now=chat.now, sessions=scenes.chat_sessions(chat),
                   replay=ExtractionReplay(recording))
    built.append(ingest)
    robot = RobotScenes(robot_world, provider, "classic")
    built.append(robot)

    qs: list[Q] = []
    for group, scene in (("chat_store", store), ("chat_ingest", ingest)):
        for q in chat.questions:
            qs.append(Q(group, f"{group}:{q.tag}", q.question, scene, q.answers, q.stale))
        cat = chat.unanswerable
        qs.append(Q(f"cat_{group.split('_')[1]}", f"{group}:{cat.tag}", cat.question, scene,
                    should_be_unknown=True))
    for case in scenes.unseen_cases(robot_world):
        (obj,) = case.data
        qs.append(Q("unseen", case.id, robot_world.where_now.format(obj=obj), robot.unknown, should_be_unknown=True))
    hop_scene, pairs = robot.hops
    for case in scenes.hop_cases(robot_world):
        (person,) = case.data
        request, placement = pairs[person]
        qs.append(Q("hops", case.id, robot_world.thing_asked.format(person=person), hop_scene, (placement,),
                    exact=True, extra={"request": request}))
    for case in scenes.churn_cases(robot_world):
        label, moves, obj = case.data
        if label != "10min":
            continue
        scene, truth = robot.churn(label, moves)
        newest = robot_world.placed.format(obj=obj, place=truth[obj])
        items, _, _ = scenes.churn(robot_world, dict(scenes.CHURN_SPACINGS)[label], moves)
        older = tuple(dict.fromkeys(i["content"] for i in items
                                    if i["content"] != newest and i["content"].startswith(f"The {obj} is on")))
        qs.append(Q("churn10", case.id, robot_world.where_now.format(obj=obj), scene, (newest,), older, exact=True))
    for case in scenes.rare_cases(robot_world):
        event, question = case.data
        qs.append(Q("flood", case.id, question, robot.flood, (event,), exact=True))
    return qs, built


# Arms ----------------------------------------------------------------------------------------------------------

def judge(decider: Decider, q: Q, rec: Rec, tag: str) -> str:
    options = OPTIONS["R1.judge"]
    index, _ = decider.decide(TEMPLATES["R1.judge"].format(question=q.question, record=rec.text), options, tag=tag)
    return options[index]


def by_label(recs: list[Rec], labels: dict[str, str]) -> list[Rec]:
    rank = {"yes": 0, "partly": 1, "no": 2}
    return sorted(recs, key=lambda r: (rank[labels[r.id]], r.order))


def cost(decisions) -> float:
    return sum(d.cost_ms for d in decisions)


def run_question(decider: Decider, q: Q) -> dict:
    top = records(q.scene.ask(q.question))
    row: dict = {"group": q.group, "id": q.id, "question": q.question, "arms": {}}

    # R0
    row["arms"]["R0"] = outcome(q, top, unknown=False, decisions=[], extra_ms=0.0)

    # R1
    mark = decider.mark()
    labels = {r.id: judge(decider, q, r, "R1.judge") for r in top}
    r1 = decider.since(mark)
    unknown = not any(v in ("yes", "partly") for v in labels.values())
    row["arms"]["R1"] = outcome(q, by_label(top, labels), unknown, r1, 0.0, labels)

    # R2
    mark = decider.mark()
    judged, labels2 = list(top), dict(labels)
    expanded: set[str] = set()
    depth, follow_ms, trace = 0, 0.0, []
    while depth < MAX_DEPTH and not any(v == "yes" for v in labels2.values()):
        partly = [r for r in judged if labels2[r.id] == "partly" and r.id not in expanded]
        if not partly:
            break
        pick, _ = decider.decide(TEMPLATES["R2.pick_record"].format(question=q.question),
                                 [r.text for r in partly], tag="R2.pick_record")
        rec = partly[pick]
        expanded.add(rec.id)
        options = content_words(rec.text, q.question) + ["none"]
        choice, _ = decider.decide(TEMPLATES["R2.pick_word"].format(question=q.question, record=rec.text),
                                   options, tag="R2.pick_word")
        depth += 1
        word = options[choice]
        step = {"depth": depth, "record": rec.text, "options": options, "word": word, "new": []}
        trace.append(step)
        if word == "none":
            break
        started = time.perf_counter()
        response = retrieve(q.scene, word)
        follow_ms += (time.perf_counter() - started) * 1000
        seen = {r.id for r in judged}
        fresh = [r for r in records(response, start=len(judged), n=FOLLOW_N) if r.id not in seen]
        for offset, r in enumerate(fresh):
            r.order = len(judged)
            judged.append(r)
            labels2[r.id] = judge(decider, q, r, "R2.judge")
            step["new"].append({"text": r.text[:120], "label": labels2[r.id]})
    r2_only = decider.since(mark)
    unknown2 = not any(v in ("yes", "partly") for v in labels2.values())
    order2 = by_label(judged, labels2)
    row["arms"]["R2"] = outcome(q, order2, unknown2, r1 + r2_only, follow_ms, labels2)
    row["arms"]["R2"]["trace"] = trace

    # R3
    mark = decider.mark()
    yes = [r for r in order2 if labels2[r.id] == "yes"]
    order3, tournament = order2, []
    if len(yes) >= 2:
        position = {r.id: i for i, r in enumerate(order2)}
        newest_first = sorted(yes, key=lambda r: (-(r.time.timestamp() if r.time else float("-inf")),
                                                  position[r.id]))
        champion, demoted = newest_first[0], set()
        for challenger in newest_first[1:]:
            a, b = sorted((champion, challenger), key=lambda r: position[r.id])
            fields = {"question": q.question, "date_a": fmt_time(a.time), "record_a": a.text,
                      "date_b": fmt_time(b.time), "record_b": b.text}
            conflict, _ = decider.decide(TEMPLATES["R3.conflict"].format(**fields), OPTIONS["R3.conflict"],
                                         tag="R3.conflict")
            match = {"a": a.text[:80], "b": b.text[:80], "conflict": bool(conflict)}
            if conflict:
                options = [f"({fmt_time(a.time)}) {a.text}", f"({fmt_time(b.time)}) {b.text}"]
                win, _ = decider.decide(TEMPLATES["R3.true_now"].format(question=q.question, now=fmt_time(q.scene.now)),
                                        options, tag="R3.true_now")
                winner, loser = (a, b) if win == 0 else (b, a)
                demoted.add(loser.id)
                champion = winner
                match["winner"] = winner.text[:80]
            tournament.append(match)
        rest_yes = [r for r in yes if r.id != champion.id and r.id not in demoted]
        partly = [r for r in order2 if labels2[r.id] == "partly"]
        no = [r for r in order2 if labels2[r.id] == "no"]
        order3 = [champion] + rest_yes + partly + [r for r in yes if r.id in demoted] + no
    r3_only = decider.since(mark)
    row["arms"]["R3"] = outcome(q, order3, unknown2, r1 + r2_only + r3_only, follow_ms, labels2)
    row["arms"]["R3"]["tournament"] = tournament

    # Post-hoc arms (designed by the lead after the first results; see the README).
    # R2b: recursion without "partly". After R1, while nothing is yes and fewer than 3 follow-ups ran: pick the
    # record most likely to lead to the answer among every judged record not picked before, plus "none of them";
    # then a content word, as in R2; retrieve it; judge the new top 5 with R1's prompt. "none of them" stops; a
    # word choice of "none" uses up that step without a retrieve. "Unknown" when no record ends up yes.
    mark = decider.mark()
    judged_b, labels_b = list(top), dict(labels)
    picked: set[str] = set()
    depth_b, follow_b, trace_b = 0, 0.0, []
    while depth_b < MAX_DEPTH and not any(v == "yes" for v in labels_b.values()):
        pool = [r for r in judged_b if r.id not in picked][:25]
        options = [r.text for r in pool] + [POSTHOC_NONE["R2b.pick_record"]]
        pick, _ = decider.decide(POSTHOC_TEMPLATES["R2b.pick_record"].format(question=q.question), options,
                                 tag="R2b.pick_record")
        depth_b += 1
        if pick == len(pool):
            trace_b.append({"depth": depth_b, "record": "none of them"})
            break
        rec = pool[pick]
        picked.add(rec.id)
        words = content_words(rec.text, q.question) + ["none"]
        choice, _ = decider.decide(TEMPLATES["R2.pick_word"].format(question=q.question, record=rec.text), words,
                                   tag="R2b.pick_word")
        step = {"depth": depth_b, "record": rec.text, "options": words, "word": words[choice], "new": []}
        trace_b.append(step)
        if words[choice] == "none":
            continue
        started = time.perf_counter()
        response = retrieve(q.scene, words[choice])
        follow_b += (time.perf_counter() - started) * 1000
        seen = {r.id for r in judged_b}
        for r in [r for r in records(response, start=len(judged_b), n=FOLLOW_N) if r.id not in seen]:
            r.order = len(judged_b)
            judged_b.append(r)
            labels_b[r.id] = judge(decider, q, r, "R2b.judge")
            step["new"].append({"text": r.text[:120], "label": labels_b[r.id]})
    r2b_only = decider.since(mark)
    unknown_b = not any(v == "yes" for v in labels_b.values())
    row["arms"]["R2b"] = outcome(q, by_label(judged_b, labels_b), unknown_b, r1 + r2b_only, follow_b, labels_b)
    row["arms"]["R2b"]["trace"] = trace_b

    # R4: a cheap gate. Judge PRME's rank 1, then rank 2, then rank 3, stopping at the first yes (the judgments
    # are R1's, same prompts, so no new call is made here; only the ones the gate needs are charged). If none of
    # the top 3 is yes, R4 is R2b (R1 on all 10, then the recursion), charged in full.
    gate = next((k for k in range(min(3, len(top))) if labels[top[k].id] == "yes"), None)
    if gate is not None:
        order4 = [top[gate]] + [r for r in top if r.id != top[gate].id]
        row["arms"]["R4"] = outcome(q, order4, False, r1[:gate + 1], 0.0, {r.id: labels[r.id] for r in top[:gate + 1]})
        row["arms"]["R4"]["path"] = f"gate at rank {gate + 1}"
    else:
        row["arms"]["R4"] = {**row["arms"]["R2b"], "path": "R2b"}
    return row


# Scoring -------------------------------------------------------------------------------------------------------

def hit(q: Q, text: str, needle: str) -> bool:
    return text == needle if q.exact else needle.lower() in text.lower()


def rank_of(q: Q, order: list[Rec], needle: str) -> int | None:
    return next((i for i, r in enumerate(order, start=1) if hit(q, r.text, needle)), None)


def outcome(q: Q, order: list[Rec], unknown: bool, decisions, extra_ms: float, labels: dict | None = None) -> dict:
    answerable = bool(q.answers)
    ranks = {a: rank_of(q, order, a) for a in q.answers}
    stale = {s: rank_of(q, order, s) for s in q.stale}
    worst = max((r or 999) for r in ranks.values()) if ranks else None
    shown = not unknown
    result = {
        "unknown": unknown,
        "rank1": shown and answerable and any(hit(q, order[0].text, a) for a in q.answers) if order else False,
        "top3": shown and answerable and all(r is not None and r <= 3 for r in ranks.values()),
        "top5": shown and answerable and all(r is not None and r <= 5 for r in ranks.values()),
        "stale_above": shown and bool(q.stale) and any(r is not None and r < worst for r in stale.values()),
        "answer_ranks": ranks,
        "calls": len(decisions),
        "added_ms": round(sum(d.cost_ms for d in decisions) + extra_ms, 1),
        "follow_ms": round(extra_ms, 1),
        "order": [{"text": r.text[:120], "label": (labels or {}).get(r.id)} for r in order[:5]],
    }
    if "request" in q.extra:
        top5 = [r.text for r in order[:5]] if shown else []
        result["both_in_top5"] = q.extra["request"] in top5 and q.answers[0] in top5
    return result


def summarize(rows: list[dict]) -> dict:
    summary = {}
    for arm in ARMS:
        def group(name):
            return [r["arms"][arm] for r in rows if r["group"] == name]

        def count(name, key):
            return sum(bool(o.get(key)) for o in group(name))

        answerable_groups = ("chat_store", "chat_ingest", "hops", "churn10", "flood")
        all_rows = [r["arms"][arm] for r in rows]
        added = sorted(o["added_ms"] for o in all_rows)
        calls = [o["calls"] for o in all_rows]
        summary[arm] = {
            "chat_store": {"n": len(group("chat_store")), "rank1": count("chat_store", "rank1"),
                           "top3": count("chat_store", "top3"), "stale_above": count("chat_store", "stale_above"),
                           "false_unknown": count("chat_store", "unknown")},
            "chat_ingest": {"n": len(group("chat_ingest")), "rank1": count("chat_ingest", "rank1"),
                            "top3": count("chat_ingest", "top3"), "stale_above": count("chat_ingest", "stale_above"),
                            "false_unknown": count("chat_ingest", "unknown")},
            "hops": {"n": len(group("hops")), "rank1": count("hops", "rank1"), "top3": count("hops", "top3"),
                     "both_in_top5": count("hops", "both_in_top5"), "false_unknown": count("hops", "unknown")},
            "churn10": {"n": len(group("churn10")), "rank1": count("churn10", "rank1"),
                        "top3": count("churn10", "top3"), "stale_above": count("churn10", "stale_above"),
                        "false_unknown": count("churn10", "unknown")},
            "flood": {"n": len(group("flood")), "rank1": count("flood", "rank1"), "top3": count("flood", "top3"),
                      "top5": count("flood", "top5"), "false_unknown": count("flood", "unknown")},
            "unknown_right": {"cat_store": count("cat_store", "unknown"), "unseen": count("unseen", "unknown"),
                              "of_7": count("cat_store", "unknown") + count("unseen", "unknown"),
                              "cat_ingest": count("cat_ingest", "unknown")},
            "false_unknown_total": sum(count(g, "unknown") for g in answerable_groups),
            "answerable_total": sum(len(group(g)) for g in answerable_groups),
            "calls_per_question": {"median": statistics.median(calls), "max": max(calls)},
            "added_ms_per_question": {"median": round(statistics.median(added), 1),
                                      "p95": round(added[min(len(added) - 1, int(0.95 * len(added)))], 1)},
        }
    base = summary["R0"]["chat_store"]["rank1"]
    for arm in ARMS:
        s = summary[arm]
        gain = s["chat_store"]["rank1"] - base
        bars = {
            "unknown_right_at_least_6_of_7": s["unknown_right"]["of_7"] >= 6,
            "false_unknowns_at_most_1": s["false_unknown_total"] <= 1,
            "rank1_chat_store_gain_at_least_4": gain >= 4,
            "median_added_ms_at_most_300": s["added_ms_per_question"]["median"] <= 300,
        }
        s["rank1_chat_store_gain_over_R0"] = gain
        s["bars"] = bars
        s["READ_PASS"] = all(bars.values())
    return summary


# Main ----------------------------------------------------------------------------------------------------------

def git_state() -> dict:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "src"], cwd=ROOT, capture_output=True, text=True).stdout
    return {"head": head, "src_clean": not dirty.strip()}


def make_provider():
    directory = tempfile.mkdtemp(prefix="decision-recursion-")
    provider = create_embedding_provider(product_config(directory).embedding)
    with no_network():
        asyncio.run(provider.embed(["warm up"]))
    return provider


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("ollama", "random", "systemone"), required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--cache-dir", default=None, help="default: <run-dir>/cache (a cold cache for a new run dir)")
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    templates = check_frozen()
    posthoc = check_posthoc_frozen()
    decider = Decider(args.backend, args.model, Path(args.cache_dir or run_dir / "cache"),
                      run_dir / "read_decisions.jsonl", seed=args.seed)
    started = time.perf_counter()
    provider = make_provider()
    qs, built = build_questions(provider)
    # Ask every question once with PRME before any decision, so the packs' first-retrieve work is not timed.
    for q in qs:
        q.scene.ask(q.question)
    build_s = time.perf_counter() - started
    loaded_before = loaded_models() if args.backend != "random" else []
    warm_ms = warm(decider)
    rows = []
    for q in qs:
        rows.append(run_question(decider, q))
    run_s = time.perf_counter() - started
    for scene in built:
        scene.close()
    summary = summarize(rows)
    result = {
        "experiment": "R", "backend": args.backend, "model": args.model, "seed": args.seed,
        **ollama_identity(args.model), "templates_sha256": templates, "posthoc_sha256": posthoc, **git_state(),
        "loaded_before_warm": loaded_before, "warm_ms": warm_ms,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "build_seconds": round(build_s, 1), "wall_seconds": round(run_s, 1),
        "model_calls": sum(not d.cached for d in decider.decisions),
        "cache_hits": sum(d.cached for d in decider.decisions),
        "summary": summary, "rows": rows,
        "decisions": [[d.tag, d.key, d.choice] for d in decider.decisions],
    }
    (run_dir / "read.json").write_text(json.dumps(result, indent=1, default=str) + "\n")
    for arm in ARMS:
        s = summary[arm]
        print(f"{arm}: store r1 {s['chat_store']['rank1']}/13 top3 {s['chat_store']['top3']} | ingest r1 "
              f"{s['chat_ingest']['rank1']} top3 {s['chat_ingest']['top3']} | hops r1 {s['hops']['rank1']} | churn r1 "
              f"{s['churn10']['rank1']}/18 | flood r1 {s['flood']['rank1']} | unknown {s['unknown_right']['of_7']}/7 "
              f"false {s['false_unknown_total']}/{s['answerable_total']} | calls med {s['calls_per_question']['median']}"
              f" | ms med {s['added_ms_per_question']['median']} p95 {s['added_ms_per_question']['p95']} | "
              f"PASS {s['READ_PASS']}")
    print(f"wall {run_s:.0f}s (build {build_s:.0f}s), model calls {result['model_calls']}, cache hits "
          f"{result['cache_hits']}")


if __name__ == "__main__":
    main()
