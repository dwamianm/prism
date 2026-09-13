"""Verify complete native-exited captures before comparing ranking and actual text."""

import argparse
import json
from pathlib import Path
import random
import statistics

from benchmarks.evidence import SourceTurn, pack_sources
from benchmarks.diagnostics.hindsight_capture import (
    canonical,
    context_from_units,
    digest,
    returned_units,
    validate_case,
    write,
)
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context
import tiktoken


def cluster_statistics(pairs, groups, *, samples=2000):
    if len(pairs) != len(groups):
        raise ValueError("One group per pair required")
    if not pairs:
        return {
            "queries": 0,
            "groups": 0,
            "before": None,
            "after": None,
            "delta": None,
            "interval_95": None,
        }
    differences = [b - a for a, b in pairs]
    result = {
        "queries": len(pairs),
        "groups": len(set(groups)),
        "before": statistics.mean(a for a, b in pairs),
        "after": statistics.mean(b for a, b in pairs),
        "delta": statistics.mean(differences),
        "wins": sum(d > 1e-12 for d in differences),
        "losses": sum(d < -1e-12 for d in differences),
        "ties": sum(abs(d) <= 1e-12 for d in differences),
    }
    if len(set(groups)) < 2:
        return dict(result, interval_95=None)
    by_group = {
        g: [d for d, h in zip(differences, groups) if h == g]
        for g in sorted(set(groups))
    }
    rng = random.Random(42)
    keys = list(by_group)
    values = sorted(
        statistics.mean(
            d for key in rng.choices(keys, k=len(keys)) for d in by_group[key]
        )
        for _ in range(samples)
    )
    return dict(
        result,
        interval_95=[
            values[int(0.025 * (samples - 1))],
            values[int(0.975 * (samples - 1))],
        ],
    )


def groups_for(cases, references):
    parents = {case["case_id"]: case["case_id"] for case in cases}

    def root(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    seen = {}
    for case in cases:
        key = case["case_id"]
        for marker in [
            ("question", references[key]["question_id"].removesuffix("_abs")),
            ("history", digest(canonical(case["turns"]))),
        ]:
            if marker in seen:
                parents[root(key)] = root(seen[marker])
            else:
                seen[marker] = key
    return {key: root(key) for key in parents}


def actual_text_metrics(entries, sources, gold):
    hits, whole, pointers = set(), set(), set()
    for source, text, content_bearing in entries:
        if source not in sources:
            raise ValueError("Unknown context source")
        if not content_bearing:
            pointers.add(source)
        elif text.strip():
            hits.add(source)
            if sources[source].content.strip() and sources[source].content in text:
                whole.add(source)
    return {
        "source_ids": sorted(hits),
        "whole_turn_record_ids": sorted(whole),
        "pointer_source_ids": sorted(pointers),
        "document_hit_recall": len(hits & gold) / len(gold) if gold else None,
        "whole_turn_record_recall": len(whole & gold) / len(gold) if gold else None,
    }


def analyze(args):
    complete = json.loads(args.completion.read_bytes())
    inputs_raw = args.inputs.read_bytes()
    refs_raw = args.references.read_bytes()
    cases = json.loads(inputs_raw)["cases"]
    reference_data = json.loads(refs_raw)
    references = {r["case_id"]: r for r in reference_data["references"]}
    expected = [c["case_id"] for c in cases]
    if (
        not cases
        or len(set(expected)) != len(cases)
        or len(reference_data["references"]) != len(references)
        or set(references) != set(expected)
    ):
        raise ValueError("Ambiguous input/reference coverage")
    config = {}
    reports = {}
    for product in ["prme", "hindsight"]:
        status = complete["products"][product]
        if status["native_exit_code"] != 0:
            raise ValueError("Native success required for both products")
        path = getattr(args, product + "_report")
        if digest(path.read_bytes()) != status["report_sha256"]:
            raise ValueError("Completion hash differs from report")
        report = json.loads(path.read_bytes())
        plan = json.loads(getattr(args, product + "_plan").read_bytes())
        if (
            digest(getattr(args, product + "_plan").read_bytes())
            != report["plan_sha256"]
        ):
            raise ValueError("Capture plan differs")
        if plan["inputs_sha256"] != digest(inputs_raw) or plan[
            "reference_sha256"
        ] != digest(refs_raw):
            raise ValueError("Study inputs differ")
        if plan["case_ids"] != expected:
            raise ValueError("Registered case coverage differs")
        if any(
            report[field] != plan[field]
            for field in ["runner_sha256", "versions", "embedding_assets"]
        ):
            raise ValueError("Reported runtime differs from registration")
        if (
            not report["complete"]
            or report["errors"]
            or [r["case_id"] for r in report["details"]] != expected
        ):
            raise ValueError("Successful complete ordered coverage required")
        reports[product] = {r["case_id"]: r for r in report["details"]}
        config[product] = plan
    if config["prme"]["context_budgets"] != config["hindsight"]["context_budgets"]:
        raise ValueError("Context budgets differ")
    encoding = tiktoken.get_encoding("cl100k_base")

    def count(text):
        return len(encoding.encode(text, disallowed_special=()))

    details = []
    for case in cases:
        validate_case(case)
        key = case["case_id"]
        sources = {turn["id"]: SourceTurn(**turn) for turn in case["turns"]}
        gold = set(references[key]["evidence_source_ids"])
        if not gold <= set(sources):
            raise ValueError("Unknown reference source")
        row = {
            "case_id": key,
            "question_id": references[key]["question_id"],
            "category": references[key]["category"],
            "source_count": len(sources),
            "evidence_source_ids": sorted(gold),
            "products": {},
        }
        for product in ["prme", "hindsight"]:
            saved = reports[product][key]
            if saved["inputs_sha256"] != digest(canonical(case)) or saved[
                "source_count"
            ] != len(sources):
                raise ValueError("Case/source identity mismatch")
            capture = saved["capture"]
            if capture["filename"] != key + ".json":
                raise ValueError("Capture filename differs")
            path = getattr(args, product + "_captures") / capture["filename"]
            if digest(path.read_bytes()) != capture["sha256"]:
                raise ValueError("Capture hash differs")
            snapshot = json.loads(path.read_bytes())
            if snapshot["case_id"] != key:
                raise ValueError("Snapshot identity differs")
            if product == "hindsight":
                ranked = returned_units(
                    snapshot["response"], case, snapshot["retained"]
                )[: config[product]["candidate_limit"]]
            else:
                nodes = snapshot["node_sources"]
                if len(nodes) != len(sources) or set(nodes.values()) != set(sources):
                    raise ValueError("PRME source mapping differs")
                candidates = [
                    RetrievalCandidate.model_validate(c) for c in snapshot["candidates"]
                ]
                for candidate in candidates:
                    sid = nodes[str(candidate.node.id)]
                    if candidate.node.content != sources[sid].content:
                        raise ValueError("PRME candidate content differs")
                ranked = list(dict.fromkeys(nodes[str(c.node.id)] for c in candidates))[
                    : config[product]["candidate_limit"]
                ]
            if ranked != saved["ranked_source_ids"]:
                raise ValueError("Ranking does not reproduce")
            metrics = {}
            for budget in config[product]["context_budgets"]:
                stored = snapshot["contexts"][str(budget)]
                if product == "hindsight":
                    reproduced = context_from_units(
                        snapshot["response"]["results"], budget, encoding
                    )
                    entries = [
                        (value["source"], value["text"], True)
                        for line in stored["context"].split("\n")
                        if line
                        for value in [json.loads(line)]
                    ]
                else:
                    packing = PackingConfig.model_validate(snapshot["packing_config"])
                    if (
                        snapshot["packing_config"]
                        != config[product]["config"]["packing"]
                    ):
                        raise ValueError("PRME packing configuration differs")
                    bundle = pack_context(
                        candidates, packing.model_copy(update={"token_budget": budget})
                    )
                    text = bundle.render()
                    reproduced = {
                        "context": text,
                        "tokens": bundle.tokens_used,
                        "sha256": digest(text.encode()),
                    }
                    entries = [
                        (
                            nodes[value["id"]],
                            value.get("text", ""),
                            value["representation"] in {"full", "prose", "structured"},
                        )
                        for line in stored["context"].split("\n")
                        if line.startswith("{")
                        for value in [json.loads(line)]
                    ]
                for field in ["context", "tokens", "sha256"]:
                    if stored[field] != reproduced[field]:
                        raise ValueError("Actual context does not reproduce")
                if (
                    count(stored["context"]) != stored["tokens"]
                    or stored["tokens"] > budget
                ):
                    raise ValueError("Serialized context violates budget")
                actual = actual_text_metrics(entries, sources, gold)
                text, ids, tokens = pack_sources(
                    [sources[sid] for sid in ranked],
                    token_budget=budget,
                    count_tokens=count,
                )
                metrics[str(budget)] = {
                    "actual": dict(
                        actual, tokens=stored["tokens"], context_sha256=stored["sha256"]
                    ),
                    "shared_whole_turn": {
                        "tokens": tokens,
                        "source_ids": ids,
                        "context_sha256": digest(text.encode()),
                        "evidence_recall": len(set(ids) & gold) / len(gold)
                        if gold
                        else None,
                    },
                }
            row["products"][product] = metrics
        details.append(row)
    groups = groups_for(cases, references)

    def summary(rows):
        values = {}
        for budget in config["prme"]["context_budgets"]:
            values[str(budget)] = {}
            for label, section, metric in [
                ("shared_whole_turn_recall", "shared_whole_turn", "evidence_recall"),
                ("actual_document_hit_recall", "actual", "document_hit_recall"),
                (
                    "actual_whole_turn_record_recall",
                    "actual",
                    "whole_turn_record_recall",
                ),
            ]:
                labelled = [
                    r
                    for r in rows
                    if r["products"]["prme"][str(budget)][section][metric] is not None
                ]
                pairs = [
                    (
                        r["products"]["hindsight"][str(budget)][section][metric],
                        r["products"]["prme"][str(budget)][section][metric],
                    )
                    for r in labelled
                ]
                values[str(budget)][label] = {
                    "query_bootstrap": cluster_statistics(
                        pairs, [r["case_id"] for r in labelled]
                    ),
                    "group_bootstrap": cluster_statistics(
                        pairs, [groups[r["case_id"]] for r in labelled]
                    ),
                }
        return values

    return {
        "complete": True,
        "direction": "before=Hindsight raw chunks; after=PRME raw notes",
        "verification_passed": True,
        "questions": len(details),
        "history_or_question_groups": len(set(groups.values())),
        "inputs_sha256": digest(inputs_raw),
        "references_sha256": digest(refs_raw),
        "analyzer_sha256": digest(Path(__file__).read_bytes()),
        "overall": summary(details),
        "categories": {
            category: summary([r for r in details if r["category"] == category])
            for category in sorted({r["category"] for r in details})
        },
        "details": details,
        "limits": [
            "Development cohort, no superiority gate or independent holdout",
            "Shared whole-turn packer reconstructs documents and measures ranking only",
            "Actual Hindsight context is adapter-rendered from exact returned units",
            "Whole-turn-in-one-record diagnostic can miss complete evidence distributed across chunks",
            "No answer generation, extraction comparison or fair speed ratio",
            "Groups cover abstention pairs and identical histories, not all partial history dependence",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in [
        "inputs",
        "references",
        "completion",
        "prme-report",
        "hindsight-report",
        "prme-captures",
        "hindsight-captures",
        "prme-plan",
        "hindsight-plan",
        "output",
    ]:
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite prior analysis")
    write(args.output, analyze(args))


if __name__ == "__main__":
    main()
