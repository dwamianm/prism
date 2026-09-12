"""Rebuildable ownership of identities allocated by immutable derivation plans.

A retired plan's index entries can only be reclaimed if no other plan owns the
same identities. Legacy overlapping plans are retained as ambiguous ownership;
new plans must allocate fresh identities. The journal remains authoritative.
"""
import json
import logging

from prme.models.derivation import DerivationPlan

logger = logging.getLogger(__name__)

DDL = (
    'CREATE TABLE IF NOT EXISTS derivation_artifact_owners (node_id TEXT PRIMARY KEY, operation_id TEXT)',
    'CREATE TABLE IF NOT EXISTS derivation_registered_plans (operation_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, event_id TEXT NOT NULL, revision INTEGER NOT NULL)',
)


def decode_plan(payload) -> DerivationPlan:
    body = json.loads(payload) if isinstance(payload, str) else payload
    plan = (DerivationPlan.model_validate_json(body['plan']) if isinstance(body['plan'], str)
            else DerivationPlan.model_validate(body['plan']))
    if plan.checksum != body['checksum']:
        raise ValueError('Prepared derivation checksum does not match')
    return plan


def register_duck(conn, plan: DerivationPlan, *, legacy: bool = False) -> None:
    for node in sorted(plan.nodes, key=lambda n: str(n.id)):
        conn.execute('INSERT INTO derivation_artifact_owners VALUES (?, ?) ON CONFLICT DO NOTHING',
                     [str(node.id), plan.prepared_operation_id])
        owner = conn.execute('SELECT operation_id FROM derivation_artifact_owners WHERE node_id = ?', [str(node.id)]).fetchone()[0]
        if owner != plan.prepared_operation_id:
            if not legacy:
                raise ValueError('Derivation artifact identity belongs to another plan')
            conn.execute('UPDATE derivation_artifact_owners SET operation_id = NULL WHERE node_id = ?', [str(node.id)])
    conn.execute('INSERT INTO derivation_registered_plans VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING',
                 [plan.prepared_operation_id, str(plan.id), str(plan.event_id), plan.revision])


async def register_pg(conn, plan: DerivationPlan, *, legacy: bool = False) -> None:
    for node in sorted(plan.nodes, key=lambda n: str(n.id)):
        await conn.execute('INSERT INTO derivation_artifact_owners VALUES ($1, $2) ON CONFLICT DO NOTHING',
                           str(node.id), plan.prepared_operation_id)
        owner = await conn.fetchval('SELECT operation_id FROM derivation_artifact_owners WHERE node_id = $1', str(node.id))
        if owner != plan.prepared_operation_id:
            if not legacy:
                raise ValueError('Derivation artifact identity belongs to another plan')
            await conn.execute('UPDATE derivation_artifact_owners SET operation_id = NULL WHERE node_id = $1', str(node.id))
    await conn.execute('INSERT INTO derivation_registered_plans VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING',
                       plan.prepared_operation_id, str(plan.id), str(plan.event_id), plan.revision)


_PENDING = ("SELECT o.id, o.payload FROM operations o WHERE o.op_type = 'DERIVATION_PREPARED' "
            'AND NOT EXISTS (SELECT 1 FROM derivation_registered_plans r WHERE r.operation_id = o.id) ORDER BY o.id')


def initialize_duck(conn) -> None:
    for statement in DDL:
        conn.execute(statement)
    conn.execute('BEGIN TRANSACTION')
    try:
        cursor = ''
        while True:
            rows = conn.execute(_PENDING.replace(' ORDER BY o.id', ' AND o.id > ? ORDER BY o.id LIMIT 128'), [cursor]).fetchall()
            if not rows:
                break
            for operation_id, payload in rows:
                cursor = operation_id
                try:
                    plan = decode_plan(payload)
                    if plan.prepared_operation_id != operation_id:
                        raise ValueError('Prepared derivation operation identity does not match')
                except (ValueError, KeyError, TypeError):
                    # Keep raw-source reads available. An incomplete registry
                    # must disable reclamation until the journal is repaired.
                    logger.warning('Derivation ownership registration incomplete for %s', operation_id)
                    continue
                register_duck(conn, plan, legacy=True)
        conn.execute('COMMIT')
    except BaseException:
        conn.execute('ROLLBACK')
        raise


async def initialize_pg(conn) -> None:
    async with conn.transaction():
        for statement in DDL:
            await conn.execute(statement)
        cursor = ''
        while True:
            rows = await conn.fetch(_PENDING.replace(' ORDER BY o.id', ' AND o.id > $1 ORDER BY o.id LIMIT 128'), cursor)
            if not rows:
                break
            for row in rows:
                cursor = row['id']
                try:
                    plan = decode_plan(row['payload'])
                    if plan.prepared_operation_id != row['id']:
                        raise ValueError('Prepared derivation operation identity does not match')
                except (ValueError, KeyError, TypeError):
                    logger.warning('Derivation ownership registration incomplete for %s', row['id'])
                    continue
                await register_pg(conn, plan, legacy=True)
