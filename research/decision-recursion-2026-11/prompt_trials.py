"""Try each decision prompt once on three made-up cases (none from tests/contract), then freeze the prompts.

The cases and their expected answers were written before the first trial run. The result goes in the README
("Prompt freeze"); this script does not tune anything.

    uv run python research/decision-recursion-2026-11/prompt_trials.py --model qwen3.5:4b
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from decide import Decider, warm  # noqa: E402
from prompts import (OPTIONS, POSTHOC_NONE, POSTHOC_TEMPLATES, TEMPLATES, content_words,  # noqa: E402
                     posthoc_sha256, templates_sha256)

# (prompt, fields, options or None for OPTIONS[prompt], expected option)
CASES = [
    ("R1.judge", {"question": "What did Yusuf cook for the party?",
                  "record": "Yusuf cooked a big pot of chili and some cornbread for the party."}, None, "yes"),
    ("R1.judge", {"question": "What did Yusuf cook for the party?",
                  "record": "Yusuf hosted a party on Saturday."}, None, "partly"),
    ("R1.judge", {"question": "What did Yusuf cook for the party?",
                  "record": "The train to Bern was late again."}, None, "no"),
    ("R2.pick_record", {"question": "Which city does the man who sold Bruno his guitar live in?"},
     ["Bruno plays the guitar every evening.", "Bruno bought his guitar from a man named Henrik."],
     "Bruno bought his guitar from a man named Henrik."),
    ("R2.pick_record", {"question": "What colour is the car Greta borrowed?"},
     ["Greta borrowed her uncle's car for the weekend.", "Greta likes red things."],
     "Greta borrowed her uncle's car for the weekend."),
    ("R2.pick_record", {"question": "When does the shop where Yusuf works open?"},
     ["Yusuf said work was busy.", "Yusuf walks to work.", "Yusuf works at a hardware shop called Bolt and Nut."],
     "Yusuf works at a hardware shop called Bolt and Nut."),
    ("R2.pick_word", {"question": "Where is the box Bruno asked for?",
                      "record": "Bruno asked the courier to bring the toolbox tomorrow."}, "words", "toolbox"),
    ("R2.pick_word", {"question": "What does Greta's brother do for work?",
                      "record": "Greta's brother Anton just moved to Oslo."}, "words", "Anton"),
    ("R2.pick_word", {"question": "Who is the dentist Yusuf recommended?",
                      "record": "Yusuf mentioned that the weather was nice."}, "words", "none"),
    ("R3.conflict", {"question": "Where does Greta live?", "date_a": "2026-03-01 09:00",
                     "record_a": "Greta lives in Graz.", "date_b": "2026-07-10 09:00",
                     "record_b": "Greta moved to Linz."}, None, "yes, they conflict"),
    ("R3.conflict", {"question": "What does Yusuf eat?", "date_a": "2026-03-01 09:00",
                     "record_a": "Yusuf eats rice most days.", "date_b": "2026-07-10 09:00",
                     "record_b": "Yusuf eats lentils on Fridays."}, None, "no, both can be true"),
    ("R3.conflict", {"question": "What time does the bakery open?", "date_a": "2026-01-10 09:00",
                     "record_a": "The bakery opens at 7.", "date_b": "2026-05-02 09:00",
                     "record_b": "The bakery now opens at 8."}, None, "yes, they conflict"),
    ("R3.true_now", {"question": "Where does Greta live?", "now": "2026-09-01 09:00"},
     ["(2026-03-01 09:00) Greta lives in Graz.", "(2026-07-10 09:00) Greta moved to Linz."],
     "(2026-07-10 09:00) Greta moved to Linz."),
    ("R3.true_now", {"question": "What time does the bakery open?", "now": "2026-09-01 09:00"},
     ["(2026-05-02 09:00) The bakery now opens at 8.", "(2026-01-10 09:00) The bakery opens at 7."],
     "(2026-05-02 09:00) The bakery now opens at 8."),
    ("R3.true_now", {"question": "Where is the blue kettle now?", "now": "2026-04-01 11:00"},
     ["(2026-04-01 10:20) The blue kettle is on the stove.", "(2026-04-01 10:40) The blue kettle is in the sink."],
     "(2026-04-01 10:40) The blue kettle is in the sink."),
    ("W1.still_true", {"date_old": "2026-02-01 09:00", "old": "I lives in Graz",
                       "date_new": "2026-06-01 09:00", "new": "I moved to Linz"}, None, "no longer true"),
    ("W1.still_true", {"date_old": "2026-02-01 09:00", "old": "I plays chess",
                       "date_new": "2026-06-01 09:00", "new": "I bought a new bike"}, None, "still true"),
    ("W1.still_true", {"date_old": "2026-02-01 09:00", "old": "Mira kin of I",
                       "date_new": "2026-06-01 09:00", "new": "Mira moved to Oslo"}, None, "still true"),
    ("W2.dependent", {"date_retired": "2026-02-01 09:00", "retired": "I works at night bakery",
                      "date_new": "2026-06-01 09:00", "new": "I works at day office",
                      "date_check": "2026-02-01 09:00", "check": "I wakes up at 3 am"}, None, "no longer true"),
    ("W2.dependent", {"date_retired": "2026-02-01 09:00", "retired": "I works at night bakery",
                      "date_new": "2026-06-01 09:00", "new": "I works at day office",
                      "date_check": "2026-02-01 09:00", "check": "I prefers green tea"}, None, "still true"),
    ("W2.dependent", {"date_retired": "2026-02-01 09:00", "retired": "I lives in Graz",
                      "date_new": "2026-06-01 09:00", "new": "I moved to Linz",
                      "date_check": "2026-03-01 09:00", "check": "I commutes by tram in Graz"}, None, "no longer true"),
]


# The post-hoc prompts (R2b, W2b), three made-up cases each, expected answers written before the trial.
POSTHOC_CASES = [
    ("R2b.pick_record", {"question": "Which city does the man who sold Bruno his guitar live in?"},
     ["Bruno plays the guitar every evening.", "Bruno bought his guitar from a man named Henrik.",
      "The train to Bern was late again."], "Bruno bought his guitar from a man named Henrik."),
    ("R2b.pick_record", {"question": "Where is the kite Greta asked for?"},
     ["The weather was windy on Sunday.", "Greta asked the neighbor to bring the kite.", "Yusuf likes chess."],
     "Greta asked the neighbor to bring the kite."),
    ("R2b.pick_record", {"question": "What is the name of Yusuf's dentist?"},
     ["The train to Bern was late again.", "Greta likes red things.", "The bakery opens at 7."], "none of them"),
    ("W2b.depends_on", {"date_check": "2026-02-01 09:00", "check": "I wakes up at 3 am"},
     ["(2026-02-01 09:00) I prefers green tea", "(2026-02-01 09:00) I works at night bakery"],
     "(2026-02-01 09:00) I works at night bakery"),
    ("W2b.depends_on", {"date_check": "2026-02-01 09:00", "check": "I prefers green tea"},
     ["(2026-02-01 09:00) I works at night bakery", "(2026-01-01 09:00) I plays chess"], "none"),
    ("W2b.depends_on", {"date_check": "2026-03-01 09:00", "check": "I commutes by tram in Graz"},
     ["(2026-02-01 09:00) I plays chess", "(2026-01-15 09:00) I lives in Graz"], "(2026-01-15 09:00) I lives in Graz"),
    ("W2b.recheck", {"date_old": "2026-02-01 09:00", "old": "I wakes up at 3 am", "date_dep": "2026-02-01 09:00",
                     "dep": "I works at night bakery", "date_new": "2026-06-01 09:00", "new": "I works at day office"},
     None, "no longer true"),
    ("W2b.recheck", {"date_old": "2026-03-01 09:00", "old": "I commutes by tram in Graz",
                     "date_dep": "2026-01-15 09:00", "dep": "I lives in Graz", "date_new": "2026-06-01 09:00",
                     "new": "I moved to Linz"}, None, "no longer true"),
    ("W2b.recheck", {"date_old": "2026-03-01 09:00", "old": "I plays chess on Mondays", "date_dep": "2026-01-15 09:00",
                     "dep": "I lives in Graz", "date_new": "2026-06-01 09:00", "new": "I moved to Linz"},
     None, "still true"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--backend", choices=("ollama", "systemone"), default="ollama")
    parser.add_argument("--posthoc", action="store_true", help="try the post-hoc prompts (R2b, W2b) instead")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    scratch = Path(tempfile.mkdtemp(prefix="prompt-trials-"))
    decider = Decider(args.backend, args.model, scratch / "cache", scratch / "log.jsonl")
    warm(decider)
    rows = []
    templates = {**TEMPLATES, **POSTHOC_TEMPLATES}
    for name, fields, options, expected in (POSTHOC_CASES if args.posthoc else CASES):
        if options is None:
            options = OPTIONS.get(name) or OPTIONS["W1.still_true"]
        elif name in POSTHOC_NONE:
            options = options + [POSTHOC_NONE[name]]
        elif options == "words":
            options = content_words(fields["record"], fields["question"]) + ["none"]
        choice, confidence = decider.decide(templates[name].format(**fields), options, tag=name)
        rows.append({"prompt": name, "options": options, "expected": expected, "chosen": options[choice],
                     "confidence": confidence, "right": options[choice] == expected})
        print(f"{'ok ' if rows[-1]['right'] else 'BAD'} {name:15} chose {options[choice]!r} (p={confidence}) "
              f"expected {expected!r}")
    result = {"model": args.model, "backend": args.backend, "posthoc": args.posthoc,
              "templates_sha256": posthoc_sha256() if args.posthoc else templates_sha256(), "right": sum(r["right"] for r in rows),
              "of": len(rows), "rows": rows}
    print(f"{result['right']} of {result['of']} right; templates {result['templates_sha256'][:12]}")
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1) + "\n")


if __name__ == "__main__":
    main()
