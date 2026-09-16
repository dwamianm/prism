"""Installed sync/async custom embedding example with real local BGE inference."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import platform
import tempfile

import prme
from prme import CachedEmbeddingProvider, MemoryClient, MemoryEngine, config_from_directory
from prme.storage.embedding import FastEmbedProvider


class BGEWithQueryInstruction(FastEmbedProvider):
    @property
    def model_version(self):
        return super().model_version + ":bge-query-instruction-v1"

    async def embed_query(self, text):
        instruction = "Represent this sentence for searching relevant passages: "
        return (await self.embed([instruction + text]))[0]


async def reopen(config):
    provider = CachedEmbeddingProvider(BGEWithQueryInstruction())
    async with MemoryEngine.open(config, embedding_provider=provider) as engine:
        response = await engine.retrieve("Aurora deployment policy", user_id="authored", include_cross_scope=False)
        assert response.results and not response.metadata.embedding_mismatch
        receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id="authored")
        assert receipt.execution.features["embedding"]["query_encoding"] == "embed_query"
        return receipt.execution.features["embedding"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite previous evidence")
    report = {"complete": False, "python": platform.python_version(),
              "prme_import_path": str(Path(prme.__file__).resolve()),
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "limits": "Authored real-model provider integration, not evidence that the query instruction improves answer quality."}
    try:
        with tempfile.TemporaryDirectory(prefix="prme-custom-embedding-") as directory:
            config = config_from_directory(directory).model_copy(update={"database_url": None})
            provider = CachedEmbeddingProvider(BGEWithQueryInstruction())
            with MemoryClient(config=config, embedding_provider=provider) as memory:
                identity = memory.store("Aurora needs approval before deployment.", user_id="authored")
                response = memory.retrieve("Aurora deployment policy", user_id="authored", include_cross_scope=False)
                assert response.results and response.bundle.render()
                assert not response.metadata.embedding_mismatch
                assert memory.get_event(identity, user_id="authored").content == "Aurora needs approval before deployment."
                effective = memory._config
                assert effective.embedding.provider == "custom" and config.embedding.provider != "custom"
                receipt = memory.get_retrieval_receipt(str(response.metadata.request_id), user_id="authored")
                assert receipt.execution.features["embedding"]["query_encoding"] == "embed_query"
                report["embedding"] = receipt.execution.features["embedding"]
            assert asyncio.run(reopen(effective)) == report["embedding"]
            report.update(complete=True, sync_store_and_retrieve=True, async_reopen_and_retrieve=True,
                          source_preserved=True, original_config_unchanged=True, encoding_identity_preserved=True)
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
