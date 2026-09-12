"""Authored Hindsight public-API preflight; no benchmark-quality claim."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys


TEXTS = ["Aurora needs approval before deployment.", "I prefer shaded paths only in summer.",
         "Which path fits my summer preference?", "Café notes: naïve examples.\nPreserve this qualification.",
         "", "Keep the final condition. " * 300, "  Keep leading and trailing whitespace.  "]
MODEL = "BAAI/bge-small-en-v1.5"
PIN = "bde55237f53bf55aacd048b01e29d7dc23b83a85"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def embedding_record(model, vectors):
    directory = Path(model.model._model_dir)
    return {"model": MODEL, "dimension": 384, "batch_size": 1, "texts_sha256": digest(canonical(TEXTS)),
            "vectors": vectors, "versions": {n: version(n) for n in ("fastembed", "onnxruntime", "numpy", "tokenizers")},
            "assets": {str(p.relative_to(directory)): digest(p.read_bytes()) for p in sorted(directory.rglob("*")) if p.is_file()}}


async def reference():
    from prme.storage.embedding import FastEmbedProvider
    provider = FastEmbedProvider()
    vectors = await provider.embed(TEXTS)
    return {"complete": True, "kind": "prme_embedding_reference", "embedding": embedding_record(provider._model, vectors)}


async def hindsight(args):
    # This isolated raw-memory preflight must never inherit credentials, remote
    # provider selection, extensions or scheduling from a host configuration.
    for key in list(os.environ):
        if key.startswith("HINDSIGHT_"):
            del os.environ[key]
    os.environ.update(HINDSIGHT_API_LLM_PROVIDER="none", HINDSIGHT_API_RETAIN_LLM_PROVIDER="none",
        HINDSIGHT_API_REFLECT_LLM_PROVIDER="none", HINDSIGHT_API_CONSOLIDATION_LLM_PROVIDER="none",
        HINDSIGHT_API_RERANKER_PROVIDER="rrf", HINDSIGHT_API_TEXT_SEARCH_EXTENSION="native",
        HINDSIGHT_API_ENABLE_OBSERVATIONS="false", HINDSIGHT_API_ENABLE_AUTO_CONSOLIDATION="false")
    os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
    from hindsight_api import MemoryEngine, RequestContext, Embeddings
    from hindsight_api.engine.cross_encoder import RRFPassthroughCrossEncoder
    import hindsight_api
    from fastembed import TextEmbedding
    import numpy as np

    source = Path(hindsight_api.__file__).resolve().parent
    installed = {str(p.relative_to(source)): digest(p.read_bytes()) for p in sorted(source.rglob("*.py"))}
    upstream = args.upstream / "hindsight-api-slim/hindsight_api"
    expected = {str(p.relative_to(upstream)): digest(p.read_bytes()) for p in sorted(upstream.rglob("*.py"))}
    if installed != expected:
        raise ValueError("Installed Hindsight source does not match pinned checkout")
    import subprocess
    if subprocess.check_output(["git", "-C", str(args.upstream), "rev-parse", "HEAD"], text=True).strip() != PIN:
        raise ValueError("Unexpected Hindsight commit")

    class MatchedBGE(Embeddings):
        provider_name = "fastembed-bge-single-text"
        dimension = 384
        query_prefix = passage_prefix = ""
        def __init__(self):
            self.model = None
            self.calls = []
        async def initialize(self):
            if self.model is None:
                self.model = await asyncio.to_thread(TextEmbedding, model_name=MODEL)
        async def encode(self, texts):
            await self.initialize()
            self.calls.extend(texts)
            return await asyncio.to_thread(lambda: [v.tolist() for v in self.model.embed(texts, batch_size=1)])

    provider = MatchedBGE()
    await provider.initialize()
    embedding = embedding_record(provider.model, await provider.encode(TEXTS))
    reference_record = json.loads(args.reference.read_bytes())
    comparison = reference_record["embedding"]
    if not reference_record["complete"] or any(embedding[key] != comparison[key] for key in ("model", "dimension", "batch_size", "texts_sha256", "assets")):
        raise ValueError("Embedding inputs or assets differ")
    difference = float(np.max(np.abs(np.asarray(embedding["vectors"]) - np.asarray(comparison["vectors"]))))
    if difference != 0:
        raise ValueError("Authored embedding parity failed")

    date = datetime(2025, 6, 1, 12, tzinfo=timezone.utc)
    request_context = RequestContext()
    contents = [("seasonal", "I prefer shaded paths only in summer."),
                ("winter", "In winter I choose sunny sheltered paths."),
                ("approval", "Aurora needs approval before deployment.")]
    foreign = "Other bank secret: I choose crowded highways during summer."
    ds_url = os.environ["PRME_HINDSIGHT_PREFLIGHT_DATABASE_URL"]
    # The caller provisions a fresh local database. This diagnostic never drops
    # banks, deletes content, or connects to a default database implicitly.
    if not ds_url.startswith("postgresql://") or "@127.0.0.1:" not in ds_url:
        raise ValueError("An explicit loopback PostgreSQL preflight database is required")

    def engine():
        return MemoryEngine(db_url=ds_url, memory_llm_provider="none", embeddings=provider,
                            cross_encoder=RRFPassthroughCrossEncoder(), pool_min_size=1, pool_max_size=6)
    first = engine()
    retained = {}
    try:
        await first.initialize()
        for document, content in contents:
            retained[document] = await first.retain_async("authored-main", content, context="authored user source",
                event_date=date, document_id=document, request_context=request_context)
            if not retained[document]:
                raise ValueError("Retain returned no source units")
        await first.retain_async("authored-other", foreign, event_date=date,
                                document_id="foreign", request_context=request_context)
        before = await first.recall_async("authored-main", TEXTS[2], question_date=date, max_tokens=4096,
            include_entities=False, include_chunks=False, reranking="rrf", request_context=request_context)
    finally:
        await first.close()
    second = engine()
    try:
        await second.initialize()
        after = await second.recall_async("authored-main", TEXTS[2], question_date=date, max_tokens=4096,
            include_entities=False, include_chunks=False, reranking="rrf", request_context=request_context)
        isolated = await second.recall_async("authored-other", TEXTS[2], question_date=date, max_tokens=4096,
            include_entities=False, include_chunks=False, reranking="rrf", request_context=request_context)
    finally:
        await second.close()
    normalized = lambda result: [(r.id, r.text, r.document_id) for r in result.results]
    if normalized(before) != normalized(after):
        raise ValueError("Reopened recall changed source identities or ordering")
    if not any(r.text == contents[0][1] for r in after.results):
        raise ValueError("Qualified source was not preserved in recall")
    expected_content = {content for _, content in contents}
    if any(r.text not in expected_content for r in after.results) or not isolated.results or any(r.text != foreign for r in isolated.results):
        raise ValueError("Source text changed or bank scope leaked")
    if any(r.id not in {uid for values in retained.values() for uid in values} for r in after.results):
        raise ValueError("Recall returned unknown source unit")
    return {"complete": True, "kind": "hindsight_authored_raw_public_api", "commit": PIN,
            "source_files_verified": len(installed), "source_sha256": digest(canonical(installed)),
            "python": sys.version, "package_version": version("hindsight-api-slim"),
            "embedding": embedding, "embedding_reference_sha256": digest(args.reference.read_bytes()),
            "embedding_maximum_absolute_difference": difference, "llm_provider": "none", "extraction_mode": "chunks",
            "text_search_extension": "native", "reranking": "rrf", "source_retain_reopen_recall": True,
            "bank_isolation": True, "retained": retained, "recall": after.model_dump(mode="json"),
            "limits": "Authored raw-chunk API compatibility and seven embedding inputs. No full-feature Hindsight quality, latency, or leaderboard claim."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["reference", "hindsight"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--upstream", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite previous output")
    result = {"complete": False}
    try:
        result = asyncio.run(reference() if args.mode == "reference" else hindsight(args))
    except BaseException as exc:
        result["error_type"] = type(exc).__name__
        raise
    finally:
        result["runner_sha256"] = digest(Path(__file__).read_bytes())
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
