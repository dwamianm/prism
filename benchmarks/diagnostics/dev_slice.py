"""A fixed development slice of LoCoMo and LongMemEval-S for quick iteration.

Building extracted packs and answering every question is too slow to repeat
after each change, so development iterates on a small slice and keeps the full
benchmarks for the gates before a merge or a default change. The slice is
chosen once by the rules below and committed, so every iteration measures the
same questions. The questions outside it are not used while iterating.

- LoCoMo: every question of the three shortest conversations (by stored turns)
  whose questions cover every category. Conversations are the unit that costs
  a pack build, and choosing them by length alone keeps outcomes out of the
  choice. The other conversations stay out of the loop.
- LongMemEval-S: every question the defaults baseline answers wrong on every
  repeated answer of an identical context ("stable_wrong"), plus a fixed number
  of "stable_right" regression canaries per question type, taken in order of
  the SHA-256 of the question ID. Each question has its own history, so each
  one costs a pack build.

"noisy" marks a question whose verdict has changed between two answers of an
identical context in any committed DeepSeek answer run, the #118 finding that
repeated answers differ. A noisy question can pass or fail with no change, so a
flip there is not evidence of an effect. LoCoMo keeps its noisy questions,
because whole conversations are kept, and tags them so a reader can set them
aside. LongMemEval-S leaves them out.

    python -m benchmarks.diagnostics.dev_slice select --output benchmarks/slices/dev-v1.json
    python -m benchmarks.diagnostics.dev_slice check benchmarks/slices/dev-v1.json
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "benchmarks" / "results" / "research"
DEFAULT_SLICE = REPO / "benchmarks" / "slices" / "dev-v1.json"
KIND = "prme-dev-slice"
SCHEMA_VERSION = 1
BENCHMARKS = ("locomo", "longmemeval")
TRACK = "ollama-deepseek-v4.1-flash-cloud"
# The defaults baseline after #177 and #187 (CLAUDE.md, epic #77 rules).
BASELINE = {name: f"2026-09-25/{TRACK}-prme@d811e3ed-{name}-result.json" for name in BENCHMARKS}
ANSWER_KINDS = ("ollama-answer-result", "ollama-answer-result-sample")  # not the lenient-judge rescoring
LOCOMO_CONVERSATIONS = 3
CANARIES_PER_TYPE = 4


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def answer_runs(benchmark: str, results: Path) -> list[Path]:
    """Every committed DeepSeek answer run for the benchmark, in a fixed order."""
    paths = sorted(results.glob(f"*/{TRACK}-*-{benchmark}-*result.json"))
    return [path for path in paths if json.loads(path.read_text()).get("kind") in ANSWER_KINDS]


def _contexts(row: dict) -> list[str]:
    """Every hash the row records for the exact context it was answered on.

    Newer runs record ``context_text_sha256`` and ``context_sha256``; older ones
    only ``context_sha256``. Each kind is kept apart, and a row is filed under
    every kind it has, so answers of provably identical contexts are grouped
    across old and new runs.
    """
    return [f"{field}:{row[field]}" for field in ("context_text_sha256", "context_sha256") if row.get(field)]


def _judged(paths: list[Path]):
    for path in paths:
        for row in json.loads(path.read_text())["rows"]:
            if row.get("outcome", "judged") == "judged" and isinstance(row.get("correct"), bool):
                yield row


def verdicts(paths: list[Path]) -> dict[str, dict[str, set[bool]]]:
    """Each question's verdicts, grouped by the exact context it was answered on."""
    seen: dict[str, dict[str, set[bool]]] = defaultdict(lambda: defaultdict(set))
    for row in _judged(paths):
        for context in _contexts(row):
            seen[row["question_id"]][context].add(row["correct"])
    return seen


def status(question_id: str, correct: bool, seen: dict) -> str:
    if any(len(values) > 1 for values in seen.get(question_id, {}).values()):
        return "noisy"
    return "stable_right" if correct else "stable_wrong"


def _answer_counts(paths: list[Path]) -> dict[str, int]:
    """The most answers any one identical context of each question received."""
    counts: dict[str, Counter] = defaultdict(Counter)
    for row in _judged(paths):
        for context in _contexts(row):
            counts[row["question_id"]][context] += 1
    return {question: max(by_context.values()) for question, by_context in counts.items()}


def select(root: Path = REPO, *, runs: dict[str, list[Path]] | None = None,
           locomo_turns: dict[str, int] | None = None) -> dict:
    """Derive the slice from the baseline and the answer runs; the same inputs give the same slice.

    ``runs`` fixes the answer runs read for noise. Without it, every committed run is read.
    """
    results = root / RESULTS.relative_to(REPO)
    turns = locomo_turns if locomo_turns is not None else _locomo_turns()
    sources, benchmarks = {"locomo_turns": dict(sorted(turns.items()))}, {}
    for name in BENCHMARKS:
        baseline_path = results / BASELINE[name]
        baseline = json.loads(baseline_path.read_text())["rows"]
        runs_read = runs[name] if runs is not None else answer_runs(name, results)
        seen, counts = verdicts(runs_read), _answer_counts(runs_read)
        sources[name] = {
            "baseline": {"path": str(baseline_path.relative_to(root)), "sha256": _sha256(baseline_path)},
            "answer_runs": [{"path": str(path.relative_to(root)), "sha256": _sha256(path)} for path in runs_read],
        }
        tags = {row["question_id"]: status(row["question_id"], row["correct"], seen) for row in baseline}
        if name == "locomo":
            chosen_rows = _locomo_rows(baseline, turns)
            units = sorted({row["cluster"] for row in chosen_rows})
        else:
            chosen_rows = _longmemeval_rows(baseline, tags)
            units = sorted(row["question_id"] for row in chosen_rows)
        questions = sorted(row["question_id"] for row in chosen_rows)
        benchmarks[name] = {
            "units": units,
            "questions": questions,
            "tags": {question: tags[question] for question in questions},
            "summary": {
                "questions": len(questions),
                "of": len(baseline),
                "tags": dict(sorted(Counter(tags[question] for question in questions).items())),
                "baseline_correct": sum(row["correct"] for row in chosen_rows),
                "types": dict(sorted(Counter(row["question_type"] for row in chosen_rows).items())),
                "fewest_repeats_on_one_context": min(counts.get(question, 0) for question in questions),
            },
        }
    return {"kind": KIND, "schema_version": SCHEMA_VERSION, "name": "dev-v1",
            "rules": {"locomo_conversations": LOCOMO_CONVERSATIONS, "canaries_per_type": CANARIES_PER_TYPE,
                      "answer_kinds": list(ANSWER_KINDS)},
            "sources": sources, "benchmarks": benchmarks}


def _locomo_turns() -> dict[str, int]:
    from benchmarks.diagnostics import extracted_packs

    return {unit.unit_id: len(unit.turns) for unit in extracted_packs.benchmark_units("locomo")}


def _locomo_rows(baseline: list[dict], turns: dict[str, int]) -> list[dict]:
    categories = {row["question_type"] for row in baseline}
    by_conversation: dict[str, list[dict]] = defaultdict(list)
    for row in baseline:
        by_conversation[row["cluster"]].append(row)
    complete = [conversation for conversation, rows in by_conversation.items()
                if {row["question_type"] for row in rows} == categories]
    chosen = sorted(complete, key=lambda conversation: (turns[conversation], conversation))[:LOCOMO_CONVERSATIONS]
    if len(chosen) < LOCOMO_CONVERSATIONS:
        raise ValueError(f"Only {len(chosen)} LoCoMo conversations cover every category")
    return [row for conversation in chosen for row in by_conversation[conversation]]


def _longmemeval_rows(baseline: list[dict], tags: dict[str, str]) -> list[dict]:
    rows = [row for row in baseline if tags[row["question_id"]] == "stable_wrong"]
    by_type: dict[str, list[dict]] = defaultdict(list)
    for row in baseline:
        if tags[row["question_id"]] == "stable_right":
            by_type[row["question_type"]].append(row)
    for kind in sorted(by_type):
        ordered = sorted(by_type[kind], key=lambda row: hashlib.sha256(row["question_id"].encode()).hexdigest())
        rows += ordered[:CANARIES_PER_TYPE]
    return rows


def load_slice(path: Path) -> dict:
    """A committed slice with its identity: the file's name and SHA-256."""
    record = json.loads(path.read_text())
    if record.get("kind") != KIND or record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path} is not a version {SCHEMA_VERSION} development slice")
    missing = [name for name in BENCHMARKS if name not in record.get("benchmarks", {})]
    if missing:
        raise ValueError(f"{path} names no {', '.join(missing)} questions")
    return {**record, "identity": {"name": record["name"], "sha256": _sha256(path)}}


def slice_questions(record: dict, benchmark: str) -> frozenset[str]:
    return frozenset(record["benchmarks"][benchmark]["questions"])


def slice_units(record: dict, benchmark: str) -> list[str]:
    return list(record["benchmarks"][benchmark]["units"])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.diagnostics.dev_slice", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    chosen = commands.add_parser("select", help="Derive the slice from the committed results")
    chosen.add_argument("--output", type=Path, required=True)
    checked = commands.add_parser("check", help="Confirm a committed slice follows from the files it names")
    checked.add_argument("slice", type=Path, nargs="?", default=DEFAULT_SLICE)
    args = parser.parse_args(argv)
    if args.command == "select":
        derived = select()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(derived, indent=2) + "\n")
        print(json.dumps({name: bench["summary"] for name, bench in derived["benchmarks"].items()}, indent=2))
        return
    committed = json.loads(args.slice.read_text())
    try:
        check(committed)
    except ValueError as exc:
        sys.exit(f"{args.slice}: {exc}")
    print(f"{args.slice} follows from the files it names")


def check(committed: dict, root: Path = REPO, *, locomo_turns: dict[str, int] | None = None) -> None:
    """The slice must follow from the files it names, which must be unchanged; later runs are ignored."""
    runs = {}
    for name in BENCHMARKS:
        named = committed["sources"][name]
        for source in [named["baseline"], *named["answer_runs"]]:
            if _sha256(root / source["path"]) != source["sha256"]:
                raise ValueError(f"{source['path']} changed since the slice was selected")
        runs[name] = [root / source["path"] for source in named["answer_runs"]]
    # The turn counts were read from the dataset at selection and are recorded, so a check needs no dataset.
    turns = locomo_turns if locomo_turns is not None else committed["sources"]["locomo_turns"]
    if select(root, runs=runs, locomo_turns=turns) != committed:
        raise ValueError("the rules no longer give this slice; select a new slice under a new name")


if __name__ == "__main__":
    main()
