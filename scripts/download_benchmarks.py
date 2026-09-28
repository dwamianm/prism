#!/usr/bin/env python3
"""Download benchmark datasets for PRME evaluation.

Downloads (or creates stubs for) external benchmark datasets:

- **LoCoMo** (Long Conversation Memory): Multi-session QA benchmark.
  Source: https://github.com/snap-research/locomo
  Paper: https://arxiv.org/abs/2402.17753

- **LongMemEval**: Long-term memory evaluation for chat assistants.
  Source: https://github.com/xiaowu0162/LongMemEval
  Paper: https://arxiv.org/abs/2410.10813

  Two files are fetched. ``longmemeval_s_cleaned.json`` is the full history that
  the registered harnesses and the evidence gate read, about 265 MB.
  ``longmemeval_oracle.json`` holds only the supporting sessions and is a
  diagnostic, so it does not test retrieval through distractors.

Datasets are saved to ``data/benchmarks/locomo/`` and
``data/benchmarks/longmemeval/`` relative to the project root. A file whose
sha256 is pinned is checked after it downloads and skipped when a matching copy
is already there, because a harness refuses a dataset whose digest differs.

Usage::

    python scripts/download_benchmarks.py [--locomo] [--longmemeval] [--all]
    python scripts/download_benchmarks.py --stub  # create synthetic stubs only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCOMO_DIR = PROJECT_ROOT / "data" / "benchmarks" / "locomo"
LONGMEMEVAL_DIR = PROJECT_ROOT / "data" / "benchmarks" / "longmemeval"

# Public dataset URLs (best-effort; these may require manual download)
LOCOMO_URL = (
    "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"
)
_LONGMEMEVAL_REPO = (
    "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main"
)
# Oracle histories hold only the supporting sessions. They are a diagnostic, not
# a retrieval test.
LONGMEMEVAL_URL = f"{_LONGMEMEVAL_REPO}/longmemeval_oracle.json"
# The full-history S file, which the registered harnesses and the evidence gate
# read. It is about 265 MB, and without it the LongMemEval-S benchmarks cannot
# run at all.
LONGMEMEVAL_S_URL = f"{_LONGMEMEVAL_REPO}/longmemeval_s_cleaned.json"

# The harnesses refuse a dataset whose digest differs, so a download that does
# not match is worse than none. The authorities for these two values are
# run_gpt54_comparison.LOCOMO_SHA and run_longmemeval_s_baseline.DATASET_SHA256;
# tests/test_download_benchmarks.py keeps this table in step with them.
CHECKSUMS = {
    "locomo10.json": "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4",
    "longmemeval_s_cleaned.json": "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442",
}


def _download_file(url: str, dest: Path) -> bool:
    """Download a file from *url* to *dest*. Returns True on success."""
    try:
        logger.info("Downloading %s -> %s", url, dest)
        urllib.request.urlretrieve(url, str(dest))
        logger.info("Downloaded %s (%.1f KB)", dest.name, dest.stat().st_size / 1024)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        logger.warning("Download failed for %s: %s", url, exc)
        return False


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()


def _verified(dest: Path) -> bool:
    """Whether *dest* is present and matches its pinned digest, if one is pinned."""
    if not dest.exists():
        return False
    expected = CHECKSUMS.get(dest.name)
    if expected is None:
        return True
    actual = _digest(dest)
    if actual == expected:
        return True
    logger.warning("%s does not match the registered dataset (%s), so it is being "
                   "downloaded again", dest.name, actual[:12])
    return False


def _fetch(url: str, dest: Path) -> bool:
    """Download *url* to *dest* unless a verified copy is already there."""
    if _verified(dest):
        logger.info("%s is already present and matches", dest.name)
        return True
    if not _download_file(url, dest):
        return False
    expected = CHECKSUMS.get(dest.name)
    if expected is not None and _digest(dest) != expected:
        logger.error("%s does not match the registered dataset. The harnesses will "
                     "refuse it. Remove it and try again.", dest.name)
        return False
    return True


def _create_locomo_stub(dest_dir: Path) -> None:
    """Create a synthetic LoCoMo-format stub for offline development."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    stub_path = dest_dir / "locomo_stub.json"

    stub = [
        {
            "conversation_id": "stub-001",
            "turns": [
                {"role": "user", "content": "Our main project is called Neptune."},
                {"role": "assistant", "content": "Got it, I'll remember Neptune."},
                {"role": "user", "content": "Alice is the tech lead for Neptune."},
                {"role": "assistant", "content": "Noted, Alice leads Neptune."},
                {"role": "user", "content": "We use Python and PostgreSQL."},
                {"role": "assistant", "content": "Python + PostgreSQL stack, understood."},
            ],
            "questions": [
                {
                    "question": "What is the main project?",
                    "answer": "Neptune",
                    "category": "qa",
                },
                {
                    "question": "Who is the tech lead?",
                    "answer": "Alice",
                    "category": "qa",
                },
            ],
        }
    ]

    stub_path.write_text(json.dumps(stub, indent=2))
    logger.info("Created LoCoMo stub at %s", stub_path)


def _create_longmemeval_stub(dest_dir: Path) -> None:
    """Create a synthetic LongMemEval-format stub for offline development."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    stub_path = dest_dir / "longmemeval_stub.json"

    stub = [
        {
            "id": "stub-ie-001",
            "ability": "info_extraction",
            "sessions": [
                {
                    "session_id": "s1",
                    "messages": [
                        {"role": "user", "content": "The office is at 100 Market Street."},
                        {"role": "assistant", "content": "Noted the office address."},
                    ],
                }
            ],
            "question": "Where is the office?",
            "answer": "100 Market Street",
        },
        {
            "id": "stub-ku-001",
            "ability": "knowledge_update",
            "sessions": [
                {
                    "session_id": "s1",
                    "messages": [
                        {"role": "user", "content": "The CEO is John Smith."},
                        {"role": "assistant", "content": "Got it."},
                    ],
                },
                {
                    "session_id": "s2",
                    "messages": [
                        {"role": "user", "content": "Jane Doe replaced John Smith as CEO."},
                        {"role": "assistant", "content": "Updated."},
                    ],
                },
            ],
            "question": "Who is the current CEO?",
            "answer": "Jane Doe",
        },
    ]

    stub_path.write_text(json.dumps(stub, indent=2))
    logger.info("Created LongMemEval stub at %s", stub_path)


def download_locomo(stub_only: bool = False) -> Path:
    """Download LoCoMo dataset or create a stub fallback.

    Returns the directory containing the dataset.
    """
    LOCOMO_DIR.mkdir(parents=True, exist_ok=True)

    if not stub_only:
        if _fetch(LOCOMO_URL, LOCOMO_DIR / "locomo10.json"):
            return LOCOMO_DIR
        logger.info("Falling back to synthetic stub.")

    _create_locomo_stub(LOCOMO_DIR)
    return LOCOMO_DIR


def download_longmemeval(stub_only: bool = False) -> Path:
    """Download LongMemEval dataset or create a stub fallback.

    Returns the directory containing the dataset.
    """
    LONGMEMEVAL_DIR.mkdir(parents=True, exist_ok=True)

    if not stub_only:
        # The S file is what the benchmarks read; the oracle file is the
        # diagnostic. Getting only one of them is still worth reporting.
        wanted = ((LONGMEMEVAL_S_URL, "longmemeval_s_cleaned.json"),
                  (LONGMEMEVAL_URL, "longmemeval_oracle.json"))
        got = [_fetch(url, LONGMEMEVAL_DIR / name) for url, name in wanted]
        if all(got):
            return LONGMEMEVAL_DIR
        for ok, (_, name) in zip(got, wanted):
            if not ok:
                logger.warning("Could not get %s. Download it from %s", name,
                               "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned")
        if any(got):
            return LONGMEMEVAL_DIR
        logger.info("Falling back to synthetic stub.")

    _create_longmemeval_stub(LONGMEMEVAL_DIR)
    return LONGMEMEVAL_DIR


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download benchmark datasets for PRME evaluation."
    )
    parser.add_argument("--locomo", action="store_true", help="Download LoCoMo dataset")
    parser.add_argument("--longmemeval", action="store_true", help="Download LongMemEval dataset")
    parser.add_argument("--all", action="store_true", help="Download all datasets")
    parser.add_argument(
        "--stub", action="store_true",
        help="Create synthetic stubs only (no network access)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if not (args.locomo or args.longmemeval or args.all):
        args.all = True

    if args.all or args.locomo:
        download_locomo(stub_only=args.stub)

    if args.all or args.longmemeval:
        download_longmemeval(stub_only=args.stub)

    logger.info("Done.")


if __name__ == "__main__":
    main()
