"""Real-embedding installed-client profile workflow; not an accuracy benchmark."""
import argparse
import hashlib
import inspect
import json
from pathlib import Path
import sys
import tempfile

from benchmarks.diagnostics._process import checked_report


def run():
    from prme import MemoryClient, MemoryEngine, PRMEConfig
    from prme.retrieval.tokenization import count_tokens
    from prme.types import EpistemicType, NodeType, Scope, SourceType

    with tempfile.TemporaryDirectory(prefix='prme-profile-fidelity-') as directory:
        root = Path(directory)
        config = PRMEConfig(database_url=None, encryption_enabled=False,
            db_path=str(root/'memory.duckdb'), vector_path=str(root/'vectors.usearch'),
            lexical_path=str(root/'lexical'), organizer={'opportunistic_enabled': False},
            embedding={'provider': 'fastembed', 'model_name': 'BAAI/bge-small-en-v1.5', 'dimension': 384, 'api_key': None},
            extraction={'provider': 'ollama', 'model': 'unused'})
        prefix = "Aurora's production deployment policy for the customer environment, after the security review, "
        texts = [prefix+'allows external access if approved.', prefix+'does not allow external access without approval.']
        with MemoryClient(config=config) as client:
            for text in texts:
                client.store(text, user_id='alice', scope=Scope.PROJECT,
                             epistemic_type=EpistemicType.CONDITIONAL, confidence=.25)
            for owner, scope, text in [('alice', Scope.PERSONAL, 'Aurora private diary.'),
                ('bob', Scope.PROJECT, 'Aurora uses another database.'),
                ('alice', Scope.PROJECT, 'Joanna likes tea.'), ('alice', Scope.PROJECT, 'Joanna likes coffee.')]:
                client.store(text, user_id=owner, scope=scope)
            events = [event.model_dump(mode='json') for event in client.get_events('alice')]
            assert client.consolidate_knowledge(user_id='alice', entity_names=['Ann']) == 0
            assert client.consolidate_knowledge(user_id='alice', scope=Scope.PROJECT,
                entity_names=['Aurora'], max_profile_tokens=1000) == 1
            profile = client.query_nodes(user_id='alice', node_type=NodeType.SUMMARY)[0]
            assert all(text in profile.content for text in texts)
            assert profile.epistemic_type == EpistemicType.INFERRED and profile.source_type == SourceType.SYSTEM_INFERRED
            assert profile.confidence == .25 and len(profile.evidence_refs) == 2
            assert profile.metadata['source_count_available'] == profile.metadata['source_count_included'] == 2
            assert profile.metadata['tokens_used'] == count_tokens(profile.content, config.packing.tokenizer) <= 1000
            assert 'epistemic=conditional' in profile.content and 'source_type=user_stated' in profile.content
            assert [event.model_dump(mode='json') for event in client.get_events('alice')] == events
            snapshot = profile.model_dump(mode='json')
        with MemoryClient(config=config) as client:
            assert client.get_node(str(profile.id), user_id='alice').model_dump(mode='json') == snapshot
            result = client.retrieve('Aurora deployment policy external access', user_id='alice',
                scope=Scope.PROJECT, include_cross_scope=False, min_score=0, token_budget=4096)
            assert str(profile.id) in {str(row.node.id) for row in result.results}
            context = result.bundle.render()
            assert all(text in context for text in texts)
            assert 'private diary' not in context and 'another database' not in context
            assert client.consolidate_knowledge(user_id='alice', scope=Scope.PROJECT,
                entity_names=['Aurora'], max_profile_tokens=1000) == 1
            rebuilt = client.query_nodes(user_id='alice', node_type=NodeType.SUMMARY)
            assert len(rebuilt) == 1 and rebuilt[0].content == profile.content
            assert [event.model_dump(mode='json') for event in client.get_events('alice')] == events
        return {'passed': True, 'package_path': str(Path(inspect.getfile(MemoryEngine)).resolve()),
            'embedding_model': 'BAAI/bge-small-en-v1.5', 'profile_format': 2,
            'profile_tokens': profile.metadata['tokens_used'],
            'profile_sha256': hashlib.sha256(profile.content.encode()).hexdigest(),
            'checks': ['complete-name matching', 'qualified sources retained', 'inferred profile and confidence cap',
                'source provenance and exact token count', 'restart durability', 'real retrieval owner/scope isolation',
                'idempotent profile content on rebuild', 'immutable source events'],
            'limits': 'One authored installed-client workflow; no extraction, answer-accuracy or comparative claim.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        result = run() if args.worker else checked_report(
            [sys.executable, '-m', 'benchmarks.diagnostics.profile_fidelity', '--worker'], timeout=180)
    except Exception as exc:
        result = {'passed': False, 'error_type': type(exc).__name__}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
