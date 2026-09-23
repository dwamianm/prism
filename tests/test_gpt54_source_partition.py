import ast

from benchmarks.integrations.gpt54_locomo_sources import CompletionCount, SourceView


def test_partition_changes_only_completion_count_literal():
    original = ast.parse("result = {'questions':1540, 'other':1540}; limit = 3996")
    t = CompletionCount(42)
    value = t.visit(original)
    assert t.changes == 1
    assert ast.dump(value) == ast.dump(ast.parse("result = {'questions':42, 'other':1540}; limit = 3996"))


def test_source_view_keeps_exact_unicode_source_and_question_values():
    import json
    original = [{'sample_id':'conv-1','conversation':{'text':'A café'},'qa':[{'answer':'untouched'}]}]
    assert json.loads(SourceView(original).read_text()) == original
