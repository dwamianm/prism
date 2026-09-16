"""Authored real-BGE truncation collision and durable organizer preservation."""

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import tempfile

import numpy as np
import prme
from prme import MemoryEngine, config_from_directory
from prme.models.events import Event
from prme.models.nodes import MemoryNode
from prme.storage.embedding import FastEmbedProvider
from prme.types import LifecycleState, NodeType


async def run(report):
    # The opposing qualifiers deliberately fall beyond the encoder's window.
    prefix = "Aurora deployment policy background and operating context. " * 160
    texts = [prefix + ending for ending in (
        "FINAL RULE: Approval is required before every deployment.",
        "FINAL RULE: Approval is never required before deployment.",
    )]
    provider = FastEmbedProvider()
    vectors = np.asarray(await provider.embed(texts), dtype=np.float32)
    difference = float(np.max(np.abs(vectors[0] - vectors[1])))
    report.update(embedding_model=provider.model_name, embedding_version=provider.model_version,
                  max_absolute_vector_difference=difference,
                  content_sha256=[hashlib.sha256(t.encode()).hexdigest() for t in texts])
    assert difference == 0, "This authored input did not reproduce an encoder collision"
    with tempfile.TemporaryDirectory(prefix="prme-merge-collision-") as directory:
        config = config_from_directory(directory).model_copy(update={"database_url": None})
        async with MemoryEngine.open(config, embedding_provider=provider) as engine:
            originals = {}
            for text in texts:
                event = Event(content=text, role="user", user_id="authored")
                await engine._event_store.append(event)
                node = MemoryNode(content=text, user_id="authored", node_type=NodeType.FACT,
                                  evidence_refs=[event.id], valid_from=datetime(2025, 1, 1, tzinfo=timezone.utc))
                # Explicit graph inputs isolate organizer semantics from LLM
                # extraction and store-time timestamps; this is not a rebuild test.
                await engine._graph_store.create_node(node)
                await engine._vector_index.index(str(node.id), text, "authored")
                originals[str(node.id)] = node.model_dump(mode="json")
            result = await engine.organize(user_id="authored", jobs=["deduplicate"], budget_ms=30000)
            report["organize"] = result.model_dump(mode="json")
            active = await engine.query_nodes(user_id="authored")
            report["active_after_organize"] = len(active)
            assert result.jobs_run == ["deduplicate"] and not result.jobs_skipped
            job = result.per_job["deduplicate"]
            assert job.errors == 0 and job.nodes_processed > 0
            assert {str(n.id): n.model_dump(mode="json") for n in active} == originals
            effective = engine._config
        async with MemoryEngine.open(effective, embedding_provider=provider) as reopened:
            active = await reopened.query_nodes(user_id="authored")
            assert {str(n.id): n.model_dump(mode="json") for n in active} == originals
            for node in active:
                assert node.lifecycle_state == LifecycleState.TENTATIVE
                event = await reopened.get_event(str(node.evidence_refs[0]), user_id="authored")
                assert event.content == node.content
            report.update(complete=True, preserved_after_reopen=True, source_evidence_preserved=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite prior evidence")
    report = {"complete": False, "python": platform.python_version(),
              "prme_import_path": str(Path(prme.__file__).resolve()),
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "limits": "Authored component workflow; graph inputs are explicit. Not an extraction, rebuild or answer-quality benchmark."}
    try:
        asyncio.run(run(report))
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
