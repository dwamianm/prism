"""Fresh installed PRME raw-source capture for the matched external study."""

import argparse
import asyncio
from datetime import datetime, timezone
from importlib.metadata import version
import json
from pathlib import Path
import tempfile
from time import perf_counter

from hindsight_capture import canonical, digest, validate_case, write


async def run(args, report):
    plan = json.loads(args.plan.read_bytes())
    cases = json.loads(args.inputs.read_bytes())["cases"]
    if (
        digest(args.inputs.read_bytes()) != plan["inputs_sha256"]
        or digest(Path(__file__).read_bytes()) != plan["runner_sha256"]
    ):
        raise ValueError("Registered inputs or runner differ")
    if [case["case_id"] for case in cases] != plan["case_ids"]:
        raise ValueError("Case coverage differs")
    for case in cases:
        validate_case(case)
    import prme
    from prme import MemoryEngine, PRMEConfig, NodeType
    from prme.storage.embedding import FastEmbedProvider
    from prme.retrieval.packing import pack_context

    installed = Path(prme.__file__).parent
    actual = {
        str(p.relative_to(installed)): digest(p.read_bytes())
        for p in sorted(installed.rglob("*.py"))
    }
    if actual != plan["package_files"]:
        raise ValueError("Installed PRME source differs from registration")

    class CountedBGE(FastEmbedProvider):
        def __init__(self):
            super().__init__()
            self.calls = self.texts = self.characters = 0

        async def embed(self, texts):
            self.calls += 1
            self.texts += len(texts)
            self.characters += sum(map(len, texts))
            return await super().embed(texts)

        def counts(self):
            return {
                "calls": self.calls,
                "texts": self.texts,
                "characters": self.characters,
            }

    provider = CountedBGE()
    await provider.embed(["model identity preflight"])
    model_dir = Path(provider._model.model._model_dir)
    assets = {
        str(p.relative_to(model_dir)): digest(p.read_bytes())
        for p in sorted(model_dir.rglob("*"))
        if p.is_file()
    }
    if assets != plan["embedding_assets"]:
        raise ValueError("Embedding assets differ")
    report.update(
        package_source_commit=plan["package_source_commit"],
        source_files_verified=len(actual),
        source_sha256=digest(canonical(actual)),
        embedding_assets=assets,
        versions={
            n: version(n)
            for n in [
                "prme",
                "fastembed",
                "onnxruntime",
                "numpy",
                "tiktoken",
                "duckdb",
                "usearch",
                "tantivy",
            ]
        },
    )
    if report["versions"] != plan["versions"]:
        raise ValueError("Installed dependency versions differ")
    for index, case in enumerate(cases):
        row = {
            "case_id": case["case_id"],
            "inputs_sha256": digest(canonical(case)),
            "source_count": len(case["turns"]),
        }
        counts = provider.counts()
        try:
            with tempfile.TemporaryDirectory(
                prefix="prme-public-capture-"
            ) as directory:
                root = Path(directory)
                config = PRMEConfig(
                    **plan["config"],
                    db_path=str(root / "memory.duckdb"),
                    vector_path=str(root / "vectors.usearch"),
                    lexical_path=str(root / "lexical"),
                    _env_file=None,
                )
                start = perf_counter()
                async with MemoryEngine.open(
                    config, embedding_provider=provider
                ) as engine:
                    row["startup_seconds"] = perf_counter() - start
                    start = perf_counter()
                    events = {}
                    for turn in case["turns"]:
                        events[turn["id"]] = await engine.store(
                            turn["content"],
                            user_id="evaluation",
                            role=turn["role"],
                            session_id=turn["session_id"],
                            node_type=NodeType.NOTE,
                            metadata={
                                "source_turn": turn["id"],
                                "source_session": turn["session_id"],
                                "source_role": turn["role"],
                                "source_date": turn["date"],
                            },
                            event_time=datetime.fromisoformat(turn["date"]),
                        )
                    row["ingestion_seconds"] = perf_counter() - start
                    nodes = await engine.query_nodes(
                        user_id="evaluation", limit=len(case["turns"]) + 1
                    )
                    by_id = {t["id"]: t for t in case["turns"]}
                    node_sources = {str(n.id): n.metadata["source_turn"] for n in nodes}
                    if len(nodes) != len(by_id) or set(node_sources.values()) != set(
                        by_id
                    ):
                        raise ValueError("Not every source is durably indexed")
                    for node in nodes:
                        turn = by_id[node.metadata["source_turn"]]
                        event = await engine.get_event(
                            events[turn["id"]], user_id="evaluation"
                        )
                        if (
                            node.content != turn["content"]
                            or event.content != turn["content"]
                            or event.role != turn["role"]
                        ):
                            raise ValueError("Public source readback differs")
                    row["documents_verified"] = len(nodes)
                    start = perf_counter()
                    response = await engine.retrieve(
                        case["question"],
                        user_id="evaluation",
                        reference_time=datetime.fromisoformat(case["question_date"]),
                    )
                    row["retrieval_seconds"] = perf_counter() - start
                    for candidate in response.results:
                        source = node_sources[str(candidate.node.id)]
                        if candidate.node.content != by_id[source]["content"]:
                            raise ValueError("Returned source changed")
                    row["ranked_source_ids"] = list(
                        dict.fromkeys(
                            node_sources[str(c.node.id)] for c in response.results
                        )
                    )[: plan["candidate_limit"]]
                    contexts = {}
                    for budget in plan["context_budgets"]:
                        bundle = pack_context(
                            response.results,
                            config.packing.model_copy(update={"token_budget": budget}),
                        )
                        rendered = bundle.render()
                        if (
                            budget == config.packing.token_budget
                            and rendered != response.bundle.render()
                        ):
                            raise ValueError(
                                "Original public context does not reproduce"
                            )
                        contexts[str(budget)] = {
                            "context": rendered,
                            "tokens": bundle.tokens_used,
                            "sha256": digest(rendered.encode()),
                        }
                    snapshot = {
                        "case_id": case["case_id"],
                        "candidates": [
                            c.model_dump(mode="json") for c in response.results
                        ],
                        "contexts": contexts,
                        "events": events,
                        "node_sources": node_sources,
                        "packing_config": config.packing.model_dump(mode="json"),
                    }
                    filename = case["case_id"] + ".json"
                    write(args.captures / filename, snapshot)
                    row["capture"] = {
                        "filename": filename,
                        "sha256": digest((args.captures / filename).read_bytes()),
                    }
        except Exception as exc:
            row["error_type"] = type(exc).__name__
            report["errors"] += 1
        row["embedding"] = {k: v - counts[k] for k, v in provider.counts().items()}
        report["details"].append(row)
        write(args.output, report)
        print(
            f"Captured {index + 1}/{len(cases)}; errors={report['errors']}", flush=True
        )
        if row.get("error_type") and args.fail_fast:
            break
    report["complete"] = len(report["details"]) == len(cases) and not report["errors"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["inputs", "plan", "output", "captures"]:
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
