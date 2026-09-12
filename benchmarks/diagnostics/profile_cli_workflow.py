"""Real installed CLI recovery after an authored interruption; no repo imports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

import prme
from prme import MemoryClient, Scope, config_from_directory
from prme.config import EmbeddingConfig, OrganizerConfig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite previous evidence")
    report = {"complete": False, "python": platform.python_version(),
              "prme_import_path": str(Path(prme.__file__).resolve()),
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "commands": [], "limits": "Authored local CLI workflow; not comparative memory quality."}
    try:
        with tempfile.TemporaryDirectory(prefix="prme-profile-cli-") as directory:
            config = config_from_directory(directory).model_copy(update={
                "database_url": None,
                "embedding": EmbeddingConfig(provider="fastembed", model_name="BAAI/bge-small-en-v1.5", dimension=384),
                "organizer": OrganizerConfig(opportunistic_enabled=False)})
            with MemoryClient(config=config) as client:
                for scope in (Scope.PROJECT, Scope.PERSONAL):
                    for text in ("Aurora keeps calibration records for thirty days.",
                                 "Aurora needs optical inspections before deployment."):
                        client.store(text, user_id="authored", scope=scope)
                async def outage(*args, **kwargs):
                    raise OSError("Authored profile staging interruption")
                client._engine._lexical_index.stage_profile = outage
                for scope in (Scope.PROJECT, Scope.PERSONAL):
                    try:
                        client.consolidate_knowledge(user_id="authored", scope=scope, entity_names=["Aurora"])
                    except OSError:
                        pass
                    else:
                        raise AssertionError("Authored interruption did not occur")
                jobs = client.profile_jobs(user_id="authored")
                assert len(jobs) == 2
                ids = {job["scope"]: job["plan_id"] for job in jobs}
                checksums = {key: client._run(client._engine._profile_work.get(value, user_id="authored")).checksum
                             for key, value in ids.items()}
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            env.update(PRME_DATABASE_URL="postgresql://unintended.invalid/other",
                       PRME_ORGANIZER__OPPORTUNISTIC_ENABLED="false",
                       PRME_EMBEDDING__PROVIDER="fastembed",
                       PRME_EMBEDDING__MODEL_NAME="BAAI/bge-small-en-v1.5",
                       PRME_EMBEDDING__DIMENSION="384")
            def command(name, *options, owner="authored", expected_exit=0):
                proc = subprocess.run([sys.executable, "-m", "prme.cli", name, config.db_path,
                                       *options, "--user-id", owner, "--format", "json"],
                                      cwd=directory, env=env, capture_output=True, text=True, timeout=60)
                # Never print potentially sensitive provider diagnostics.
                if proc.returncode != expected_exit:
                    raise AssertionError(f"Unexpected exit for {name}: {proc.returncode}")
                value = json.loads(proc.stdout)
                report["commands"].append({"command": name, "exit_code": proc.returncode, "stdout_is_json": True})
                return value
            assert [job["plan_id"] for job in command("profile-jobs", "--scope", "project")] == [ids["project"]]
            assert command("resume-profile", ids["project"], owner="foreign", expected_exit=1) == {
                "profile_id": None, "resumed": False}
            assert command("process-profiles", "--scope", "project", "--budget-ms", "0")["pending"] == 1
            assert command("process-profiles", "--scope", "project") == {
                "processed": 1, "failed": 0, "pending": 0, "errors": {}}
            assert command("resume-profile", ids["project"])["profile_id"] == ids["project"]
            for _ in range(2):
                assert command("discard-profile", ids["personal"])["discarded"]
            assert command("collect-profile-staging", "--scope", "personal") == {
                "collected": 1, "failed": 0, "remaining": 0, "errors": {}, "blocked_reason": None}
            assert command("profile-jobs") == []
            with MemoryClient(config=config) as client:
                assert len(client.query_nodes(user_id="authored", node_type="note")) == 4
                assert [str(n.id) for n in client.query_nodes(user_id="authored", node_type="summary")] == [ids["project"]]
                for key, identity in ids.items():
                    assert client._run(client._engine._profile_work.get(identity, user_id="authored")).checksum == checksums[key]
            report.update(complete=True, local_target_ignores_ambient_database=True,
                          journals_unchanged=True, source_count_preserved=4,
                          exact_profile_recovered=True, abandoned_stage_collected=True)
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
