"""CL06 component checks: actual HMAC/native custody with inherited OS adapter fixture."""

from uuid import uuid4

import pytest

from prme.privacy.capabilities import RetryContext
from prme.privacy.policy import PrivacyDenied
from prme.privacy.retry_commitments import NativeRetryCommitter
from tests import test_privacy_keys as key_fixtures
from tests.test_privacy_crypto import NOW, setup_crypto

custody = key_fixtures.custody
native = key_fixtures.native


async def test_CL06_component_scoped_original_policy_equality(custody):
    _, saved, *_ = setup_crypto()
    context = RetryContext(request_id=uuid4(), snapshot=saved)
    committer = NativeRetryCommitter(custody, clock=lambda: NOW)
    first = await committer.commit(b"sanitized accepted input", context)
    assert await committer.commit(b"sanitized accepted input", context) == first
    assert await committer.verify(b"sanitized accepted input", first)
    assert not await committer.verify(b"changed input", first)
    other = await committer.commit(
        b"sanitized accepted input", context.model_copy(update={"request_id": uuid4()})
    )
    assert first.value != other.value and first.key_version != other.key_version
    assert first.retention_ref != other.retention_ref
    with pytest.raises(PrivacyDenied):
        await committer.verify(
            b"sanitized accepted input",
            first.model_copy(update={"retention_ref": uuid4()}),
        )
    with pytest.raises(PrivacyDenied):
        await committer.verify(
            b"sanitized accepted input",
            first.model_copy(update={"key_version": uuid4()}),
        )


@pytest.mark.parametrize("loss", ["retired", "missing", "expired"])
async def test_CL06_component_unavailable_never_reprovisions(custody, native, loss):
    _, saved, *_ = setup_crypto()
    context = RetryContext(request_id=uuid4(), snapshot=saved)
    committer = NativeRetryCommitter(custody, clock=lambda: NOW)
    commitment = await committer.commit(b"sanitized accepted input", context)
    writes = native.writes
    if loss == "retired":
        await custody.retire(commitment.key_version)
    elif loss == "missing":
        native.values.clear()
    else:
        deadline = committer._deadline(context)
        custody.clock = committer.clock = lambda: deadline
    with pytest.raises(PrivacyDenied, match="privacy_retry_unavailable"):
        await committer.verify(b"sanitized accepted input", commitment)
    with pytest.raises(PrivacyDenied, match="privacy_retry_unavailable"):
        await committer.commit(b"sanitized accepted input", context)
    assert native.writes == writes
