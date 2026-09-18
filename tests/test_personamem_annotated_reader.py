"""The diagnostic may select source text, but may never inject oracle-only text."""

import json
from types import SimpleNamespace

import pytest

from benchmarks.diagnostics.personamem_annotated_reader import annotated_context
from benchmarks.evidence import SourceTurn


def test_only_actual_role_text_matches_survive_in_original_order():
    turns = [SourceTurn("t00000", "", "user", "Only in summer.", ""),
             SourceTurn("t00001", "", "assistant", "Only in summer.", ""),
             SourceTurn("t00002", "", "user", "Other source", ""),
             SourceTurn("t00003", "", "assistant", "Preserve the condition.", "")]
    snippets = [{"role": "assistant", "content": "Preserve the condition."},
                {"role": "user", "content": "Only in summer.", "hidden_label": "do not copy"},
                {"role": "user", "content": "Oracle-only secret"}]
    result = annotated_context(turns, snippets)
    assert result["source_ids"] == ["t00000", "t00003"]
    assert result["source_roles"] == ["user", "assistant"]
    assert result["context"] == "[USER source=t00000]\nOnly in summer.\n\n[ASSISTANT source=t00003]\nPreserve the condition."
    assert "hidden" not in result["context"] and "Oracle" not in result["context"]
    assert result == annotated_context(turns, list(reversed(snippets)))


def test_unmatched_sensitive_annotation_leaves_empty_control():
    result = annotated_context([SourceTurn("t00000", "", "user", "Public source", "")],
                               [{"role": "user", "content": "Secret absent from source history"}])
    assert result["context"] == "" and result["tokens"] == 0 and result["source_ids"] == []


def test_over_budget_does_not_clip_or_drop_source():
    text = "Full qualified source. " * 5000
    with pytest.raises(ValueError, match="Do not clip"):
        annotated_context([SourceTurn("t00000", "", "user", text, "")], [{"role": "user", "content": text}])


@pytest.mark.parametrize("snippet", [[], {}, [{"role": "system", "content": "oracle"}],
                                    [{"role": "user", "content": {"text": "unsupported"}}]])
def test_invalid_annotation_fails_without_substitution(snippet):
    with pytest.raises(ValueError):
        annotated_context([], snippet)


@pytest.mark.parametrize("fail_at", [None, 4])
def test_reader_loop_preserves_complete_coverage_or_retains_failure(tmp_path, monkeypatch, fail_at):
    from benchmarks.diagnostics import personamem_annotated_reader as runner
    from benchmarks.diagnostics.packing_reader import canonical, digest, write
    prepared = []
    for i in range(96):
        context = annotated_context([SourceTurn("t00000", "", "user", "Only in summer.", "")],
                                    [{"role": "user", "content": "Only in summer."}])
        prepared.append({"question": {"id": str(i), "persona_id": str(i // 4), "query": "Which route?",
            "options": ["one", "two", "three", "four"]}, "contexts": {"annotated_source": context,
            "no_memory": {"context": "", "tokens": 0, "sha256": digest(b""), "source_ids": [], "source_roles": []}}})
    observed = {"reader": {"model": "authored"}}
    references = [{"hidden-label": "never send to reader"} for _ in prepared]
    monkeypatch.setattr(runner, "registered_inputs", lambda _: (prepared, references, observed))
    args = SimpleNamespace(plan=tmp_path / "plan.json", artifacts=tmp_path / "artifacts", output=tmp_path / "output.json",
                           run_id="authored", base_url="unused")
    write(args.plan, {"inputs": observed, "registered_at": "2026-01-01"})
    calls = []
    def request(base, path, payload):
        assert "hidden-label" not in canonical(payload).decode()
        calls.append(payload)
        if len(calls) == fail_at:
            raise OSError("Authored transport failure")
        return {"model": "authored", "done": True, "done_reason": "stop", "prompt_eval_count": 100, "eval_count": 7,
                "message": {"content": '{"answer":"A"}'}}
    monkeypatch.setattr(runner, "request", request)
    if fail_at is None:
        runner.run(args)
        result = json.loads(args.output.read_bytes())
        assert result["complete"] and result["errors"] == 0
        assert len(result["details"]) == 96 and len(result["reader_repeats"]) == 24 and len(calls) == 216
    else:
        with pytest.raises(OSError, match="transport"):
            runner.run(args)
        result = json.loads(args.output.read_bytes())
        assert not result["complete"] and result["errors"] == 1 and result["error_type"] == "OSError"
        assert len(result["details"]) == 1 and len(calls) == fail_at
