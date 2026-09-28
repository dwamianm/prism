"""The dataset downloader agrees with the harnesses that refuse a wrong dataset."""
import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script():
    """Load the script by path. It is not part of an importable package."""
    spec = importlib.util.spec_from_file_location(
        "download_benchmarks", ROOT / "scripts" / "download_benchmarks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return load_script()


class TestPinnedChecksums:
    """A downloaded dataset is useless if the harness will not accept it."""

    def test_locomo_matches_the_registered_comparison(self, script):
        from benchmarks.integrations import run_gpt54_comparison

        assert script.CHECKSUMS["locomo10.json"] == run_gpt54_comparison.LOCOMO_SHA

    def test_longmemeval_s_matches_the_baseline_runner(self, script):
        from benchmarks.integrations import run_longmemeval_s_baseline

        assert (script.CHECKSUMS["longmemeval_s_cleaned.json"]
                == run_longmemeval_s_baseline.DATASET_SHA256)

    def test_the_full_history_file_is_fetched_not_only_the_oracle(self, script):
        """The oracle file alone cannot test retrieval through distractors."""
        assert script.LONGMEMEVAL_S_URL.endswith("/longmemeval_s_cleaned.json")
        assert script.LONGMEMEVAL_URL.endswith("/longmemeval_oracle.json")


class TestVerification:
    def test_a_missing_file_is_not_verified(self, script, tmp_path):
        assert script._verified(tmp_path / "locomo10.json") is False

    def test_a_matching_file_is_verified(self, script, tmp_path, monkeypatch):
        dest = tmp_path / "locomo10.json"
        dest.write_bytes(b"payload")
        monkeypatch.setitem(script.CHECKSUMS, "locomo10.json",
                            hashlib.sha256(b"payload").hexdigest())
        assert script._verified(dest) is True

    def test_a_file_with_the_wrong_digest_is_rejected(self, script, tmp_path):
        dest = tmp_path / "locomo10.json"
        dest.write_bytes(b"not the dataset")
        assert script._verified(dest) is False

    def test_an_unpinned_file_is_accepted_as_present(self, script, tmp_path):
        dest = tmp_path / "longmemeval_oracle.json"
        dest.write_bytes(b"anything")
        assert script._verified(dest) is True

    def test_a_verified_file_is_not_downloaded_again(self, script, tmp_path, monkeypatch):
        dest = tmp_path / "longmemeval_oracle.json"
        dest.write_bytes(b"anything")
        monkeypatch.setattr(script, "_download_file",
                            lambda *a: pytest.fail("downloaded an existing file"))
        assert script._fetch("https://example.invalid/x.json", dest) is True

    def test_a_download_that_does_not_match_fails(self, script, tmp_path, monkeypatch):
        dest = tmp_path / "locomo10.json"

        def write_wrong(url, path):
            Path(path).write_bytes(b"wrong")
            return True

        monkeypatch.setattr(script, "_download_file", write_wrong)
        assert script._fetch("https://example.invalid/x.json", dest) is False
