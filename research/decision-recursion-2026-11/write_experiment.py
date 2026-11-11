"""Experiment W (write): can a small decision model decide which stored facts to retire?

Facts are the chat's claims as extracted in tests/contract/fixtures/chat_extraction.json (subject, predicate,
object, polarity), each dated with its turn's time from tests/contract/world.py (the fixture has no time field;
its turns are matched to the chat by their text). They are processed in that order, which is time order.

For each new fact, the candidates are the facts currently live whose subject is the new fact's subject entity or
whose object is that entity (a dict, no engine). Entities match after lower-casing and dropping a leading "a",
"an", "the" or "my". Facts from the new fact's own turn are never candidates: one utterance does not retire
its own parts.

- W0: nothing retired.
- W1: for each candidate, "After the new statement, is the old statement still true?" [still true, no longer
  true, cannot tell]; retire on "no longer true".
- W2: W1, then for every fact just retired, its other live neighbors (the same rule, around the retired fact's
  subject, still excluding the new fact's turn) are asked "This statement depended on one that is no longer
  true; is it still true?", up to depth 2 (neighbors of neighbors that were retired at depth 1).

Scored against write_answer_key.json, which was written before any model ran.

    uv run python research/decision-recursion-2026-11/write_experiment.py --backend ollama --model qwen3.5:4b \
        --run-dir research/decision-recursion-2026-11/results/<label>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from decide import Decider, loaded_models, ollama_identity, warm  # noqa: E402
from prompts import (OPTIONS, POSTHOC_NONE, POSTHOC_TEMPLATES, TEMPLATES, check_frozen,  # noqa: E402
                     check_posthoc_frozen, fmt_time)

from tests.contract import scenes, world  # noqa: E402

KEY_PATH = HERE / "write_answer_key.json"
KEY_SHA256 = "790c905303167268f66d43e7af5c2ca10619c6f016d9da69c447975e45675b16"
MAX_DEPTH = 2
ARMS = ("W0", "W1", "W2", "W2b")


@dataclass(frozen=True)
class Fact:
    id: str
    subject: str
    predicate: str
    object: str
    polarity: str
    time: datetime
    turn: tuple[int, int]       # (call, turn): facts of one turn share it
    relation: str | None = None  # the fixture's typed relation, when extraction gave one

    @property
    def triple(self) -> str:
        return f"{self.subject} | {self.predicate} | {self.object} | {self.polarity}"

    def text(self) -> str:
        said = f"{self.subject} {self.predicate.replace('_', ' ')} {self.object}"
        return f"It is not true that {said}" if self.polarity == "negative" else said


def norm(entity: str) -> str:
    entity = entity.lower().strip()
    for prefix in ("a ", "an ", "the ", "my "):
        if entity.startswith(prefix):
            entity = entity[len(prefix):]
    return entity


def load_facts() -> list[Fact]:
    recording = json.loads((ROOT / "tests/contract/fixtures/chat_extraction.json").read_text())
    times = {m["content"]: m["event_time"] for m in scenes.chat_messages(world.CHAT)}
    facts = []
    for call_index, call in enumerate(recording["calls"]):
        if call["method"] != "extract_batch":
            raise SystemExit(f"unexpected call {call['method']!r} in the recording")
        contents = {t["index"]: t["content"] for t in call["turns"]}
        for entry in call["response"]["turns"]:
            content = contents[entry["turn"]]
            if content not in times:
                raise SystemExit(f"a recorded turn is not in the chat: {content!r}")
            for fact in entry["facts"]:
                for name in ("subject", "predicate", "object", "polarity"):
                    if not fact.get(name):
                        raise SystemExit(f"a recorded fact lacks {name}: {fact}")
                facts.append(Fact(f"F{len(facts) + 1:02d}", fact["subject"], fact["predicate"], fact["object"],
                                  fact["polarity"], times[content], (call_index, entry["turn"]),
                                  fact.get("relation")))
    return facts


def load_key(facts: list[Fact]) -> dict:
    raw = KEY_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != KEY_SHA256:
        raise SystemExit(f"the answer key changed after it was frozen: {digest}")
    key = json.loads(raw)
    if set(key["facts"]) != {f.id for f in facts}:
        raise SystemExit("the key's fact ids do not match the recording")
    for f in facts:
        if key["facts"][f.id]["triple"] != f.triple:
            raise SystemExit(f"the key's {f.id} is {key['facts'][f.id]['triple']!r}, the recording's is {f.triple!r}")
    return key


def neighbors(live: list[Fact], around: Fact, exclude_turn: tuple[int, int]) -> list[Fact]:
    entity = norm(around.subject)
    return [f for f in live if f.id != around.id and f.turn != exclude_turn
            and (norm(f.subject) == entity or norm(f.object) == entity)]


def simulate(decider: Decider, facts: list[Fact], recursive: bool, by_dependency: bool = False) -> dict:
    """W1; with ``recursive``, W2; with ``by_dependency``, W2b (post-hoc, see ``dependency_pass``)."""
    live: list[Fact] = []
    retired: dict[str, dict] = {}
    per_fact = []
    for new in facts:
        mark = decider.mark()
        just = []
        before = list(live)
        unsure = []
        for old in neighbors(live, new, new.turn):
            fields = {"date_old": fmt_time(old.time), "old": old.text(), "date_new": fmt_time(new.time),
                      "new": new.text()}
            choice, confidence = decider.decide(TEMPLATES["W1.still_true"].format(**fields), OPTIONS["W1.still_true"],
                                                tag="W1")
            if choice == 1:
                retired[old.id] = {"by": new.id, "stage": "W1", "confidence": confidence}
                just.append(old)
            elif choice == 2:
                unsure.append(old)
        live = [f for f in live if f.id not in retired]
        if by_dependency:
            dependency_pass(decider, new, before, unsure, just, retired)
            live = [f for f in live if f.id not in retired]
        depth = 0
        while recursive and just and depth < MAX_DEPTH:
            depth += 1
            following = []
            for gone in just:
                for check in neighbors(live, gone, new.turn):
                    if check.id in retired:
                        continue
                    fields = {"date_retired": fmt_time(gone.time), "retired": gone.text(),
                              "date_new": fmt_time(new.time), "new": new.text(),
                              "date_check": fmt_time(check.time), "check": check.text()}
                    choice, confidence = decider.decide(TEMPLATES["W2.dependent"].format(**fields),
                                                        OPTIONS["W2.dependent"], tag=f"W2.depth{depth}")
                    if choice == 1:
                        retired[check.id] = {"by": new.id, "via": gone.id, "stage": f"W2.depth{depth}",
                                             "confidence": confidence}
                        following.append(check)
                live = [f for f in live if f.id not in retired]
            just = following
        live.append(new)
        made = decider.since(mark)
        per_fact.append({"id": new.id, "calls": len(made), "cost_ms": round(sum(d.cost_ms for d in made), 1)})
    return {"retired": retired, "per_fact": per_fact}


def dependency_pass(decider: Decider, new: Fact, before: list[Fact], unsure: list[Fact], just: list[Fact],
                    retired: dict) -> None:
    """W2b, post-hoc (designed by the lead after the first results): recursion by dependency, not by retirement.

    For each candidate W1 answered "cannot tell", ask which earlier statement it depends on: its neighbors among
    the facts live before this step (W1's neighbor rule around its subject, stated no later than it, not from the
    new fact's turn), at most the 25 most recent, plus "none". The question is asked once per candidate and only
    when this step retired something. If the chosen statement was retired in this step, W1 is asked again about
    the candidate with that statement and the new one shown. Depth 2: a candidate retired that way can in turn
    be the chosen statement of another.
    """
    frontier = {f.id for f in just}
    depends: dict[str, str | None] = {}
    depth = 0
    while frontier and depth < MAX_DEPTH:
        depth += 1
        following = set()
        for check in unsure:
            if check.id in retired:
                continue
            if check.id not in depends:
                pool = [f for f in neighbors(before, check, new.turn) if f.time <= check.time][-25:]
                options = [f"({fmt_time(f.time)}) {f.text()}" for f in pool] + [POSTHOC_NONE["W2b.depends_on"]]
                choice, _ = decider.decide(POSTHOC_TEMPLATES["W2b.depends_on"].format(
                    date_check=fmt_time(check.time), check=check.text()), options, tag="W2b.depends_on")
                depends[check.id] = pool[choice].id if choice < len(pool) else None
            dep = depends[check.id]
            if dep is None or dep not in frontier:
                continue
            gone = next(f for f in before if f.id == dep)
            fields = {"date_old": fmt_time(check.time), "old": check.text(), "date_dep": fmt_time(gone.time),
                      "dep": gone.text(), "date_new": fmt_time(new.time), "new": new.text()}
            choice, confidence = decider.decide(POSTHOC_TEMPLATES["W2b.recheck"].format(**fields),
                                                OPTIONS["W1.still_true"], tag=f"W2b.recheck.depth{depth}")
            if choice == 1:
                retired[check.id] = {"by": new.id, "via": dep, "stage": f"W2b.depth{depth}",
                                     "confidence": confidence}
                following.add(check.id)
        frontier = following


def score(sim: dict, facts: list[Fact], key: dict) -> dict:
    should = set(key["summary"]["retired"])
    did = set(sim["retired"])
    by_id = {f.id: f for f in facts}
    calls = [p["calls"] for p in sim["per_fact"]]
    cost = sorted(p["cost_ms"] for p in sim["per_fact"])
    wrong = sorted(did - should)
    result = {
        "retired_correctly": len(did & should), "of_key": len(should),
        "retired_correctly_ids": sorted(did & should),
        "missed": [{"id": i, "triple": by_id[i].triple} for i in sorted(should - did)],
        "retired_wrongly": len(wrong),
        "retired_wrongly_list": [{"id": i, "triple": by_id[i].triple, **sim["retired"][i],
                                  "by_triple": by_id[sim["retired"][i]["by"]].triple,
                                  "contestable": bool(key["facts"][i].get("contestable"))} for i in wrong],
        "calls_per_new_fact": {"mean": round(statistics.mean(calls), 2), "median": statistics.median(calls),
                               "max": max(calls), "total": sum(calls)},
        "ms_per_new_fact": {"mean": round(statistics.mean(cost), 1), "median": round(statistics.median(cost), 1),
                            "p95": cost[min(len(cost) - 1, int(0.95 * len(cost)))], "total": round(sum(cost), 1)},
        "retirements": {i: {**v, "triple": by_id[i].triple} for i, v in sorted(sim["retired"].items())},
    }
    result["WRITE_PASS"] = result["retired_correctly"] >= 5 and result["retired_wrongly"] == 0
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("ollama", "random", "systemone"), required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--cache-dir", default=None, help="default: <run-dir>/cache")
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    templates = check_frozen()
    posthoc = check_posthoc_frozen()
    facts = load_facts()
    key = load_key(facts)
    decider = Decider(args.backend, args.model, Path(args.cache_dir or run_dir / "cache"),
                      run_dir / "write_decisions.jsonl", seed=args.seed)
    loaded_before = loaded_models() if args.backend != "random" else []
    warm_ms = warm(decider)
    started = time.perf_counter()
    arms = {"W0": score({"retired": {}, "per_fact": [{"id": f.id, "calls": 0, "cost_ms": 0.0} for f in facts]},
                        facts, key)}
    arms["W1"] = score(simulate(decider, facts, recursive=False), facts, key)
    arms["W2"] = score(simulate(decider, facts, recursive=True), facts, key)
    arms["W2b"] = score(simulate(decider, facts, recursive=False, by_dependency=True), facts, key)
    wall = time.perf_counter() - started
    result = {
        "experiment": "W", "backend": args.backend, "model": args.model, "seed": args.seed,
        **ollama_identity(args.model), "templates_sha256": templates, "posthoc_sha256": posthoc, "key_sha256": KEY_SHA256,
        "loaded_before_warm": loaded_before, "warm_ms": warm_ms,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "wall_seconds": round(wall, 1),
        "facts": len(facts), "model_calls": sum(not d.cached for d in decider.decisions),
        "cache_hits": sum(d.cached for d in decider.decisions), "arms": arms,
        "decisions": [[d.tag, d.key, d.choice] for d in decider.decisions],
    }
    (run_dir / "write.json").write_text(json.dumps(result, indent=1, default=str) + "\n")
    for arm in ARMS:
        a = arms[arm]
        print(f"{arm}: retired correctly {a['retired_correctly']}/{a['of_key']} wrongly {a['retired_wrongly']} "
              f"{[w['id'] for w in a['retired_wrongly_list']]} | calls/fact mean {a['calls_per_new_fact']['mean']} "
              f"max {a['calls_per_new_fact']['max']} | ms/fact mean {a['ms_per_new_fact']['mean']} | PASS {a['WRITE_PASS']}")
    print(f"wall {wall:.0f}s, model calls {result['model_calls']}, cache hits {result['cache_hits']}")


if __name__ == "__main__":
    main()
