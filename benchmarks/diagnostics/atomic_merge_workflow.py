"""Installed public-API organizer workflow with real local embeddings and restart."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import platform
import tempfile

import prme
from prme import MemoryEngine, config_from_directory
from prme.types import LifecycleState, NodeType


async def run(report):
    with tempfile.TemporaryDirectory(prefix="prme-atomic-merge-") as directory:
        config = config_from_directory(directory)
        config = config.model_copy(update={"database_url": None, "enable_store_supersedence": False,
            "organizer": config.organizer.model_copy(update={"opportunistic_enabled": False})})
        async with MemoryEngine.open(config) as memory:
            event_ids = [await memory.store("Aurora", user_id="authored", node_type=NodeType.ENTITY,
                metadata={"entity_type": "project"}) for _ in range(2)]
            result = await memory.organize(user_id="authored", jobs=["deduplicate"], budget_ms=30000)
            job = result.per_job["deduplicate"]
            assert job.nodes_modified == 1 and job.errors == 0
            nodes = await memory.query_nodes(user_id="authored", lifecycle_states=list(LifecycleState))
            kept = next(node for node in nodes if node.lifecycle_state == LifecycleState.TENTATIVE)
            retired = next(node for node in nodes if node.lifecycle_state == LifecycleState.SUPERSEDED)
            assert retired.superseded_by == kept.id
            assert set(map(str, kept.evidence_refs)) == set(event_ids)
            response = await memory.retrieve("Aurora", user_id="authored", include_cross_scope=False)
            assert str(retired.id) not in {str(item.node.id) for item in response.results}
            assert str(kept.id) in {str(item.node.id) for item in response.results}
            before = {str(node.id): node.model_dump(mode="json") for node in nodes}
            report["organize"] = result.model_dump(mode="json")
        async with MemoryEngine.open(config) as memory:
            result = await memory.organize(user_id="authored", jobs=["deduplicate"], budget_ms=30000)
            assert result.per_job["deduplicate"].nodes_modified == 0
            nodes = await memory.query_nodes(user_id="authored", lifecycle_states=list(LifecycleState))
            assert {str(node.id): node.model_dump(mode="json") for node in nodes} == before
            for event_id in event_ids:
                assert (await memory.get_event(event_id, user_id="authored")).content == "Aurora"
            report.update(complete=True, evidence_union_preserved=True, source_events_preserved=True,
                          retired_candidate_excluded=True, reopened_state_preserved=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite prior evidence")
    report = {"complete": False, "python": platform.python_version(),
              "prme_import_path": str(Path(prme.__file__).resolve()),
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "limits": "Authored installed public-API workflow. No extraction, general entity-resolution or memory-QA claim."}
    try:
        asyncio.run(run(report))
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
