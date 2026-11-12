"""P5 privacy probe: does the batch path read, and so check, the same planning inputs as the current path?

    PYTHONPATH=. uv run python research/write-batch-2026-11/privacy_probe.py

Each case is a fresh store per path. A setup stated fact is written; one of its stored rows (a claim or an entity)
then gets a privacy marker it cannot satisfy, as tests/test_batch_write_path.py does, so any legacy input check that
reads that row refuses. Then one probe stated fact is written, and the probe records whether it was refused (with
its error chain) and how many fact nodes the store holds after it.

- claim-other-key: the marked row is an earlier claim on the probe's subject, with another claim key. The current
  path's claim merge reads every claim of the subject; the batch path's key filter reads only those the key can
  match.
- claim-same-key: the same, with the same claim key.
- entity-other-name: the marked row is an entity whose name the probe does not hold. The current path's entity
  lookup scans every entity of the scope; the filtered lookup reads only names the probe holds.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import datetime, timedelta, timezone

import batch as harness

common = harness.common
T0 = datetime(2026, 11, 2, 8, tzinfo=timezone.utc)


def stated(index: int, subject: str, relation: str, obj: str, object_type: str) -> dict:
    return {"content": f"The {subject} {relation.replace('_', ' ')} the {obj}.", "role": "system",
            "event_time": T0 + timedelta(seconds=10 * index),
            "assertions": [{"subject": subject, "subject_type": "product", "relation": relation,
                            "object": obj, "object_type": object_type, "status": "current"}]}


async def run(path: str, case: dict, provider) -> dict:
    """One fresh store: the setup claim, one of its stored rows marked, then the one probe write."""
    from prme.privacy.records import PRIVACY_KEY

    directory = os.path.join(harness.SCRATCH, "privacy-probe", f"{path}-{case['name']}")
    if os.path.exists(directory):
        shutil.rmtree(directory)
    os.makedirs(directory)
    engine, guard = await common.new_engine(directory, provider, **harness.PATHS[path])
    try:
        await engine.ingest_batch([stated(0, *case["setup"])], user_id=common.USER, wait_for_extraction=True)
        conn = engine._graph_store._conn
        node_type, content = case["mark"]
        marked = conn.execute("SELECT CAST(id AS VARCHAR) FROM nodes WHERE node_type=? AND user_id=?"
                              + ("" if content is None else " AND content=?"),
                              [node_type, common.USER] + ([] if content is None else [content])).fetchall()
        assert len(marked) == 1, marked
        conn.execute("UPDATE nodes SET metadata=json_merge_patch(metadata, ?) WHERE id=?",
                     [json.dumps({PRIVACY_KEY: {"event_id": "not-a-uuid"}}), marked[0][0]])
        refused = None
        try:
            await engine.ingest_batch([stated(1, *case["probe"])], user_id=common.USER, wait_for_extraction=True)
        except Exception as exc:  # noqa: BLE001 - the probe records what the write raised
            refused, cause = [], exc
            while cause is not None and len(refused) < 6:
                refused.append(getattr(cause, "code", None) or type(cause).__name__)
                cause = cause.__cause__ or cause.__context__
        facts = conn.execute("SELECT count(*) FROM nodes WHERE node_type='fact' AND user_id=?",
                             [common.USER]).fetchone()[0]
        assert not guard.misses
    finally:
        await engine.close()
    return {"path": path, "case": case["name"], "refused": refused, "fact_nodes_after": facts}


CASES = (
    # Claim merge: the marked row is an earlier claim on the probe's subject.
    {"name": "claim-other-key", "setup": ("red mug", "part_of", "tea set", "product"), "mark": ("fact", None),
     "probe": ("red mug", "located_in", "kitchen shelf", "location")},
    {"name": "claim-same-key", "setup": ("red mug", "part_of", "tea set", "product"), "mark": ("fact", None),
     "probe": ("red mug", "part_of", "tea set", "product")},
    # Entity lookup: the marked row is an entity whose name the probe does not hold.
    {"name": "entity-other-name", "setup": ("blue towel", "located_in", "garage bench", "location"),
     "mark": ("entity", "blue towel"), "probe": ("red mug", "located_in", "kitchen shelf", "location")},
)


async def main() -> list[dict]:
    common.blocked_environment()
    provider = await common.embedding_provider()
    return [await run(path, case, provider) for case in CASES for path in ("current", "batch")]


if __name__ == "__main__":
    result = asyncio.run(main())
    target = os.path.join(harness.HERE, "results", "privacy-probe.json")
    with open(target, "w") as handle:
        json.dump(result, handle, indent=1)
    print(json.dumps(result, indent=1))
