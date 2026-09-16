"""Measure real-model raw-source batch recovery and exact serial retrieval parity.

All arms copy the same unprocessed source artifact. Models are warmed before
processing; startup/model-loading time is excluded. This authored local diagnostic
is not a competitive speed or memory-quality benchmark.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys
import tempfile
import time
from uuid import uuid4

from prme import MemoryEngine
from benchmarks.diagnostics.hybrid_lexical import raw_config, embedding_identity, candidate_bytes
from benchmarks.diagnostics.packing_reader import canonical, digest, write
from benchmarks.retrieval_eval import provenance, supervise


def config_at(root):
    return raw_config().model_copy(update={'db_path': str(root/'memory.duckdb'),
                                          'lexical_path': str(root/'lexical'),
                                          'vector_path': str(root/'vectors.usearch')})


async def run(args):
    initial = provenance(raw_config())
    assets = await embedding_identity()
    report = {'run_id': args.run_id, 'complete': False, 'errors': 0, 'provenance': initial,
              'embedding_identity': assets, 'sources': args.sources, 'repetitions': args.repetitions,
              'runner_sha256': digest(Path(__file__).read_bytes()), 'trials': [],
              'limits': 'Authored local histories, warmed real BGE model, one host. Startup/model loading excluded. '
                        'Concurrent hybrid study may affect timings. No competitive speed or quality claim.'}
    write(args.output, report)
    with tempfile.TemporaryDirectory(prefix='prme-batch-diagnostic-') as tmp:
        root = Path(tmp)
        original = root/'original'
        original.mkdir()
        source_config = config_at(original)
        contents = [f'Observation {i}: The cobalt telescope retains calibration records for thirty days. '
                    'The nightly procedure verifies the optical filter and records the observer name.'
                    for i in range(args.sources)]
        async with MemoryEngine.open(source_config) as engine:
            for i, content in enumerate(contents):
                await engine.ingest_fast(content, user_id='authored', metadata={'source_turn':str(i)},
                                         role='user' if i % 2 else 'assistant', session_id=f'episode-{i//4}')
        report['source_texts_sha256'] = digest(canonical(contents))
        report['source_artifact_sha256'] = digest((original/'memory.duckdb').read_bytes())
        reference_time = datetime.now(timezone.utc)
        order = [(repetition, mode) for repetition in range(args.repetitions) for mode in ('serial','batch')]
        order.sort(key=lambda value: digest(canonical(value)))
        report['order'] = order
        reference = None
        for repetition, mode in order:
            directory = root/f'{repetition}-{mode}'
            shutil.copytree(original, directory)
            async with MemoryEngine.open(config_at(directory)) as engine:
                await engine._vector_index._provider.embed(['Authored embedding warm-up.'])
                committed = 0
                original_writer = engine._lexical_index._ensure_writer
                class CountedWriter:
                    def __init__(self, writer):
                        self.writer = writer
                    def __getattr__(self, name):
                        return getattr(self.writer, name)
                    def commit(self):
                        nonlocal committed
                        committed += 1
                        return self.writer.commit()
                engine._lexical_index._ensure_writer = lambda: CountedWriter(original_writer())
                start = time.perf_counter()
                if mode == 'serial':
                    for event in await engine._event_store.pending_materializations(user_id='authored', limit=args.sources+1):
                        await engine._materialization_queue.process_one(engine, event)
                else:
                    outcome = await engine.process_pending(user_id='authored', budget_ms=60000)
                    if outcome.processed != args.sources or outcome.pending or outcome.failed:
                        raise ValueError('Batch did not complete all authored sources')
                seconds = time.perf_counter()-start
                if engine.materialization_debt:
                    raise ValueError('Unacknowledged sources remain')
                expected = args.sources if mode == 'serial' else 1
                if committed != expected:
                    raise ValueError('Unexpected native lexical commit count')
                contexts = []
                for query in ('How long are calibration records retained?', 'What is the nightly telescope procedure?'):
                    response = await engine.retrieve(query, user_id='authored', reference_time=reference_time,
                                                     include_cross_scope=False, token_budget=4096)
                    if not response.results or not response.bundle.render():
                        raise ValueError('Authored retrieval returned no context')
                    contexts.append({'candidates_sha256':digest(candidate_bytes(response)),
                                     'context_sha256':digest(response.bundle.render().encode())})
                if reference is None:
                    reference = contexts
                elif reference != contexts:
                    raise ValueError('Batching or repetition changed product candidates or contexts')
                report['trials'].append({'repetition':repetition, 'mode':mode, 'seconds':seconds,
                                        'lexical_commits':committed, 'retrieval':contexts})
            write(args.output, report)
            print(f'Completed {len(report["trials"])}/{len(order)} authored trials', flush=True)
    if provenance(raw_config()) != initial or await embedding_identity() != assets:
        raise ValueError('Runtime or model assets changed during the diagnostic')
    report.update(complete=True, candidate_and_context_parity=True)
    write(args.output, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sources', type=int, default=32)
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--run-id', default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.sources <= 500 or not 1 <= args.repetitions <= 10:
        raise ValueError('Sources must be 1–500 and repetitions 1–10')
    if args.worker:
        asyncio.run(run(args))
        return
    if args.output.exists():
        raise ValueError('Refusing to overwrite prior diagnostic')
    run_id = str(uuid4())
    report = supervise(args.output, [sys.executable, '-m', __spec__.name, *sys.argv[1:],
                                    '--worker', '--run-id', run_id], run_id)
    raise SystemExit(0 if report['complete'] else 1)


if __name__ == '__main__':
    main()
