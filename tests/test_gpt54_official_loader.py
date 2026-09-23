import pytest

from benchmarks.integrations.gpt54_official_prompt_loader import load_prompt


def test_loader_executes_only_prompt_function(tmp_path):
    p = tmp_path/'src/evaluation/evaluate_qa.py'
    p.parent.mkdir(parents=True)
    p.write_text("import nonexistent_unused_cli_dependency\n"
                 "raise RuntimeError('CLI side effect')\n"
                 "def get_anscheck_prompt(task, question, answer, response, abstention=False):\n"
                 "    return (task, question, answer, response, abstention)\n")
    assert load_prompt(tmp_path)('t','q','a','r',True) == ('t','q','a','r',True)


def test_loader_rejects_decorated_or_missing_function(tmp_path):
    p = tmp_path/'src/evaluation/evaluate_qa.py'
    p.parent.mkdir(parents=True)
    for source in ['pass', '@wrapper\ndef get_anscheck_prompt():\n    pass']:
        p.write_text(source)
        with pytest.raises(ValueError):
            load_prompt(tmp_path)
