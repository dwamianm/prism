"""Protocol controls for the paired MemoryArena travel runner."""

import json
from pathlib import Path

import pytest

from benchmarks.integrations import run_memoryarena_travel as runner


def _rows():
    rows = []
    identity = 1
    for count in (5, 6, 7, 8):
        for _ in range(5):
            rows.append({
                "id": identity,
                "questions": [f"q-{index}" for index in range(count)],
            })
            identity += 1
    return rows


def test_cohort_selection_is_seeded_stratified_and_excludes_preflight():
    first = runner.select_cohort(
        _rows(), seed=17, groups_per_stratum=2, excluded_ids={1}
    )
    second = runner.select_cohort(
        _rows(), seed=17, groups_per_stratum=2, excluded_ids={1}
    )

    assert first == second
    assert len(first) == 8
    assert 1 not in {item["id"] for item in first}
    assert sorted(item["person_count"] for item in first) == [5, 5, 6, 6, 7, 7, 8, 8]
    assert len({item["id"] for item in first}) == len(first)


@pytest.mark.parametrize("groups", [0, -1, 6])
def test_cohort_selection_rejects_invalid_or_unavailable_strata(groups):
    with pytest.raises(ValueError):
        runner.select_cohort(
            _rows(), seed=17, groups_per_stratum=groups, excluded_ids=set()
        )


def test_group_checkpoints_resume_and_bind_registration(tmp_path: Path):
    path = tmp_path / "checkpoints.jsonl"
    record = {
        "schema_version": 1,
        "registration_sha256": "a" * 64,
        "arm": "prme",
        "group_id": 7,
        "persons": [],
    }
    runner._append_checkpoint(path, record)
    assert runner._load_checkpoints(path, "a" * 64) == {("prme", 7): record}

    with pytest.raises(RuntimeError, match="registration differs"):
        runner._load_checkpoints(path, "b" * 64)


def test_group_checkpoint_repairs_only_incomplete_tail(tmp_path: Path):
    path = tmp_path / "checkpoints.jsonl"
    record = {
        "registration_sha256": "a" * 64,
        "arm": "prme",
        "group_id": 7,
    }
    path.write_bytes(runner._canonical(record) + b"\n{\"interrupted\"")

    assert runner._load_checkpoints(path, "a" * 64) == {("prme", 7): record}
    assert path.read_bytes().endswith(b"\n")
    assert len(path.read_text().splitlines()) == 1


def test_submission_preserves_explicit_failed_plans(tmp_path: Path):
    path = tmp_path / "submission.jsonl"
    rows = runner._write_submission(path, [{
        "group_id": 3,
        "persons": [{
            "person_idx": 1,
            "name": "Ada",
            "query": "plan",
            "plan": None,
        }],
    }])

    assert rows[0]["persons"][0]["plan"] is None
    assert json.loads(path.read_text())["persons"][0]["plan"] is None


def test_server_attempt_paths_do_not_reuse_a_prior_pack(tmp_path: Path, monkeypatch):
    (tmp_path / "prme-pack-attempt-1").mkdir()
    captured = {}

    class Process:
        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

    def popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    monkeypatch.setattr(runner, "_wait_for_server", lambda *_args: None)

    _process, log = runner._start_server(Path("/project"), tmp_path, 8018, 4096)
    log.close()

    command = captured["command"]
    assert str(tmp_path / "prme-pack-attempt-2") in command
    assert (tmp_path / "prme-adapter-attempt-2.log").is_file()
