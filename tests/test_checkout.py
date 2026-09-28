"""Resolving the checkout the benchmark datasets and research data live in."""
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from benchmarks import checkout


ORIGINAL = Path("/Users/dwamianm/Sites/prism")
MATRIX = ORIGINAL.parent / "prism-opt-in-study-2026-09-22"


def frozen_study():
    """The path globals exactly as the registered harness declares them."""
    return SimpleNamespace(
        ORIGINAL=ORIGINAL, MATRIX=MATRIX,
        LOCOMO=ORIGINAL / "data/benchmarks/locomo/locomo10.json",
        LONGMEM=ORIGINAL / "data/benchmarks/longmemeval/longmemeval_s_cleaned.json",
        OFFICIAL=MATRIX / "data/opt-in-study/LongMemEval-official",
        HISTORICAL=MATRIX / "data/opt-in-study/opt-in-successor-v2/baseline")


@pytest.fixture(autouse=True)
def _clear_cache():
    checkout.main_checkout.cache_clear()
    yield
    checkout.main_checkout.cache_clear()


class TestMainCheckout:
    def test_a_plain_clone_resolves_to_itself(self):
        assert checkout.main_checkout() == checkout.REPO

    def test_the_environment_overrides_the_derived_root(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PRME_ORIGINAL_ROOT", str(tmp_path))
        assert checkout.main_checkout() == tmp_path.resolve()

    def test_a_user_path_in_the_override_is_expanded(self, monkeypatch):
        monkeypatch.setenv("PRME_ORIGINAL_ROOT", "~")
        assert checkout.main_checkout() == Path.home().resolve()

    def test_a_worktree_resolves_to_the_main_checkout(self, monkeypatch, tmp_path):
        """Builds run from a worktree and must read the main checkout's datasets."""
        main = tmp_path / "prism"
        monkeypatch.setattr(checkout, "REPO", tmp_path / "prism-study")
        monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: f"{main}/.git\n")
        assert checkout.main_checkout() == main

    def test_a_clone_without_git_falls_back_to_the_repository(self, monkeypatch):
        def refuse(*args, **kwargs):
            raise FileNotFoundError("git")

        monkeypatch.setattr(subprocess, "check_output", refuse)
        assert checkout.main_checkout() == checkout.REPO


class TestRetarget:
    def test_every_recorded_path_moves_to_this_checkout(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PRME_ORIGINAL_ROOT", str(tmp_path))
        study = frozen_study()
        checkout.retarget(study)
        assert study.ORIGINAL == tmp_path.resolve()
        assert study.LOCOMO == tmp_path.resolve() / "data/benchmarks/locomo/locomo10.json"
        assert study.LONGMEM.is_relative_to(tmp_path.resolve())

    def test_the_study_worktree_keeps_its_dated_name_beside_the_checkout(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PRME_ORIGINAL_ROOT", str(tmp_path / "prism"))
        study = frozen_study()
        checkout.retarget(study)
        assert study.MATRIX == (tmp_path / "prism-opt-in-study-2026-09-22").resolve()
        assert study.OFFICIAL == study.MATRIX / "data/opt-in-study/LongMemEval-official"

    def test_the_study_worktree_has_its_own_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PRME_ORIGINAL_ROOT", str(tmp_path / "prism"))
        monkeypatch.setenv("PRME_MATRIX_ROOT", str(tmp_path / "elsewhere"))
        study = frozen_study()
        checkout.retarget(study)
        assert study.MATRIX == (tmp_path / "elsewhere").resolve()

    def test_a_path_aimed_elsewhere_on_purpose_stays_put(self, monkeypatch, tmp_path):
        """A test or a caller may point one dataset at its own file."""
        monkeypatch.setenv("PRME_ORIGINAL_ROOT", str(tmp_path))
        study = frozen_study()
        study.LOCOMO = tmp_path / "scratch" / "locomo10.json"
        checkout.retarget(study)
        assert study.LOCOMO == tmp_path / "scratch" / "locomo10.json"
        assert study.LONGMEM.is_relative_to(tmp_path.resolve())

    def test_calling_it_again_changes_nothing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PRME_ORIGINAL_ROOT", str(tmp_path))
        study = frozen_study()
        checkout.retarget(study)
        once = vars(study).copy()
        checkout.retarget(study)
        assert vars(study) == once

    def test_the_original_machine_is_left_untouched(self, monkeypatch):
        """On the machine the harness records, nothing moves."""
        monkeypatch.setenv("PRME_ORIGINAL_ROOT", str(ORIGINAL))
        study = frozen_study()
        checkout.retarget(study)
        assert vars(study) == vars(frozen_study())


class TestFrozenEvidence:
    """The registered harness is pinned by sha256, so re-rooting must not edit it."""

    def test_the_registered_sources_still_match_the_registration(self):
        from benchmarks.integrations import gpt54_baselines, run_gpt54_comparison

        registration = json.loads(run_gpt54_comparison.REG.read_text())
        for path in gpt54_baselines.FROZEN_SOURCES:
            digest = hashlib.sha256((run_gpt54_comparison.ROOT / path).read_bytes()).hexdigest()
            assert digest == registration["sources"][path], path

    def test_the_gate_harness_reads_this_checkout(self):
        from benchmarks.diagnostics import product_packing

        assert product_packing._harness().ORIGINAL == checkout.main_checkout()
