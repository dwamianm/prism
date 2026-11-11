"""Write the probe conversations' turns, and nothing else, for the annotator (GUIDE.md, "Who sees what").

Each turns/<name>.json holds the conversation's name, user_name, owner, reference_time, whether the probe build
names the owner as the speaker of their turns, and the sessions with their ids, start times and turns (role, text,
and the turn's index in its session). Nothing from the conversation's "key" or "description" is copied. From the
worktree root:

    uv run python research/oracle-extraction-2026-11/export_turns.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
NAMES = ("defects-v1", "chat-v1", "member-v1")
# The probe rule (CLAUDE.md, "Alpha and beta gate"): only the member probe is built with --speaker.
SPEAKER_NAMED = {"member-v1"}


def export(name: str) -> dict:
    conversation = json.loads((ROOT / "benchmarks" / "conversations" / f"{name}.json").read_text())
    return {
        "name": conversation["name"],
        "user_name": conversation["user_name"],
        "owner": conversation["owner"],
        "reference_time": conversation["reference_time"],
        "speaker_named": name in SPEAKER_NAMED,
        "sessions": [{"id": session["id"], "start": session["start"],
                      "turns": [{"index": index, "role": turn["role"], "text": turn["text"]}
                                for index, turn in enumerate(session["turns"])]}
                     for session in conversation["sessions"]],
    }


def main() -> None:
    for name in NAMES:
        target = HERE / "turns" / f"{name}.json"
        target.write_text(json.dumps(export(name), indent=1, ensure_ascii=False) + "\n")
        turns = sum(len(session["turns"]) for session in json.loads(target.read_text())["sessions"])
        print(f"wrote {target.relative_to(ROOT)}: {turns} turns")


if __name__ == "__main__":
    main()
