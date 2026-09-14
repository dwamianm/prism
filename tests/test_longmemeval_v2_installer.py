"""Installer checks for the pinned LongMemEval-V2 adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.integrations import install_longmemeval_v2 as installer


def upstream_layout(root: Path) -> None:
    memory = root / "memory_modules"
    configs = root / "evaluation" / "memory_configs"
    memory.mkdir(parents=True)
    configs.mkdir(parents=True)
    (memory / "memory.py").write_text("class Memory: pass\n")
    (memory / "__init__.py").write_text("from .memory import Memory\n")


def test_installer_is_idempotent_and_preserves_registry(tmp_path: Path, monkeypatch) -> None:
    upstream_layout(tmp_path)
    monkeypatch.setattr(installer, "_checkout_revision", lambda root: installer.UPSTREAM_REVISION)

    first = installer.install(tmp_path)
    second = installer.install(tmp_path)

    assert first["adapter"] == first["config"] == first["registry_import"] == "installed"
    assert second["adapter"] == second["config"] == second["registry_import"] == "unchanged"
    registry = (tmp_path / "memory_modules" / "__init__.py").read_text()
    assert registry.startswith("from .memory import Memory\n")
    assert registry.count(installer._IMPORT_LINE) == 1


def test_installer_rejects_revision_drift_and_conflicts(tmp_path: Path, monkeypatch) -> None:
    upstream_layout(tmp_path)
    monkeypatch.setattr(installer, "_checkout_revision", lambda root: "newer-revision")
    with pytest.raises(RuntimeError, match="unsupported LongMemEval-V2 revision"):
        installer.install(tmp_path)

    (tmp_path / "memory_modules" / "prme.py").write_text("conflicting adapter\n")
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        installer.install(tmp_path, allow_revision_mismatch=True)
