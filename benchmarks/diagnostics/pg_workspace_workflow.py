"""Installed real-BGE PostgreSQL project workflow, including native backup/restore."""

import argparse
import asyncio
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import subprocess
from time import perf_counter
from urllib.parse import urlsplit, urlunsplit, unquote
from uuid import uuid4

import asyncpg
import prme
from prme import MemoryWorkspace, PRMEConfig, Scope, NodeType
from prme.storage.embedding import FastEmbedProvider
from prme.storage.pg.vector_sql import quote_identifier
from workspace_workflow import check, sample


def utility(binary, parts, database, *arguments):
    environment = dict(os.environ, PGHOST=parts.hostname or 'localhost', PGPORT=str(parts.port or 5432),
                       PGUSER=unquote(parts.username or ''), PGPASSWORD=unquote(parts.password or ''),
                       PGDATABASE=database, PGSSLMODE='disable')
    result = subprocess.run([str(binary), *map(str, arguments)], env=environment, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError(f'{binary.name} failed with exit {result.returncode}')


async def run(args, report):
    # This diagnostic requires a disposable local PostgreSQL server with CREATEDB.
    parts = urlsplit(os.environ['PRME_TEST_DATABASE_URL'])
    admin = await asyncpg.connect(os.environ['PRME_TEST_DATABASE_URL'])
    original, copied = 'prme_workflow_' + uuid4().hex, 'prme_workflow_' + uuid4().hex
    created = []
    def config(database):
        url = urlunsplit((parts.scheme, parts.netloc, '/' + database, parts.query, parts.fragment))
        return PRMEConfig(database_url=url, namespace_id=None, organizer={'opportunistic_enabled': False},
                          encryption_enabled=False, encryption_key=None, enable_store_supersedence=False,
                          enable_reranker=False, enable_query_reformulation=False)
    try:
        report['server_version'] = await admin.fetchval('SHOW server_version')
        for database in [original, copied]:
            await admin.execute(f'CREATE DATABASE {quote_identifier(database)}')
            created.append(database)
        provider = FastEmbedProvider()
        await provider.embed(['Aurora'])
        report['provider'] = {'model': provider.model_name, 'version': provider.model_version}
        sample(report, 'shared_provider_warmed')
        expected = {}
        async with MemoryWorkspace.open_postgres(config(original), name='authored-workflow',
                embedding_provider=provider, max_open=4, max_connections=3) as workspace:
            for index in range(args.count):
                name = f'project/{index}'
                async with workspace.namespace(name) as memory:
                    events = [await memory.store('Aurora', user_id='same-owner', scope=Scope.PROJECT,
                                                 node_type=NodeType.ENTITY, metadata={'entity_type': 'project'})
                              for _ in range(2)]
                    result = await memory.organize(jobs=['deduplicate'], budget_ms=30000)
                    assert result.per_job['deduplicate'].nodes_modified == 1
                    entities = await memory.query_nodes(user_id='same-owner', node_type=NodeType.ENTITY)
                    assert len(entities) == 1 and set(map(str, entities[0].evidence_refs)) == set(events)
                    fact = f"Aurora's retention period is {index + 1} days."
                    pending = await memory.ingest_fast(fact, user_id='same-owner', scope=Scope.PROJECT)
                    expected[name] = {'namespace_id': str(memory.namespace.id), 'events': events + [pending],
                                      'entity_id': str(entities[0].id), 'pending': pending, 'fact': fact}
                sample(report, f'populated_{index}')
            assert len(await workspace.list_namespaces()) == args.count
            async def inspect(index):
                name = f'project/{index}'
                foreign = expected[f'project/{(index + 1) % args.count}'] if args.count > 1 else None
                await check(workspace, name, expected[name], foreign)
                assert len(workspace._entries) <= 4
                assert workspace._pool.get_size() <= 3
                report['pool_samples'].append(workspace._pool.get_size())
                sample(report, f'retrieved_{index}')
            await asyncio.gather(*(inspect(i) for i in range(args.count)))
        # The original workspace is closed before a whole-database native backup.
        dump = args.output.with_suffix('.dump')
        utility(args.pg_bin / 'pg_dump', parts, original, '--format=custom', '--no-owner', '--no-acl', '--file', dump)
        report['dump'] = {'path': str(dump), 'sha256': hashlib.sha256(dump.read_bytes()).hexdigest(),
                          'bytes': dump.stat().st_size}
        utility(args.pg_bin / 'pg_restore', parts, copied, '--exit-on-error', '--no-owner', '--no-acl',
                '--dbname', copied, dump)
        async with MemoryWorkspace.open_postgres(config(copied), name='authored-workflow',
                embedding_provider=provider, max_open=4, max_connections=3) as workspace:
            assert len(await workspace.list_namespaces()) == args.count
            for index in range(args.count):
                name = f'project/{index}'
                foreign = expected[f'project/{(index + 1) % args.count}'] if args.count > 1 else None
                await check(workspace, name, expected[name], foreign)
                sample(report, f'restored_{index}')
        report['expected'] = expected
        report['complete'] = True
    finally:
        for database in reversed(created):
            await admin.execute(f'DROP DATABASE {quote_identifier(database)} WITH (FORCE)')
        await admin.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, choices=[2, 100], required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pg-bin', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.with_suffix('.dump').exists():
        raise ValueError('Refusing to overwrite prior evidence')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    helper = Path(__file__).with_name('workspace_workflow.py')
    report = {'complete': False, 'count': args.count, 'max_open': 4, 'max_connections': 3,
              'python': platform.python_version(), 'prme_import_path': str(Path(prme.__file__).resolve()),
              'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest(),
              'versions': {name: version(name) for name in ['prme', 'asyncpg', 'fastembed', 'psutil']},
              'resources': [], 'pool_samples': [],
              'limits': 'Authored workflow; adjacent-project checks, sampled client resources only; '
                        'no live LLM extraction, hosted grants, production-scale latency or competitive QA claim.'}
    start = perf_counter()
    try:
        asyncio.run(run(args, report))
    except BaseException as exc:
        report['complete'] = False
        report['error_type'] = type(exc).__name__
        raise
    finally:
        report['elapsed_seconds'] = perf_counter() - start
        args.output.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
