"""Synthetic contract checks for the pinned LongMemEval-V2 PRME adapter."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from benchmarks.integrations.longmemeval_v2 import PRMEMemory


def trajectory(root: Path) -> dict:
    screenshots = root / "screenshots" / "trajectory-1"
    screenshots.mkdir(parents=True, exist_ok=True)
    for index in range(2):
        (screenshots / f"{index}.png").write_bytes(b"synthetic-image-" + bytes([index]))
    return {
        "id": "trajectory-1",
        "domain": "web",
        "environment": "shop",
        "goal": "Submit an order without duplicating it",
        "outcome": "success",
        "start_url": "https://shop.test/cart",
        "states": [
            {
                "state_index": 0,
                "step": 0,
                "url": "https://shop.test/cart",
                "action": "click Checkout",
                "thought": "Review the cart first",
                "accessibility_tree": "Cart total $42. Checkout button.",
                "screenshot": "screenshots/trajectory-1/0.png",
            },
            {
                "state_index": 1,
                "step": 1,
                "url": "https://shop.test/checkout",
                "action": "click Place Order once",
                "thought": "Never click Place Order twice; the first response is delayed.",
                "accessibility_tree": "Place Order button. A delayed confirmation appears after submission.",
                "screenshot": "screenshots/trajectory-1/1.png",
            },
        ],
        "answer": "must never be visible to memory",
    }


def params(root: Path, pack: Path | None = None) -> dict[str, object]:
    return {
        "storage_path": str(pack) if pack is not None else None,
        "trajectories_root_dir": str(root),
        "user_id": "evaluation",
        "token_budget": 4096,
        "result_limit": 20,
        "include_images": True,
        "image_limit": 2,
        "max_chunk_chars": 1024,
        "context_item_max_chars": 512,
    }


def test_adapter_round_trips_public_context_and_images(
    tmp_path: Path, monkeypatch
) -> None:
    source = trajectory(tmp_path / "data")
    memory = PRMEMemory(params(tmp_path / "data", tmp_path / "pack"))
    try:
        memory.insert(source)
        before = list(memory._client.iter_nodes(user_id="evaluation", batch_size=100))
        memory.insert(source)
        after = list(memory._client.iter_nodes(user_id="evaluation", batch_size=100))
        assert len(after) == len(before) >= 4
        procedure = [
            node for node in after
            if (node.metadata or {}).get("source_kind") == "trajectory_procedure"
        ]
        assert procedure
        procedure_text = "\n".join(node.content for node in procedure)
        assert "Environment: shop" in procedure_text
        assert "Trajectory goal: Submit an order without duplicating it" in procedure_text
        assert "Observed transition from state 0 to state 1: click Place Order once" in procedure_text
        assert "Recorded agent thought at this state (unverified)" in procedure_text
        assert "must never be visible to memory" not in procedure_text
        assert memory._client._engine._retrieval_pipeline._packing_config.session_context_window == 0
        manifest = json.loads(
            (tmp_path / "pack" / "longmemeval_v2_manifest.json").read_text()
        )
        record = manifest["trajectories"]["trajectory-1"]
        assert manifest["schema_version"] == 3
        query_reference_time = datetime.fromisoformat(manifest["query_reference_time"])
        assert query_reference_time.utcoffset() is not None
        assert record["state_count"] == 2
        assert record["node_count"] == len(after)
        assert record["status"] == "complete"

        retrieve = memory._client.retrieve
        retrieval_clocks: list[datetime] = []

        def capture_retrieve(*args, **kwargs):
            retrieval_clocks.append(kwargs["reference_time"])
            return retrieve(*args, **kwargs)

        monkeypatch.setattr(memory._client, "retrieve", capture_retrieve)
        context = memory.query("What should I know about submitting the order?")
        text = "\n".join(item["value"] for item in context if item["type"] == "text")
        assert sum(item["type"] == "text" for item in context) >= 2
        images = [Path(item["value"]) for item in context if item["type"] == "image"]
        assert "Place Order" in text
        assert "must never be visible to memory" not in text
        assert images and all(image.is_file() for image in images)
        hook = memory.post_query_hook(
            query="order", query_image="question.png", memory_context=context
        )
        assert hook["query_image_used_for_retrieval"] is False
        assert hook["query_clock_source"] == "manifest"
        assert datetime.fromisoformat(hook["query_reference_time"]) == query_reference_time
        assert retrieval_clocks == [query_reference_time]

        saved = tmp_path / "saved"
        saved.mkdir()
        memory._save_backend(saved)
        assert memory._client is None
        reopened_context = memory.query("What happens after submitting the order?")
        assert memory._client is not None
        assert any(item["type"] == "text" for item in reopened_context)
        restored = PRMEMemory(params(tmp_path / "data"))
        try:
            restored._load_backend(saved)
            restored_context = restored.query("Why should Place Order be clicked once?")
            restored_text = "\n".join(
                item["value"] for item in restored_context if item["type"] == "text"
            )
            assert "delayed confirmation" in restored_text
            restored_images = [
                Path(item["value"])
                for item in restored_context
                if item["type"] == "image"
            ]
            assert restored_images
            assert all((saved / "prme_pack") in image.parents for image in restored_images)
        finally:
            restored.close()
    finally:
        memory.close()


def test_schema_two_pack_derives_a_read_only_stable_clock(tmp_path: Path) -> None:
    root = tmp_path / "data"
    pack = tmp_path / "pack"
    source = trajectory(root)
    memory = PRMEMemory(params(root, pack))
    memory.insert(source)
    expected = max(
        node.created_at
        for node in memory._client.iter_nodes(user_id="evaluation", batch_size=100)
    )
    memory.close()

    manifest_path = pack / "longmemeval_v2_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = 2
    manifest.pop("query_reference_time")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")
    legacy_bytes = manifest_path.read_bytes()

    legacy = PRMEMemory(params(root, pack))
    try:
        assert legacy._query_reference_time == expected
        assert legacy._query_clock_source == "legacy_max_created_at"
        with pytest.raises(RuntimeError, match="schema 2 packs are read-only"):
            legacy.insert(source)
        context = legacy.query("What happens after submitting the order?")
        assert any(item["type"] == "text" for item in context)
    finally:
        legacy.close()
    assert manifest_path.read_bytes() == legacy_bytes


def test_adapter_rejects_changed_or_interrupted_trajectory(tmp_path: Path) -> None:
    source = trajectory(tmp_path / "data")
    memory = PRMEMemory(params(tmp_path / "data", tmp_path / "pack"))
    try:
        memory.insert(source)
        changed = {**source, "goal": "A changed identity"}
        with pytest.raises(RuntimeError, match="identity changed"):
            memory.insert(changed)

        screenshot = tmp_path / "data" / "screenshots" / "trajectory-1" / "0.png"
        screenshot.write_bytes(b"changed-visual-evidence")
        with pytest.raises(RuntimeError, match="identity changed"):
            memory.insert(source)
        screenshot.write_bytes(b"synthetic-image-\x00")

        manifest_path = tmp_path / "pack" / "longmemeval_v2_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["trajectories"]["trajectory-1"]["status"] = "preparing"
        manifest_path.write_text(json.dumps(manifest))
    finally:
        memory.close()

    reopened = PRMEMemory(params(tmp_path / "data", tmp_path / "pack"))
    try:
        with pytest.raises(RuntimeError, match="insert was interrupted"):
            reopened.insert(source)
    finally:
        reopened.close()


def test_adapter_validates_configuration_and_public_schema(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="unexpected keys"):
        PRMEMemory({"hidden_answer": True})

    root = tmp_path / "data"
    source = trajectory(root)
    source["states"][1]["state_index"] = 2
    invalid = PRMEMemory(params(root, tmp_path / "unused"))
    try:
        with pytest.raises(RuntimeError, match="contiguous and ordered"):
            invalid.insert(source)
    finally:
        invalid.close()

    source = trajectory(root)
    source["states"][0]["screenshot"] = "missing.png"
    memory = PRMEMemory(params(root, tmp_path / "pack"))
    try:
        with pytest.raises(RuntimeError, match="resolve trajectory screenshot"):
            memory.insert(source)
    finally:
        memory.close()
