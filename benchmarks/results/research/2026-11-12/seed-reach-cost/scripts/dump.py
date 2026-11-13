"""Save the real inputs of ``_seed_reach`` on m3 questions, for offline micro benchmarks.

    cd ~/Sites/prism-seed-reach && PYTHONPATH=src:. .venv/bin/python /tmp/task26/probes/dump.py \
        --pack /tmp/task26/packs/m3 --out /tmp/task26/results/dump-m3.pickle

For each question: the seed ids, which known nodes are owner entities, the known ids, the depths, the links
``walk_links`` returned, max hops, the graph candidate ids the stage kept, and the result of ``_seed_reach``.
"""
import argparse, asyncio, json, os, pickle, shutil, subprocess, sys
from pathlib import Path

sys.path.insert(0, "/tmp/task26/probes")
from parts import M3_QUESTIONS, M3_TIME, M3_USER  # noqa: E402

os.environ.update({"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant-blocked", "PRME_EXTRACTION_API_KEY": "",
                   "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9"})
sys.path.insert(0, os.getcwd())


async def main(args):
    from benchmarks.diagnostics.product_packing import _quiet_offline_cli, gate_config
    from prme import MemoryEngine
    from prme.ingestion.owner_name import OWNER_NAME_KEY
    from prme.retrieval import candidates

    _quiet_offline_cli()
    copy = Path("/tmp/task26/scratch/dump-copy")
    shutil.rmtree(copy, ignore_errors=True)
    subprocess.run(["cp", "-cR", args.pack, str(copy)], check=True)
    original_reach, original_stage = candidates._seed_reach, candidates._generate_graph_candidates
    current: dict = {}
    saved = []

    async def reach(graph_store, seeds, nodes, max_hops, depths=None):
        links = []
        original_links = type(graph_store).walk_links

        async def capture(self, node_ids):
            found = await original_links(self, node_ids)
            links.extend(found)
            return found

        type(graph_store).walk_links = capture
        try:
            result = await original_reach(graph_store, seeds, nodes, max_hops, depths)
        finally:
            type(graph_store).walk_links = original_links
        known = {**nodes, **{str(seed.id): seed for seed in seeds}}
        current.update(
            seeds=[str(seed.id) for seed in seeds],
            seed_owner=[candidates._is_owner_entity(seed, OWNER_NAME_KEY) for seed in seeds],
            owners=sorted(i for i, n in known.items() if candidates._is_owner_entity(n, OWNER_NAME_KEY)),
            known=list(known), depths=dict(depths or {}), max_hops=max_hops, links=links, result=result,
        )
        return result

    async def stage(*a, **k):
        current.clear()
        found = await original_stage(*a, **k)
        if current:
            saved.append({**current, "targets": [str(c["node"].id) for c in found]})
        return found

    candidates._seed_reach, candidates._generate_graph_candidates = reach, stage
    async with MemoryEngine.open(gate_config(copy)) as engine:
        for question in M3_QUESTIONS:
            before = len(saved)
            await engine.retrieve(question, user_id=M3_USER, reference_time=M3_TIME)
            if len(saved) > before:
                saved[-1]["question"] = question
    shutil.rmtree(copy, ignore_errors=True)
    Path(args.out).write_bytes(pickle.dumps(saved))
    for item in saved:
        print(json.dumps({"q": item["question"], "seeds": len(item["seeds"]), "known": len(item["known"]),
                          "links": len(item["links"]), "targets": len(item["targets"]),
                          "limit": item["result"] is None}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", required=True)
    parser.add_argument("--out", required=True)
    asyncio.run(main(parser.parse_args()))
