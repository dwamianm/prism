"""P6 bar 6: does the working tier refuse and hide what the current path refuses and hides?

    PYTHONPATH=. uv run python research/working-tier-2026-11/privacy_probe.py

Each case is a fresh store. A setup fact is written and kept in the working table; one stored row of its scope is
then given a privacy marker it cannot satisfy, as ``tests/test_batch_write_path.py`` does, so any legacy check that
reads that row refuses. Then:

- ``current``: the second write goes through ``ingest_batch`` and its result is recorded.
- ``working``: the second fact is accepted, the working table is read through ``lookup``, ``facts_by_address`` and
  ``retrieve_with_working``, and the error chain of each is recorded.

The cases:

- ``marked-scope``: a claim row in the probe's own scope is marked. The current path refuses the write; the tier
  must refuse the read of its working table.
- ``marked-other-user``: a row of another owner is marked. Neither path may see it, and both proceed.
- ``other-user-table``: another owner's working fact sits in the same table. The probe's owner must not see it.
- ``other-scope-table``: the same fact in the ``project`` scope. A ``personal`` read must not see it.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil

import harness

common = harness.common


def stated(index: int, subject: str, relation: str, obj: str, object_type: str) -> dict:
    from datetime import datetime, timedelta, timezone

    start = datetime(2026, 11, 2, 8, tzinfo=timezone.utc)
    return {"content": f"The {subject} {relation.replace('_', ' ')} the {obj}.", "role": "system",
            "event_time": start + timedelta(seconds=10 * index),
            "assertions": [{"subject": subject, "subject_type": "product", "relation": relation,
                            "object": obj, "object_type": object_type, "status": "current"}]}


def chain(exc: Exception) -> list[str]:
    out, cause = [], exc
    while cause is not None and len(out) < 6:
        out.append(getattr(cause, "code", None) or type(cause).__name__)
        cause = cause.__cause__ or cause.__context__
    return out


async def run(case: dict, path: str, provider) -> dict:
    from prme.privacy.records import PRIVACY_KEY
    from prme.types import NodeType, Scope

    directory = os.path.join(harness.SCRATCH, "privacy-probe", f"{path}-{case['name']}")
    if os.path.exists(directory):
        shutil.rmtree(directory)
    os.makedirs(directory)
    engine, guard = await common.new_engine(directory, provider)
    import tier

    working = tier.WorkingTier(engine, directory)
    assertation = tier.Assertion
    result = {"path": path, "case": case["name"]}
    try:
        setup = stated(0, *case["setup"])
        if path == "current":
            await engine.ingest_batch([setup], user_id=common.USER, wait_for_extraction=True)
        else:
            await working.accept(setup, user_id=common.USER)
            await working.drain_once(user_id=common.USER)
        conn = engine._graph_store._conn
        if case["mark"] is not None:
            node_type, content = case["mark"]
            marked = conn.execute("SELECT CAST(id AS VARCHAR) FROM nodes WHERE node_type=? AND user_id=?"
                                  + ("" if content is None else " AND content=?"),
                                  [node_type, common.USER] + ([] if content is None else [content])).fetchall()
            assert len(marked) == 1, marked
            conn.execute("UPDATE nodes SET metadata=json_merge_patch(metadata, ?) WHERE id=?",
                         [json.dumps({PRIVACY_KEY: {"event_id": "not-a-uuid"}}), marked[0][0]])
        if path == "working":
            # Another owner's fact, and the same owner's fact in another scope, sit beside the probe's own.
            # Both carry the probe's own address, so a read that ignored the owner or the scope would return them.
            foreign = stated(5, *case["probe"])
            await working.accept(foreign, user_id="other-1")
            scoped = stated(6, *case["probe"])
            await working.accept(scoped, user_id=common.USER, scope=Scope.PROJECT)
        probe = stated(1, *case["probe"])
        refused = None
        try:
            if path == "current":
                await engine.ingest_batch([probe], user_id=common.USER, wait_for_extraction=True)
            else:
                await working.accept(probe, user_id=common.USER)
        except Exception as exc:  # noqa: BLE001 - the probe records what the write raised
            refused = chain(exc)
        result["write_refused"] = refused
        if path == "working":
            assertion = assertation(**probe["assertions"][0]).normalized()
            for label, call in (
                ("lookup", working.lookup(assertion, user_id=common.USER)),
                ("by_address", working.facts_by_address(user_id=common.USER)),
                ("retrieve_with_working", working.retrieve_with_working(
                    f"Where is the {probe['assertions'][0]['subject']} now?", user_id=common.USER,
                    subject=probe["assertions"][0]["subject"], relation="located_in")),
            ):
                try:
                    value = await call
                    found = len(value) if label == "lookup" else (
                        len(value) if label == "by_address" else len(value[1]))
                    result[label] = {"refused": None, "addresses_returned": found,
                                     "subjects": sorted({fact.subject for group in (
                                         [value] if label == "lookup" else
                                         (value.values() if label == "by_address" else value[1].values()))
                                         for fact in group})}
                except Exception as exc:  # noqa: BLE001 - the probe records what the read raised
                    result[label] = {"refused": chain(exc)}
            result["working_backlog"] = working.backlog(user_id=common.USER)
            result["working_backlog_other_user"] = working.backlog(user_id="other-1")
            result["working_backlog_project"] = working.backlog(user_id=common.USER, scope=Scope.PROJECT)
        else:
            facts = conn.execute("SELECT count(*) FROM nodes WHERE node_type='fact' AND user_id=?",
                                 [common.USER]).fetchone()[0]
            result["fact_nodes_after"] = facts
    finally:
        assert not guard.misses, guard.misses[:3]
        working.close()
        await engine.close()
    return result


CASES = (
    {"name": "marked-scope", "setup": ("red mug", "part_of", "tea set", "product"), "mark": ("fact", None),
     "probe": ("red mug", "located_in", "kitchen shelf", "location")},
    {"name": "marked-other-user", "setup": ("red mug", "part_of", "tea set", "product"), "mark": None,
     "probe": ("green mug", "located_in", "kitchen shelf", "location")},
    {"name": "other-user-table", "setup": ("red mug", "part_of", "tea set", "product"), "mark": None,
     "probe": ("red mug", "located_in", "kitchen shelf", "location")},
    {"name": "other-scope-table", "setup": ("red mug", "part_of", "tea set", "product"), "mark": None,
     "probe": ("red mug", "located_in", "kitchen shelf", "location")},
)


async def main() -> list[dict]:
    common.blocked_environment()
    provider = await common.embedding_provider()
    return [await run(case, path, provider) for case in CASES for path in ("current", "working")]


if __name__ == "__main__":
    results = asyncio.run(main())
    target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "privacy-probe.json")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w") as handle:
        json.dump(results, handle, indent=1)
    print(json.dumps(results, indent=1))
