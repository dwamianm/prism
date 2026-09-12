"""Local embedding initialization configures native runtime before import."""

import builtins
import os
from types import SimpleNamespace

import pytest

from prme.storage.embedding import FastEmbedProvider


@pytest.mark.parametrize("explicit", [None, "0", "1"])
def test_native_telemetry_default_is_set_before_dependency_load(monkeypatch, explicit):
    monkeypatch.setenv("ORT_DISABLE_TELEMETRY", "test-placeholder")
    if explicit is None:
        monkeypatch.delenv("ORT_DISABLE_TELEMETRY", raising=False)
    else:
        monkeypatch.setenv("ORT_DISABLE_TELEMETRY", explicit)
    original_import = builtins.__import__
    models = []
    def load_model(**kwargs):
        model = object()
        models.append(model)
        return model
    def guarded_import(name, *args, **kwargs):
        if name == "fastembed":
            assert os.environ["ORT_DISABLE_TELEMETRY"] == (explicit or "1")
            return SimpleNamespace(TextEmbedding=load_model)
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    provider = FastEmbedProvider()
    provider._ensure_model()
    provider._ensure_model()
    assert len(models) == 1 and provider._model is models[0]
