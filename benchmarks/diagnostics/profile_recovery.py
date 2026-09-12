"""Installed-package profile recovery with real embeddings and authored failures.

Runs without repository helper imports. This verifies recovery and developer
workflow behavior, not comparative memory quality or production latency.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import tempfile

import duckdb
import prme
from prme import MemoryClient, Scope, NodeType, config_from_directory
from prme.config import EmbeddingConfig, OrganizerConfig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite prior evidence")
    report = {
        "complete": False,
        "python": platform.python_version(),
        "duckdb": duckdb.__version__,
        "prme": prme.__version__,
        "prme_import_path": str(Path(prme.__file__).resolve()),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "limits": "Authored local failure/restart workflow using real BGE embeddings. No competitive quality claim.",
    }
    try:
        with tempfile.TemporaryDirectory(prefix="prme-profile-workflow-") as directory:
            config = config_from_directory(directory).model_copy(
                update={
                    "embedding": EmbeddingConfig(
                        provider="fastembed",
                        model_name="BAAI/bge-small-en-v1.5",
                        dimension=384,
                    ),
                    "organizer": OrganizerConfig(opportunistic_enabled=False),
                }
            )
            with MemoryClient(config=config) as client:
                for content in (
                    "Aurora retains calibration records for thirty days.",
                    "Aurora runs optical inspections before each deployment.",
                ):
                    client.store(content, user_id="authored", scope=Scope.PROJECT)
                assert (
                    client.consolidate_knowledge(
                        user_id="authored", scope=Scope.PROJECT, entity_names=["Aurora"]
                    )
                    == 1
                )
                old = client.query_nodes(
                    user_id="authored", node_type=NodeType.SUMMARY
                )[0]
                client.store(
                    "Aurora assigns deployment approval to the observatory lead.",
                    user_id="authored",
                    scope=Scope.PROJECT,
                )

                async def outage(*args, **kwargs):
                    raise OSError("Authored lexical-stage outage")

                client._engine._lexical_index.stage_profile = outage
                try:
                    client.consolidate_knowledge(
                        user_id="authored", scope=Scope.PROJECT, entity_names=["Aurora"]
                    )
                except OSError:
                    pass
                else:
                    raise AssertionError("Authored failure did not occur")
                assert [
                    n.id
                    for n in client.query_nodes(
                        user_id="authored", node_type=NodeType.SUMMARY
                    )
                ] == [old.id]
                jobs = client.profile_jobs(user_id="authored")
                assert len(jobs) == 1
                identity = jobs[0]["plan_id"]
                assert client.profile_jobs(user_id="foreign") == []
                assert client.resume_profile(identity, user_id="foreign") is None
                saved = client._run(
                    client._engine._profile_work.get(identity, user_id="authored")
                )
                checksum = saved.checksum
                provider = client._engine._vector_index._provider
                report["embedding"] = {
                    "model": provider.model_name,
                    "version": provider.model_version,
                    "dimension": provider.dimension,
                }
            with MemoryClient(config=config) as client:
                provider = client._engine._vector_index._provider
                original_embed = provider.embed

                async def forbidden(*args, **kwargs):
                    raise AssertionError("Recovery attempted fresh model inference")

                provider.embed = forbidden
                client._engine._pipeline._extraction_provider.extract = forbidden
                result = client.process_profiles(
                    user_id="authored", scope=Scope.PROJECT
                )
                assert result == {
                    "processed": 1,
                    "failed": 0,
                    "pending": 0,
                    "errors": {},
                }
                assert client.resume_profile(identity, user_id="authored") == identity
                saved = client._run(
                    client._engine._profile_work.get(identity, user_id="authored")
                )
                assert saved.checksum == checksum
                assert [
                    str(n.id)
                    for n in client.query_nodes(
                        user_id="authored", node_type=NodeType.SUMMARY
                    )
                ] == [identity]
                provider.embed = original_embed
                response = client.retrieve(
                    "Aurora deployment approval and optical inspections",
                    user_id="authored",
                    scope=Scope.PROJECT,
                    include_cross_scope=False,
                )
                assert identity in {
                    str(candidate.node.id) for candidate in response.results
                }
                assert response.bundle.render()
                provider.embed = forbidden
                client.archive(identity, user_id="authored")
                assert client.resume_profile(identity, user_id="authored") == identity
                assert (
                    client.query_nodes(user_id="authored", node_type=NodeType.SUMMARY)
                    == []
                )
                # A second interrupted preparation can be explicitly abandoned
                # and reclaimed without re-embedding or changing source history.
                provider.embed = original_embed
                client._engine._lexical_index.stage_profile = outage
                try:
                    client.consolidate_knowledge(
                        user_id="authored", scope=Scope.PROJECT, entity_names=["Aurora"]
                    )
                except OSError:
                    pass
                else:
                    raise AssertionError("Second authored outage did not occur")
                pending_id = client.profile_jobs(user_id="authored")[0]["plan_id"]
                provider.embed = forbidden
                assert client.discard_profile(pending_id, user_id="authored")
                cleanup = client.collect_profile_staging(
                    user_id="authored", scope=Scope.PROJECT
                )
                assert cleanup == {
                    "collected": 1,
                    "failed": 0,
                    "remaining": 0,
                    "errors": {},
                    "blocked_reason": None,
                }
                assert (
                    client.collect_profile_staging(user_id="authored")["collected"] == 0
                )
                report["abandoned_stage_collected"] = True
                report["cleanup"] = cleanup
                report.update(
                    complete=True,
                    recovery=result,
                    exact_prepared_checksum=checksum,
                    model_free_recovery=True,
                    retrieved_profile=True,
                    completed_replay_preserves_archival=True,
                    foreign_owner_isolated=True,
                )
    except BaseException as exc:
        report["error_type"] = type(exc).__name__
        raise
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
