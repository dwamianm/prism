"""The experimental head cannot displace pins or bypass the real token budget."""
from benchmarks.diagnostics.packing_head import pack_head
from prme.models import MemoryNode
from prme.retrieval import packing
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate


def candidate(text, score, **kwargs):
    return RetrievalCandidate(node=MemoryNode(user_id='u', node_type='note',
        content=text, **kwargs), composite_score=score, path_count=2,
        paths=['VECTOR', 'LEXICAL'])


def test_head_preserves_whole_qualified_source_and_restores_inputs():
    head = candidate('Only if the pilot succeeds. ' * 30, .99)
    short = [candidate(f'Short unrelated note {i}.', .4) for i in range(8)]
    cfg = PackingConfig(token_budget=1000, overhead_tokens=0, min_fidelity='full')
    cfg = cfg.model_copy(update={'token_budget': packing.pack_context([head], cfg).tokens_used})
    candidates = [*short, head]
    before = [c.model_dump(mode='json') for c in candidates]
    original = packing.compute_str
    baseline = packing.pack_context(candidates, cfg)
    assert str(head.node.id) not in baseline.render()
    result = pack_head(candidates, cfg)
    assert result.render() == packing.pack_context([head], cfg).render()
    assert result.tokens_used <= cfg.token_budget
    assert [c.model_dump(mode='json') for c in candidates] == before
    assert packing.compute_str is original


def test_pin_retains_priority_over_head():
    pin = candidate('Pinned evidence.', .01, pinned=True)
    head = candidate('Other evidence.', 1)
    cfg = PackingConfig(token_budget=1000, overhead_tokens=0, min_fidelity='full')
    expected = packing.pack_context([pin], cfg)
    cfg = cfg.model_copy(update={'token_budget': expected.tokens_used})
    assert pack_head([head, pin], cfg).render() == expected.render()


def test_oversize_head_is_excluded_without_truncation():
    head = candidate('A very long conditional statement. ' * 200, 1)
    short = candidate('Small available evidence.', .1)
    cfg = PackingConfig(token_budget=500, overhead_tokens=50, min_fidelity='full')
    result = pack_head([head, short], cfg)
    assert result.render() == packing.pack_context([short], cfg).render()
    assert head.node.id in result.excluded_ids
    assert result.tokens_used <= cfg.token_budget - cfg.overhead_tokens
