"""Exercise durable extraction recovery through public processing APIs.

Uses a temporary pack and synthetic source. Requires Ollama plus the selected
model. This is a workflow check, not an extraction accuracy benchmark.
"""

import argparse
import asyncio
import hashlib
import inspect
import json
from pathlib import Path
import tempfile
import time

from prme import MemoryEngine, PRMEConfig
from prme.ingestion.errors import ExtractionError
from prme.ingestion.pipeline import IngestionPipeline


async def run(args):
    calls = {'extract': 0, 'embed': 0, 'stage': 0}
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='prme-live-derivation-') as directory:
        root = Path(directory)
        (root / 'lexical').mkdir()
        config = PRMEConfig(
            database_url=None, encryption_enabled=False, db_path=str(root / 'memory.duckdb'),
            vector_path=str(root / 'vectors.usearch'), lexical_path=str(root / 'lexical'),
            organizer={'opportunistic_enabled': False},
            embedding={'provider': 'fastembed', 'model_name': 'BAAI/bge-small-en-v1.5', 'dimension': 384, 'api_key': None},
            extraction={'provider': 'ollama', 'model': args.model, 'base_url': args.base_url,
                        'api_key': None, 'timeout': args.timeout, 'max_retries': 1},
        )
        async with MemoryEngine.open(config) as engine:
            pipeline = engine._pipeline
            pipeline._retry_delays = ()
            extract, embed, stage = pipeline._extraction_provider.extract, engine._vector_index._provider.embed, engine._vector_index.stage
            async def counted_extract(*args, **kwargs):
                calls['extract'] += 1
                return await extract(*args, **kwargs)
            async def counted_embed(*args, **kwargs):
                calls['embed'] += 1
                return await embed(*args, **kwargs)
            async def failed_stage(*args, **kwargs):
                calls['stage'] += 1
                await stage(*args, **kwargs)
                raise RuntimeError('injected failure after durable vector staging')
            pipeline._extraction_provider.extract = counted_extract
            engine._vector_index._provider.embed = counted_embed
            engine._vector_index.stage = failed_stage
            try:
                await engine.ingest('The Aster service uses PostgreSQL. Production credentials must never be used in staging.',
                                    user_id='alice', wait_for_extraction=True)
            except ExtractionError as failure:
                event_id = failure.event_id
            else:
                raise AssertionError('Expected injected staging failure')
            plan = await engine._event_store.get_derivation_plan(event_id, user_id='alice')
            saved = await engine.get_extraction(event_id, user_id='alice')
            assert plan and saved and saved.result['facts']
            assert calls == {'extract': 1, 'embed': 1, 'stage': 1}, calls
            assert await engine.get_event_nodes(event_id, user_id='alice') == []
            assert await engine._event_store.get_derivation_receipt(event_id, user_id='alice') is None
            failed = await engine.extraction_status(event_id, user_id='alice')
            assert failed.status == 'failed' and failed.phase == 'publication' and failed.attempts == 1
        async with MemoryEngine.open(config) as engine:
            async def forbidden(*args, **kwargs):
                raise AssertionError('Recovery must not call either provider')
            engine._pipeline._extraction_provider.extract = forbidden
            real_embed = engine._vector_index._provider.embed
            engine._vector_index._provider.embed = forbidden
            assert (await engine.extraction_status(event_id, user_id='alice')).status == 'failed'
            assert await engine.extraction_status(event_id, user_id='bob') is None
            assert (await engine.retry_extraction(event_id, user_id='alice')).status == 'pending'
            processed = await engine.process_extractions(user_id='alice')
            assert (processed.processed, processed.pending, processed.failed) == (1, 0, 0)
            complete = await engine.extraction_status(event_id, user_id='alice')
            assert complete.status == 'complete' and complete.attempts == 2
            receipt = await engine._event_store.get_derivation_receipt(event_id, user_id='alice')
            assert receipt.plan_checksum == plan.checksum
            assert {node.id for node in await engine.get_event_nodes(event_id, user_id='alice')} == {node.id for node in plan.nodes}
            assert await engine._event_store.get_derivation_receipt(event_id, user_id='bob') is None
            engine._vector_index._provider.embed = real_embed
            response = await engine.retrieve('What database does the Aster service use?', user_id='alice')
            assert any('PostgreSQL' in candidate.node.content for candidate in response.results)
            for node in plan.nodes:
                await engine.archive(str(node.id))
            engine._vector_index._provider.embed = forbidden
            engine._vector_index.stage = forbidden
            engine._lexical_index.stage = forbidden
            assert (await engine.retry_extraction(event_id, user_id='alice')).status == 'complete'
            assert (await engine.process_extractions(user_id='alice')).processed == 0
            assert await engine._event_store.get_derivation_receipt(event_id, user_id='alice') == receipt
        report = {
            'passed': True,
            'pipeline_sha256': hashlib.sha256(Path(inspect.getfile(IngestionPipeline)).read_bytes()).hexdigest(),
            'package_path': str(Path(inspect.getfile(IngestionPipeline)).resolve()),
            'provider': saved.provider, 'model': saved.model,
            'embedding_model': plan.embeddings[0].model, 'initial_provider_method_calls': calls,
            'facts': len(saved.result['facts']), 'prepared_nodes': len(plan.nodes),
            'elapsed_seconds': round(time.perf_counter() - started, 3),
            'checks': ['real local extraction and embeddings',
                       'journaled plan before injected staging failure', 'no partial graph publication',
                       'restart replays exact plan without either provider', 'scoped receipt and public work status',
                       'public retry and processing after restart without providers',
                       'retrieval finds grounded database fact', 'archived completion skips staging'],
            'limits': 'One synthetic workflow; explicit durable recovery, not extraction accuracy or comparative evidence.',
        }
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("Timeout must be positive")
    try:
        report = asyncio.run(run(args))
    except Exception as exc:
        report = {"passed": False, "error_type": type(exc).__name__,
                  "limits": "Incomplete workflow; no success or accuracy claim."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
