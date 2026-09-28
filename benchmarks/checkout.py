"""Where the benchmark datasets and the shared research data live.

The registered GPT-5.4 harness (``benchmarks/integrations/run_gpt54_comparison.py``)
is frozen research evidence. Its bytes are pinned by sha256 in the saved
registration under ``benchmarks/results/research/2026-09-23/`` and checked again
at run time by ``gpt54_baselines.registered_protocol``, so the absolute paths
written into it on the original research machine cannot be edited away. They
would otherwise stop anyone whose clone sits elsewhere, which is every
contributor.

This module resolves the same two locations for any checkout and re-roots that
module's path globals after it is imported. The file on disk is untouched, so
its digest still matches the registration.

Two roots, and why they are not the repository root:

- The main checkout holds ``data/`` and ``.env``. Studies and pack builds run
  from their own worktrees, so a worktree has to read the main checkout's
  datasets and write to its one spending ledger and answer store.
- The opt-in study worktree holds the official LongMemEval judge and the
  historical baseline that the comparison reuses.

``PRME_ORIGINAL_ROOT`` and ``PRME_MATRIX_ROOT`` override them for a clone that
git cannot relate, such as an unpacked archive.
"""
from __future__ import annotations

from functools import cache
import os
from pathlib import Path
import subprocess

REPO = Path(__file__).resolve().parents[1]

# Each root and the module paths derived from it. The relative layout is read
# back from the frozen module rather than repeated here, so this cannot drift
# from what that module declares.
_DERIVED = {"ORIGINAL": ("LOCOMO", "LONGMEM"), "MATRIX": ("OFFICIAL", "HISTORICAL")}


def _override(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else None


@cache
def main_checkout() -> Path:
    """The checkout holding ``data/`` and ``.env``, from a worktree or from itself.

    Git names the main checkout from any worktree through its common directory,
    so the location is derived rather than written in. A clone without git, or
    without that information, falls back to this repository root.
    """
    override = _override("PRME_ORIGINAL_ROOT")
    if override:
        return override
    try:
        common = subprocess.check_output(
            ["git", "rev-parse", "--git-common-dir"], cwd=REPO, text=True,
            stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.SubprocessError):
        return REPO
    return (REPO / common).resolve().parent if common else REPO


def study_worktree(name: str) -> Path:
    """The dated opt-in study worktree, beside the main checkout."""
    return _override("PRME_MATRIX_ROOT") or main_checkout().parent / name


def retarget(study) -> None:
    """Point the frozen harness at this machine's checkout. Safe to call again."""
    roots = {"ORIGINAL": main_checkout(), "MATRIX": study_worktree(study.MATRIX.name)}
    for root_name, derived in _DERIVED.items():
        old, new = getattr(study, root_name), roots[root_name]
        if old == new:
            continue
        for name in derived:
            # Only what still sits under the old root moves. A path aimed
            # somewhere else on purpose, as a test's dataset is, stays put.
            value = getattr(study, name)
            if value.is_relative_to(old):
                setattr(study, name, new / value.relative_to(old))
        setattr(study, root_name, new)
