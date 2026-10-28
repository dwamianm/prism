"""Score an extracted graph against a gold graph: entity and relation recall and precision.

The extraction target in numbers. A candidate graph is a JSON object in the
gold file's format (``entities`` with ``names``, ``relations`` with ``s``,
``r``, ``o`` or ``attr``, ``polarity`` and ``status``); its ids may differ from
the gold ids, because an entity matches a gold entity when any of their names
is the same text, case blind. A stated gold claim is found when the candidate
has the same subject, relation and object (or, for a literal claim, the same
subject and attribute). Inferred containment is scored apart: it is world
knowledge, so a candidate that omits it is not wrong.
"""
from __future__ import annotations

from benchmarks.graph_oracle.gold import Gold, stems


def _match_entities(gold: Gold, candidate: dict) -> dict[str, str | None]:
    names = {name.casefold(): entity.id for entity in gold.entities.values() for name in entity.names}
    return {item.get("id", item["names"][0]): next((names[name.casefold()] for name in item["names"]
                                                    if name.casefold() in names), None)
            for item in candidate["entities"]}


def _keys(claim: dict, mapped: dict[str, str | None]) -> tuple | None:
    subject = mapped.get(claim["s"])
    if subject is None:
        return None
    if claim.get("o") is None:
        return (subject, "has", frozenset(stems(claim.get("attr", ""))))
    target = mapped.get(claim["o"])
    return None if target is None else (subject, claim["r"], target)


def _ratio(found: int, total: int) -> float:
    return round(found / total, 3) if total else 1.0


def fidelity(gold: Gold, candidate: dict) -> dict:
    mapped = _match_entities(gold, candidate)
    matched_gold = {entity for entity in mapped.values() if entity}
    stated = [claim for claim in gold.claims if not claim.inferred]
    inferred = [claim for claim in gold.claims if claim.inferred]
    gold_keys: dict[tuple, list] = {}
    for claim in stated:
        key = (claim.s, claim.r, claim.o) if claim.o else (claim.s, "has", frozenset(stems(claim.attr)))
        gold_keys.setdefault(key, []).append(claim)
    inferred_keys = {(claim.s, claim.r, claim.o) for claim in inferred}
    found_keys, candidate_keys, polarity, status = set(), [], 0, 0
    for claim in candidate["relations"]:
        key = _keys(claim, mapped)
        candidate_keys.append(key)
        if key in gold_keys:
            found_keys.add(key)
            polarity += any(item.polarity == claim.get("polarity", "positive") for item in gold_keys[key])
            status += any(item.status == claim.get("status", "current") for item in gold_keys[key])
    correct = [key for key in candidate_keys if key in gold_keys or key in inferred_keys]
    matched = sum(key in gold_keys for key in candidate_keys)
    return {
        "entity_recall": _ratio(len(matched_gold), len(gold.entities)),
        "entity_precision": _ratio(sum(entity is not None for entity in mapped.values()), len(mapped)),
        "relation_recall": _ratio(len(found_keys), len(gold_keys)),
        "relation_precision": _ratio(len(correct), len(candidate_keys)),
        "polarity_accuracy": _ratio(polarity, matched),
        "status_accuracy": _ratio(status, matched),
        "inferred_containment_found": _ratio(sum(key in inferred_keys for key in candidate_keys), len(inferred_keys)),
        "gold": {"entities": len(gold.entities), "claims": len(gold_keys), "inferred": len(inferred_keys)},
        "candidate": {"entities": len(mapped), "claims": len(candidate_keys)},
    }


def fidelity_markdown(result: dict) -> str:
    lines = ["| Measure | Value |", "|---|---|"]
    lines += [f"| {name.replace('_', ' ')} | {value} |" for name, value in result.items() if not isinstance(value, dict)]
    lines += [f"| {name} | {', '.join(f'{key} {count}' for key, count in value.items())} |"
              for name, value in result.items() if isinstance(value, dict)]
    return "\n".join(lines) + "\n"
