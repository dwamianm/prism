"""Contracts for the PRME subprocess bridge used by the MELT benchmark."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid5

from benchmarks.integrations import melt_sut
from prme.types import DecayProfile, LifecycleState


def test_git_identity_is_read_from_the_prme_checkout(monkeypatch) -> None:
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout="a" * 40 + "\n")

    monkeypatch.setattr(melt_sut.subprocess, "run", run)
    assert melt_sut._git_commit() == "a" * 40
    assert calls[0][:3] == ["git", "-C", str(Path(melt_sut.__file__).resolve().parents[2])]


class _FakeClient:
    def __init__(self, directory: str, **kwargs) -> None:
        self.directory = directory
        self.nodes: list[SimpleNamespace] = []
        self.by_event: dict[str, SimpleNamespace] = {}
        self.closed = False
        self.organize_calls = 0

    def store(self, content: str, **kwargs) -> str:
        index = len(self.nodes) + 1
        event_id = str(uuid5(UUID(int=0), f"event-{index}"))
        node = SimpleNamespace(
            id=uuid5(UUID(int=0), f"node-{index}"),
            content=content,
            metadata=kwargs["metadata"],
            lifecycle_state=LifecycleState.TENTATIVE,
            superseded_by=None,
            decay_profile=DecayProfile.MEDIUM,
        )
        self.nodes.append(node)
        self.by_event[event_id] = node
        return event_id

    def get_event_nodes(self, event_id: str, *, user_id: str) -> list[SimpleNamespace]:
        return [self.by_event[event_id]]

    def supersede(self, old_node_id: str, new_node_id: str, *, user_id: str) -> None:
        old = next(node for node in self.nodes if str(node.id) == old_node_id)
        new = next(node for node in self.nodes if str(node.id) == new_node_id)
        old.lifecycle_state = LifecycleState.SUPERSEDED
        old.superseded_by = new.id

    def contradict(self, old_node_id: str, new_node_id: str, *, user_id: str) -> None:
        old = next(node for node in self.nodes if str(node.id) == old_node_id)
        new = next(node for node in self.nodes if str(node.id) == new_node_id)
        old.lifecycle_state = LifecycleState.CONTESTED
        new.lifecycle_state = LifecycleState.CONTESTED
        old.contradicts_id = new.id
        new.contradicts_id = old.id

    def retrieve(self, query: str, **kwargs) -> SimpleNamespace:
        candidates = []
        for node in self.nodes:
            if node.lifecycle_state == LifecycleState.SUPERSEDED:
                continue
            candidates.append(
                SimpleNamespace(
                    node=node,
                    composite_score=0.8,
                    contradicts_id=getattr(node, "contradicts_id", None),
                )
            )
        return SimpleNamespace(results=candidates)

    def query_nodes(self, **kwargs) -> list[SimpleNamespace]:
        return list(self.nodes)

    def organize(self, **kwargs) -> SimpleNamespace:
        self.organize_calls += 1
        return SimpleNamespace(jobs_run=["confidence_update"])

    def close(self) -> None:
        self.closed = True


def _hello(state_dir: Path, *, overrides: dict | None = None) -> dict:
    return {
        "op": "hello",
        "contract_version": "b2",
        "owner": {"user_id": "melt-user", "workspace_id": "workspace"},
        "paths": {"state_dir": str(state_dir)},
        "capabilities_requested": [
            "reset",
            "query_as_of",
            "answer_generation",
        ],
        "config_overrides": overrides or {},
    }


def _new_sut(monkeypatch, tmp_path: Path) -> tuple[melt_sut.PrmeMeltSut, _FakeClient]:
    clients: list[_FakeClient] = []

    def factory(directory: str, **kwargs) -> _FakeClient:
        client = _FakeClient(directory, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(melt_sut, "MemoryClient", factory)
    sut = melt_sut.PrmeMeltSut()
    ack = sut.handle(_hello(tmp_path, overrides={"min_score": 0.2, "unknown": True}))
    assert ack["capabilities_supported"] == ["reset", "query_as_of"]
    assert ack["capabilities_unsupported"] == ["answer_generation"]
    assert ack["config_overrides_accepted"] == {"min_score": 0.2}
    assert ack["config_overrides_rejected"] == {"unknown": "unsupported override"}
    sut.handle({"op": "reset", "run_id": "run/case"})
    return sut, clients[-1]


def test_raw_ingest_returns_ranked_source_evidence(monkeypatch, tmp_path: Path) -> None:
    sut, client = _new_sut(monkeypatch, tmp_path)
    ack = sut.handle(
        {
            "op": "ingest",
            "event_id": "preference-source",
            "content": "Morgan prefers concise updates.",
            "session_id": "session-1",
            "source_type": "user",
            "timestamp": "2026-02-01T09:00:00Z",
        }
    )
    result = sut.handle(
        {
            "op": "query",
            "query_id": "q1",
            "query": "concise updates",
            "top_k": 1,
            "timestamp": "2026-02-02T09:00:00Z",
        }
    )
    assert ack["source_id"] == "preference-source"
    assert result["evidence"][0]["source_id"] == "preference-source"
    assert result["evidence"][0]["text"] == "Morgan prefers concise updates."
    assert result["evidence"][0]["metadata"]["decay_score"] < 1.0
    sut.close()
    assert client.closed is True


def test_structured_correction_preserves_as_of_history(monkeypatch, tmp_path: Path) -> None:
    sut, _ = _new_sut(monkeypatch, tmp_path)
    old = sut.handle(
        {
            "op": "memory_write",
            "event_id": "drink-old",
            "source_id": "drink-old-source",
            "entry_type": "fact",
            "key": "drink",
            "value": "Morgan preferred jasmine tea.",
            "timestamp": "2026-02-03T10:00:00Z",
        }
    )
    sut.handle(
        {
            "op": "memory_write",
            "event_id": "drink-new",
            "source_id": "drink-new-source",
            "entry_type": "fact",
            "key": "drink",
            "value": "Morgan prefers oolong tea.",
            "supersedes": old["entry_id"],
            "timestamp": "2026-02-04T10:00:00Z",
        }
    )
    current = sut.handle(
        {
            "op": "query",
            "query_id": "current",
            "query": "Morgan tea",
            "top_k": 2,
            "timestamp": "2026-02-05T10:00:00Z",
        }
    )
    historical = sut.handle(
        {
            "op": "query",
            "query_id": "historical",
            "query": "Morgan tea",
            "top_k": 2,
            "timestamp": "2026-02-05T10:00:00Z",
            "as_of": "2026-02-03T12:00:00Z",
        }
    )
    assert [item["source_id"] for item in current["evidence"]] == ["drink-new-source"]
    assert [item["source_id"] for item in historical["evidence"]] == ["drink-old-source"]


def test_conflicting_preferences_surface_counterpart_provenance(
    monkeypatch, tmp_path: Path
) -> None:
    sut, client = _new_sut(monkeypatch, tmp_path)
    for event_id, tea in (("tea-a", "jasmine"), ("tea-b", "oolong")):
        sut.handle(
            {
                "op": "memory_write",
                "event_id": event_id,
                "source_id": f"{event_id}-source",
                "entry_type": "preference",
                "key": "favorite_drink",
                "value": f"Morgan likes {tea} tea.",
                "timestamp": "2026-02-03T10:00:00Z",
            }
        )
    consolidate = sut.handle({"op": "consolidate", "scope": "incremental"})
    result = sut.handle(
        {
            "op": "query",
            "query_id": "conflict",
            "query": "jasmine oolong tea",
            "top_k": 3,
            "timestamp": "2026-02-04T10:00:00Z",
        }
    )
    assert consolidate["jobs_run"] == ["confidence_update"]
    assert client.organize_calls == 1
    assert {item["source_id"] for item in result["evidence"]} == {
        "tea-a-source",
        "tea-b-source",
    }
    assert all(item["metadata"]["conflict_source_ids"] for item in result["evidence"])
