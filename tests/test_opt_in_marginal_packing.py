from datetime import datetime, timezone
from uuid import UUID
import json

import pytest

from benchmarks.diagnostics.opt_in_marginal_packing import Policy, episode_support, pack
from prme.models.nodes import MemoryNode
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context
from prme.retrieval.tokenization import count_tokens
from prme.types import NodeType, Scope


def candidate(index, session, score, text, pinned=False):
    now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    node = MemoryNode(id=UUID(int=index), user_id='authored', session_id=session, node_type=NodeType.FACT,
        scope=Scope.PERSONAL, content=text, created_at=now, updated_at=now, valid_from=now,
        last_reinforced_at=now, pinned=pinned)
    return RetrievalCandidate(node=node, paths=['VECTOR', 'LEXICAL'], path_count=2, composite_score=score)


def ids(bundle):
    return [c.node.id for rows in bundle.sections.values() for c in rows if c.rendered_text == c.node.content]


def test_full_sources_budget_and_input_scores_are_preserved():
    source = 'I did not buy the red car. I plan to buy a blue car next year. '
    candidates = [candidate(i+1, str(i % 2), .9-i/20, source * 5) for i in range(5)]
    before = [c.model_dump(mode='json') for c in candidates]
    config = PackingConfig(token_budget=900, overhead_tokens=100)
    bundle = pack(candidates, 'Which car did I buy?', config, Policy(.25, .1))
    assert count_tokens(bundle.render(), config.tokenizer) == bundle.tokens_used <= 800
    assert [c.model_dump(mode='json') for c in candidates] == before
    for rows in bundle.sections.values():
        for c in rows:
            if c.representation.value == 'full':
                assert c.rendered_text == c.node.content


def test_extra_session_members_compete_without_reserved_episode_priority():
    a = candidate(1, 'a', .9, 'launch approval record ' * 30)
    weak = candidate(2, 'a', .01, 'launch approval minor record ' * 30)
    b = candidate(3, 'b', .8, 'The launch approval was given by Maya. ' * 20)
    two_records = pack_context([a, b], PackingConfig(token_budget=10000, overhead_tokens=0, min_fidelity='full'))
    config = PackingConfig(token_budget=two_records.tokens_used, overhead_tokens=0, min_fidelity='full')
    bundle = pack([a, weak, b], 'launch approval', config, Policy(.25, .1))
    assert a.node.id in ids(bundle) and b.node.id in ids(bundle)
    assert weak.node.id not in ids(bundle)


def test_pin_precedes_research_selection_and_tiny_budgets_never_overflow():
    pin = candidate(1, 'pin', .001, 'Keep the user instruction. ' * 20, pinned=True)
    other = candidate(2, 'other', .99, 'A high score distractor. ' * 30)
    bundle = pack([pin, other], 'distractor', PackingConfig(token_budget=500, overhead_tokens=0), Policy(.25, .1))
    assert pin.node.id in ids(bundle)
    tiny = pack([pin, other], 'distractor', PackingConfig(token_budget=3, overhead_tokens=0), Policy(.25, .1))
    assert tiny.tokens_used <= 3


def test_support_is_bounded_and_partitioned_by_scope_session():
    a = candidate(1, 'shared', .1, 'launch approval')
    b = candidate(2, 'shared', .1, 'launch date')
    foreign_scope = candidate(3, 'shared', .9, 'launch approval')
    foreign_scope.node.scope = Scope.PROJECT
    values = episode_support([a, b, foreign_scope], 'launch approval')
    assert a.node.id in values and foreign_scope.node.id not in values
    assert all(0 <= value <= 1 for value in values.values())


def test_repeated_execution_is_deterministic():
    values = [candidate(i+1, str(i % 3), .5, 'Evidence for a telescope ' * 10) for i in range(8)]
    config = PackingConfig(token_budget=1200)
    assert pack(values, 'telescope', config, Policy(.25, .1)).render() == pack(values, 'telescope', config, Policy(.25, .1)).render()


async def test_source_assay_replays_a_complete_authored_pack_and_fails_on_context_drift(tmp_path, monkeypatch):
    from benchmarks.diagnostics import opt_in_marginal_study as assay
    from benchmarks.diagnostics.register_opt_in_interactions import clean_config
    case = {'question_id': 'authored', 'question': 'Which telescope color do I prefer?',
        'question_date': '2024/01/02 (Tue) 12:00', 'question_type': 'single-session-user',
        'haystack_session_ids': ['authored-session'], 'haystack_dates': ['2024/01/01 (Mon) 12:00'],
        'haystack_sessions': [[{'role': 'user', 'content': 'I prefer blue telescopes, not red ones.', 'has_answer': True}]],
        'answer_session_ids': ['authored-session']}
    config = clean_config()
    config.update(db_path='{pack}/memory.duckdb', vector_path='{pack}/vectors.usearch',
                  lexical_path='{pack}/lexical_index', duckdb_threads=1,
                  organizer={**config['organizer'], 'opportunistic_enabled': False})
    arm = {'config': config, 'stratum': 'fresh'}
    location = tmp_path / 'opt-in-successor-v2/baseline/authored'
    location.mkdir(parents=True)
    monkeypatch.setattr(assay.study, 'PRIVATE', tmp_path)
    with assay.study.matched_admission():
        await assay.study.capture(case, arm, location, None)
    output = tmp_path / 'source-assay'
    output.mkdir()
    row = await assay.evaluate(case, arm, output)
    assert row['status'] == 'complete'
    capture = json.loads((output / 'cases/authored.json').read_text())
    assert set(capture['arms']) == {'control', *assay.marginal.POLICIES}
    assert all(r['tokens'] <= 3996 and r['evidence']['complete_source_literal_recall'] for r in capture['arms'].values())
    saved = json.loads((location / 'capture.json').read_text())
    saved['retrievals'][0]['context'] += ' changed control'
    (location / 'capture.json').write_text(json.dumps(saved))
    with pytest.raises(assay.study.old.ResearchFailure, match='replay differs'):
        await assay.evaluate(case, arm, tmp_path / 'bad-replay')
