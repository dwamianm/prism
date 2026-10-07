"""Authored stale-claim context check for #241; no reader/judge or extraction calls.

The public benchmark packs may have no replacement relationships. These cases
exercise explicit SUPERSEDES edges on otherwise active claims, independently of
lifecycle exclusion. They measure node and context state, not answer accuracy.
"""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile

from benchmarks.diagnostics.product_packing import gate_config, _quiet_offline_cli
from benchmarks.retrieval_eval import provenance
from prme import MemoryEngine
from prme.models.edges import MemoryEdge
from prme.types import EdgeType, NodeType, RetrievalMode

REFERENCE = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)


async def run() -> dict:
    rows = []
    with tempfile.TemporaryDirectory(prefix='prme-read-supersedence-') as temp:
        cfg = gate_config(Path(temp))
        identity = provenance(cfg)
        async with MemoryEngine.open(cfg) as engine:
            cases = []
            for kind in (NodeType.FACT, NodeType.PREFERENCE, NodeType.DECISION, NodeType.TASK):
                owner = f'authored-{kind.value}'
                await engine.store('My current editor is Vim.', user_id=owner, node_type=kind,
                                   valid_from=REFERENCE-timedelta(days=2))
                await engine.store('My current editor is Emacs.', user_id=owner, node_type=kind,
                                   valid_from=REFERENCE-timedelta(days=1))
                nodes = await engine.query_nodes(user_id=owner, node_type=kind)
                old = next(n for n in nodes if 'Vim' in n.content)
                new = next(n for n in nodes if 'Emacs' in n.content)
                await engine._graph_store.create_edge(MemoryEdge(
                    source_id=new.id, target_id=old.id, user_id=owner, edge_type=EdgeType.SUPERSEDES,
                    valid_from=REFERENCE-timedelta(days=1)))
                cases.append((owner, old, new))
        for arm in ('control', 'suppression'):
            enabled = cfg.model_copy(update={'enable_read_supersedence': arm == 'suppression'})
            async with MemoryEngine.open(enabled) as engine:
                for owner, old, new in cases:
                    for request, query, extra in (
                        ('current', 'What is my current editor?', {}),
                        ('historical', 'What was my editor previously?', {}),
                        ('explicit', 'What is my current editor?', {'retrieval_mode': RetrievalMode.EXPLICIT}),
                    ):
                        response = await engine.retrieve(query, user_id=owner, reference_time=REFERENCE, **extra)
                        packed = {c.node.id for group in response.bundle.sections.values() for c in group}
                        receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=owner)
                        assert receipt.replay_ranking() == tuple(c.node.id for c in response.results)
                        assert (await engine._graph_store.get_node(str(old.id))).model_dump() == old.model_dump()
                        rows.append({'arm': arm, 'kind': old.node_type.value, 'request': request,
                                     'prior_claim_in_context': old.id in packed, 'current_claim_in_context': new.id in packed,
                                     'prior_node_unchanged': True, 'receipt': receipt.model_dump(mode='json')})
        for row in rows:
            assert row['current_claim_in_context']
            assert row['prior_claim_in_context'] == (row['arm'] == 'control' or row['request'] != 'current')
    return {'kind': 'authored-read-supersedence-context-check', 'complete': True, 'provenance': identity,
            'limitations': 'Explicit authored edges on active claims; not native extraction, benchmark answer accuracy, or dependent-record cascade.',
            'summary': {arm: {'current_cases': 4, 'prior_claims_in_current_context': sum(
                row['prior_claim_in_context'] for row in rows if row['arm'] == arm and row['request'] == 'current')}
                for arm in ('control', 'suppression')}, 'rows': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    _quiet_offline_cli()
    report = asyncio.run(run())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report['summary'], indent=2))


if __name__ == '__main__':
    main()
