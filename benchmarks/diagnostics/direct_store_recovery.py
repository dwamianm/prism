"""Installed-package recovery with real embeddings and synchronous public APIs."""
import argparse
import hashlib
import importlib.metadata
import inspect
import json
from pathlib import Path
import platform
import sys
import tempfile
from prme import EpistemicType, MemoryClient, MemoryEngine, MaterializationError, NodeType, PRMEConfig, SourceType

def run():
    with tempfile.TemporaryDirectory(prefix='prme-direct-store-probe-') as directory:
        root = Path(directory)
        (root / 'lexical').mkdir()
        config = PRMEConfig(database_url=None, encryption_enabled=False,
            db_path=str(root / 'memory.duckdb'), vector_path=str(root / 'vectors.usearch'),
            lexical_path=str(root / 'lexical'), organizer={'opportunistic_enabled': False},
            embedding={'provider': 'fastembed', 'model_name': 'BAAI/bge-small-en-v1.5', 'dimension': 384, 'api_key': None})
        async def outage(*args, **kwargs):
            raise OSError('Synthetic storage outage')
        async def no_llm(*args, **kwargs):
            raise AssertionError('Direct storage must not invoke extraction')
        with MemoryClient(config=config) as client:
            engine = client._engine
            engine._pipeline._extraction_provider.extract = no_llm
            original = engine._vector_index.index
            engine._vector_index.index = outage
            eid = client.store('Always record observation timestamps in UTC', user_id='alice',
                               node_type=NodeType.INSTRUCTION, confidence=.87, ttl_days=43,
                               epistemic_type=EpistemicType.ASSERTED, source_type=SourceType.USER_STATED)
            engine._vector_index.index = original
            saved = client.get_event_nodes(eid, user_id='alice')[0]
            assert client.processing_status(eid, user_id='alice').status == 'pending'
            original_create = engine._graph_store.create_node
            engine._graph_store.create_node = outage
            try:
                client.store('Use PostgreSQL for the observatory database', user_id='alice', node_type=NodeType.DECISION)
            except MaterializationError as failure:
                second_id = failure.event_id
                assert failure.reason_code == 'OSError'
            else:
                raise AssertionError('Graph creation must report accepted work')
            finally:
                engine._graph_store.create_node = original_create
            assert client.get_event_nodes(second_id, user_id='alice') == []
        with MemoryClient(config=config) as client:
            client._engine._pipeline._extraction_provider.extract = no_llm
            assert client.process_pending(user_id='bob').processed == 0
            result = client.process_pending(user_id='alice', budget_ms=60000)
            assert (result.processed, result.pending, result.failed) == (2, 0, 0)
            node = client.get_event_nodes(eid, user_id='alice')[0]
            assert (node.id, node.created_at, node.confidence, node.ttl_days, node.node_type) == (
                saved.id, saved.created_at, saved.confidence, 43, NodeType.INSTRUCTION)
            assert node.epistemic_type == EpistemicType.ASSERTED
            assert node.source_type == SourceType.USER_STATED
            assert client.get_event_nodes(second_id, user_id='alice')[0].node_type == NodeType.DECISION
            response = client.retrieve('Which timezone should observation timestamps use?', user_id='alice')
            assert any(candidate.node.id == node.id for candidate in response.results)
            assert client.processing_status(eid, user_id='alice').status == 'complete'
            assert client.processing_status(second_id, user_id='alice').status == 'complete'
    implementation = Path(inspect.getfile(MemoryEngine))
    return {'passed': True, 'python': platform.python_version(), 'package_path': str(implementation),
            'engine_sha256': hashlib.sha256(implementation.read_bytes()).hexdigest(),
            'dependencies': {name: importlib.metadata.version(name) for name in ('prme','duckdb','tantivy','usearch','fastembed','onnxruntime')},
            'checks': ['public synchronous store retains pending indexing', 'graph failure exposes saved event ID',
                       'restart repairs both requests with real default embeddings', 'foreign user cannot drain requests',
                       'original typed fields preserved', 'retrieval finds repaired instruction', 'no LLM extraction invoked'],
            'limits': 'Two synthetic recovery workflows with injected storage faults. Not an accuracy or latency benchmark.'}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        report = run()
    else:
        from benchmarks.diagnostics._process import checked_report
        report = checked_report([sys.executable, '-m', 'benchmarks.diagnostics.direct_store_recovery', '--worker'], timeout=180)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    if not args.worker:
        print(json.dumps(report, indent=2))
    raise SystemExit(0 if report['passed'] else 1)
