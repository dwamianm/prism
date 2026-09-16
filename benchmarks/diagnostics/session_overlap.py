"""Describe session overlap in a frozen cohort without inspecting answer quality."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def analyze(dataset: list[dict], selected_ids: list[str]) -> dict:
    selected = set(selected_ids)
    if not selected or len(selected) != len(selected_ids):
        raise ValueError("Cohort must contain distinct question identities")
    lookup = {row["question_id"]: row for row in dataset}
    if len(lookup) != len(dataset) or not selected <= lookup.keys():
        raise ValueError("Dataset identities are duplicate or missing from the cohort")
    rows = [lookup[q] for q in sorted(selected)]
    result = {"queries": len(rows), "session_identity": "exact dataset session ID"}
    for field in ("haystack_session_ids", "answer_session_ids"):
        owners = {}
        parent = {row["question_id"]: row["question_id"] for row in rows}
        counts = Counter()

        def find(value):
            while parent[value] != value:
                parent[value] = parent[parent[value]]
                value = parent[value]
            return value

        for row in rows:
            values = row[field]
            if not isinstance(values, list) or any(
                not isinstance(v, str) or not v for v in values
            ):
                raise ValueError("Session identities must be nonempty strings")
            qid = row["question_id"]
            for session in sorted(set(values)):
                counts[session] += 1
                if session in owners:
                    parent[find(qid)] = find(owners[session])
                else:
                    owners[session] = qid
        components = Counter(find(qid) for qid in parent)
        result[field] = {
            "unique_ids": len(counts),
            "ids_shared_across_queries": sum(count > 1 for count in counts.values()),
            "connected_components": len(components),
            "component_sizes": sorted(components.values(), reverse=True),
        }
    histories = Counter(tuple(sorted(set(row["haystack_session_ids"]))) for row in rows)
    result["exact_history_groups"] = len(histories)
    result["exact_history_group_sizes"] = sorted(histories.values(), reverse=True)
    result["limits"] = (
        "Session-ID overlap does not measure dependence strength or answer quality. "
        "Distinct IDs may still contain related content. A single overlap component "
        "cannot support a history-component bootstrap interval. This is a post-study "
        "structural audit; it does not change the frozen query-bootstrap results."
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dataset, references = args.dataset.read_bytes(), args.references.read_bytes()
    result = analyze(
        json.loads(dataset), [row["question_id"] for row in json.loads(references)]
    )
    result["dataset_sha256"] = hashlib.sha256(dataset).hexdigest()
    result["references_sha256"] = hashlib.sha256(references).hexdigest()
    result["diagnostic_sha256"] = hashlib.sha256(
        Path(__file__).read_bytes()
    ).hexdigest()
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
