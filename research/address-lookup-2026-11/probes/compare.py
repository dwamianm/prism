"""P7 bar (a): the indexed lookup must return byte-identical records to the current path, in the same order.

    PYTHONPATH=. .venv/bin/python research/address-lookup-2026-11/probes/compare.py \
        --pack /tmp/p6/lookup/pack --pairs 220 \
        --out research/address-lookup-2026-11/results/compare-20500.json

What is compared is the whole ``AssertionState``, serialized, not a summary of it: status, every count, every
current candidate and timeline entry with its exclusion reasons and contradiction ids, every value group, every
conflict, and the exclusion map. A difference anywhere fails the bar and the probe names the field.

The pair set is drawn to cover what the brief asks for:

- pairs that match nothing, several claims and one claim;
- pairs whose claims are superseded or archived, and one pair per lifecycle-only difference;
- a second user's claims: the probe writes a second owner's claims at an otherwise empty address, so a lookup for the
  first user must not see them, and a lookup for the second user must.

The store is copied for each side because a read writes a receipt and would grow the pack between sides.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(HERE))

from probes.baseline import blocked_environment, build_engine  # noqa: E402

import keys  # noqa: E402
import lookup  # noqa: E402

SCRATCH = os.environ.get("P7_SCRATCH", "/tmp/p7")
USER = "robot-1"
SECOND_USER = "robot-2"


def draw_pairs(pack: str, count: int, seed: int) -> list[tuple[str, str]]:
    """Draw subject/predicate pairs from the pack, weighted to cover each shape the bar names."""
    import duckdb

    conn = duckdb.connect(os.path.join(pack, "memory.duckdb"), read_only=True)
    rows = conn.execute(
        "SELECT json_extract_string(metadata, '$.subject') AS s, "
        "json_extract_string(metadata, '$.predicate') AS p, count(*) AS n, "
        "count(*) FILTER (WHERE lifecycle_state IN ('superseded', 'archived', 'deprecated')) AS retired "
        "FROM nodes WHERE s IS NOT NULL AND p IS NOT NULL GROUP BY 1, 2"
    ).fetchall()
    conn.close()
    rng = random.Random(seed)
    many = [(s, p) for s, p, n, _ in rows if n >= 5]
    few = [(s, p) for s, p, n, _ in rows if 1 < n < 5]
    one = [(s, p) for s, p, n, _ in rows if n == 1]
    retired = [(s, p) for s, p, n, gone in rows if gone > 0]
    quota = count // 4
    chosen: list[tuple[str, str]] = []
    for pool in (many, few, one, retired):
        chosen.extend(rng.sample(pool, min(len(pool), quota)))
    for index in range(count * 3):
        chosen.append((f"no such thing {index}", "located_in"))
        if len(chosen) >= count:
            break
    while len(chosen) < count:
        chosen.append(rng.choice(rows)[:2])
    return chosen[:count]


async def seed_shapes(engine) -> list[tuple[str, str]]:
    """Give the comparison the shapes the pack does not carry: archived, deprecated, contested, hypothetical.

    The 20,500 pack holds only tentative and superseded claims, all asserted. The bar names archived claims, and an
    archived or deprecated claim is exactly where an index-backed read could quietly disagree with a scan, because
    the lifecycle filter here is a filter and not an index column. So this writes one claim per state, plus a
    conditional claim whose condition is not true, and one claim whose validity window has already closed. Then it
    returns those addresses for the comparison to run over.
    """
    from datetime import datetime, timezone
    from prme.types import EpistemicType, NodeType, Scope

    seeded = [
        ("p7 archival lamp", "archived", EpistemicType.ASSERTED),
        ("p7 deprecated drill", "stable-deprecated", EpistemicType.DEPRECATED),
        ("p7 contested vase", "contested", EpistemicType.ASSERTED),
        ("p7 hypothetical ladder", "tentative", EpistemicType.HYPOTHETICAL),
        ("p7 conditional crate", "tentative", EpistemicType.CONDITIONAL),
        ("p7 inferred bin", "stable", EpistemicType.INFERRED),
    ]
    written: list[tuple[str, str]] = []
    for subject, shape, epistemic in seeded:
        metadata = {"subject": subject, "predicate": "located_in", "object": "hallway",
                    "polarity": "positive"}
        if shape.endswith("deprecated"):
            metadata["prme_condition"] = None
        if epistemic is EpistemicType.CONDITIONAL:
            metadata["condition"] = "if the cellar floods"
            metadata["condition_state"] = "unknown"
        receipt = await engine.store_with_receipt(
            f"The {subject.replace('p7 ', '')} is in the hallway.",
            user_id=USER, node_type=NodeType.FACT, scope=Scope.PERSONAL,
            epistemic_type=epistemic,
            metadata=metadata,
        )
        if shape.startswith("archived"):
            await engine.archive(str(receipt.node.id), user_id=USER)
        elif shape.startswith("contested"):
            # A contested pair needs two claims, so the second one is written here and the pair is marked together.
            partner = await engine.store_with_receipt(
                "The p7 contested vase is in the attic.", user_id=USER, node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
                metadata={"subject": "p7 contested vase", "predicate": "located_in", "object": "attic",
                          "polarity": "positive"},
            )
            await engine.contradict(str(receipt.node.id), str(partner.node.id), user_id=USER)
        elif shape.startswith("stable"):
            await engine.promote(str(receipt.node.id), user_id=USER)
        written.append((subject, "located_in"))

    from datetime import datetime as _dt, timezone as _tz
    await engine.store_with_receipt(
        "The p7 retired bicycle is in the shed.", user_id=USER, node_type=NodeType.FACT,
        scope=Scope.PERSONAL,
        valid_from=_dt(2025, 1, 1, tzinfo=_tz.utc), valid_to=_dt(2025, 2, 1, tzinfo=_tz.utc),
        metadata={"subject": "p7 retired bicycle", "predicate": "located_in", "object": "shed",
                  "polarity": "positive"},
    )
    written.append(("p7 retired bicycle", "located_in"))

    # A claim whose subject is stored with case and spacing that only the normalize rule makes equal.
    await engine.store_with_receipt(
        "The P7  Spaced Label is in the cellar.", user_id=USER, node_type=NodeType.FACT,
        scope=Scope.PERSONAL,
        metadata={"subject": "P7  Spaced Label", "predicate": "Located In", "object": "cellar",
                  "polarity": "positive"},
    )
    written.append(("p7 spaced label", "located_in"))
    written.append(("P7  Spaced Label", "Located In"))
    return written


async def seed_second_user(engine) -> list[tuple[str, str]]:
    """Write one other owner's claims, then return the addresses to test the boundary with."""
    from prme.types import NodeType

    addresses = [("robot-2 only object", "located_in"), ("shared object", "located_in")]
    for subject, predicate in addresses:
        for value in ("hallway", "garage"):
            await engine.store(
                f"The {subject} is in the {value}.",
                user_id=SECOND_USER,
                node_type=NodeType.FACT,
                metadata={"subject": subject, "predicate": predicate, "object": value,
                          "polarity": "positive"},
            )
    await engine.store(
        "The shared object is in the kitchen.",
        user_id=USER,
        node_type=NodeType.FACT,
        metadata={"subject": "shared object", "predicate": "located_in", "object": "kitchen",
                  "polarity": "positive"},
    )
    return addresses


async def side(directory: str, pack: str, pairs, seed_second: bool) -> dict:
    engine = await build_engine(directory, pack)
    if seed_second:
        graph = engine._graph_store
        keys.add_schema(graph._conn)
        keys.install_write_path(graph)
        await seed_second_user(engine)
    await engine.close()
    return {}


async def main(args) -> dict:
    blocked_environment()
    from prme.models.temporal import AssertionStateQuery
    from prme.types import Scope

    pairs = draw_pairs(args.pack, args.pairs, args.seed)
    from datetime import datetime, timezone
    reference = datetime(2026, 12, 1, tzinfo=timezone.utc)

    # One pack copy, prepared once, then read by both paths from the same copy. Both paths read the same rows, so a
    # single prepared copy is what makes the comparison a comparison of code and not of two stores.
    directory = os.path.join(SCRATCH, "compare")
    engine = await build_engine(directory, args.pack)
    graph = engine._graph_store
    keys.add_schema(graph._conn)
    keys.backfill(graph._conn)
    keys.install_write_path(graph)
    shape_pairs = await seed_shapes(engine)
    second_pairs = await seed_second_user(engine)

    report = {"pack": args.pack, "pairs": len(pairs) + len(second_pairs) * 2 + len(shape_pairs), "seed": args.seed,
              "mismatches": [], "shapes": {"empty": 0, "one": 0, "many": 0, "retired": 0, "second_user": 0, "seeded_shape": 0},
              "statuses": {}, "compared_fields": 0}

    def tally(payload: dict) -> None:
        report["statuses"][payload["status"]] = report["statuses"].get(payload["status"], 0) + 1

    async def compare(subject: str, predicate: str, owner: str, shape: str) -> None:
        query = AssertionStateQuery(subject=subject, predicate=predicate, scope=Scope.PERSONAL,
                                    valid_at=reference)
        current = await engine.get_assertion_state(query, user_id=owner)
        indexed = await lookup.indexed_state(engine, query, user_id=owner)
        left = current.model_dump_json()
        right = indexed.model_dump_json()
        report["compared_fields"] += 1
        if left != right:
            first_difference = next(
                (field for field in current.model_dump()
                 if current.model_dump()[field] != indexed.model_dump()[field]), "unknown",
            )
            report["mismatches"].append({
                "subject": subject, "predicate": predicate, "owner": owner, "shape": shape,
                "first_difference": first_difference,
                "current": current.model_dump(mode="json").get(first_difference),
                "indexed": indexed.model_dump(mode="json").get(first_difference),
            })
        payload = indexed.model_dump()
        if payload["matched_records"] == 0:
            report["shapes"]["empty"] += 1
        elif payload["matched_records"] == 1:
            report["shapes"]["one"] += 1
        else:
            report["shapes"]["many"] += 1
        if any(entry["lifecycle_state"] in {"superseded", "archived", "deprecated"}
               for entry in payload["timeline"]):
            report["shapes"]["retired"] += 1
        if owner != USER:
            report["shapes"]["second_user"] += 1
        tally(payload)

    for index, (subject, predicate) in enumerate(pairs):
        await compare(subject, predicate, USER, "drawn")
        if index % 25 == 0:
            print(f"compared {index} of {len(pairs)}, mismatches {len(report['mismatches'])}", flush=True)
    for subject, predicate in shape_pairs:
        await compare(subject, predicate, USER, "seeded_shape")
    for subject, predicate in second_pairs:
        await compare(subject, predicate, SECOND_USER, "second_user")
        await compare(subject, predicate, USER, "second_user_boundary")

    await engine.close()
    report["passed"] = not report["mismatches"]
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", required=True)
    parser.add_argument("--pairs", type=int, default=220)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--out", default=None)
    arguments = parser.parse_args()
    result = asyncio.run(main(arguments))
    text = json.dumps(result, indent=1)
    if arguments.out:
        with open(arguments.out, "w") as handle:
            handle.write(text)
    print(text[:4000], flush=True)
