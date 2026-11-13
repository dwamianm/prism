"""Offline check and timing of a seed reach count computed only for the graph candidates.

    cd ~/Sites/prism-seed-reach && .venv/bin/python /tmp/task26/probes/micro.py /tmp/task26/results/dump-m3.pickle

The current walk (``_seed_reach``) builds the adjacency of every reached node and walks from every seed, then the
stage reads the result for its 150 candidates only. Shortest paths whose inner nodes are not owner entities are
the same read from either end, so for max hops 3 a seed s reaches a candidate t at:

- 1 when t is a neighbor of s;
- 2 when a neighbor of both is not an owner;
- 3 when a non-owner neighbor of s links to a non-owner neighbor of t.

So two passes over the same links are enough: the first finds the neighbors of the seeds and of the candidates,
the second the links between those two neighbor sets.
"""
import pickle, statistics, sys, time
from collections import defaultdict


def current(seeds, known, owners, links, max_hops):
    adjacency = defaultdict(set)
    for source, target in links:
        if source in known and target in known:
            adjacency[source].add(target)
            adjacency[target].add(source)
    hops = defaultdict(dict)
    for seed_id in seeds:
        seen, frontier = {seed_id}, [seed_id]
        for depth in range(1, max_hops + 1):
            reached = []
            for node_id in frontier:
                if node_id in owners and node_id != seed_id:
                    continue
                for other in adjacency[node_id]:
                    if other not in seen:
                        seen.add(other)
                        reached.append(other)
                        hops[other][seed_id] = depth
            frontier = reached
    return {n: (len(b), sum(b.values()), min(b.values())) for n, b in hops.items()}


def targeted(seeds, known, owners, links, max_hops, targets):
    seed_set = set(seeds)
    target_set = set(targets) & known
    near_seed = defaultdict(set)    # node -> the seeds it is a neighbor of
    near_target = defaultdict(set)  # target -> its neighbors
    for source, target in links:
        if source in known and target in known:
            if source in seed_set:
                near_seed[target].add(source)
            if target in seed_set:
                near_seed[source].add(target)
            if source in target_set:
                near_target[source].add(target)
            if target in target_set:
                near_target[target].add(source)
    between = defaultdict(set)  # non-owner neighbor of a target -> non-owner neighbors of seeds it links to
    if max_hops >= 3:
        seed_side = {node for node in near_seed if node not in owners}
        target_side = {node for nodes in near_target.values() for node in nodes if node not in owners}
        for source, target in links:
            if source in seed_side and target in target_side:
                between[target].add(source)
            if target in seed_side and source in target_side:
                between[source].add(target)
    result = {}
    for node in target_set:
        best = {}
        for other in near_target.get(node, ()):
            if other in seed_set and other != node and max_hops >= 1:
                best[other] = 1
        if max_hops >= 2:
            for middle in near_target.get(node, ()):
                if middle in owners:
                    continue
                for seed in near_seed.get(middle, ()):
                    if seed != node and seed not in best:
                        best[seed] = 2
        if max_hops >= 3:
            for middle in near_target.get(node, ()):
                if middle in owners:
                    continue
                for first in between.get(middle, ()):
                    for seed in near_seed[first]:
                        if seed != node and seed not in best:
                            best[seed] = 3
        if best:
            result[node] = (len(best), sum(best.values()), min(best.values()))
    return result


def main(path):
    items = pickle.load(open(path, "rb"))
    rows = []
    for item in items:
        if item["result"] is None:
            continue
        seeds, known, owners = item["seeds"], set(item["known"]), set(item["owners"])
        links, max_hops, targets = item["links"], item["max_hops"], item["targets"]
        full = current(seeds, known, owners, links, max_hops)
        assert full == item["result"], "replayed current walk differs from the saved result"
        # Equal on every candidate, and on every known node when every known node is a target.
        mine = targeted(seeds, known, owners, links, max_hops, targets)
        assert {t: full.get(t) for t in targets} == {t: mine.get(t) for t in targets}
        assert targeted(seeds, known, owners, links, max_hops, known) == full
        for hops in (1, 2):
            assert targeted(seeds, known, owners, links, hops, known) == current(seeds, known, owners, links, hops)
        # Owners: mark a few hubs and seeds as owners to exercise the rule (no m3 node is one).
        degree = defaultdict(int)
        for s, t in links:
            degree[s] += 1
            degree[t] += 1
        fake = set(sorted(known, key=lambda n: (-degree[n], n))[:5]) | set(seeds[:2])
        assert targeted(seeds, known, fake, links, max_hops, known) == current(seeds, known, fake, links, max_hops)
        times = {"current": [], "targeted": []}
        for _ in range(7):
            started = time.perf_counter()
            current(seeds, known, owners, links, max_hops)
            times["current"].append(1000 * (time.perf_counter() - started))
            started = time.perf_counter()
            targeted(seeds, known, owners, links, max_hops, targets)
            times["targeted"].append(1000 * (time.perf_counter() - started))
        rows.append((item["question"], len(seeds), len(links), statistics.median(times["current"]),
                     statistics.median(times["targeted"])))
        print(f"{item['question'][:36]:36} seeds {len(seeds):3} links {len(links):6} "
              f"current {rows[-1][3]:6.1f} ms targeted {rows[-1][4]:6.1f} ms", flush=True)
    print("median current", round(statistics.median(r[3] for r in rows), 1),
          "targeted", round(statistics.median(r[4] for r in rows), 1))


if __name__ == "__main__":
    main(sys.argv[1])
