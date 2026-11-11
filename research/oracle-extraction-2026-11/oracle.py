"""The perfect extraction test's loader and build (GUIDE.md): facts written by hand, loaded with no model.

**The mention rule of this experiment.** ``ingest(content, assertions=...)`` refuses an assertion whose subject or
object the content does not mention, by plain text (``prme.ingestion.assertions.prepare_assertions``), and the
pipeline checks the claims it builds from them the same way (``validate_grounding``, without a speaker). A turn's
"I" is then never a mention of its speaker's name. The lead decided on 2026-11-11 to widen the rule on this
research branch only, at run time, with no change to ``src/``, and to count each widening as a finding about the
form. A subject or object is accepted when:

- ``turn``: the turn mentions it (the engine's rule);
- ``owner``: it is the owner's name (``user_name`` or its first word) and the turn is a user turn that says I, me,
  my, mine, myself, we, us or our, or an assistant turn that says you or your;
- ``carried``: an earlier turn of the conversation mentions it (a pronoun or "we" refers back to it).

A literal value (relation ``has``) must be in the turn, except in an O2 closure, which may name a value an earlier
turn mentions (``closure``). Anything else is refused, with its reason, and so is an assertion the engine's form
refuses (a relation outside the vocabulary, a bad type, status or role). A turn is loaded with the assertions that
pass.

To make the engine accept exactly these, :class:`MentionPatch` replaces ``_mentioned`` in
``prme.ingestion.assertions`` and ``prme.ingestion.grounding`` while a turn is ingested: a value this rule accepted
for that turn counts as mentioned in any part of that turn's text. Outside a turn, and for any other value, the
engine's own check answers.

**The build.** :func:`build` runs ``benchmarks.diagnostics.chat_probe.build`` unchanged, so turns, event times,
session ids, roles, speaker, the end of the session and every measurement are the model build's. Its ``prepare``
hook (run before the first turn) does two things. It wraps ``engine.ingest`` so that a turn with accepted
assertions is ingested with them. It replaces the extraction provider's ``extract``, ``extract_batch`` and
``repair_pronouns`` with stubs: ``ingest()`` refuses an empty assertion list, so a turn with no assertions goes
through ``ingest()`` as any turn does and the stub answers its extraction with nothing (no entities, no facts),
which stores the turn exactly as a model build stores a turn the model found nothing in. A pronoun repair or a
single-turn extraction raises. The probe build counts the model client's calls; :func:`build` requires zero.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pydantic import ValidationError  # noqa: E402

import prme.ingestion.assertions as assertions_module  # noqa: E402
import prme.ingestion.grounding as grounding_module  # noqa: E402
from prme.ingestion.assertions import ATTRIBUTE_RELATION, Assertion, _reason, prepare_assertions  # noqa: E402

HERE = Path(__file__).resolve().parent
LEVELS = ("O1", "O2")
SETS = {"O1": ("O1",), "O1+O2": ("O1", "O2")}
ENGINE_MENTIONED = grounding_module._mentioned
_OWNER_WORDS = {
    "user": re.compile(r"(?<!\w)(?:I(?!\.e\.)|me|my|mine|myself|we|us|our)(?!\w)", re.IGNORECASE),
    "assistant": re.compile(r"(?<!\w)(?:you|your)(?!\w)", re.IGNORECASE),
}
KINDS = ("turn", "owner", "carried", "closure")
MODEL = "deepseek-v4.1-flash:cloud"


def turn_index(turns_file: dict) -> dict[str, dict]:
    """Each turn by its probe key, ``<session id>#<index>``, as the probe build keys it, in conversation order."""
    return {f"{session['id']}#{turn['index']}": turn
            for session in turns_file["sessions"] for turn in session["turns"]}


def owner_names(turns_file: dict) -> set[str]:
    name = " ".join(turns_file["user_name"].split())
    return {name.casefold(), name.split()[0].casefold()}


def _kind(value: str, field: str, assertion: Assertion, level: str, turn: dict, earlier: list[str],
          owners: set[str]) -> str | None:
    """Which part of the rule accepts ``value``, or None."""
    text = turn["text"]
    if ENGINE_MENTIONED(value, text):
        return "turn"
    literal = field == "object" and assertion.relation == ATTRIBUTE_RELATION
    if literal:
        if level == "O2" and any(ENGINE_MENTIONED(value, before) for before in earlier):
            return "closure"
        return None
    words = _OWNER_WORDS.get(turn["role"])
    if value.casefold() in owners and words is not None and words.search(text):
        return "owner"
    if any(ENGINE_MENTIONED(value, before) for before in earlier):
        return "carried"
    return None


def _refusal(field: str, assertion: Assertion, level: str) -> str:
    value = getattr(assertion, field)
    if field == "object" and assertion.relation == ATTRIBUTE_RELATION:
        where = "earlier turns" if level == "O2" else "the turn"
        return f"literal value {value!r} not in {where}"
    return f"{field} {value!r} not in the turn, not the owner, not named earlier"


def check(facts: dict, turns_file: dict) -> dict:
    """Every assertion checked alone under this experiment's rule.

    Returns ``{"problems", "turns", "roles"}``. A problem is a malformed entry (no such turn, a repeated or
    out-of-order turn, an assertion without a level). ``turns`` maps each turn key to its rows:
    ``{"position", "level", "assertion", "kinds", "reason"}``, where ``assertion`` is the engine's form (level
    removed, names normalized), ``kinds`` says which part of the rule accepted the subject and the object, and
    ``reason`` is None when the assertion is loaded.
    """
    turns = turn_index(turns_file)
    order = list(turns)
    owners = owner_names(turns_file)
    problems: list[str] = []
    checked: dict[str, list[dict]] = {}
    entries = facts.get("turns")
    if not isinstance(entries, list):
        return {"problems": ["the facts file needs a 'turns' list"], "turns": {}, "roles": {}}
    last = -1
    for number, entry in enumerate(entries):
        key = f"{entry.get('session')}#{entry.get('index')}"
        if key not in turns:
            problems.append(f"turns[{number}]: no turn {key}")
            continue
        if key in checked:
            problems.append(f"turns[{number}]: {key} is listed twice")
            continue
        position_in_order = order.index(key)
        if position_in_order < last:
            problems.append(f"turns[{number}]: {key} is out of order")
        last = max(last, position_in_order)
        earlier = [turns[before]["text"] for before in order[:position_in_order]]
        rows = []
        for position, item in enumerate(entry.get("assertions") or []):
            if not isinstance(item, dict):
                problems.append(f"{key} [{position}]: an assertion is an object")
                continue
            level = item.get("level")
            if level not in LEVELS:
                problems.append(f"{key} [{position}]: level must be O1 or O2, not {level!r}")
                continue
            raw = {name: value for name, value in item.items() if name != "level"}
            try:
                assertion = Assertion.model_validate(raw).normalized()
            except (ValidationError, TypeError, ValueError) as exc:
                rows.append({"position": position, "level": level, "assertion": raw, "kinds": {},
                             "reason": _reason(exc)})
                continue
            kinds, reason = {}, None
            for field in ("subject", "object"):
                kinds[field] = _kind(getattr(assertion, field), field, assertion, level, turns[key], earlier, owners)
                if kinds[field] is None and reason is None:
                    reason = _refusal(field, assertion, level)
            rows.append({"position": position, "level": level, "kinds": kinds, "reason": reason,
                         "assertion": assertion.model_dump(mode="json", exclude_none=True)})
        checked[key] = rows
    missing = [key for key in order if key not in checked]
    if missing:
        problems.append(f"{len(missing)} turns have no entry: {', '.join(missing)}")
    return {"problems": problems, "turns": checked, "roles": {key: turn["role"] for key, turn in turns.items()}}


def plan(result: dict, levels: tuple[str, ...]) -> dict[str, dict]:
    """What the build loads for each turn at these levels: the accepted assertions and the values the rule accepted.

    ``approved`` holds the casefolded subjects and objects that only this experiment's rule accepts (owner,
    carried, closure), the values :class:`MentionPatch` must let through for that turn.
    """
    loaded = {}
    for key, rows in result["turns"].items():
        accepted = [row for row in rows if row["level"] in levels and row["reason"] is None]
        loaded[key] = {
            "assertions": [row["assertion"] for row in accepted],
            "approved": {row["assertion"][field].casefold() for row in accepted for field in ("subject", "object")
                         if row["kinds"].get(field) != "turn"},
        }
    return loaded


def tally(result: dict, levels: tuple[str, ...]) -> dict:
    """Counts for the report: assertions, accepted, refused by reason, and the assertions each widening needed."""
    rows = [row for found in result["turns"].values() for row in found if row["level"] in levels]
    accepted = [row for row in rows if row["reason"] is None]
    refused = Counter(_reason_kind(row["reason"]) for row in rows if row["reason"] is not None)
    needed = Counter()
    for row in accepted:
        used = set(row["kinds"].values())
        if used == {"turn"}:
            needed["turn only"] += 1
        for kind in ("owner", "carried", "closure"):
            if kind in used:
                needed[kind] += 1
    return {"assertions": len(rows), "turns": sum(any(row["level"] in levels for row in found)
                                                  for found in result["turns"].values()),
            "accepted": len(accepted), "refused": sum(refused.values()),
            "refused_by_reason": dict(refused.most_common()),
            "accepted_needing": {kind: needed[kind] for kind in ("turn only", "owner", "carried", "closure")}}


def _reason_kind(reason: str) -> str:
    if reason.startswith("literal value"):
        return "literal value not in the turn" if reason.endswith("the turn") else "closure value not in earlier turns"
    for field in ("subject", "object"):
        if reason.startswith(f"{field} "):
            return f"{field} not in the turn, not the owner, not named earlier"
    if "is not in the vocabulary" in reason:
        return "relation not in the vocabulary"
    return re.sub(r"'[^']*'", "'...'", reason)


class MentionPatch:
    """While a turn is ingested, the values this experiment's rule accepted for it count as mentioned in its text."""

    def __init__(self) -> None:
        self.current: tuple[str, frozenset[str]] | None = None
        self.uses = 0

    def mentioned(self, value: str, text: str) -> bool:
        if ENGINE_MENTIONED(value, text):
            return True
        if self.current is None:
            return False
        content, approved = self.current
        if value.strip().casefold() in approved and text in content:
            self.uses += 1
            return True
        return False

    @contextlib.contextmanager
    def installed(self):
        saved = assertions_module._mentioned, grounding_module._mentioned
        assertions_module._mentioned = grounding_module._mentioned = self.mentioned
        try:
            yield self
        finally:
            assertions_module._mentioned, grounding_module._mentioned = saved

    @contextlib.contextmanager
    def turn(self, content: str, approved: set[str]):
        self.current = (content, frozenset(approved))
        try:
            yield
        finally:
            self.current = None


def verify_plan(loaded: dict[str, dict], turns_file: dict) -> list[str]:
    """Each turn's accepted assertions, given to ``prepare_assertions`` together under the patch, as ingest() will."""
    turns = turn_index(turns_file)
    patch, failures = MentionPatch(), []
    with patch.installed():
        for key, item in loaded.items():
            if not item["assertions"]:
                continue
            with patch.turn(turns[key]["text"], item["approved"]):
                try:
                    prepare_assertions(turns[key]["text"], item["assertions"])
                except ValueError as exc:
                    failures.append(f"{key}: the engine refuses what the rule accepted: {exc}")
    return failures


# --- The build ----------------------------------------------------------------------------------------------------


class EmptyExtraction:
    """Stands in for the model on turns with no assertions: every turn extracted to nothing, with no call."""

    def __init__(self) -> None:
        self.turns = 0

    def install(self, provider) -> None:
        provider.extract_batch = self.extract_batch
        provider.extract = self.extract
        provider.repair_pronouns = self.repair_pronouns

    async def extract_batch(self, turns, **_options) -> dict:
        self.turns += len(turns)
        return {"turns": [{"turn": turn.index, "entities": [], "facts": [], "relationships": []} for turn in turns]}

    async def extract(self, *_args, **_options):
        raise RuntimeError("the oracle build expects batched extraction only")

    async def repair_pronouns(self, *_args, **_options):
        raise RuntimeError("the oracle build makes no pronoun repair")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(label: str, *, name: str, facts_path: Path, levels: tuple[str, ...], overrides: dict | None = None,
          speaker: bool = False, replace: bool = False) -> dict:
    """Build the probe pack ``label`` of conversation ``name`` from a facts file, and measure it with the probe."""
    import asyncio

    from benchmarks.diagnostics import chat_probe

    conversation_path = ROOT / "benchmarks" / "conversations" / f"{name}.json"
    turns_file = json.loads((HERE / "turns" / f"{name}.json").read_text())
    result = check(json.loads(facts_path.read_text()), turns_file)
    if result["problems"]:
        raise ValueError(f"{facts_path}: " + "; ".join(result["problems"]))
    loaded = plan(result, levels)
    failures = verify_plan(loaded, turns_file)
    if failures:
        raise ValueError("; ".join(failures))
    patch, empty = MentionPatch(), EmptyExtraction()
    ingested = Counter()

    def prepare(engine) -> None:
        empty.install(engine._pipeline._extraction_provider)
        original = engine.ingest

        async def ingest(content, **options):
            item = loaded.get(options["metadata"]["turn_key"], {"assertions": [], "approved": set()})
            if not item["assertions"]:
                ingested["without assertions"] += 1
                return await original(content, **options)
            ingested["with assertions"] += 1
            with patch.turn(content, item["approved"]):
                return await original(content, assertions=item["assertions"], **options)

        engine.ingest = ingest

    with patch.installed():
        # The model build's configuration (extraction through deepseek-v4.1-flash:cloud), so only the facts
        # differ; the stubs answer every extraction call, and the build fails on any model call.
        report = asyncio.run(chat_probe.build(label, conversation_path=conversation_path, overrides=overrides,
                                              model=MODEL, cloud=True, speaker=speaker, cache=False,
                                              replace=replace, prepare=prepare))
    folder = chat_probe.default_root() / name / label
    totals = json.loads((folder / "build.json").read_text())["totals"]
    if totals.get("calls"):
        raise RuntimeError(f"{label}: {totals['calls']} model calls; the oracle build allows none")
    record = {"kind": "oracle-extraction-build", "label": label, "conversation": name, "levels": list(levels),
              "facts": str(facts_path.relative_to(ROOT)) if facts_path.is_relative_to(ROOT) else str(facts_path),
              "facts_sha256": digest(facts_path), "overrides": overrides or {}, "speaker": speaker,
              "model_calls": totals.get("calls", 0), "turns": dict(ingested),
              "empty_extractions": empty.turns, "mention_patch_uses": patch.uses,
              "tally": tally(result, levels)}
    (folder / "oracle.json").write_text(json.dumps(record, indent=2) + "\n")
    return {"report": report, "oracle": record, "folder": folder}
