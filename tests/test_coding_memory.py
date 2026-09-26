"""Coding adapter preserves repository boundaries, provenance and MCP identity."""

import json
import subprocess

import httpx
import pytest

from prme.integrations.coding import (
    CodingMemory,
    MemoryTools,
    configure,
    repository,
    source_excerpt,
)


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "notes.md").write_text("first\nsecond\nthird\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "notes.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    return tmp_path


def test_private_configuration_survives_repeat_and_worktrees(repo, tmp_path):
    path = configure(repo, port=18769)
    original = path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    assert configure(repo, port=18770).read_bytes() == original
    compose = json.loads(path.with_name("compose.json").read_text())
    assert compose["services"]["memory"]["ports"] == ["127.0.0.1:18769:8000"]
    worktree = tmp_path.parent / (tmp_path.name + "-worktree")
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", str(worktree)],
        check=True,
        capture_output=True,
    )
    assert repository(worktree)[1] == repository(repo)[1]
    assert configure(worktree).read_bytes() == original


def test_explicit_tracked_evidence_and_path_boundaries(repo):
    item = source_excerpt(repo, "notes.md:2-3")
    assert item["text"] == "second\nthird\n"
    assert len(item["sha256"]) == 64
    (repo / "untracked").write_text("private")
    with pytest.raises(subprocess.CalledProcessError):
        source_excerpt(repo, "untracked")
    with pytest.raises(ValueError):
        source_excerpt(repo, "../outside")
    (repo / "outside-link").symlink_to(repo.parent)
    with pytest.raises(ValueError):
        source_excerpt(repo, "outside-link/private")
    with pytest.raises(ValueError):
        source_excerpt(repo, "notes.md:0-3")


def test_capture_preserves_source_and_does_not_claim_verification(repo):
    configure(repo)
    calls = []

    def respond(request):
        calls.append((request, json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                "result": {"content": [{"type": "text", "text": '{"node_id":"test"}'}]}
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        memory = CodingMemory(repo, client=client)
        memory.remember(
            "This might fix it.", status="hypothesis", sources=["notes.md:2"]
        )
        request, call = calls[0]
        args = call["params"]["arguments"]
        assert request.headers["Authorization"].startswith("Bearer ")
        assert args["role"] == "assistant" and args["scope"] == "project"
        assert "second\n" in args["content"]
        assert "second\n" not in args["retrieval_content"]
        provenance = args["metadata"]["coding_memory_v1"]
        assert provenance["status"] == "hypothesis" and not provenance["dirty"]
        (repo / "notes.md").write_text("changed\n")
        memory.remember("Changed", sources=["notes.md"])
        assert calls[1][1]["params"]["arguments"]["metadata"]["coding_memory_v1"][
            "dirty"
        ]


def test_recall_is_bounded_scoped_and_returns_context(repo):
    configure(repo)
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200, json={"result": {"content": [{"text": '{"context":"bounded"}'}]}}
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        memory = CodingMemory(repo, client=client)
        assert memory.recall("task", budget=1024)["context"] == "bounded"
        args = calls[0]["params"]["arguments"]
        assert args["scope"] == "project" and not args["include_cross_scope"]
        assert args["include_context"] and args["token_budget"] == 1024
        with pytest.raises(ValueError):
            memory.recall("task", budget=0)


@pytest.mark.parametrize(
    "response",
    [
        {"error": {"message": "broken"}},
        {"result": {"isError": True}},
        {"result": {"content": [{"text": '{"error":"not found"}'}]}},
    ],
)
def test_protocol_and_tool_failures_surface(response):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    ) as client:
        memory = MemoryTools("http://127.0.0.1:18765/mcp", "token", client=client)
        with pytest.raises(RuntimeError):
            memory.recall("test")


def test_remote_endpoint_rejected():
    with pytest.raises(ValueError):
        MemoryTools("https://example.com/mcp", "private")
