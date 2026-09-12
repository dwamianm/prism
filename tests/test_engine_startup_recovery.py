"""Failed engine startup releases acquired resources and preserves source history."""

import asyncio
from unittest.mock import patch

import duckdb
import pytest

from prme import MemoryEngine
from tests.test_durable_ingestion import config, user  # noqa: F401


async def test_failed_startup_releases_database_and_queue(config, user, monkeypatch):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        event_id = await engine.store('Durable source before a startup failure', user_id=user)
    connections, pools, queues = [], [], []
    import prme.storage.engine as module
    import prme.storage.pg as pg
    from prme.storage.write_queue import WriteQueue
    initialize = module.initialize_database
    create_pool = pg.create_pool
    start_queue = WriteQueue.start

    def initialize_and_capture(conn):
        connections.append(conn)
        return initialize(conn)

    async def capture_pool(*args, **kwargs):
        pool = await create_pool(*args, **kwargs)
        pools.append(pool)
        return pool

    async def capture_queue(queue):
        queues.append(queue)
        return await start_queue(queue)

    monkeypatch.setattr(module, 'initialize_database', initialize_and_capture)
    monkeypatch.setattr(pg, 'create_pool', capture_pool)
    monkeypatch.setattr(WriteQueue, 'start', capture_queue)
    with patch('prme.ingestion.extraction.create_extraction_provider', side_effect=RuntimeError('startup fault')):
        with pytest.raises(RuntimeError, match='startup fault'):
            await MemoryEngine.create(config)
    for conn in connections:
        with pytest.raises(duckdb.ConnectionException):
            conn.execute('SELECT 1')
    for pool in pools:
        assert pool.is_closing()
    for queue in queues:
        assert queue._consumer_task is None or queue._consumer_task.done()
    # Reopening must work without forcing garbage collection or losing history.
    async with MemoryEngine.open(config) as recovered:
        assert (await recovered.get_event(event_id, user_id=user)).content == 'Durable source before a startup failure'


async def test_cancelled_startup_releases_pool_or_file(config, monkeypatch):  # noqa: F811
    from prme.storage.durable_queue import DurableMaterializationQueue
    reached = asyncio.Event()
    original = DurableMaterializationQueue.debt

    async def block_debt(queue):
        reached.set()
        await asyncio.Future()

    with monkeypatch.context() as failure:
        failure.setattr(DurableMaterializationQueue, 'debt', block_debt)
        task = asyncio.create_task(MemoryEngine.create(config))
        await asyncio.wait_for(reached.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert DurableMaterializationQueue.debt is original
    async with MemoryEngine.open(config) as engine:
        assert engine is not None


@pytest.mark.parametrize('fault', ['provider', 'cancel', 'partial_decrypt'])
async def test_failed_encrypted_open_restores_ciphertext(config, user, monkeypatch, fault):  # noqa: F811
    if config.backend != 'duckdb':
        pytest.skip('File-pack encryption is local-backend specific')
    import json
    from pathlib import Path
    from cryptography.fernet import Fernet
    from pydantic import SecretStr
    from prme.storage.encryption import EncryptionProvider, EncryptionError
    from prme.storage.durable_queue import DurableMaterializationQueue
    config.encryption_enabled = True
    config.encryption_key = SecretStr(Fernet.generate_key().decode())
    async with MemoryEngine.open(config) as engine:
        event = await engine.store('Encrypted original source', user_id=user)
    encrypted_before = {str(p) for p in Path(config.db_path).parent.rglob('*.enc')}
    with monkeypatch.context() as failure:
        if fault == 'provider':
            failure.setattr('prme.ingestion.extraction.create_extraction_provider',
                            lambda _: (_ for _ in ()).throw(RuntimeError('startup fault')))
            with pytest.raises(RuntimeError, match='startup fault'):
                await MemoryEngine.create(config)
        elif fault == 'partial_decrypt':
            original = EncryptionProvider.decrypt_file

            def fail_vector(provider, path):
                if str(path) == config.vector_path + '.enc':
                    raise EncryptionError('interrupted decryption')
                return original(provider, path)

            failure.setattr(EncryptionProvider, 'decrypt_file', fail_vector)
            with pytest.raises(EncryptionError, match='interrupted decryption'):
                await MemoryEngine.create(config)
        else:
            reached = asyncio.Event()

            async def block(queue):
                reached.set()
                await asyncio.Future()

            failure.setattr(DurableMaterializationQueue, 'debt', block)
            task = asyncio.create_task(MemoryEngine.create(config))
            await asyncio.wait_for(reached.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert not Path(config.db_path).exists() and not Path(config.vector_path).exists()
    assert not [p for p in Path(config.lexical_path).iterdir() if p.is_file() and p.suffix != '.enc']
    encrypted_after = {str(p) for p in Path(config.db_path).parent.rglob('*.enc')}
    assert encrypted_before <= encrypted_after
    manifest = json.loads((Path(config.db_path).parent / 'manifest.json').read_text())
    base_dir = Path(config.db_path).parent
    assert {str(base_dir / name) for name in manifest["encryption"]["files"]} == encrypted_after
    async with MemoryEngine.open(config) as recovered:
        assert (await recovered.get_event(event, user_id=user)).content == 'Encrypted original source'


async def test_wrong_key_does_not_mutate_pack(config, user):  # noqa: F811
    if config.backend != 'duckdb':
        pytest.skip('File-pack encryption is local-backend specific')
    from pathlib import Path
    from cryptography.fernet import Fernet
    from pydantic import SecretStr
    from prme.storage.encryption import EncryptionError
    config.encryption_enabled = True
    config.encryption_key = SecretStr(Fernet.generate_key().decode())
    async with MemoryEngine.open(config) as engine:
        await engine.store('Source preserved on invalid credentials', user_id=user)
    root = Path(config.db_path).parent
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    config.encryption_key = SecretStr(Fernet.generate_key().decode())
    with pytest.raises(EncryptionError):
        await MemoryEngine.create(config)
    assert before == {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


async def test_restore_encryption_error_is_visible_and_handles_are_closed(config, user, monkeypatch):  # noqa: F811
    if config.backend != 'duckdb':
        pytest.skip('File-pack encryption is local-backend specific')
    from cryptography.fernet import Fernet
    from pydantic import SecretStr
    from prme.storage.encryption import EncryptionProvider, EncryptionError
    config.encryption_enabled = True
    config.encryption_key = SecretStr(Fernet.generate_key().decode())
    async with MemoryEngine.open(config) as engine:
        event = await engine.store('Recoverable after failed re-encryption', user_id=user)
    with monkeypatch.context() as failure:
        failure.setattr('prme.ingestion.extraction.create_extraction_provider',
                        lambda _: (_ for _ in ()).throw(RuntimeError('startup fault')))
        failure.setattr(EncryptionProvider, 'encrypt_file',
                        lambda *args: (_ for _ in ()).throw(OSError('disk failure')))
        with pytest.raises(EncryptionError, match='may remain plaintext'):
            await MemoryEngine.create(config)
    # Failure is surfaced; after correcting the cause the pack can be reopened
    # and encrypted normally. Closing handles must not depend on encryption.
    async with MemoryEngine.open(config) as recovered:
        assert (await recovered.get_event(event, user_id=user)).content == 'Recoverable after failed re-encryption'
