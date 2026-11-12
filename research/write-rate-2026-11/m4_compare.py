"""M4 (PROTOCOL.md): compare two packs built from the same observations, apart from generated ids and times.

    PYTHONPATH=. uv run python research/write-rate-2026-11/m4_compare.py \
        --a data/write-rate-2026-11/m4/current-a --b data/write-rate-2026-11/m4/current-b \
        --points 400 4000 --out data/write-rate-2026-11/m4-current-vs-current.json

Each side is a directory that ``m4_build.py`` wrote, with a closed pack copy at ``<dir>/<point>/pack``. The copy is
copied again to a scratch directory before it is opened, so the kept copies are never changed.

What is compared, per store size:

- every event, node and edge, and every row of the operations log;
- the vector index's rows (node, model, version, dimension, key, staged text) and a digest of each stored vector;
- every live document of the lexical index;
- ``retrieve()`` for 25 questions (20 "Where is the {object} now?", 5 "What is on the {place}?") at a fixed reference
  time: the whole response (ranked results with their scores and paths, the packed bundle, the metadata) and the
  retrieval receipt the operations log keeps.

Normalization, and only this: every UUID is replaced by a label (events are labeled in event-time order, nodes in
the order of their own normalized rows), and every timestamp on or after 2026-11-11 is replaced by ``<clock>``. The
observations' event times are all on 2026-11-02 to 2026-11-04, so such a timestamp is a build clock (a creation,
update or reinforcement time). Durations measured in milliseconds (``timing_ms`` and the like) are dropped. A
retrieval receipt's ``receipt_checksum`` is dropped too: it is a digest over the receipt text, which holds the
generated request id, so it differs between any two requests; the receipt text itself is compared after masking.

Build clocks also reach the scores: under rank fusion the recency anchor is the newest candidate's time, and an
entity has no event time, so its update time (a build clock) is the anchor. Two builds of the same observations
then score the same records about 1e-7 apart. So, before the questions are asked, every build clock in the scratch
copy (every timestamp on or after 2026-11-11, in every table) is set to one fixed time, 2026-11-20T00:00:00Z, on
both sides. (A first try kept the order of the clocks instead; two builds do not share the same set of equal
clocks, so it could not make them read the same. It is not used.)

Ranks among exact ties are not reproducible between two builds of the same observations, on any path: a tie is
broken by generated ids or by the lexical index's segment layout. So each response is compared at three levels:
``whole`` (the whole normalized response), ``top10`` (the first 10 results as normalized record and score) and
``bundle`` (the packed context's records in order), with the expected claim's rank beside them. Which level is
reproducible is decided by the current path against itself, and the prototype is judged at that level only.

The opportunistic organizer is off while the questions are asked: its passes stop on a time budget, so how much of
its work runs depends on the clock, and its work would make two equal packs differ for that reason alone. Both
sides open with the settings they were built with; the prototype setting does nothing when reading.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import re
import shutil
import sys
from datetime import timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CLOCK = re.compile(r"2026-11-(1[1-9]|2\d|3[01])[T ]\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:?\d{2}|Z)?")
DROPPED_KEYS = {"timing_ms", "receipt_logging_ms"}
SCRATCH = "/tmp/p2/m4-compare"


def plain(value):
    """A JSON-ready value: datetimes as ISO text, bytes as a digest, UUIDs as text."""
    if hasattr(value, "astimezone") and getattr(value, "tzinfo", None) is not None:
        return value.astimezone(timezone.utc).isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "sha256:" + hashlib.sha256(bytes(value)).hexdigest()[:16]
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def drop_durations(value):
    if isinstance(value, dict):
        return {key: drop_durations(item) for key, item in value.items()
                if key not in DROPPED_KEYS and not key.endswith("_ms")}
    if isinstance(value, list):
        return [drop_durations(item) for item in value]
    return value


def unfolded(value):
    """JSON text inside a value read as JSON, so a receipt stored as text inside a payload is compared field by field."""
    if isinstance(value, str) and value[:1] in "{[":
        try:
            return unfolded(json.loads(value))
        except ValueError:
            return value
    if isinstance(value, dict):
        return {key: unfolded(item) for key, item in value.items() if key != "receipt_checksum"}
    if isinstance(value, list):
        return [unfolded(item) for item in value]
    return value


def mask(text: str, labels: dict[str, str]) -> str:
    text = CLOCK.sub("<clock>", text)
    return UUID.sub(lambda found: labels.get(found.group(0), "<id>"), text)


def questions(point: int) -> list[tuple[str, str | None]]:
    """20 object questions with their expected newest sentence, and 5 place questions, fixed by the store size."""
    truth = common.truth_after(common.observations(point))
    rng = random.Random(common.SEED + point)
    asked = [(f"Where is the {obj} now?", f"The {obj} is on the {truth[obj]}.")
             for obj in rng.sample(sorted(truth), 20)]
    asked += [(f"What is on the {place}?", None) for place in rng.sample(sorted(set(truth.values())), 5)]
    return asked


async def read_side(name: str, kept_pack: str, point: int, provider, settings: dict) -> dict:
    """Ask the questions on a scratch copy of the pack, then read its records."""
    import duckdb
    import tantivy

    directory = os.path.join(SCRATCH, f"{name}-{point}")
    if os.path.exists(directory):
        shutil.rmtree(directory)
    shutil.copytree(kept_pack, os.path.join(directory, "pack"))
    clocks = fixed_build_clocks(os.path.join(directory, "pack", "memory.duckdb"))
    engine, guard = await common.new_engine(directory, provider, organizer={"opportunistic_enabled": False},
                                            **settings)
    reference_time = common.BASE + common.STEP * point + timedelta(minutes=1)
    answers = []
    try:
        for question, expected in questions(point):
            response = await engine.retrieve(question, user_id=common.USER, reference_time=reference_time)
            contents = [candidate.node.content for candidate in response.results]
            answers.append({
                "question": question,
                "expected_rank": (contents.index(expected) + 1) if expected in contents else None,
                "response": drop_durations(response.model_dump(mode="json")),
            })
        assert not guard.misses, f"a model was called: {guard.misses[:3]}"
    finally:
        await engine.close()

    pack = os.path.join(directory, "pack")
    conn = duckdb.connect(os.path.join(pack, "memory.duckdb"), read_only=True)
    try:
        def rows(sql: str) -> list[dict]:
            cursor = conn.execute(sql)
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, (plain(value) for value in row))) for row in cursor.fetchall()]

        events = rows("SELECT * FROM events")
        nodes = rows("SELECT * FROM nodes")
        edges = rows("SELECT * FROM edges")
        operations = [unfolded(row) for row in
                      rows("SELECT op_type, target_id, payload, actor_id, namespace_id FROM operations")]
        vectors = rows("""
            SELECT m.node_id, m.user_id, m.embedding_model, m.embedding_version, m.embedding_dim, m.vector_key,
                   m.created_at, p.vector_data AS payload, s.content AS staged_text
            FROM vector_metadata m LEFT JOIN vector_payloads p USING (vector_key)
                 LEFT JOIN vector_staging s ON s.node_id = m.node_id AND s.vector_key = m.vector_key
        """)
    finally:
        conn.close()

    index = tantivy.Index.open(os.path.join(pack, "lexical_index"))
    searcher = index.searcher()
    lexical = []
    if searcher.num_docs:
        for _score, address in searcher.search(tantivy.Query.all_query(), limit=searcher.num_docs).hits:
            lexical.append({key: value for key, value in searcher.doc(address).to_dict().items()})
    return {"events": events, "nodes": nodes, "edges": edges, "operations": operations, "vectors": vectors,
            "lexical": lexical, "answers": answers, "build_clocks": clocks}


def fixed_build_clocks(database: str) -> int:
    """Set every build clock in ``database`` to one fixed time (module docstring). Returns how many cells changed."""
    import duckdb

    conn = duckdb.connect(database)
    changed = 0
    try:
        columns = conn.execute("""
            SELECT table_name, column_name FROM information_schema.columns
            WHERE data_type LIKE 'TIMESTAMP%' AND table_schema = 'main'
            ORDER BY table_name, column_name
        """).fetchall()
        for table, column in columns:
            changed += conn.execute(
                f'UPDATE "{table}" SET "{column}" = TIMESTAMPTZ \'2026-11-20 00:00:00+00\' '
                f'WHERE "{column}" >= TIMESTAMPTZ \'2026-11-11 00:00:00+00\'').fetchone()[0]
        conn.execute("CHECKPOINT")
    finally:
        conn.close()
    return changed


def normalized(side: dict) -> dict:
    """Every record of one side with ids replaced by labels and build clocks masked (module docstring)."""
    labels: dict[str, str] = {}
    for number, event in enumerate(sorted(side["events"], key=lambda event: event["event_time"]), 1):
        labels[str(event["id"])] = f"event-{number}"
    node_rows = sorted(((mask(json.dumps(node, sort_keys=True), labels), node) for node in side["nodes"]),
                       key=lambda item: item[0])
    ambiguous = sum(1 for (one, _), (two, _) in zip(node_rows, node_rows[1:]) if one == two)
    for number, (_text, node) in enumerate(node_rows, 1):
        labels[str(node["id"])] = f"node-{number}"

    def table(name: str) -> list[str]:
        return sorted(mask(json.dumps(row, sort_keys=True), labels) for row in side[name])

    return {
        "build_clocks": side["build_clocks"],
        "ambiguous_node_rows": ambiguous,
        "events": table("events"), "nodes": table("nodes"), "edges": table("edges"),
        "operations": table("operations"), "vectors": table("vectors"), "lexical": table("lexical"),
        "answers": [{"question": answer["question"], "expected_rank": answer["expected_rank"],
                     "response": json.loads(mask(json.dumps(answer["response"], sort_keys=True), labels))}
                    for answer in side["answers"]],
    }


def top10(response: dict) -> list:
    """The first 10 results as (normalized record without its label, composite score)."""
    return [(_record(result["node"]), result["composite_score"]) for result in response["results"][:10]]


def bundle(response: dict) -> list:
    """The packed context's records in order, without labels."""
    sections = response["bundle"]["sections"]
    return [(name, [_record(candidate["node"]) for candidate in sections[name]]) for name in sorted(sections)]


def _record(node: dict) -> str:
    return json.dumps({key: value for key, value in node.items() if key != "id"}, sort_keys=True)


def first_differences(one, two, path: str = "", found: list | None = None, limit: int = 5) -> list[str]:
    found = [] if found is None else found
    if len(found) >= limit:
        return found
    if isinstance(one, dict) and isinstance(two, dict):
        for key in sorted(set(one) | set(two)):
            if key not in one or key not in two:
                found.append(f"{path}.{key}: only on side {'b' if key not in one else 'a'}")
            else:
                first_differences(one[key], two[key], f"{path}.{key}", found, limit)
    elif isinstance(one, list) and isinstance(two, list):
        if len(one) != len(two):
            found.append(f"{path}: {len(one)} items against {len(two)}")
        for index, (left, right) in enumerate(zip(one, two)):
            first_differences(left, right, f"{path}[{index}]", found, limit)
    elif one != two:
        found.append(f"{path}: {json.dumps(one)[:160]} against {json.dumps(two)[:160]}")
    return found


def compare_point(a: dict, b: dict) -> dict:
    out = {"ambiguous_node_rows": [a["ambiguous_node_rows"], b["ambiguous_node_rows"]],
           "build_clocks": [a["build_clocks"], b["build_clocks"]]}
    same_everywhere = True
    for name in ("events", "nodes", "edges", "operations", "vectors", "lexical"):
        left, right = a[name], b[name]
        only_a = sorted(set(left) - set(right))
        only_b = sorted(set(right) - set(left))
        same = left == right
        same_everywhere &= same
        out[name] = {"count": [len(left), len(right)], "same": same, "rows_only_a": len(only_a),
                     "rows_only_b": len(only_b), "example_a": only_a[:2], "example_b": only_b[:2]}
    answers = []
    for left, right in zip(a["answers"], b["answers"]):
        same = left["response"] == right["response"]
        same_everywhere &= same
        levels = {"whole": same, "top10": top10(left["response"]) == top10(right["response"]),
                  "bundle": bundle(left["response"]) == bundle(right["response"])}
        answers.append({"question": left["question"], "same": same, "levels": levels,
                        "expected_rank": [left["expected_rank"], right["expected_rank"]],
                        "results": [len(left["response"]["results"]), len(right["response"]["results"])],
                        "first_differences": [] if same else first_differences(left["response"], right["response"])})
    out["answers"] = answers
    out["answers_same"] = sum(answer["same"] for answer in answers)
    out["answers_same_by_level"] = {level: sum(answer["levels"][level] for answer in answers)
                                    for level in ("whole", "top10", "bundle")}
    out["expected_rank_same"] = sum(answer["expected_rank"][0] == answer["expected_rank"][1] for answer in answers)
    out["same_everywhere"] = same_everywhere
    return out


async def main(args) -> dict:
    common.blocked_environment()
    provider = await common.embedding_provider()
    report = {"a": args.a, "b": args.b, "points": {}}
    for point in args.points:
        sides = []
        for label, root in (("a", args.a), ("b", args.b)):
            settings = common.PROTOTYPE if "prototype" in os.path.basename(os.path.normpath(root)) else {}
            side = await read_side(f"{os.path.basename(os.path.normpath(root))}-{label}",
                                   os.path.join(root, str(point), "pack"), point, provider, settings)
            sides.append(normalized(side))
        result = compare_point(*sides)
        report["points"][str(point)] = result
        print(f"{point}: same everywhere {result['same_everywhere']}, answers by level "
              f"{result['answers_same_by_level']}, expected rank same {result['expected_rank_same']}/25, "
              f"clocks {result['build_clocks']}, "
              + ", ".join(f"{name} {result[name]['count']} same={result[name]['same']}"
                          for name in ("events", "nodes", "edges", "operations", "vectors", "lexical")),
              flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--a", required=True)
    parser.add_argument("--b", required=True)
    parser.add_argument("--points", type=int, nargs="+", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = asyncio.run(main(args))
    with open(args.out, "w") as handle:
        json.dump(result, handle, indent=1)
