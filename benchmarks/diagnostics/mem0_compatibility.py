"""Check a pinned Mem0 raw-memory adapter and matched embeddings; no quality claim."""

import argparse
import asyncio
import hashlib
from importlib.metadata import version
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from benchmarks.diagnostics._process import checked_report


def run(upstream: Path) -> dict:
    commit = subprocess.check_output(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True
    ).strip()
    if subprocess.check_output(
        ["git", "-C", str(upstream), "status", "--porcelain"], text=True
    ).strip():
        raise ValueError("Competitor checkout must be clean")
    with tempfile.TemporaryDirectory(prefix="prme-mem0-compatibility-") as directory:
        root = Path(directory)
        os.environ["MEM0_TELEMETRY"] = "false"
        os.environ["MEM0_DIR"] = str(root / "settings")
        from mem0 import Memory
        import mem0
        import numpy as np
        from prme.storage.embedding import FastEmbedProvider

        installed = Path(inspect.getfile(mem0)).parent
        source_files = sorted((upstream / "mem0").rglob("*.py"))
        if not source_files:
            raise ValueError("Competitor source is missing")
        for path in source_files:
            if (
                path.read_bytes()
                != (installed / path.relative_to(upstream / "mem0")).read_bytes()
            ):
                raise ValueError("Installed competitor differs from its pinned source")
        config = {
            "embedder": {
                "provider": "fastembed",
                "config": {"model": "BAAI/bge-small-en-v1.5", "embedding_dims": 384},
            },
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "collection_name": "compatibility",
                    "path": str(root / "qdrant"),
                    "embedding_model_dims": 384,
                },
            },
            "history_db_path": str(root / "history.db"),
            "llm": {
                "provider": "ollama",
                "config": {
                    "model": "gemma4:26b",
                    "ollama_base_url": "http://127.0.0.1:11434",
                },
            },
        }

        def no_inference(*args, **kwargs):
            raise AssertionError("Raw compatibility probe must not call an LLM")

        def close(memory):
            memory.close()
            # The comparison adapter owns both resources. Memory.close() in
            # this revision closes SQLite; explicitly close the Qdrant client.
            memory.vector_store.client.close()

        memory = Memory.from_config(config)
        memory.llm.generate_response = no_inference
        try:
            texts = [
                "Aurora requires deployment approval.",
                "Aurora keeps nightly backups for 30 days.",
                "First line.\nSecond line.",
                "Café notes: naïve examples.",
            ]
            provider = FastEmbedProvider()

            async def reference_vectors():
                # Actual raw writes and retrieval queries embed one text per
                # call in both products. Test that same shape, and separately
                # retain batching sensitivity instead of hiding the difference.
                batch = np.asarray(await provider.embed(texts))
                single = np.asarray(
                    [(await provider.embed([text]))[0] for text in texts]
                )
                return single, batch

            reference, batched = asyncio.run(reference_vectors())
            observed = np.asarray(
                [memory.embedding_model.embed(text, "add") for text in texts]
            )
            maximum_difference = float(np.max(np.abs(reference - observed)))
            assert reference.shape == observed.shape == (4, 384)
            assert np.allclose(reference, observed, atol=1e-6, rtol=0), (
                "single-text embedding parity"
            )
            batch_difference = float(np.max(np.abs(batched - reference)))
            model_directory = Path(provider._model.model._model_dir)
            other_directory = Path(memory.embedding_model.dense_model.model._model_dir)

            def assets(directory):
                return {
                    str(path.relative_to(directory)): hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
                    for path in sorted(directory.rglob("*"))
                    if path.is_file()
                }

            model_assets = assets(model_directory)
            assert model_assets and model_assets == assets(other_directory), (
                "embedding model asset identity"
            )
            ids = {}
            for source, owner, scope, content in [
                ("policy", "alice", "project", texts[0]),
                ("backup", "alice", "project", texts[1]),
                ("personal", "alice", "personal", "Aurora is the name of my dog."),
                ("foreign", "bob", "project", "Aurora deploys without approval."),
            ]:
                result = memory.add(
                    [{"role": "user", "content": content}],
                    user_id=owner,
                    metadata={"scope": scope, "source_id": source},
                    infer=False,
                )
                assert len(result["results"]) == 1
                ids[source] = result["results"][0]["id"]
            found = memory.search(
                "Aurora deployment policy",
                filters={"user_id": "alice", "scope": "project"},
                top_k=10,
                threshold=0,
                rerank=False,
            )
            visible = {row["id"] for row in found["results"]}
            assert ids["policy"] in visible
            assert visible <= {ids["policy"], ids["backup"]}
        finally:
            close(memory)
        memory = Memory.from_config(config)
        memory.llm.generate_response = no_inference
        try:
            reopened = memory.search(
                "Aurora deployment policy",
                filters={"user_id": "alice", "scope": "project"},
                top_k=10,
                threshold=0,
                rerank=False,
            )
            assert {row["id"] for row in reopened["results"]} == visible
        finally:
            close(memory)
        return {
            "passed": True,
            "upstream_commit": commit,
            "installed_source_files_verified": len(source_files),
            "versions": {
                name: version(name)
                for name in (
                    "mem0ai",
                    "prme",
                    "fastembed",
                    "onnxruntime",
                    "numpy",
                    "qdrant-client",
                    "ollama",
                    "openai",
                )
            },
            "python": sys.version.split()[0],
            "embedding_model": "BAAI/bge-small-en-v1.5",
            "embedding_comparison_inputs_sha256": hashlib.sha256(
                json.dumps(texts).encode()
            ).hexdigest(),
            "embedding_maximum_absolute_difference": maximum_difference,
            "embedding_absolute_tolerance": 1e-6,
            "embedding_call_shape": "one text per call in each product",
            "prme_batch_vs_single_maximum_absolute_difference": batch_difference,
            "model_assets_sha256": model_assets,
            "checks": [
                "installed source matches pinned checkout",
                "raw writes without LLM calls",
                "same-model numerical embedding comparison",
                "owner and metadata-scope filtering",
                "reopen preserves result identities",
            ],
            "adapter_cleanup": "Memory.close() plus explicit Qdrant client.close()",
            "limits": "Authored raw-mode compatibility probe with infer=False. No extraction, answer judging, benchmark accuracy or comparative leadership result. Four embedding inputs do not prove equality on all texts. The first probe used mismatched batch shapes; its failure is retained separately.",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    try:
        result = (
            run(args.upstream)
            if args.worker
            else checked_report(
                [
                    sys.executable,
                    "-m",
                    "benchmarks.diagnostics.mem0_compatibility",
                    "--worker",
                    "--upstream",
                    str(args.upstream),
                ],
                timeout=240,
            )
        )
    except Exception as exc:
        result = {"passed": False, "error_type": type(exc).__name__}
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
