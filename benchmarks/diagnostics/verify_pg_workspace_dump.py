"""Verify restored workspace source and merged-entity evidence without engine startup."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
from prme.storage.pg.vector_sql import quote_identifier
from pg_workspace_workflow import utility


async def verify(args, result):
    report = json.loads(args.report.read_text())
    dump = Path(report['dump']['path'])
    assert report['complete'] is True
    assert hashlib.sha256(dump.read_bytes()).hexdigest() == report['dump']['sha256']
    result.update(parent_report_sha256=hashlib.sha256(args.report.read_bytes()).hexdigest(),
                  dump_sha256=report['dump']['sha256'])
    parts = urlsplit(os.environ['PRME_TEST_DATABASE_URL'])
    admin = await asyncpg.connect(os.environ['PRME_TEST_DATABASE_URL'])
    database = 'prme_verify_' + uuid4().hex
    created = False
    try:
        await admin.execute(f'CREATE DATABASE {quote_identifier(database)}')
        created = True
        utility(args.pg_bin / 'pg_restore', parts, database, '--exit-on-error', '--no-owner', '--no-acl',
                '--dbname', database, dump)
        url = urlunsplit((parts.scheme, parts.netloc, '/' + database, parts.query, parts.fragment))
        conn = await asyncpg.connect(url)
        try:
            async with conn.transaction(readonly=True):
                rows = await conn.fetch('SELECT name, id, initialized FROM prme_workspace.namespaces')
                assert {r['name'] for r in rows} == set(report['expected'])
                for row in rows:
                    expected = report['expected'][row['name']]
                    assert str(row['id']) == expected['namespace_id'] and row['initialized']
                    schema = quote_identifier('prme_ns_' + row['id'].hex)
                    identity = await conn.fetch(f'SELECT version,namespace_id FROM {schema}.prme_namespace_identity')
                    assert len(identity) == 1 and identity[0]['version'] == 1 and identity[0]['namespace_id'] == row['id']
                    entity = await conn.fetchrow(f'SELECT * FROM {schema}.nodes WHERE id=$1', expected['entity_id'])
                    assert entity is not None and entity['content'] == 'Aurora'
                    assert entity['user_id'] == 'same-owner' and entity['scope'] == 'project'
                    assert entity['lifecycle_state'] in {'tentative', 'stable', 'contested'}
                    assert set(json.loads(entity['evidence_refs'])) == set(expected['events'][:2])
                    assert entity['embedding'] is not None
                    assert entity['embedding_model'] == report['provider']['model']
                    assert entity['embedding_version'] == report['provider']['version']
                    edges = await conn.fetch(f"SELECT target_id FROM {schema}.edges WHERE source_id=$1 AND edge_type='supersedes'",
                                             expected['entity_id'])
                    assert len(edges) == 1
                    retired = await conn.fetchrow(f'SELECT lifecycle_state,superseded_by FROM {schema}.nodes WHERE id=$1',
                                                  edges[0]['target_id'])
                    assert retired['lifecycle_state'] == 'superseded' and str(retired['superseded_by']) == expected['entity_id']
                    for index, event_id in enumerate(expected['events']):
                        event = await conn.fetchrow(f'SELECT content,content_hash,user_id,scope FROM {schema}.events WHERE id=$1', event_id)
                        content = 'Aurora' if index < 2 else expected['fact']
                        assert event['content'] == content
                        assert event['content_hash'] == hashlib.sha256(content.encode()).hexdigest()
                        assert event['user_id'] == 'same-owner' and event['scope'] == 'project'
                    result['verified_namespaces'].append(expected['namespace_id'])
                assert len(result['verified_namespaces']) == report['count']
        finally:
            await conn.close()
        result['complete'] = True
    finally:
        if created:
            await admin.execute(f'DROP DATABASE {quote_identifier(database)} WITH (FORCE)')
        await admin.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pg-bin', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing to overwrite verification evidence')
    result = {'complete': False, 'verified_namespaces': [], 'engine_startup': False,
              'verifier_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    try:
        asyncio.run(verify(args, result))
    except BaseException as exc:
        result.update(complete=False, error_type=type(exc).__name__)
        raise
    finally:
        args.output.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
