"""Capture pinned Hindsight raw public returns from label-free case records.

Invoke by file path in the isolated competitor environment. No PRME import,
source reconstruction, answer labels or correctness metrics enter this worker.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

PIN = "bde55237f53bf55aacd048b01e29d7dc23b83a85"
MODEL = "BAAI/bge-small-en-v1.5"


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical(value) + b"\n")
    temporary.replace(path)


def validate_case(case):
    if set(case) != {"case_id", "question", "question_date", "turns"}:
        raise ValueError("Only neutral case fields are accepted")
    if not case["case_id"] or not isinstance(case["question"], str):
        raise ValueError("Case identity and query required")
    date = datetime.fromisoformat(case["question_date"])
    if date.tzinfo is None:
        raise ValueError("Explicit timezone-aware question date required")
    seen = set()
    for turn in case["turns"]:
        if set(turn) != {"id", "session_id", "role", "content", "date"}:
            raise ValueError("Only neutral source fields are accepted")
        if (
            turn["id"] in seen
            or turn["role"] not in {"user", "assistant"}
            or not isinstance(turn["content"], str)
        ):
            raise ValueError("Invalid source identity, role or content")
        seen.add(turn["id"])
        if datetime.fromisoformat(turn["date"]).tzinfo is None:
            raise ValueError("Explicit timezone-aware source date required")
    return case


def returned_units(response, case, retained):
    sources = {turn["id"]: turn for turn in case["turns"]}
    unit_owners = {unit: source for source, units in retained.items() for unit in units}
    if sum(map(len, retained.values())) != len(unit_owners):
        raise ValueError("Retain returned duplicate memory-unit identities")
    seen = set()
    for row in response["results"]:
        source = row["document_id"]
        if (
            source not in sources
            or row["id"] in seen
            or unit_owners.get(row["id"]) != source
        ):
            raise ValueError("Unknown, duplicated or foreign returned unit")
        seen.add(row["id"])
        expected = sources[source]
        metadata = row["metadata"] or {}
        if any(
            metadata.get(key) != expected[field]
            for key, field in [
                ("source_turn", "id"),
                ("source_session", "session_id"),
                ("source_role", "role"),
                ("source_date", "date"),
            ]
        ):
            raise ValueError("Returned provenance differs from admitted metadata")
        if row["text"] not in expected["content"]:
            raise ValueError("Raw returned text is not an exact source substring")
    return list(dict.fromkeys(row["document_id"] for row in response["results"]))


def context_from_units(rows, budget, encoding):
    """Adapter renderer of returned units only; never substitute a source document."""
    entries, ids = [], []
    for row in rows:
        metadata = row["metadata"] or {}
        entry = {
            "id": row["id"],
            "source": row["document_id"],
            "role": metadata.get("source_role"),
            "date": metadata.get("source_date"),
            "text": row["text"],
        }
        proposed = "\n".join([*entries, canonical(entry).decode()])
        if len(encoding.encode(proposed, disallowed_special=())) <= budget:
            entries.append(canonical(entry).decode())
            ids.append(row["id"])
    context = "\n".join(entries)
    return {
        "context": context,
        "sha256": digest(context.encode()),
        "unit_ids": ids,
        "tokens": len(encoding.encode(context, disallowed_special=())),
    }


async def run(args, report):
    plan = json.loads(args.plan.read_bytes())
    cases = json.loads(args.inputs.read_bytes())["cases"]
    if (
        digest(args.inputs.read_bytes()) != plan["inputs_sha256"]
        or digest(Path(__file__).read_bytes()) != plan["runner_sha256"]
        or plan["hindsight_commit"] != PIN
    ):
        raise ValueError("Input/runner/pin does not match registration")
    for case in cases:
        validate_case(case)
    if [c["case_id"] for c in cases] != plan["case_ids"] or len(
        set(plan["case_ids"])
    ) != len(cases):
        raise ValueError("Case coverage differs from registration")
    for key in list(os.environ):
        if key.startswith("HINDSIGHT_"):
            del os.environ[key]
    os.environ.update(
        HINDSIGHT_API_LLM_PROVIDER="none",
        HINDSIGHT_API_RETAIN_LLM_PROVIDER="none",
        HINDSIGHT_API_REFLECT_LLM_PROVIDER="none",
        HINDSIGHT_API_CONSOLIDATION_LLM_PROVIDER="none",
        HINDSIGHT_API_RERANKER_PROVIDER="rrf",
        HINDSIGHT_API_TEXT_SEARCH_EXTENSION="native",
        HINDSIGHT_API_ENABLE_OBSERVATIONS="false",
        HINDSIGHT_API_ENABLE_AUTO_CONSOLIDATION="false",
    )
    import asyncpg
    import hindsight_api
    from hindsight_api import MemoryEngine, RequestContext, Embeddings
    from hindsight_api.engine.memory_engine import Budget
    from hindsight_api.engine.cross_encoder import RRFPassthroughCrossEncoder
    from fastembed import TextEmbedding
    import tiktoken

    installed = Path(hindsight_api.__file__).parent
    upstream = args.upstream / "hindsight-api-slim/hindsight_api"

    def files(root):
        return {
            str(p.relative_to(root)): digest(p.read_bytes())
            for p in sorted(root.rglob("*.py"))
        }

    actual = files(installed)
    if not actual or actual != files(upstream):
        raise ValueError("Installed competitor source does not match checkout")
    if (
        subprocess.check_output(
            ["git", "-C", str(args.upstream), "rev-parse", "HEAD"], text=True
        ).strip()
        != PIN
    ):
        raise ValueError("Checkout pin mismatch")

    class MatchedBGE(Embeddings):
        provider_name = "fastembed-bge-single-text"
        dimension = 384
        query_prefix = passage_prefix = ""

        def __init__(self):
            self.model = None
            self.calls = self.texts = self.characters = 0

        async def initialize(self):
            if self.model is None:
                self.model = await asyncio.to_thread(TextEmbedding, model_name=MODEL)

        async def encode(self, texts):
            await self.initialize()
            self.calls += 1
            self.texts += len(texts)
            self.characters += sum(map(len, texts))
            return await asyncio.to_thread(
                lambda: [v.tolist() for v in self.model.embed(texts, batch_size=1)]
            )

        def counts(self):
            return {
                "calls": self.calls,
                "texts": self.texts,
                "characters": self.characters,
            }

    provider = MatchedBGE()
    await provider.initialize()
    model_dir = Path(provider.model.model._model_dir)
    assets = {
        str(p.relative_to(model_dir)): digest(p.read_bytes())
        for p in sorted(model_dir.rglob("*"))
        if p.is_file()
    }
    if assets != plan["embedding_assets"]:
        raise ValueError("Dense model assets differ from registration")
    report.update(
        source_files_verified=len(actual),
        source_sha256=digest(canonical(actual)),
        versions={
            n: version(n)
            for n in [
                "hindsight-api-slim",
                "fastembed",
                "onnxruntime",
                "numpy",
                "tiktoken",
            ]
        },
        python=sys.version,
        model=MODEL,
        embedding_assets=assets,
    )
    if report["versions"] != plan["versions"]:
        raise ValueError("Dependency versions differ from registration")
    dsn = os.environ["PRME_HINDSIGHT_EVAL_DATABASE_URL"]
    parts = urlsplit(dsn)
    if parts.scheme != "postgresql" or parts.hostname != "127.0.0.1":
        raise ValueError(
            "An explicit disposable loopback PostgreSQL server is required"
        )
    admin = await asyncpg.connect(dsn)
    encoding = tiktoken.get_encoding("cl100k_base")
    context = RequestContext()
    try:
        for index, case in enumerate(cases):
            row = {
                "case_id": case["case_id"],
                "inputs_sha256": digest(canonical(case)),
                "source_count": len(case["turns"]),
            }
            database = "prme_hcap_" + uuid4().hex
            engine = None
            created = False
            start_counts = provider.counts()
            try:
                row["stage"] = "database_startup"
                await admin.execute('CREATE DATABASE "' + database + '"')
                created = True
                url = urlunsplit(
                    (
                        parts.scheme,
                        parts.netloc,
                        "/" + database,
                        parts.query,
                        parts.fragment,
                    )
                )
                start = perf_counter()
                engine = MemoryEngine(
                    db_url=url,
                    memory_llm_provider="none",
                    embeddings=provider,
                    cross_encoder=RRFPassthroughCrossEncoder(),
                    pool_min_size=1,
                    pool_max_size=6,
                )
                await engine.initialize()
                row["startup_seconds"] = perf_counter() - start
                row["stage"] = "retention"
                retained = {}
                start = perf_counter()
                # Native batches use source order; no source labels or query-based admission.
                for offset in range(0, len(case["turns"]), plan["retain_batch_size"]):
                    batch = case["turns"][offset : offset + plan["retain_batch_size"]]
                    items = [
                        {
                            "content": t["content"],
                            "document_id": t["id"],
                            "context": "source role: " + t["role"],
                            "event_date": datetime.fromisoformat(t["date"]),
                            "metadata": {
                                "source_turn": t["id"],
                                "source_session": t["session_id"],
                                "source_role": t["role"],
                                "source_date": t["date"],
                            },
                        }
                        for t in batch
                    ]
                    ids = await engine.retain_batch_async(
                        "evaluation", items, request_context=context
                    )
                    if len(ids) != len(batch):
                        raise ValueError("Retain omitted an input result")
                    retained.update({t["id"]: units for t, units in zip(batch, ids)})
                row["ingestion_seconds"] = perf_counter() - start
                row["stage"] = "document_readback"
                for turn in case["turns"]:
                    document = await engine.get_document(
                        turn["id"], "evaluation", request_context=context
                    )
                    if (
                        document is None
                        or document["original_text"] != turn["content"]
                        or document["memory_unit_count"] != len(retained[turn["id"]])
                    ):
                        write(
                            args.captures / (case["case_id"] + ".readback-error.json"),
                            {
                                "source_id": turn["id"],
                                "source_sha256": digest(turn["content"].encode()),
                                "document": document,
                            },
                        )
                        raise ValueError(
                            "Public document readback differs from admitted source"
                        )
                row["documents_verified"] = len(case["turns"])
                row["stage"] = "recall"
                start = perf_counter()
                response = await engine.recall_async(
                    "evaluation",
                    case["question"],
                    question_date=datetime.fromisoformat(case["question_date"]),
                    budget=Budget(plan["recall_budget"]),
                    max_tokens=plan["recall_max_text_tokens"],
                    include_entities=False,
                    include_chunks=False,
                    include_source_facts=False,
                    reranking="rrf",
                    request_context=context,
                )
                row["retrieval_seconds"] = perf_counter() - start
                public = response.model_dump(mode="json")
                row["stage"] = "return_validation"
                write(
                    args.captures / (case["case_id"] + ".public.json"),
                    {
                        "case_id": case["case_id"],
                        "response": public,
                        "retained": retained,
                    },
                )
                row["ranked_source_ids"] = returned_units(public, case, retained)[
                    : plan["candidate_limit"]
                ]
                row["contexts"] = {
                    str(b): context_from_units(public["results"], b, encoding)
                    for b in plan["context_budgets"]
                }
                snapshot = {
                    "case_id": case["case_id"],
                    "response": public,
                    "retained": retained,
                    "contexts": row["contexts"],
                }
                filename = case["case_id"] + ".json"
                write(args.captures / filename, snapshot)
                row["capture"] = {
                    "filename": filename,
                    "sha256": digest((args.captures / filename).read_bytes()),
                }
                row.pop("contexts")
                row["stage"] = "complete"
            except Exception as exc:
                row["error_type"] = type(exc).__name__
                report["errors"] += 1
                # No error body: third-party exceptions can contain connection details.
            finally:
                if engine is not None:
                    await engine.close()
                if created:
                    await admin.execute('DROP DATABASE "' + database + '" WITH (FORCE)')
            row["embedding"] = {
                k: v - start_counts[k] for k, v in provider.counts().items()
            }
            report["details"].append(row)
            write(args.output, report)
            print(
                f"Captured {index + 1}/{len(cases)}; errors={report['errors']}",
                flush=True,
            )
            if row.get("error_type") and args.fail_fast:
                break
        report["complete"] = (
            len(report["details"]) == len(cases) and report["errors"] == 0
        )
    finally:
        await admin.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["inputs", "plan", "output", "captures", "upstream"]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--fail-fast", action="store_true")
    args = parser.parse_args()
    if args.output.exists() or args.captures.exists():
        raise ValueError("Refusing to overwrite prior captures")
    report = {
        "complete": False,
        "errors": 0,
        "details": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "plan_sha256": digest(args.plan.read_bytes()),
        "runner_sha256": digest(Path(__file__).read_bytes()),
    }
    try:
        asyncio.run(run(args, report))
    except BaseException as exc:
        report.update(complete=False, fatal_error_type=type(exc).__name__)
        raise
    finally:
        write(args.output, report)
    raise SystemExit(0 if report["complete"] else 1)


if __name__ == "__main__":
    main()
