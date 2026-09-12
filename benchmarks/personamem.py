"""Strict, label-separated PersonaMem-v2 text adapter for local memory trials.

The persona-hidden variant omits the single initial oracle-persona system message.
It is deliberately distinct from the upstream full-context benchmark protocol.
"""

from __future__ import annotations

import ast
import csv
from dataclasses import dataclass
import io
import json
from pathlib import Path, PurePosixPath

from benchmarks.diagnostics.packing_reader import canonical, digest
from benchmarks.evidence import SourceTurn

SELECTION_SEED = "prme-personamem-v2-packing-pilot-v1"


@dataclass(frozen=True)
class ChoiceQuestion:
    id: str
    persona_id: str
    query: str
    options: tuple[str, ...]


def parse_query(value):
    try:
        query = json.loads(value)
    except json.JSONDecodeError:
        query = ast.literal_eval(value)
    if (not isinstance(query, dict) or set(query) != {"role", "content"}
            or query["role"] != "user" or not isinstance(query["content"], str)
            or not query["content"].strip()):
        raise ValueError("Expected a nonempty text user query")
    return query["content"]


def case_id(row):
    return digest(canonical([row["persona_id"], parse_query(row["user_query"])]))


def history_path(row):
    value = row["chat_history_32k_link"]
    path = PurePosixPath(value)
    if (path.is_absolute() or len(path.parts) != 3 or path.parts[:2] != ("data", "chat_history_32k")
            or not path.name.endswith(f"_persona{row['persona_id']}.json")
            or str(path) != value or "\\" in value):
        raise ValueError("Invalid persona history path")
    return value


def read_rows(raw):
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    if not rows:
        raise ValueError("Empty benchmark")
    ids = [case_id(row) for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate persona/query identity")
    for row in rows:
        if not row["persona_id"].isdigit():
            raise ValueError("Expected numeric persona identity")
        history_path(row)
    return rows


def select_rows(rows, *, personas=24, questions_per_persona=4):
    """Select by identities only; every persona gets the same query count."""
    if personas < 1 or questions_per_persona < 1:
        raise ValueError("Positive cohort sizes required")
    groups = {}
    for row in rows:
        groups.setdefault(row["persona_id"], []).append(row)
    owners = sorted(groups, key=lambda p: digest(f"{SELECTION_SEED}:persona:{p}".encode()))[:personas]
    if len(owners) != personas:
        raise ValueError("Insufficient personas")
    chosen = []
    for owner in owners:
        values = sorted(groups[owner], key=lambda r: digest(f"{SELECTION_SEED}:query:{case_id(r)}".encode()))
        if len(values) < questions_per_persona:
            raise ValueError("Selected persona has too few questions; do not substitute")
        if len({history_path(r) for r in values}) != 1:
            raise ValueError("Persona has inconsistent history references")
        chosen.extend(values[:questions_per_persona])
    return chosen


def question_and_reference(row):
    """Options may reach the reader; their correct label never reaches retrieval."""
    query = parse_query(row["user_query"])
    wrong = json.loads(row["incorrect_answers"])
    if not isinstance(wrong, list) or len(wrong) != 3:
        raise ValueError("Exactly three distractors required")
    values = [row["correct_answer"], *wrong]
    if any(not isinstance(v, str) or not v.strip() for v in values) or len(set(values)) != 4:
        raise ValueError("Options must be four distinct nonempty strings")
    qid = case_id(row)
    # Deterministic content-keyed order avoids Python's process-randomized hash.
    # The correct answer has no privileged position in the ordering.
    values.sort(key=lambda text: digest(canonical([SELECTION_SEED, "option", qid, text])))
    correct = "ABCD"[values.index(row["correct_answer"])]
    question = ChoiceQuestion(qid, row["persona_id"], query, tuple(values))
    reference = {"question_id": qid, "persona_id": row["persona_id"], "correct": correct,
                 **{k: row[k] for k in ("pref_type", "who", "updated", "conversation_scenario")}}
    return question, reference


def conversation_sources(raw):
    """Ignore metadata and the initial provided persona; preserve dialog verbatim."""
    history = json.loads(raw)
    if not isinstance(history, dict) or not isinstance(history.get("chat_history"), list):
        raise ValueError("Expected chat_history object")
    messages = history["chat_history"]
    if not messages or messages[0].get("role") != "system":
        raise ValueError("Expected one leading oracle-persona message")
    if any(not isinstance(m, dict) or not {"role", "content"} <= set(m)
           or not isinstance(m["content"], str) for m in messages):
        raise ValueError("Expected plain role/content messages")
    if any(m["role"] not in {"user", "assistant"} for m in messages[1:]):
        raise ValueError("Unexpected dialog role; do not silently omit messages")
    if len(messages) == 1:
        raise ValueError("Empty conversation")
    # Message metadata is also excluded by the role/content whitelist. One
    # downloaded text message has extra generation keys; these are not memory.
    # No session or timestamp exists in this format. Empty session/date strings
    # explicitly mean unavailable, not an inferred chronology.
    return [SourceTurn(id=f"t{i:05d}", session_id="", role=m["role"], content=m["content"], date="")
            for i, m in enumerate(messages[1:])]


def cohort_identity(csv_path: Path, root: Path):
    raw = csv_path.read_bytes()
    selected = select_rows(read_rows(raw))
    histories = {}
    for row in selected:
        name = history_path(row)
        source_raw = (root / name).read_bytes()
        turns = conversation_sources(source_raw)
        histories[name] = {"sha256": digest(source_raw), "source_count": len(turns),
                           "sources_sha256": digest(canonical([vars(t) for t in turns]))}
        question_and_reference(row)
    return selected, {"csv_sha256": digest(raw), "selection_seed": SELECTION_SEED,
                      "selected_question_ids": [case_id(r) for r in selected],
                      "selected_persona_ids": list(dict.fromkeys(r["persona_id"] for r in selected)),
                      "histories": histories}
