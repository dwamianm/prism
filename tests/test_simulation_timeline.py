"""Simulations cannot use future sources or rewrite append-only event history."""

from pathlib import Path

from prme import PRMEConfig
from simulations.harness import SimulationRunner, SimScenario, SimMessage, SimCheckpoint
from tests.test_durable_ingestion import MockEmbeddingProvider


async def test_checkpoints_only_see_arrived_messages_and_preserve_event_timestamps(tmp_path, monkeypatch):
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())
    snapshots = []

    class ObservedRunner(SimulationRunner):
        async def _evaluate_checkpoint(self, engine, checkpoint, messages, node_ids, organize=False):
            events = await engine.get_events(self.USER_ID, limit=100)
            snapshots.append({event.content: (event.timestamp, event.created_at, event.event_time, event.content_hash)
                              for event in events})
            return await super()._evaluate_checkpoint(engine, checkpoint, messages, node_ids, organize=organize)

    scenario = SimScenario("timeline", "Verify causal arrival", [
        SimMessage(1, "user", "Present source", ["present"]),
        SimMessage(5, "user", "Future source", ["future"]),
    ], [
        SimCheckpoint(0, "sources", [], ["Present", "Future"], "Before either source"),
        SimCheckpoint(2, "Present source", ["Present"], ["Future"], "Before future source"),
        SimCheckpoint(6, "Future source", ["Future"], [], "After both sources"),
    ])
    caller_path = tmp_path / "must-not-be-opened.duckdb"
    config = PRMEConfig(db_path=str(caller_path), organizer={"opportunistic_enabled": False})
    report = await ObservedRunner().run(scenario, config=config, organize_at_checkpoints=False)
    assert report.overall_pass_rate == 1.0
    assert report.checkpoints[0].rendered_context == ""
    assert "Present source" in report.checkpoints[1].rendered_context
    assert "Future source" not in report.checkpoints[1].rendered_context
    assert snapshots[0] == {}
    assert set(snapshots[1]) == {"Present source"}
    assert set(snapshots[2]) == {"Present source", "Future source"}
    assert snapshots[1]["Present source"] == snapshots[2]["Present source"]
    assert (snapshots[2]["Future source"][0] - snapshots[2]["Present source"][0]).days == 4
    assert all(values[0] == values[2] for values in snapshots[2].values())
    assert not caller_path.exists()
    assert not Path(report.config_summary["db_path"]).parent.exists()
