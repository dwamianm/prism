"""Opt-in real OS custody; skipped platform execution is reported as a gap."""

import json
import os
import platform
import subprocess
import sys
from datetime import timedelta
from importlib.metadata import version
from uuid import uuid4

import pytest

from prme.privacy.crypto import CryptoProtector
from prme.privacy.keys import KeyBoundary, LocalAccessAuthority, utc_now
from prme.privacy.local_keys import NativeKeyCustody, public_registry
from tests.test_privacy_crypto import Audit, Fence, setup_crypto


@pytest.mark.skipif(os.getenv("PRME_RUN_NATIVE_KEYRING_321") != "1",
                    reason="explicit task-owned native keyring conformance only")
async def test_real_native_task_owned_provision_recovery_loss(tmp_path):
    backend = {"Darwin": "macos", "Windows": "windows", "Linux": "secretservice"}.get(platform.system())
    assert backend is not None
    custody = NativeKeyCustody.create(tmp_path / "public.json", backend=backend)
    _, saved, context, *_ = setup_crypto(clock=utc_now)
    # Fresh finite policy time for native execution, not the frozen fixture clock.
    from prme.privacy.policy import snapshot
    saved = snapshot(None, saved.policy, utc_now())
    context = context.model_copy(update={"event_id": uuid4()})
    boundary = KeyBoundary.from_context(context)
    try:
        descriptor = await custody.provision_wrapping(boundary, "evidence")
        writer = CryptoProtector(public_registry(custody.path))
        payload = await writer.seal(b"PV321_NATIVE_OWNED_SYNTHETIC", context, original_snapshot=saved)
        result = subprocess.run([sys.executable, "-m", "prme.cli", "privacy-keys", "status",
                                  "--registry", str(custody.path)], check=True, capture_output=True, text=True)
        assert not json.loads(result.stdout)["native_private_checked"]
        authority = LocalAccessAuthority(Audit())
        grant = await authority.grant("diagnostic", ((context, saved),), expires_at=utc_now() + timedelta(minutes=1))
        def private_read():
            return custody.unlocked((descriptor.ref,), expires_at=utc_now() + timedelta(minutes=1))
        with private_read() as wrapper:
            reader = CryptoProtector(public_registry(custody.path, private_wrapper=wrapper), authority=authority)
            assert await reader.open(payload, grant, original_snapshot=saved, guard=Fence().guard) == b"PV321_NATIVE_OWNED_SYNTHETIC"
        pack = tmp_path / "pack"
        pack.mkdir()
        await custody.export_recovery(descriptor.ref, tmp_path / "recovery.pem",
                                       passphrase=b"synthetic-native-recovery-321", pack_path=pack)
        # Remove only this freshly generated task's exact native version.
        custody._native.remove(descriptor.ref.key_version)
        from prme.privacy.policy import PrivacyDenied
        with pytest.raises(PrivacyDenied):
            with private_read():
                pass
        await custody.import_recovery(descriptor.ref, tmp_path / "recovery.pem", passphrase=b"synthetic-native-recovery-321")
        with private_read() as wrapper:
            reader = CryptoProtector(public_registry(custody.path, private_wrapper=wrapper), authority=authority)
            assert await reader.open(payload, grant, original_snapshot=saved, guard=Fence().guard) == b"PV321_NATIVE_OWNED_SYNTHETIC"
        print(json.dumps({"platform": platform.platform(), "keyring": version("keyring"),
                          "cryptography": version("cryptography"), "backend": backend,
                          "native_executed": True, "os_prompt_observed": "not_asserted"}))
    finally:
        for entry in custody.inventory():
            await custody.retire(entry.version)
