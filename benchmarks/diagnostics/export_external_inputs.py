"""Export the existing development cohort into separate neutral inputs and labels."""

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from benchmarks.evidence import longmemeval_sources, select_questions
from benchmarks.longmemeval import _parse_haystack_date


def export(raw):
    selected = select_questions(json.loads(raw), split="dev", seed="prme-evidence-v1")
    cases, references = [], []
    for index, question in enumerate(selected):
        case_id = f"case-{index:04d}"
        sources, gold = longmemeval_sources(question)
        turns = []
        for source in sources:
            turn = asdict(source)
            date = _parse_haystack_date(source.date)
            if date is None:
                raise ValueError("Every source needs a valid supplied date")
            turn["date"] = date.isoformat()
            turns.append(turn)
        date = _parse_haystack_date(question["question_date"])
        if date is None:
            raise ValueError("Every query needs a valid supplied date")
        cases.append(
            {
                "case_id": case_id,
                "question": question["question"],
                "question_date": date.isoformat(),
                "turns": turns,
            }
        )
        references.append(
            {
                "case_id": case_id,
                "question_id": question["question_id"],
                "category": question["question_type"],
                "answer": question["answer"],
                "evidence_source_ids": sorted(gold),
            }
        )
    return {"cases": cases}, {
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "split": "dev",
        "split_seed": "prme-evidence-v1",
        "references": references,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--references", type=Path, required=True)
    args = parser.parse_args()
    if args.inputs.exists() or args.references.exists():
        raise ValueError("Refusing to overwrite prior inputs")
    inputs, references = export(args.dataset.read_bytes())
    for path, value in [(args.inputs, inputs), (args.references, references)]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        )
    print(
        {
            "cases": len(inputs["cases"]),
            "turns": sum(len(case["turns"]) for case in inputs["cases"]),
        }
    )


if __name__ == "__main__":
    main()
